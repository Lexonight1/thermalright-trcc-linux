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
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import usb.core
import usb.util

from ...core.errors import PermissionError_, TransportError
from ...core.logs import per_frame
from ...core.ports import BulkTransport, WriteBuffer
from ._pyusb_find import find as usb_find

# Optional hidapi backend (the [hid] extra)
try:
    import hid as _hid_module  # pyright: ignore[reportMissingImports]
    HIDAPI_AVAILABLE = True
except ImportError:
    _hid_module = None
    HIDAPI_AVAILABLE = False

# Bound unconditionally so every reference below is well-defined (and
# patchable in tests) on a box with no hid package installed.  Typed Any
# because the two supported bindings expose different classes — see
# _HidBinding.
hidapi: Any = _hid_module

PYUSB_AVAILABLE = True

log = logging.getLogger(__name__)
# ``write``/``read`` fire once per CHUNK of every frame — bulk_lcd sends a frame
# as 16 KiB writes — so they log through the per-frame family and stay silent
# until ``-vvv``.  Their FAILURE paths use ``log``: a USB error is rare, is the
# whole diagnosis when a panel dies mid-session, and must survive every rung.
frame_log = per_frame(__name__)


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
        log.debug("PyUsbBulkTransport.__init__: %04x:%04x serial=%s",
                  vid, pid, serial or "(any)")

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
        log.info("PyUsbBulkTransport.close: %04x:%04x (was_open=%s)",
                 self._vid, self._pid, self._is_open)
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
        frame_log.debug("PyUsbBulkTransport.is_open: %s", self._is_open)
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
            log.warning("PyUsbBulkTransport.write: transport not open "
                        "(%04x:%04x)", self._vid, self._pid)
            raise TransportError("Transport not open")
        ep = self._ep_out if self._ep_out is not None else endpoint
        try:
            sent = self._device.write(ep, data, timeout=timeout_ms)
        except usb.core.USBError as e:
            log.warning("PyUsbBulkTransport.write: ep=0x%02x %d byte(s) failed "
                        "after %dms: %s", ep, len(data), timeout_ms, e)
            raise TransportError(f"USB write failed: {e}") from e
        frame_log.debug("PyUsbBulkTransport.write: ep=0x%02x %d/%d byte(s)",
                        ep, sent, len(data))
        return sent

    def read(self, endpoint: int, length: int,
             timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bytes:
        if not self._is_open or self._device is None:
            log.warning("PyUsbBulkTransport.read: transport not open "
                        "(%04x:%04x)", self._vid, self._pid)
            raise TransportError("Transport not open")
        ep = self._ep_in if self._ep_in is not None else endpoint
        try:
            data = bytes(self._device.read(ep, length, timeout=timeout_ms))
        except usb.core.USBError as e:
            log.warning("PyUsbBulkTransport.read: ep=0x%02x want %d byte(s), "
                        "failed after %dms: %s", ep, length, timeout_ms, e)
            raise TransportError(f"USB read failed: {e}") from e
        frame_log.debug("PyUsbBulkTransport.read: ep=0x%02x %d/%d byte(s)",
                        ep, len(data), length)
        return data

    @property
    def ep_out(self) -> int | None:
        log.debug("PyUsbBulkTransport.ep_out: 0x%02x", self._ep_out or 0)
        return self._ep_out

    @property
    def ep_in(self) -> int | None:
        log.debug("PyUsbBulkTransport.ep_in: 0x%02x", self._ep_in or 0)
        return self._ep_in

    def __enter__(self) -> PyUsbBulkTransport:
        log.debug("PyUsbBulkTransport.__enter__: %04x:%04x",
                  self._vid, self._pid)
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        log.debug("__exit__: closing transport (exc=%s)", exc[0] if exc else None)
        self.close()


# =========================================================================
# HidApiTransport — hidapi backend (alternative for HID-only devices)
# =========================================================================
#
# TWO different PyPI packages both import as ``hid``, with incompatible
# APIs, and OUR OWN packaging ships a different one per distro:
#
#   binding                     shipped by                  class
#   cython-hidapi (PyPI hidapi) pyproject, RPM, Arch, Gentoo hid.device
#   apmorton      (PyPI hid)    the Debian/Ubuntu .deb       hid.Device
#
#                     construct                open              non-blocking
#   hid.device        device()  (args ignored) .open(vid,pid,sn) .set_nonblocking(0)
#   hid.Device        Device(vid=,pid=,serial=) in the ctor      .nonblocking = 0
#
# ``read`` / ``write`` / ``close`` agree, so only the open path differs.
# One ABC + a child per binding, composed into the transport (composition,
# not inheritance, so read/write/close stay single-sourced) — adding a third
# binding is one more child.  (#244 / #253: the old code picked
# ``hid.device`` then used ``hid.Device``'s API, so it never called
# ``.open()`` at all and died on ``.nonblocking`` — every pip / Fedora /
# Arch install of a quirked HID panel crashed on connect.)


class _HidBinding(ABC):
    """One ``hid`` python binding.  Children differ only in how a handle
    is opened and put into blocking mode; everything downstream is shared."""

    #: Attribute name of this binding's device class on the ``hid`` module.
    CLASS_ATTR: ClassVar[str]

    @classmethod
    def device_class(cls) -> Any | None:
        klass = getattr(hidapi, cls.CLASS_ATTR, None) if HIDAPI_AVAILABLE else None
        log.debug("%s.device_class: hidapi=%s attr=%r -> %s",
                  cls.__name__, HIDAPI_AVAILABLE, cls.CLASS_ATTR,
                  "present" if klass else "absent")
        return klass

    @classmethod
    def _require_class(cls) -> Any:
        """The device class, or ImportError.  ``detect()`` already proved it
        exists; this narrows for the type checker and guards direct use."""
        klass = cls.device_class()
        if klass is None:
            log.warning("%s._require_class: hid module exposes no %r",
                        cls.__name__, cls.CLASS_ATTR)
            raise ImportError(f"hid module exposes no {cls.CLASS_ATTR!r}")
        return klass

    @classmethod
    def detect(cls) -> type[_HidBinding] | None:
        """The binding actually installed, or None."""
        for child in (_CythonHidBinding, _ApmortonHidBinding):
            if child.device_class() is not None:
                log.info("_HidBinding.detect: using %s", child.__name__)
                return child
        log.warning("_HidBinding.detect: NO hid binding installed — "
                    "HID panels cannot be opened (pip install hidapi)")
        return None

    @classmethod
    def open_errors(cls) -> tuple[type[BaseException], ...]:
        """What this binding raises when a handle cannot be opened.

        cython-hidapi raises ``OSError``; apmorton raises its own
        ``hid.HIDException``, which is NOT an ``OSError`` — resolved lazily
        by name so the tuple is correct whichever package is installed.
        """
        extra = getattr(hidapi, "HIDException", None) if HIDAPI_AVAILABLE else None
        errors = (OSError, extra) if isinstance(extra, type) else (OSError,)
        log.debug("%s.open_errors: %s", cls.__name__,
                  [e.__name__ for e in errors])
        return errors

    @classmethod
    @abstractmethod
    def open(cls, vid: int, pid: int, serial: str | None) -> Any:
        """Return an opened, blocking-mode handle.

        Raises one of :meth:`open_errors` when the device is absent or
        inaccessible."""


class _CythonHidBinding(_HidBinding):
    """PyPI ``hidapi`` (cython-hidapi) — ``hid.device``."""

    CLASS_ATTR: ClassVar[str] = "device"

    @classmethod
    def open(cls, vid: int, pid: int, serial: str | None) -> Any:
        log.info("_CythonHidBinding.open: %04x:%04x serial=%s",
                 vid, pid, serial or "(any)")
        handle = cls._require_class()()        # ctor takes no useful args
        handle.open(vid, pid, serial)          # THE call the old code skipped
        handle.set_nonblocking(0)
        return handle


class _ApmortonHidBinding(_HidBinding):
    """PyPI ``hid`` (apmorton) — ``hid.Device``, opens in its constructor."""

    CLASS_ATTR: ClassVar[str] = "Device"

    @classmethod
    def open(cls, vid: int, pid: int, serial: str | None) -> Any:
        log.info("_ApmortonHidBinding.open: %04x:%04x serial=%s",
                 vid, pid, serial or "(any)")
        handle = cls._require_class()(vid=vid, pid=pid, serial=serial)
        handle.nonblocking = 0
        return handle


class HidApiTransport(BulkTransport):
    """USB transport via hidapi.

    Report-based (max 64 bytes per report for interrupt endpoints).
    Large bulk transfers should prefer PyUsbBulkTransport.
    """

    def __init__(self, vid: int, pid: int,
                 serial: str | None = None) -> None:
        if not HIDAPI_AVAILABLE:
            raise ImportError(
                "hidapi not installed — pip install hidapi "
                "(also libhidapi: apt install libhidapi-dev)"
            )
        self._vid = vid
        self._pid = pid
        self._serial = serial
        self._device: Any = None
        self._is_open = False
        log.debug("HidApiTransport.__init__: %04x:%04x serial=%s",
                  vid, pid, serial or "(any)")

    def open(self) -> bool:
        binding = _HidBinding.detect()
        if binding is None:
            raise ImportError(
                "the installed 'hid' module exposes neither 'device' "
                "(cython-hidapi) nor 'Device' (apmorton hid) — install "
                "'hidapi' from PyPI, or python3-hid on Debian/Ubuntu"
            )
        log.info("HidApiTransport.open: %04x:%04x via %s",
                 self._vid, self._pid, binding.__name__)
        try:
            self._device = binding.open(self._vid, self._pid, self._serial)
        except binding.open_errors() as e:
            # hidapi reports "open failed" for both absent and EACCES; the
            # udev-rules hint is the actionable half on Linux.
            raise PermissionError_(
                f"cannot open HID device {self._vid:04x}:{self._pid:04x} "
                f"({e}) — device absent, or missing udev rules "
                f"(run `trcc system setup`)"
            ) from e
        self._is_open = True
        return True

    def close(self) -> None:
        log.info("HidApiTransport.close: %04x:%04x (was_open=%s)",
                 self._vid, self._pid, self._is_open)
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        frame_log.debug("HidApiTransport.is_open: %s", self._is_open)
        return self._is_open

    def write(self, endpoint: int, data: WriteBuffer,
              timeout_ms: int = DEFAULT_TIMEOUT_MS) -> int:
        if not self._is_open or self._device is None:
            log.warning("HidApiTransport.write: transport not open "
                        "(%04x:%04x)", self._vid, self._pid)
            raise TransportError("Transport not open")
        # hidapi prepends a report ID byte (0x00 for default); bytes(data)
        # normalizes any buffer (incl. a memoryview slice) before concat.
        sent = self._device.write(bytes([0x00]) + bytes(data))
        frame_log.debug("HidApiTransport.write: %d byte(s) + report id -> %s",
                        len(data), sent)
        return sent

    def read(self, endpoint: int, length: int,
             timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bytes:
        if not self._is_open or self._device is None:
            log.warning("HidApiTransport.read: transport not open (%04x:%04x)",
                        self._vid, self._pid)
            raise TransportError("Transport not open")
        data = self._device.read(length, timeout_ms)
        frame_log.debug("HidApiTransport.read: want %d, got %d byte(s)",
                        length, len(data) if data else 0)
        return bytes(data) if data else b''

    def __enter__(self) -> HidApiTransport:
        log.debug("HidApiTransport.__enter__: %04x:%04x", self._vid, self._pid)
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        log.debug("__exit__: closing transport (exc=%s)", exc[0] if exc else None)
        self.close()
