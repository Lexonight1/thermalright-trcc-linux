"""USB Bulk-Only Transport (BOT) wrapper for ScsiTransport.

On OSes without a kernel SCSI passthrough (macOS, FreeBSD, OpenBSD) we
frame SCSI CDBs as USB BOT: CBW → data phase → CSW, all over plain
bulk transfers.  This class composes on top of any `BulkTransport` so
the USB plumbing isn't duplicated.

BOT is defined by USB Mass Storage Class — every mass-storage device
accepts this wire protocol, so one class covers every non-Linux,
non-Windows OS.

Not used on Linux (SG_IO is faster and doesn't require kernel-driver
detach) nor on Windows (DeviceIoControl is native).
"""
from __future__ import annotations

import logging
import struct

from ...core.device_recovery import is_disconnect_error
from ...core.errors import TransportError
from ...core.logs import per_frame
from ...core.ports import BulkTransport
from .scsi_transport import BaseScsiTransport, CdbResult

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


# USB Bulk-Only Transport constants ---------------------------------------

_CBW_SIGNATURE = 0x43425355   # 'USBC'
_CSW_SIZE = 13
_CBW_DIR_OUT = 0x00
_CBW_DIR_IN = 0x80

# USB mass-storage spec endpoints (vendors override via auto-detect)
_EP_OUT = 0x02
_EP_IN = 0x81


class UsbBotScsiTransport(BaseScsiTransport):
    """SCSI over USB Bulk-Only Transport -- composes on a BulkTransport.

    The wrapped bulk transport is the handle; this class adds the CBW/CSW
    framing.  Logging, the failure policy and open/close are
    ``BaseScsiTransport``'s, as on Linux and Windows.
    """

    def __init__(self, bulk: BulkTransport) -> None:
        log.debug("__init__: bulk=%s", bulk)
        self._bulk = bulk
        self._tag = 0
        super().__init__("USB BOT")

    def _open_handle(self) -> bool:
        log.debug("UsbBotScsiTransport._open_handle")
        return self._bulk.open()

    def _close_handle(self) -> None:
        log.debug("UsbBotScsiTransport._close_handle")
        self._bulk.close()

    def _send(self, cdb: bytes, data: bytes, timeout_ms: int) -> CdbResult:
        """CBW, the data, then the CSW."""
        cbw = self._build_cbw(len(data), _CBW_DIR_OUT, cdb)
        try:
            self._bulk.write(_EP_OUT, cbw, timeout_ms)
            if data:
                self._bulk.write(_EP_OUT, data, timeout_ms)
            csw = self._bulk.read(_EP_IN, _CSW_SIZE, timeout_ms)
        except TransportError as e:
            log.debug("UsbBotScsiTransport._send: %s", e)
            return CdbResult(ok=False, gone=is_disconnect_error(e),
                             detail=f"transfer failed: {e}")
        return _csw_result(csw)

    def _read(self, cdb: bytes, length: int, timeout_ms: int) -> CdbResult:
        """CBW, up to *length* bytes in, then the CSW."""
        cbw = self._build_cbw(length, _CBW_DIR_IN, cdb)
        try:
            self._bulk.write(_EP_OUT, cbw, timeout_ms)
            data = self._bulk.read(_EP_IN, length, timeout_ms)
            csw = self._bulk.read(_EP_IN, _CSW_SIZE, timeout_ms)
        except TransportError as e:
            log.debug("UsbBotScsiTransport._read: %s", e)
            return CdbResult(ok=False, gone=is_disconnect_error(e),
                             detail=f"transfer failed: {e}")
        result = _csw_result(csw)
        return result._replace(data=data) if result.ok else result

    def _build_cbw(self, data_length: int, direction: int, cdb: bytes) -> bytes:
        """Command Block Wrapper — 31 bytes, CDB zero-padded to 16.  One per
        CDB, so its line is per-frame."""
        frame_log.debug("_build_cbw: data_length=%s direction=%s", data_length, direction)
        self._tag = (self._tag + 1) & 0xFFFFFFFF
        cbw = struct.pack(
            "<IIIBBB",
            _CBW_SIGNATURE,
            self._tag,
            data_length,
            direction,
            0,                 # LUN
            len(cdb),
        )
        return cbw + cdb.ljust(16, b"\x00")


def _csw_result(csw: bytes) -> CdbResult:
    """A Command Status Wrapper's verdict: status 0 is a clean completion."""
    if len(csw) >= _CSW_SIZE and csw[12] == 0:
        return CdbResult(ok=True)
    log.debug("_csw_result: %s", csw.hex())
    return CdbResult(ok=False, detail=(
        f"CSW status {csw[12]}" if len(csw) >= _CSW_SIZE else "short CSW"))
