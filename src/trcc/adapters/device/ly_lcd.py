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

import dataclasses
import logging
import struct

from ...core.errors import (
    HandshakeError,
    TransportError,
)
from ...core.models import HandshakeResult, ProductInfo, Wire
from ...core.ports import BulkTransport, Device
from ...core.protocol import (
    DeviceProfile,
    get_profile,
    pm_to_fbl,
    resolve_encode_sub,
)
from . import DeviceFactory

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


@DeviceFactory.register(Wire.LY)
class LyLcd(Device[BulkTransport]):
    """LY-series USB bulk LCD (Trofeo Vision 9.16)."""

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._pm: int = 0
        self._sub: int = 0
        # LY uses chunk header byte[8]=1, LY1 uses byte[8]=2
        self._chunk_cmd: int = 1 if info.pid == _PID_LY else 2
        # Cached at handshake. FBL 192 (Trofeo Vision 9.16 family) carries
        # both jpeg=True and the multi-sub rotation table — DisplayService
        # reads profile.jpeg / .rotate / .encode_sub_bases from here.
        self._profile: DeviceProfile | None = None

    @property
    def profile(self) -> DeviceProfile | None:
        """Handshake-derived profile; None pre-handshake."""
        return self._profile

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        log.info("LyLcd %s: opening transport", self.info.key)
        if not self._transport.open():
            log.error("LyLcd %s: transport open failed", self.info.key)
            raise HandshakeError(f"Failed to open USB transport for {self.info.key}")

        try:
            self._transport.write(_EP_WRITE, _HANDSHAKE_PAYLOAD, _HANDSHAKE_TIMEOUT_MS)
            resp = self._transport.read(_EP_READ, _HANDSHAKE_READ_SIZE, _HANDSHAKE_TIMEOUT_MS)
        except TransportError as e:
            log.error("LyLcd %s: handshake I/O failed: %s", self.info.key, e)
            raise HandshakeError(f"LyLcd handshake I/O failed: {e}") from e

        if (len(resp) < 37 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1):
            log.error("LyLcd %s: handshake validation failed (len=%d)",
                      self.info.key, len(resp))
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

        fbl = pm_to_fbl(self._pm, self._sub)
        # Fold the sub-byte override into encode_base now that SUB is known so
        # render-time resolve_encode_angle only needs the user orientation
        # (C# ImageToJpg mySubMode branch — FBL 192 sub 2/3/4 → 0°). (#169)
        base = get_profile(fbl, self._pm)
        self._profile = dataclasses.replace(
            base,
            encode_base=resolve_encode_sub(base, self._sub),
            encode_sub_bases=(),
        )

        result = HandshakeResult(
            resolution=self._profile.resolution,
            model_id=self._pm,
            pm_byte=self._pm,
            sub_byte=self._sub,
            fbl=fbl,
            raw_response=bytes(resp[:64]),
        )
        self._handshake = result
        log.info("LyLcd handshake OK: PM=%d SUB=%d resolution=%s (pid=0x%04x)",
                 self._pm, self._sub, result.resolution, self.info.pid)
        return result

    def send(self, payload: bytes) -> bool:
        if not self._transport.is_open:
            log.error("LyLcd %s: send() called before connect()", self.info.key)
            raise TransportError(
                f"LyLcd {self.info.key} not connected — call connect() first"
            )

        total_size = len(payload)
        num_chunks = total_size // _CHUNK_DATA_SIZE + 1
        log.debug("LyLcd %s: sending %d-byte payload in %d chunks",
                  self.info.key, total_size, num_chunks)
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

        def _write_frame() -> bool:
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

        # Shared reconnect + recovery policy (base Device): a transport error
        # gets one in-place close→open→handshake retry (heals a stale handle
        # after resume, #189) before escalating to the recovery tracker.
        return self._send_with_recovery(_write_frame)

    def disconnect(self) -> None:
        log.info("LyLcd %s: disconnecting", self.info.key)
        self._transport.close()
        self._handshake = None
        self._profile = None
