"""LyLcd — Device implementation for Trofeo Vision 9.16 LCD hardware.

Two PID variants on VID 0x0416:
    0x5408 (LY)   — chunk header byte[8]=1, pad chunk count to mult-of-4
    0x5409 (LY1)  — chunk header byte[8]=2, no padding

Handshake:   write 2048 bytes → read 512-byte response.
             Validation: resp[0]=3, resp[1]=0xFF, resp[8]=1.
             PM = 64 + resp[20] (LY) or 50 + resp[36] (LY1).
Frame send:  payload → 512-byte chunks (16-byte header + 496 data),
             sent in 4096-byte USB writes, then 512-byte ACK read.

Protocol reverse-engineered from TRCC v2.1.2 USBLCDNEW.dll
ThreadSendDeviceDataLY / ThreadSendDeviceDataLY1.
"""
from __future__ import annotations

import logging
import struct

from ...core.errors import HandshakeError, TransportError
from ...core.models import HandshakeResult, ProductInfo
from ...core.ports import BulkTransport, Device

log = logging.getLogger(__name__)


# ── Wire constants ─────────────────────────────────────────────────────

_EP_WRITE = 0x01
_EP_READ = 0x81

_PID_LY = 0x5408
_PID_LY1 = 0x5409

_HANDSHAKE_HEADER = bytes([
    0x02, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
])
_HANDSHAKE_PAYLOAD = _HANDSHAKE_HEADER + bytes(2032)

_HANDSHAKE_READ_SIZE = 512
_HANDSHAKE_TIMEOUT_MS = 1000
_WRITE_TIMEOUT_MS = 5000
_READ_TIMEOUT_MS = 1000

_CHUNK_SIZE = 512
_CHUNK_HEADER_SIZE = 16
_CHUNK_DATA_SIZE = 496
_USB_WRITE_SIZE = 4096


class LyLcd(Device[BulkTransport]):
    """LY-series USB bulk LCD (Trofeo Vision 9.16)."""

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._pm: int = 0
        self._sub: int = 0
        # LY uses chunk header byte[8]=1, LY1 uses byte[8]=2
        self._chunk_cmd: int = 1 if info.pid == _PID_LY else 2

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        if not self._transport.open():
            raise HandshakeError(f"Failed to open USB transport for {self.info.key}")

        try:
            self._transport.write(_EP_WRITE, _HANDSHAKE_PAYLOAD, _HANDSHAKE_TIMEOUT_MS)
            resp = self._transport.read(_EP_READ, _HANDSHAKE_READ_SIZE, _HANDSHAKE_TIMEOUT_MS)
        except TransportError as e:
            raise HandshakeError(f"LyLcd handshake I/O failed: {e}") from e

        if (len(resp) < 37 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1):
            raise HandshakeError(
                f"LyLcd handshake validation failed "
                f"([0]={resp[0] if len(resp) > 0 else 'N/A'}, "
                f"[1]={resp[1] if len(resp) > 1 else 'N/A'}, "
                f"[8]={resp[8] if len(resp) > 8 else 'N/A'})"
            )

        # PM extraction differs per variant
        if self.info.pid == _PID_LY:
            raw = resp[20]
            if raw <= 3:
                raw = 1
            self._pm = 64 + raw
            self._sub = resp[22] + 1 if len(resp) > 22 else 0
        else:
            self._pm = 50 + resp[36]
            self._sub = resp[22] if len(resp) > 22 else 0

        result = HandshakeResult(
            resolution=self.info.native_resolution,
            model_id=self._pm,
            pm_byte=self._pm,
            sub_byte=self._sub,
            raw_response=bytes(resp[:64]),
        )
        self._handshake = result
        log.info("LyLcd handshake OK: PM=%d SUB=%d resolution=%s (pid=0x%04x)",
                 self._pm, self._sub, result.resolution, self.info.pid)
        return result

    def send(self, payload: bytes) -> bool:
        if not self._transport.is_open:
            raise TransportError(
                f"LyLcd {self.info.key} not connected — call connect() first"
            )

        total_size = len(payload)
        num_chunks = total_size // _CHUNK_DATA_SIZE + 1
        last_chunk_data = total_size % _CHUNK_DATA_SIZE

        chunks = bytearray(num_chunks * _CHUNK_SIZE)
        for i in range(num_chunks):
            offset = i * _CHUNK_SIZE
            is_last = (i == num_chunks - 1)
            data_len = last_chunk_data if is_last else _CHUNK_DATA_SIZE

            # 16-byte chunk header
            chunks[offset] = 0x01
            chunks[offset + 1] = 0xFF
            struct.pack_into("<I", chunks, offset + 2, total_size)
            struct.pack_into("<H", chunks, offset + 6, data_len)
            chunks[offset + 8] = self._chunk_cmd
            struct.pack_into("<H", chunks, offset + 9, num_chunks)
            struct.pack_into("<H", chunks, offset + 11, i)

            src_offset = i * _CHUNK_DATA_SIZE
            chunks[offset + _CHUNK_HEADER_SIZE:offset + _CHUNK_HEADER_SIZE + data_len] = (
                payload[src_offset:src_offset + data_len]
            )

        # Pad chunk count to multiple-of-4 (LY) or 1 (LY1)
        pad_multiple = 4 if self.info.pid == _PID_LY else 1
        padded_chunks = num_chunks
        remainder = padded_chunks % pad_multiple
        if remainder != 0:
            padded_chunks += pad_multiple - remainder
        total_bytes = padded_chunks * _CHUNK_SIZE
        send_buf = bytes(chunks) + bytes(total_bytes - len(chunks))

        try:
            pos = 0
            while pos < total_bytes:
                remaining = total_bytes - pos
                if remaining >= _USB_WRITE_SIZE:
                    write_size = _USB_WRITE_SIZE
                else:
                    write_size = min(2048, remaining) if self.info.pid == _PID_LY else remaining
                self._transport.write(
                    _EP_WRITE, send_buf[pos:pos + write_size], _WRITE_TIMEOUT_MS,
                )
                pos += _USB_WRITE_SIZE

            # ACK read
            self._transport.read(_EP_READ, _HANDSHAKE_READ_SIZE, _READ_TIMEOUT_MS)
            return True
        except TransportError:
            log.exception("LyLcd frame send failed (%d bytes, %d chunks)",
                          total_size, num_chunks)
            return False

    def disconnect(self) -> None:
        self._transport.close()
        self._handshake = None
