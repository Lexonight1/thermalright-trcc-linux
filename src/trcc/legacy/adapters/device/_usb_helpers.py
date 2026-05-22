"""Shared USB device lifecycle helpers for bulk-class transports.

Eliminates duplication between BulkDevice and LyDevice — both use the same
find → detach → configure → claim sequence, endpoint discovery, and close().

BulkFrameDevice is the shared base class; subclasses implement only
handshake() and send_frame().
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trcc.legacy.core.models import UsbAddress

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers (not exported — used only by open_usb_device)
# ---------------------------------------------------------------------------

_ERR_NOT_FOUND = "USB device {vid:04x}:{pid:04x} not found"


def close_usb_device(dev: Any, interface: int = 0) -> None:
    """Release interface and dispose USB device resources.

    Safe to call on None or already-closed devices.
    """
    if dev is None:
        return
    import usb.util
    try:
        usb.util.release_interface(dev, interface)
    except (OSError, AttributeError) as e:
        # Best-effort cleanup — interface may already be released.
        log.debug("usb_helpers: release_interface failed: %s", e)
    try:
        usb.util.dispose_resources(dev)
    except (OSError, AttributeError) as e:
        # Best-effort cleanup — resources may already be disposed.
        log.debug("usb_helpers: dispose_resources failed: %s", e)


def _err_interface_busy() -> str:
    """Platform-aware error for USB interface claim failure after driver detach."""
    from trcc.legacy.core.platform import LINUX
    if LINUX:
        return (
            "USB interface busy — SELinux may be blocking USB device access. "
            "Run 'sudo trcc setup-selinux' to install the policy module, "
            "then unplug and replug the device."
        )
    return (
        "USB interface busy — the kernel driver could not be detached. "
        "Ensure no other application is using the device and try again."
    )
_ERR_EBUSY = (
    "USB device {vid:04x}:{pid:04x} interface is in use by another process. "
    "Close any other TRCC instances and try again."
)


def _find_vendor_interface(cfg: Any) -> Any:
    """Prefer vendor-specific interface (bInterfaceClass=255), fallback to (0,0)."""
    for candidate in cfg:
        if candidate.bInterfaceClass == 255:  # type: ignore[union-attr]
            return candidate
    return cfg[(0, 0)]  # type: ignore[index]


def _detach_kernel_drivers(dev: Any, count: int = 4) -> bool:
    """Detach kernel drivers from interfaces 0..count-1.

    Returns True if the driver could not be detached (driver still active
    after detach attempt -- on Linux this typically means SELinux blocking).
    """
    import usb.core  # type: ignore[import-untyped]

    detach_blocked = False
    for i in range(count):
        try:
            if not dev.is_kernel_driver_active(i):  # type: ignore[union-attr]
                continue
            dev.detach_kernel_driver(i)  # type: ignore[union-attr]
            # Verify detach actually worked
            if dev.is_kernel_driver_active(i):  # type: ignore[union-attr]
                detach_blocked = True
                log.warning("Kernel driver still active on interface %d after "
                            "detach — the OS may be blocking USB ioctls", i)
            else:
                log.debug("Detached kernel driver from interface %d", i)
        except usb.core.USBError as e:
            log.debug("Could not detach kernel driver from interface %d: %s", i, e)
            try:
                if dev.is_kernel_driver_active(i):  # type: ignore[union-attr]
                    detach_blocked = True
                    log.warning("Kernel driver still active on interface %d "
                                "after detach error — the OS may be blocking", i)
            except (usb.core.USBError, NotImplementedError):
                pass
        except NotImplementedError:
            pass
    return detach_blocked


def _reset_and_refind(dev: Any, vid: int, pid: int) -> Any:
    """Reset USB device, wait, re-find, detach drivers. Returns new handle."""
    from . import _pyusb_find

    dev.reset()  # type: ignore[union-attr]
    time.sleep(0.5)
    new_dev = _pyusb_find.find(idVendor=vid, idProduct=pid)
    if new_dev is None:
        raise RuntimeError(_ERR_NOT_FOUND.format(vid=vid, pid=pid))
    _detach_kernel_drivers(new_dev)
    return new_dev


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def open_usb_device(
    vid: int, pid: int,
    *, usb_address: UsbAddress | None = None,
) -> tuple[Any, Any]:
    """Find, configure, and claim a vendor-class USB device.

    Handles the full USB lifecycle:
      find -> detach kernel drivers -> configure
      -> find vendor interface -> claim (with EBUSY retry) -> return

    ``usb_address`` (bus + address) binds to a specific physical USB device
    when two coolers share VID:PID (issue #128). Without it, pyusb returns
    the first match — fine for single-device users, ambiguous for dual.

    Returns:
        (device, interface) tuple ready for endpoint detection.

    Raises:
        RuntimeError: Device not found, or kernel driver could not be detached.
        usb.core.USBError: Permission denied (errno 13).
    """
    import usb.core  # type: ignore[import-untyped]  # for USBError below
    import usb.util  # type: ignore[import-untyped]

    from . import _pyusb_find

    kwargs: dict[str, Any] = {'idVendor': vid, 'idProduct': pid}
    if usb_address is not None:
        kwargs['custom_match'] = usb_address.matches
    dev = _pyusb_find.find(**kwargs)
    if dev is None:
        where = f" @ {usb_address}" if usb_address else ""
        raise RuntimeError(_ERR_NOT_FOUND.format(vid=vid, pid=pid) + where)

    # 1. Detach kernel drivers (post-verification included)
    detach_blocked = _detach_kernel_drivers(dev)

    # 2. Configure device (skip if already configured)
    try:
        cfg = dev.get_active_configuration()  # type: ignore[union-attr]
        log.debug("Device already configured, skipping set_configuration()")
    except usb.core.USBError as e:
        if e.errno == 13:  # EACCES — permission denied
            raise
        log.debug("No active configuration, calling set_configuration()")
        try:
            dev.set_configuration()  # type: ignore[union-attr]
        except usb.core.USBError:
            log.warning("set_configuration() failed, resetting device and retrying")
            dev = _reset_and_refind(dev, vid, pid)
            dev.set_configuration()  # type: ignore[union-attr]
        cfg = dev.get_active_configuration()  # type: ignore[union-attr]

    # 3. Find vendor-specific interface
    intf = _find_vendor_interface(cfg)

    # 4. Claim interface
    try:
        usb.util.claim_interface(dev, intf.bInterfaceNumber)  # type: ignore[union-attr]
    except usb.core.USBError as e:
        if e.errno != 16:  # Not EBUSY
            raise
        if detach_blocked:
            raise RuntimeError(_err_interface_busy()) from e
        raise RuntimeError(_ERR_EBUSY.format(vid=vid, pid=pid)) from e

    return dev, intf


# ---------------------------------------------------------------------------
# BulkFrameDevice — shared base for BulkDevice + LyDevice
# ---------------------------------------------------------------------------

class BulkFrameDevice:
    """Shared base for USB bulk-transport LCD devices (Bulk + LY).

    Provides: __init__, _open() (endpoint discovery), close().
    Subclasses implement: handshake(), send_frame().
    """

    def __init__(
        self, vid: int, pid: int, usb_path: str = "",
        *, usb_address: UsbAddress | None = None,
    ):
        self.vid = vid
        self.pid = pid
        self.usb_path = usb_path
        self.usb_address = usb_address  # disambiguates dual same-VID/PID coolers (#128)
        self._dev: Any = None
        self._ep_out: Any = None
        self._ep_in: Any = None
        self._intf: int = 0
        self.pm: int = 0
        self.sub_type: int = 0
        self.width: int = 0
        self.height: int = 0
        self.use_jpeg: bool = True
        self._raw_handshake: bytes = b""

    def _open(self) -> None:
        """Find and claim the USB device, discover bulk IN/OUT endpoints."""
        import usb.util

        dev, intf = open_usb_device(self.vid, self.pid, usb_address=self.usb_address)

        self._ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
            ),
        )
        self._ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
            ),
        )

        if self._ep_out is None or self._ep_in is None:
            raise RuntimeError("Could not find bulk IN/OUT endpoints")

        self._intf = intf.bInterfaceNumber  # type: ignore[union-attr]
        self._dev = dev
        log.info("Opened %s %04x:%04x (EP OUT=0x%02x, EP IN=0x%02x)",
                 type(self).__name__, self.vid, self.pid,
                 self._ep_out.bEndpointAddress,  # type: ignore[union-attr]
                 self._ep_in.bEndpointAddress)  # type: ignore[union-attr]
        # Autosuspend is managed by the udev rule
        # (``power/autosuspend_delay_ms=10000``) — that's the kernel-native
        # path the v9.5.7 #143 fix landed on.  We used to override it from
        # userspace here by writing ``-1`` to ``power/autosuspend``, which
        # silently undid the udev tuning for Bulk/LY devices and meant
        # those panels never slept on shutdown.

    def close(self) -> None:
        """Release USB device."""
        if self._dev is not None:
            close_usb_device(self._dev, self._intf)
            self._dev = None
            self._ep_out = None
            self._ep_in = None
            log.info("%s closed", type(self).__name__)
