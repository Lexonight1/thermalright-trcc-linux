"""ScsiLcd — Device implementation for SCSI-protocol LCD hardware.

The physical device enumerates as USB mass-storage.  We talk to it via
a `ScsiTransport` (kernel-native on Linux/Windows, userspace BOT on
macOS/BSD) — the transport handles the wire framing, this class only
knows the SCSI CDB vocabulary the device expects.
"""
from __future__ import annotations

import binascii
import logging
import struct
import time
import zlib

from ...core.errors import TransportError
from ...core.models import HandshakeResult, Wire
from ...core.ports import ScsiTransport
from ...core.protocol import get_profile
from . import DeviceFactory
from ._base import BaseDevice

log = logging.getLogger(__name__)


# =========================================================================
# SCSI protocol constants (USBLCD.exe decompiled C#)
# =========================================================================

# Handshake / init
_BOOT_SIGNATURE = b'\xa1\xa2\xa3\xa4'
_BOOT_WAIT_S = 3.0
_BOOT_MAX_RETRIES = 5
_POST_INIT_DELAY_S = 0.1

_POLL_CMD = 0xF5
_INIT_CMD = 0x1F5
_POLL_SIZE = 0xE100

# Frame chunking
_FRAME_CMD_BASE = 0x101F5
_CHUNK_SIZE_LARGE = 0x10000
_CHUNK_SIZE_SMALL = 0xE100
_SMALL_DISPLAY_PIXELS = 76800      # ≤320×240 uses the small chunk size

# Timeouts
_HANDSHAKE_TIMEOUT_MS = 10000
_FRAME_TIMEOUT_MS = 5000

# Boot animation — compressed multi-frame upload to device flash.
# Command bytes from USBLCD.exe reverse engineering: first frame carries
# the total frame count in CDB word2; carousel frames carry the index in
# word2 and pack the dwell-byte into CDB cmd's high byte.
_ANIM_FIRST_CMD = 0x000201F5
_ANIM_CAROUSEL_CMD = 0x000301F5
_ANIM_COMPRESS_LEVEL = 3       # zlib level — fast, matches the Windows app
_ANIM_FIRST_DELAY_S = 0.5      # 500 ms settle after first-frame upload
_ANIM_FRAME_DELAY_S = 0.01     # 10 ms pacing between carousel frames
_ANIM_MAX_FRAMES = 249         # firmware rejects ≥ 250
_ANIM_DELAY_MAX_BYTE = 250     # wire byte saturates at 250 (≈ 2.5 s)

# Boot animation is only supported on these display geometries — the
# firmware ignores the carousel upload otherwise.
_BOOT_ANIM_RESOLUTIONS: frozenset[tuple[int, int]] = frozenset({
    (240, 240), (240, 320), (320, 240), (320, 320),
})


# =========================================================================
# ScsiLcd
# =========================================================================


@DeviceFactory.register(Wire.SCSI)
class ScsiLcd(BaseDevice[ScsiTransport]):
    """SCSI LCD device.

    connect():   poll (with boot-retry) + init handshake → FBL
    send(data):  RGB565 frame in 16-byte-CDB chunked writes
    disconnect(): close transport

    ``self._profile`` (set below from the reported FBL) carries PM-derived
    geometry + encoding flags, so ``send`` and the DisplayService pipeline
    honor what the device actually claims — not the registry's static
    ``native_resolution``.
    """

    # ── Device ABC ────────────────────────────────────────────────────

    def _do_handshake(self) -> HandshakeResult:
        """Poll (with boot-retry) + init, then resolve geometry from the FBL."""
        # Step 1: Poll (data-in) with boot-state check
        poll_cdb = self._build_cdb(_POLL_CMD, _POLL_SIZE)
        response = b""
        for attempt in range(_BOOT_MAX_RETRIES):
            response = self._transport.read_cdb(
                poll_cdb, _POLL_SIZE, _HANDSHAKE_TIMEOUT_MS,
            )
            if len(response) >= 8 and response[4:8] == _BOOT_SIGNATURE:
                log.info("Device %s still booting (attempt %d/%d), waiting %.0fs",
                         self.info.key, attempt + 1, _BOOT_MAX_RETRIES, _BOOT_WAIT_S)
                time.sleep(_BOOT_WAIT_S)
            else:
                break

        fbl = response[0] if response else (self.info.fbl or 100)
        log.debug("SCSI poll byte[0] = %d (FBL)", fbl)

        # Step 2: Init (data-out, 0xE100 zeros)
        init_cdb = self._build_cdb(_INIT_CMD, _POLL_SIZE)
        self._transport.send_cdb(
            init_cdb, b"\x00" * _POLL_SIZE, _HANDSHAKE_TIMEOUT_MS,
        )

        # Step 3: let the display controller settle
        time.sleep(_POST_INIT_DELAY_S)

        # Geometry comes from the FBL byte via get_profile — a device that
        # reports e.g. FBL=102 surfaces its resolution from the profile,
        # not the registry's static native_resolution. SCSI uses PM=FBL.
        self._profile = get_profile(fbl, fbl)
        return HandshakeResult(
            resolution=self._profile.resolution,
            model_id=fbl,
            pm_byte=fbl,
            sub_byte=0,
            fbl=fbl,
            raw_response=bytes(response[:64]),
        )

    def _handshake_detail(self, result: HandshakeResult) -> str:
        """SCSI reports one byte — call it FBL, since PM *is* FBL here."""
        return f" (FBL {result.fbl})"

    def _frame_size(self) -> tuple[int, int]:
        """The resolution the device reported, or the registry default."""
        return (self._handshake.resolution if self._handshake
                else self.info.native_resolution)

    def _prepare_frame(self, payload: bytes) -> bytes:
        """Pad the RGB565 payload out to the full frame the CDBs will ask for.

        The chunk sizes are derived from the resolution, so a short payload
        would leave the last CDB waiting for bytes that never arrive.
        """
        width, height = self._frame_size()
        total = width * height * 2       # RGB565 = 2 bytes per pixel
        if len(payload) >= total:
            return payload
        log.debug("ScsiLcd %s: payload %d < expected %d; padding with zeros",
                  self.info.key, len(payload), total)
        return payload + b"\x00" * (total - len(payload))

    def _write_frame(self, frame: bytes) -> bool:
        """Write the frame as 16-byte-CDB chunks sized by resolution class."""
        chunks = self._frame_chunks(*self._frame_size())
        log.debug("ScsiLcd %s: sending %d bytes in %d chunk(s)",
                  self.info.key, len(frame), len(chunks))
        offset = 0
        for cmd, size in chunks:
            cdb = self._build_cdb(cmd, size)
            ok = self._transport.send_cdb(
                cdb, frame[offset:offset + size], _FRAME_TIMEOUT_MS,
            )
            if not ok:
                log.warning(
                    "ScsiLcd %s: chunk write failed at offset %d "
                    "(cmd=0x%x size=%d)",
                    self.info.key, offset, cmd, size,
                )
                return False
            offset += size
        return True

    # ── Boot animation ────────────────────────────────────────────────

    @property
    def can_boot_animate(self) -> bool:
        return True

    def send_boot_animation(
        self,
        frames: list[bytes],
        delays_ds: list[int],
    ) -> int:
        """Upload a multi-frame compressed boot animation to device flash.

        ``frames`` are RGB565 byte buffers at the device's resolution
        (caller is responsible for encoding).  ``delays_ds[i]`` is the
        dwell time before frame ``i+1`` plays, in **deciseconds** (10ths
        of a second) — matches the legacy USBLCD.exe UI unit.  Range on
        the wire is 1 ds … 25 ds (firmware caps at 250).

        Returns the number of frames successfully uploaded (0 on reject,
        full count on success, partial count on mid-stream failure).
        """
        if not self._transport.is_open:
            raise TransportError(
                f"ScsiLcd {self.info.key} not connected — call connect() first"
            )

        resolution = (self._handshake.resolution if self._handshake
                      else self.info.native_resolution)
        if resolution not in _BOOT_ANIM_RESOLUTIONS:
            log.warning("Boot animation not supported for %dx%d", *resolution)
            return 0

        count = len(frames)
        if count == 0 or count >= _ANIM_MAX_FRAMES:
            log.warning("Boot animation frame count %d out of range (1-%d)",
                        count, _ANIM_MAX_FRAMES - 1)
            return 0

        # Phase 1: first frame — frame_count in word2, no delay byte.
        compressed = zlib.compress(frames[0], _ANIM_COMPRESS_LEVEL)
        cdb = self._build_anim_cdb(_ANIM_FIRST_CMD, count, len(compressed))
        if not self._transport.send_cdb(cdb, compressed, _FRAME_TIMEOUT_MS):
            log.error("Boot animation: first-frame write failed")
            return 0
        log.info("Boot animation: first frame sent (%d bytes compressed, %d total)",
                 len(compressed), count)
        time.sleep(_ANIM_FIRST_DELAY_S)

        # Phase 2: carousel frames — index in word2, delay packed into
        # the cmd's high byte (delays_ds * 10, saturated at 250).
        for idx, frame in enumerate(frames):
            compressed = zlib.compress(frame, _ANIM_COMPRESS_LEVEL)
            delay_ds = delays_ds[idx] if idx < len(delays_ds) else 10
            delay_byte = min(delay_ds * 10, _ANIM_DELAY_MAX_BYTE) & 0xFF
            cmd = _ANIM_CAROUSEL_CMD | (delay_byte << 24)
            cdb = self._build_anim_cdb(cmd, idx, len(compressed))
            if not self._transport.send_cdb(cdb, compressed, _FRAME_TIMEOUT_MS):
                log.error("Boot animation: carousel frame %d write failed", idx)
                return idx  # partial — caller sees how far we got
            time.sleep(_ANIM_FRAME_DELAY_S)

        log.info("Boot animation: %d frames uploaded successfully", count)
        return count

    @staticmethod
    def _build_anim_cdb(cmd: int, word2: int, compressed_size: int) -> bytes:
        """16-byte CDB for compressed-animation commands (no CRC32 trailer).

        Layout differs from the frame CDB:
            [0:4]   cmd (carousel folds dwell-byte into bits [31:24])
            [4:8]   zeros
            [8:12]  word2 — frame_count for first, frame_index for carousel
            [12:16] compressed_size
        """
        return struct.pack("<IIII", cmd, 0, word2, compressed_size)

    # ── SCSI framing ──────────────────────────────────────────────────

    @staticmethod
    def _build_cdb(cmd: int, size: int) -> bytes:
        """Build the 16-byte SCSI CDB: cmd(4) + zeros(8) + size(4) + crc32(4)."""
        header_16 = struct.pack("<I", cmd) + b"\x00" * 8 + struct.pack("<I", size)
        crc = binascii.crc32(header_16) & 0xFFFFFFFF
        full = header_16 + struct.pack("<I", crc)
        return full[:16]

    @staticmethod
    def _frame_chunks(width: int, height: int) -> list[tuple[int, int]]:
        """Compute (cmd, size) pairs for chunked frame send."""
        pixels = width * height
        chunk_size = (_CHUNK_SIZE_SMALL if pixels <= _SMALL_DISPLAY_PIXELS
                      else _CHUNK_SIZE_LARGE)
        total = pixels * 2  # RGB565 = 2 bytes per pixel
        chunks: list[tuple[int, int]] = []
        offset = 0
        idx = 0
        while offset < total:
            size = min(chunk_size, total - offset)
            cmd = _FRAME_CMD_BASE | (idx << 24)
            chunks.append((cmd, size))
            offset += size
            idx += 1
        return chunks
