"""BulkLcd — Device implementation for raw-bulk USBLCDNew devices.

Vendor-specific (bInterfaceClass=255) LCD hardware that doesn't speak
SCSI or HID.  Protocol from USBLCDNew.exe ThreadSendDeviceData
(87AD:70DB GrandVision series and related products).

Handshake:   write 64-byte request → read 1024-byte response.
             PM at resp[24], SUB at resp[36].
Frame send:  64-byte header + payload (JPEG or raw RGB565),
             chunked into 16 KiB USB writes, ZLP on 512-byte alignment.
"""
from __future__ import annotations

import logging
import struct
from typing import Set

from ...core.errors import HandshakeError, TransportError
from ...core.models import HandshakeResult, ProductInfo
from ...core.ports import BulkTransport, Device

log = logging.getLogger(__name__)


# ── Wire constants ─────────────────────────────────────────────────────

_EP_WRITE = 0x01
_EP_READ = 0x81

_HANDSHAKE_PAYLOAD = bytes([
    0x12, 0x34, 0x56, 0x78, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 1, 0, 0, 0,
    0, 0, 0, 0,
])

_HANDSHAKE_READ_SIZE = 1024
_HANDSHAKE_TIMEOUT_MS = 1000
_WRITE_TIMEOUT_MS = 5000
_WRITE_CHUNK_SIZE = 16 * 1024

# PM values that use raw RGB565 (cmd=3); everything else uses JPEG (cmd=2).
_RGB565_PMS: Set[int] = {32}


class BulkLcd(Device[BulkTransport]):
    """Raw USB bulk LCD device (USBLCDNew protocol)."""

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._pm: int = 0
        self._sub: int = 0
        self._use_jpeg: bool = True

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        if not self._transport.open():
            raise HandshakeError(f"Failed to open USB transport for {self.info.key}")

        try:
            self._transport.write(_EP_WRITE, _HANDSHAKE_PAYLOAD, _HANDSHAKE_TIMEOUT_MS)
            resp = self._transport.read(_EP_READ, _HANDSHAKE_READ_SIZE, _HANDSHAKE_TIMEOUT_MS)
        except TransportError as e:
            raise HandshakeError(f"BulkLcd handshake I/O failed: {e}") from e

        if len(resp) < 41 or resp[24] == 0:
            raise HandshakeError(
                f"BulkLcd handshake validation failed "
                f"(len={len(resp)}, resp[24]={resp[24] if len(resp) > 24 else 'N/A'})"
            )

        self._pm = resp[24]
        self._sub = resp[36]
        self._use_jpeg = self._pm not in _RGB565_PMS

        result = HandshakeResult(
            resolution=self.info.native_resolution,
            model_id=self._pm,
            pm_byte=self._pm,
            sub_byte=self._sub,
            raw_response=bytes(resp[:64]),
        )
        self._handshake = result
        log.info("BulkLcd handshake OK: PM=%d SUB=%d resolution=%s (%s)",
                 self._pm, self._sub, result.resolution,
                 "JPEG" if self._use_jpeg else "RGB565")
        return result

    def send(self, payload: bytes) -> bool:
        if not self._transport.is_open:
            raise TransportError(
                f"BulkLcd {self.info.key} not connected — call connect() first"
            )

        width, height = self.info.native_resolution
        cmd = 2 if self._use_jpeg else 3

        header = bytearray(64)
        header[0:4] = _HANDSHAKE_PAYLOAD[0:4]
        struct.pack_into("<I", header, 4, cmd)
        struct.pack_into("<I", header, 8, width)
        struct.pack_into("<I", header, 12, height)
        struct.pack_into("<I", header, 56, 2)
        struct.pack_into("<I", header, 60, len(payload))

        frame = bytes(header) + payload

        try:
            for offset in range(0, len(frame), _WRITE_CHUNK_SIZE):
                self._transport.write(
                    _EP_WRITE, frame[offset:offset + _WRITE_CHUNK_SIZE],
                    _WRITE_TIMEOUT_MS,
                )
            # Zero-length packet on 512-byte alignment (frame delimiter)
            if len(frame) % 512 == 0:
                self._transport.write(_EP_WRITE, b"", _WRITE_TIMEOUT_MS)
            return True
        except TransportError:
            log.exception("BulkLcd frame send failed (cmd=%d, %d bytes)",
                          cmd, len(payload))
            return False

    def disconnect(self) -> None:
        self._transport.close()
        self._handshake = None
