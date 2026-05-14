"""USB Bulk-Only Transport (BOT) SCSI base.

macOS and FreeBSD/OpenBSD both reach USB mass-storage devices the same
way: detach the kernel driver, claim the interface, frame SCSI CDBs as
USB BOT (CBW → data → CSW) via pyusb.  This base owns that shared
implementation; OS subclasses only differ in log labels and install
hints.
"""
from __future__ import annotations

import logging
import struct
from typing import TYPE_CHECKING, Any, ClassVar

from trcc.adapters.device.scsi import ScsiTransport

if TYPE_CHECKING:
    from trcc.core.models import UsbAddress

log = logging.getLogger(__name__)

# USB Bulk-Only Transport (BOT) constants
CBW_SIGNATURE = 0x43425355  # Command Block Wrapper
CBW_SIZE = 31
CSW_SIZE = 13


class UsbBotScsiTransport(ScsiTransport):
    """SCSI over USB Bulk-Only Transport via pyusb.

    Subclasses customize `_platform_name` and `_pyusb_install_hint` for
    OS-appropriate logging.  The wire protocol (CBW/data/CSW framing,
    kernel-driver detach, endpoint auto-detect) is identical.
    """

    _platform_name: ClassVar[str] = "USB BOT"
    _pyusb_install_hint: ClassVar[str] = "pyusb not installed"

    def __init__(
        self, vid: int, pid: int,
        *, usb_address: UsbAddress | None = None,
    ) -> None:
        self._vid = vid
        self._pid = pid
        self._usb_address = usb_address  # bind to specific (bus, address) — issue #128
        self._dev: Any = None
        self._ep_out: int | None = None
        self._ep_in: int | None = None
        self._tag = 0

    def open(self) -> bool:
        """Find device, detach kernel driver, claim interface."""
        try:
            import usb.util  # pyright: ignore[reportMissingImports]

            from . import _pyusb_find
        except ImportError:
            log.error(self._pyusb_install_hint)
            return False

        kwargs: dict[str, Any] = {'idVendor': self._vid, 'idProduct': self._pid}
        if self._usb_address is not None:
            kwargs['custom_match'] = self._usb_address.matches
        dev: Any = _pyusb_find.find(**kwargs)
        if dev is None:
            where = f" @ {self._usb_address}" if self._usb_address else ""
            log.error("Device %04X:%04X%s not found", self._vid, self._pid, where)
            return False

        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
                log.info("Detached %s kernel driver for %04X:%04X",
                         self._platform_name, self._vid, self._pid)

            dev.set_configuration()

            cfg = dev.get_active_configuration()
            intf = cfg[(0, 0)]
            for ep in intf:
                direction = usb.util.endpoint_direction(ep.bEndpointAddress)
                if direction == usb.util.ENDPOINT_OUT:
                    self._ep_out = ep.bEndpointAddress
                elif direction == usb.util.ENDPOINT_IN:
                    self._ep_in = ep.bEndpointAddress

            if self._ep_out is None or self._ep_in is None:
                log.error("Could not find bulk endpoints")
                return False

            self._dev = dev
            return True

        except Exception:
            log.exception("Failed to open %s SCSI device %04X:%04X",
                          self._platform_name, self._vid, self._pid)
            return False

    def close(self) -> None:
        """Release interface and re-attach kernel driver."""
        if self._dev is not None:
            try:
                import usb.util  # pyright: ignore[reportMissingImports]
                usb.util.dispose_resources(self._dev)
                try:
                    self._dev.attach_kernel_driver(0)
                except (OSError, AttributeError) as e:
                    # Re-attach is best-effort — kernel driver may not have
                    # been the one we detached, or the device disappeared.
                    log.debug(
                        "usb_bot_scsi: attach_kernel_driver(0) failed during close: %s", e,
                    )
            except (OSError, ImportError, AttributeError) as e:
                # Outer cleanup catch — dispose_resources or import failure.
                log.debug("usb_bot_scsi: cleanup failed (already disposed?): %s", e)
            self._dev = None

    def _build_cbw(self, data_length: int, direction: int, cdb: bytes) -> bytes:
        """Build a Command Block Wrapper for the given CDB and direction."""
        self._tag += 1
        cbw = struct.pack(
            '<IIIBBB',
            CBW_SIGNATURE,
            self._tag,
            data_length,
            direction,  # 0x00 = host→device, 0x80 = device→host
            0,          # LUN
            len(cdb),
        )
        return cbw + cdb.ljust(16, b'\x00')

    def send_cdb(
        self,
        cdb: bytes,
        data: bytes,
        *,
        timeout: int = 5000,
    ) -> bool:
        """Send a SCSI CDB with data payload via USB BOT (host→device)."""
        if self._dev is None or self._ep_out is None:
            log.error("%s SCSI device not open", self._platform_name)
            return False

        cbw = self._build_cbw(len(data), 0x00, cdb)

        try:
            self._dev.write(self._ep_out, cbw, timeout=timeout)
            if data:
                self._dev.write(self._ep_out, data, timeout=timeout)

            csw = self._dev.read(self._ep_in, CSW_SIZE, timeout=timeout)
            if len(csw) >= CSW_SIZE:
                status = csw[12]
                if status != 0:
                    log.warning("SCSI command status %d", status)
                    return False
            return True

        except (OSError, ValueError) as e:
            # USB I/O failure (USBError extends OSError); ValueError on bad endpoint.
            log.warning("%s SCSI transfer failed: %s", self._platform_name, e)
            return False

    def read_cdb(
        self,
        cdb: bytes,
        length: int,
        *,
        timeout: int = 5000,
    ) -> bytes:
        """Send a SCSI CDB and read data-in via USB BOT (device→host)."""
        if self._dev is None or self._ep_out is None or self._ep_in is None:
            log.error("%s SCSI device not open", self._platform_name)
            return b''

        cbw = self._build_cbw(length, 0x80, cdb)

        try:
            self._dev.write(self._ep_out, cbw, timeout=timeout)
            data = bytes(self._dev.read(self._ep_in, length, timeout=timeout))

            csw = self._dev.read(self._ep_in, CSW_SIZE, timeout=timeout)
            if len(csw) >= CSW_SIZE and csw[12] != 0:
                log.warning("SCSI read command status %d", csw[12])
                return b''

            return data

        except (OSError, ValueError) as e:
            # USB I/O failure (USBError extends OSError); ValueError on bad endpoint.
            log.warning("%s SCSI read failed: %s", self._platform_name, e)
            return b''

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
