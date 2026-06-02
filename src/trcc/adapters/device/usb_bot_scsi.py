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

from ...core.errors import TransportError
from ...core.ports import BulkTransport, ScsiTransport

log = logging.getLogger(__name__)


# USB Bulk-Only Transport constants ---------------------------------------

_CBW_SIGNATURE = 0x43425355   # 'USBC'
_CSW_SIZE = 13
_CBW_DIR_OUT = 0x00
_CBW_DIR_IN = 0x80

# USB mass-storage spec endpoints (vendors override via auto-detect)
_EP_OUT = 0x02
_EP_IN = 0x81


class UsbBotScsiTransport(ScsiTransport):
    """SCSI over USB Bulk-Only Transport — composes on a BulkTransport.

    Lifecycle is delegated to the wrapped bulk transport; this class
    only adds CBW/CSW framing and the `send_cdb` / `read_cdb` shape.
    """

    def __init__(self, bulk: BulkTransport) -> None:
        self._bulk = bulk
        self._tag = 0

    @property
    def is_open(self) -> bool:
        return self._bulk.is_open

    def open(self) -> bool:
        return self._bulk.open()

    def close(self) -> None:
        self._bulk.close()

    def send_cdb(self, cdb: bytes, data: bytes,
                 timeout_ms: int = 5000) -> bool:
        cbw = self._build_cbw(len(data), _CBW_DIR_OUT, cdb)
        try:
            self._bulk.write(_EP_OUT, cbw, timeout_ms)
            if data:
                self._bulk.write(_EP_OUT, data, timeout_ms)
            csw = self._bulk.read(_EP_IN, _CSW_SIZE, timeout_ms)
        except TransportError:
            log.exception("USB BOT send_cdb transfer failed")
            return False
        if len(csw) < _CSW_SIZE or csw[12] != 0:
            log.warning("USB BOT send_cdb CSW status=%d",
                        csw[12] if len(csw) >= _CSW_SIZE else -1)
            return False
        return True

    def read_cdb(self, cdb: bytes, length: int,
                 timeout_ms: int = 5000) -> bytes:
        cbw = self._build_cbw(length, _CBW_DIR_IN, cdb)
        try:
            self._bulk.write(_EP_OUT, cbw, timeout_ms)
            data = self._bulk.read(_EP_IN, length, timeout_ms)
            csw = self._bulk.read(_EP_IN, _CSW_SIZE, timeout_ms)
        except TransportError:
            log.exception("USB BOT read_cdb transfer failed")
            return b""
        if len(csw) < _CSW_SIZE or csw[12] != 0:
            log.warning("USB BOT read_cdb CSW status=%d",
                        csw[12] if len(csw) >= _CSW_SIZE else -1)
            return b""
        return data

    def _build_cbw(self, data_length: int, direction: int, cdb: bytes) -> bytes:
        """Command Block Wrapper — 31 bytes, CDB zero-padded to 16."""
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
