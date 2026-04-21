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
from typing import Any, Optional

import usb.core
import usb.util

from ...core.errors import PermissionError_, TransportError
from ...core.ports import BulkTransport

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
                 serial: Optional[str] = None) -> None:
        self._vid = vid
        self._pid = pid
        self._serial = serial
        self._device: Any = None
        self._is_open = False
        self._ep_out: Optional[int] = None
        self._ep_in: Optional[int] = None

    def open(self) -> bool:
        kwargs: dict[str, Any] = {'idVendor': self._vid, 'idProduct': self._pid}
        if self._serial:
            kwargs['serial_number'] = self._serial

        self._device = usb.core.find(**kwargs)
        if self._device is None:
            log.error("USB device %04X:%04X not found", self._vid, self._pid)
            return False

        try:
            if self._device.is_kernel_driver_active(USB_INTERFACE):
                self._device.detach_kernel_driver(USB_INTERFACE)
                log.debug("Detached kernel driver from interface %d", USB_INTERFACE)
        except Exception as e:
            log.debug("Kernel driver detach: %s", e)

        try:
            cfg: Any = self._device.get_active_configuration()
            if cfg.bConfigurationValue != USB_CONFIGURATION:
                self._device.set_configuration(USB_CONFIGURATION)
        except usb.core.USBError as e:
            if e.errno == 13:
                raise PermissionError_(
                    f"USB access denied for {self._vid:04X}:{self._pid:04X} — "
                    "check udev rules or run 'trcc setup'"
                ) from e
            self._device.set_configuration(USB_CONFIGURATION)

        usb.util.claim_interface(self._device, USB_INTERFACE)
        self._is_open = True
        self._detect_endpoints()
        return True

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

    def write(self, endpoint: int, data: bytes,
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
    def ep_out(self) -> Optional[int]:
        return self._ep_out

    @property
    def ep_in(self) -> Optional[int]:
        return self._ep_in

    def __enter__(self) -> "PyUsbBulkTransport":
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

    def __init__(self, vid: int, pid: int,
                 serial: Optional[str] = None) -> None:
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

    def open(self) -> bool:
        kwargs: dict[str, Any] = {'vid': self._vid, 'pid': self._pid}
        if self._serial:
            kwargs['serial'] = self._serial
        DeviceClass = getattr(hidapi, 'device', None) or getattr(hidapi, 'Device', None)
        if DeviceClass is None:
            raise ImportError("hidapi module has neither 'device' nor 'Device' class")
        self._device = DeviceClass(**kwargs)
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

    def write(self, endpoint: int, data: bytes,
              timeout_ms: int = DEFAULT_TIMEOUT_MS) -> int:
        if not self._is_open or self._device is None:
            raise TransportError("Transport not open")
        # hidapi prepends a report ID byte (0x00 for default)
        return self._device.write(bytes([0x00]) + data)

    def read(self, endpoint: int, length: int,
             timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bytes:
        if not self._is_open or self._device is None:
            raise TransportError("Transport not open")
        data = self._device.read(length, timeout_ms)
        return bytes(data) if data else b''

    def __enter__(self) -> "HidApiTransport":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
