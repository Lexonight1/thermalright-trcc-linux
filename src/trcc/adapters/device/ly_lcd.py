"""LyLcd — Device implementation for Trofeo Vision 9.16 LCD hardware.

Two PID variants on VID 0x0416:
    0x5408 (LY)   — chunk header byte[8]=1, pad chunk count to mult-of-4
    0x5409 (LY1)  — chunk header byte[8]=2, no padding

Handshake:   write 2048 bytes → read 512-byte response.
             Validation: resp[0]=3, resp[1]=0xFF, resp[8]=1.
             PM = 64 + resp[20] (LY) or 50 + resp[36] (LY1).
Frame send:  payload → 512-byte chunks (16-byte header + 496 data),
             sent in 4096-byte USB writes, then 512-byte ACK read.

Protocol read from TRCC 2.1.6 ``USBLCDNEW.dll`` — ``ThreadSendDeviceDataLY``
/ ``ThreadSendDeviceDataLY1``, with Form1.cs:1071 naming which shared-memory
slot is PM and which is SUB.  See doc/PROTOCOL_USBLCDNEW.md.
"""
from __future__ import annotations

import dataclasses
import logging
import struct

from ...core.errors import (
    HandshakeError,
)
from ...core.logs import per_frame
from ...core.models import HandshakeResult, ProductInfo, Wire
from ...core.ports import BulkTransport
from ...core.protocol import (
    get_profile,
    pm_to_fbl,
    resolve_encode_rotation,
)
from ._base import BaseBulkDevice

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


# ── Wire constants ─────────────────────────────────────────────────────

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

# NOTE: the JPEG size ceiling that used to live here as `_MAX_FRAME_BYTES =
# 512 * 1024` is gone — `DeviceProfile.max_frame_bytes` now carries the C#'s
# own 450000 for every JPEG panel, which is both stricter and better grounded.
#
# It is worth recording why, because the two numbers agree: the #251 reporter
# measured this exact panel at ~360 KB displayed and ~570 KB ignored, and
# proposed 512 KB as a midpoint.  TRCC 2.1.6 `ImageToJpg` never sends 450000
# bytes or more — and 450000 falls inside the reporter's measured window.  A
# bench measurement on a Trofeo Vision 9.16 and the vendor's own constant
# bracket each other, so the guess is now a citation.

_CHUNK_SIZE = 512
_CHUNK_HEADER_SIZE = 16
_CHUNK_DATA_SIZE = 496
_USB_WRITE_SIZE = 4096


class LyLcd(BaseBulkDevice, wire=Wire.LY):
    """LY-series USB bulk LCD (Trofeo Vision 9.16).

    ``self._profile`` is cached at handshake, carrying the encode rotation
    already resolved for this panel's resolution, encoder and SUB byte —
    DisplayService reads ``profile.jpeg`` / ``.rotate`` / ``.encode_base`` /
    ``.encode_invert`` from there and resolves nothing per frame.

    FBL 192 is shared by 1920x462, 1920x440 and 1280x480, and the C# gives the
    first two a base of 180 at most SUB bytes but 0 at 2, 3 and 4 — which is
    why the rotation is keyed on the resolution the PM byte lands on, never on
    the FBL.
    """

    _EP_WRITE = 0x01

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._pm: int = 0
        self._sub: int = 0
        # LY uses chunk header byte[8]=1, LY1 uses byte[8]=2
        self._chunk_cmd: int = 1 if info.pid == _PID_LY else 2

    # ── Device ABC ────────────────────────────────────────────────────

    def _do_handshake(self) -> HandshakeResult:
        resp = self._exchange(_HANDSHAKE_PAYLOAD, _HANDSHAKE_READ_SIZE,
                              _HANDSHAKE_TIMEOUT_MS)

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
            raw_sub = resp[22] if len(resp) > 22 else 0
            # The +1 is the vendor's own, cited: USBLCDNEW.dll 2.1.6
            # ``ThreadSendDeviceDataLY`` packs this handshake into shared
            # memory as ``obj3[1] = (byte)(1 + array[22])``, and Form1.cs:1071
            # unpacks slot 1 as the SUB argument —
            # ``FormCZTVInit(72, 2, …, shareMemoryValRGB[4], text,
            # shareMemoryValRGB[1])``.  The comment here used to say no
            # citation existed in either tree; that was true only of the 2.0.3
            # decompile, whose USBLCDNEW has no LY thread at all (it declares
            # three VID/PID pairs, 2.1.6 declares five).
            #
            # It was independently established as CORRECT on 2026-08-17,
            # before that citation was found, without hardware:
            #
            #   * #248's reporter states `handshake PM=65 SUB=5` on a real
            #     Trofeo Vision 9.16 — that is this line's OUTPUT, so their
            #     panel puts 4 in resp[22].
            #   * `ADDUserButton` case 65 (UCDevice.cs) keys the cooler's own
            #     button image on the C#'s pmSub: 3 and 5 both give A1LD7,
            #     captioned "Trofeo Vision LCD 9.16"; 4 gives A1LD11, "Trofeo
            #     Vision LCD_9.16 ARGB" — a different SKU.
            #   * For 1920x462 the encode base is 180 at sub 5 and 0 at sub 4.
            #     180 is what every release has shipped for this panel, and
            #     that reporter filed six detailed issues about it (blur
            #     measured by photo-FFT, dropped frames, reconnect, black
            #     canvas, docs, video) without ever reporting an upside-down
            #     image.
            #
            # So resp[22] + 1 reproduces the C#'s pmSub, and this panel's
            # rotation is unchanged by the sub-aware encode table.
            self._sub = raw_sub + 1
        else:
            raw_sub = resp[22] if len(resp) > 22 else 0
            self._pm = 50 + resp[36]
            self._sub = raw_sub

        fbl = pm_to_fbl(self._pm, self._sub)
        # Resolve the wire rotation now that resolution, encoder and SUB are
        # all known, so the render path reads a value and branches on nothing.
        # The C# varies the base by SUB in six families (its ``mySubMode``
        # arms); 1920x462 — this wire's panel — takes base 0 at SUB 2/3/4 and
        # base 180 everywhere else.
        base = get_profile(fbl, self._pm)
        rotation = resolve_encode_rotation(
            base.resolution, base.jpeg, self._sub)
        log.info(
            "LyLcd %s: %dx%d jpeg=%s raw resp[22]=%d → sub=%d → encode "
            "base %d° invert=%s",
            self.info.key, base.width, base.height, base.jpeg, raw_sub,
            self._sub, rotation.base, rotation.invert)

        self._profile = dataclasses.replace(
            base,
            encode_base=rotation.base,
            encode_invert=rotation.invert,
        )

        return HandshakeResult(
            resolution=self._profile.resolution,
            model_id=self._pm,
            pm_byte=self._pm,
            sub_byte=self._sub,
            fbl=fbl,
            raw_response=bytes(resp),
        )

    def _handshake_detail(self, result: HandshakeResult) -> str:
        """The PID picks the variant (LY vs LY1) — worth having on the record."""
        return f" (pid=0x{self.info.pid:04x})"

    def _prepare_frame(self, payload: bytes) -> bytes:
        """Slice the payload into 512-byte chunks (16-byte header + 496 data).

        Chunk count is padded to a multiple of 4 on LY, left alone on LY1.
        """
        total_size = len(payload)
        num_chunks = total_size // _CHUNK_DATA_SIZE + 1
        frame_log.debug("LyLcd %s: sending %d-byte payload in %d chunks",
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
        return bytes(chunks) + bytes(total_bytes - len(chunks))

    def _write_frame(self, frame: bytes) -> bool:
        """4096-byte USB writes over the chunk buffer, then the 512-byte ACK."""
        total_bytes = len(frame)
        pos = 0
        while pos < total_bytes:
            remaining = total_bytes - pos
            if remaining >= _USB_WRITE_SIZE:
                write_size = _USB_WRITE_SIZE
            else:
                write_size = min(2048, remaining) if self.info.pid == _PID_LY else remaining
            self._transport.write(
                self._EP_WRITE, frame[pos:pos + write_size], _WRITE_TIMEOUT_MS,
            )
            pos += _USB_WRITE_SIZE
        # ACK read
        self._transport.read(self._EP_READ, _HANDSHAKE_READ_SIZE, _READ_TIMEOUT_MS)
        return True
