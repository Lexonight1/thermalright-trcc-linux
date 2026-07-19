"""Bulk/interrupt USB transport implementations.

Concrete BulkTransport subclasses.  PyUsbBulkTransport (libusb via pyusb)
is the default for every OS; HidApiTransport is a fallback for devices
that enumerate as pure HID on Windows.

SCSI transports live in the OS platform files (adapters/system/{os}.py)
because Linux (SG_IO) and Windows (DeviceIoControl) are OS-native; only
macOS/BSD fall back to userspace USB BOT.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import usb.core
import usb.util

from ...core.errors import PermissionError_, TransportError
from ...core.ports import BulkTransport, WriteBuffer
from ._pyusb_find import find as usb_find

# Optional hidapi backend (the [hid] extra)
try:
    import hid as hidapi  # pyright: ignore[reportMissingImports]
    HIDAPI_AVAILABLE = True
except ImportError:
    HIDAPI_AVAILABLE = False

PYUSB_AVAILABLE = True

log = logging.getLogger(__name__)


DEFAULT_TIMEOUT_MS = 100
USB_CONFIGURATION = 1
USB_INTERFACE = 0

# Some devices expose 1–4 interfaces (vendor + HID + mass-storage) and
# the kernel may attach drivers to ANY of them; detaching only iface 0
# leaves a driver holding bulk endpoints we need to claim.  Legacy
# iterates 0..3 (open_usb_device:75-107).
_DETACH_INTERFACES = 4
# Seconds to sleep between dev.reset() and the re-find.  Mirrors
# legacy ``_reset_and_refind`` — short enough not to feel like a hang,
# long enough for the kernel to re-enumerate the device.
_RESET_SETTLE_S = 0.5

# Linux errno used by pyusb wraps over libusb.
_ERRNO_EACCES = 13   # Permission denied — udev rules missing
_ERRNO_EBUSY = 16    # Interface claimed by another process


# =========================================================================
# PyUsbBulkTransport — libusb backend (works on Linux/Windows/macOS/BSD)
# =========================================================================


class PyUsbBulkTransport(BulkTransport):
    """USB transport via pyusb (libusb backend).

    C# LibUsbDotNet parity:
        find(vid, pid, serial?) → set_configuration(1) → claim_interface(0)
        → auto-detect endpoints → bulk read/write → release/close.
    """

    def __init__(self, vid: int, pid: int,
                 serial: str | None = None) -> None:
        self._vid = vid
        self._pid = pid
        self._serial = serial
        self._device: Any = None
        self._is_open = False
        self._ep_out: int | None = None
        self._ep_in: int | None = None

    def open(self) -> bool:
        kwargs: dict[str, Any] = {'idVendor': self._vid, 'idProduct': self._pid}
        if self._serial:
            kwargs['serial_number'] = self._serial

        self._device = usb_find(**kwargs)
        if self._device is None:
            log.error("USB device %04X:%04X not found", self._vid, self._pid)
            return False

        # 1. Detach kernel drivers from interfaces 0..3.  USB cooler
        # firmware presents multiple interfaces (vendor + HID + mass-
        # storage); the kernel may attach drivers to any of them, and a
        # held driver on a sibling interface can prevent claiming our
        # vendor one.  Legacy parity: open_usb_device:161 →
        # _detach_kernel_drivers(dev, count=4).
        detach_blocked = self._detach_kernel_drivers()

        # 2. Apply configuration.  If the device is in an unconfigured
        # state and set_configuration fails, dev.reset() + refind clears
        # the stale state and lets us retry — legacy:171-178.
        try:
            cfg: Any = self._device.get_active_configuration()
            if cfg.bConfigurationValue != USB_CONFIGURATION:
                self._device.set_configuration(USB_CONFIGURATION)
        except usb.core.USBError as e:
            if e.errno == _ERRNO_EACCES:
                raise PermissionError_(
                    f"USB access denied for {self._vid:04X}:{self._pid:04X} — "
                    "check udev rules or run 'trcc system setup'"
                ) from e
            log.warning(
                "%04X:%04X: set_configuration failed (%s) — "
                "resetting device and retrying",
                self._vid, self._pid, e,
            )
            self._reset_and_refind()
            self._device.set_configuration(USB_CONFIGURATION)

        # 3. Claim the vendor interface.  EBUSY at this stage means a
        # sibling process holds it (another TRCC instance) or, on
        # Linux, that SELinux is blocking the ioctl even though detach
        # appeared to succeed (detach_blocked=True flags the latter).
        # Legacy:183-191.
        try:
            usb.util.claim_interface(self._device, USB_INTERFACE)
        except usb.core.USBError as e:
            if e.errno != _ERRNO_EBUSY:
                raise
            if detach_blocked:
                raise TransportError(
                    f"USB interface busy for {self._vid:04X}:{self._pid:04X} — "
                    "SELinux may be blocking USB ioctls.  Run "
                    "'trcc system setup' to install the policy module, then "
                    "unplug and replug the device.",
                ) from e
            raise TransportError(
                f"USB device {self._vid:04X}:{self._pid:04X} interface is in "
                "use by another process.  Close any other TRCC instances "
                "and try again.",
            ) from e

        self._is_open = True
        self._detect_endpoints()
        return True

    def _detach_kernel_drivers(self) -> bool:
        """Detach kernel drivers from interfaces 0..``_DETACH_INTERFACES-1``.

        Returns ``True`` when a driver remained active after the detach
        call — on Linux this typically means SELinux blocking the
        ioctl.  Caller uses the flag to phrase the EBUSY error
        appropriately.
        """
        detach_blocked = False
        for i in range(_DETACH_INTERFACES):
            try:
                if not self._device.is_kernel_driver_active(i):
                    continue
                self._device.detach_kernel_driver(i)
                # Verify the detach actually took.
                if self._device.is_kernel_driver_active(i):
                    detach_blocked = True
                    log.warning(
                        "%04X:%04X: kernel driver still active on iface %d "
                        "after detach — OS may be blocking USB ioctls",
                        self._vid, self._pid, i,
                    )
                else:
                    log.debug("Detached kernel driver from iface %d", i)
            except usb.core.USBError as e:
                log.debug(
                    "Could not detach kernel driver from iface %d: %s", i, e,
                )
                try:
                    if self._device.is_kernel_driver_active(i):
                        detach_blocked = True
                except (usb.core.USBError, NotImplementedError):
                    pass
            except NotImplementedError:
                # Windows / macOS pyusb backends don't implement the
                # kernel-driver methods — silently skip; their drivers
                # are managed by the OS (WinUSB / IOKit) outside libusb.
                pass
        return detach_blocked

    def _reset_and_refind(self) -> None:
        """Reset the device and re-acquire the handle.

        Used when ``set_configuration`` fails — the device is likely in
        a stale state from a prior crashed app instance.  Resetting
        forces the kernel to re-enumerate; we then have to re-find
        because the original handle is now invalid, and re-detach
        kernel drivers on the new handle (the kernel will have
        re-attached them during enumeration).
        """
        try:
            self._device.reset()
        except usb.core.USBError as e:
            log.debug(
                "%04X:%04X: dev.reset() raised: %s",
                self._vid, self._pid, e,
            )
        time.sleep(_RESET_SETTLE_S)
        kwargs: dict[str, Any] = {
            'idVendor': self._vid, 'idProduct': self._pid,
        }
        if self._serial:
            kwargs['serial_number'] = self._serial
        new_device = usb_find(**kwargs)
        if new_device is None:
            raise TransportError(
                f"USB device {self._vid:04X}:{self._pid:04X} disappeared "
                "after reset",
            )
        self._device = new_device
        self._detach_kernel_drivers()

    def close(self) -> None:
        if self._device is not None:
            try:
                usb.util.release_interface(self._device, USB_INTERFACE)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self._device)
            except Exception:
                pass
            self._device = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    def _detect_endpoints(self) -> None:
        try:
            cfg = self._device.get_active_configuration()
            intf = cfg[(USB_INTERFACE, 0)]
            for ep in intf:
                direction = usb.util.endpoint_direction(ep.bEndpointAddress)
                if direction == usb.util.ENDPOINT_OUT and self._ep_out is None:
                    self._ep_out = ep.bEndpointAddress
                elif direction == usb.util.ENDPOINT_IN and self._ep_in is None:
                    self._ep_in = ep.bEndpointAddress
            log.debug("Endpoints detected: OUT=0x%02x IN=0x%02x",
                      self._ep_out or 0, self._ep_in or 0)
        except Exception as e:
            log.debug("Endpoint auto-detection failed: %s", e)

    def write(self, endpoint: int, data: WriteBuffer,
              timeout_ms: int = DEFAULT_TIMEOUT_MS) -> int:
        if not self._is_open or self._device is None:
            raise TransportError("Transport not open")
        ep = self._ep_out if self._ep_out is not None else endpoint
        try:
            return self._device.write(ep, data, timeout=timeout_ms)
        except usb.core.USBError as e:
            raise TransportError(f"USB write failed: {e}") from e

    def read(self, endpoint: int, length: int,
             timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bytes:
        if not self._is_open or self._device is None:
            raise TransportError("Transport not open")
        ep = self._ep_in if self._ep_in is not None else endpoint
        try:
            return bytes(self._device.read(ep, length, timeout=timeout_ms))
        except usb.core.USBError as e:
            raise TransportError(f"USB read failed: {e}") from e

    @property
    def ep_out(self) -> int | None:
        return self._ep_out

    @property
    def ep_in(self) -> int | None:
        return self._ep_in

    def __enter__(self) -> PyUsbBulkTransport:
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# =========================================================================
# HidApiTransport — hidapi backend (alternative for HID-only devices)
# =========================================================================


class HidApiTransport(BulkTransport):
    """USB transport via hidapi.

    Report-based (max 64 bytes per report for interrupt endpoints).
    Large bulk transfers should prefer PyUsbBulkTransport.
    """

    def __init__(
        self,
        vid: int,
        pid: int,
        serial: str | None = None,
        *,
        device_release: int | None = None,
    ) -> None:
        if not HIDAPI_AVAILABLE:
            raise ImportError(
                "hidapi not installed — pip install hidapi "
                "(also libhidapi: apt install libhidapi-dev)"
            )
        self._vid = vid
        self._pid = pid
        self._serial = serial
        self._device_release = device_release
        self._device: Any = None
        self._is_open = False

    def open(self) -> bool:
        DeviceClass = getattr(hidapi, 'device', None) or getattr(hidapi, 'Device', None)
        if DeviceClass is None:
            raise ImportError("hidapi module has neither 'device' nor 'Device' class")
        if getattr(hidapi, 'device', None) is DeviceClass:
            self._device = DeviceClass()
            if self._serial:
                self._device.open(self._vid, self._pid, self._serial)
            else:
                self._device.open(self._vid, self._pid)
        else:
            kwargs: dict[str, Any] = {'vid': self._vid, 'pid': self._pid}
            if self._serial:
                kwargs['serial'] = self._serial
            self._device = DeviceClass(**kwargs)
        set_nonblocking = getattr(self._device, "set_nonblocking", None)
        if callable(set_nonblocking):
            set_nonblocking(0)
        else:
            self._device.nonblocking = 0
        self._is_open = True
        return True

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def device_release(self) -> int | None:
        return self._device_release

    @property
    def uses_hid_reports(self) -> bool:
        return True

    def write(self, endpoint: int, data: WriteBuffer,
              timeout_ms: int = DEFAULT_TIMEOUT_MS) -> int:
        if not self._is_open or self._device is None:
            raise TransportError("Transport not open")
        # hidapi prepends a report ID byte (0x00 for default); bytes(data)
        # normalizes any buffer (incl. a memoryview slice) before concat.
        payload = bytes(data)
        transferred = self._device.write(bytes([0x00]) + payload)
        # BulkTransport reports payload bytes, while hidapi includes the report
        # ID in its successful byte count.
        if transferred == len(payload) + 1:
            return len(payload)
        return transferred

    def read(self, endpoint: int, length: int,
             timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bytes:
        if not self._is_open or self._device is None:
            raise TransportError("Transport not open")
        data = self._device.read(length, timeout_ms)
        return bytes(data) if data else b''

    def __enter__(self) -> HidApiTransport:
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
