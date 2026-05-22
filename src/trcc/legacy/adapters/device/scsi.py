"""SCSI LCD protocol adapter — command blocks, frame chunking, boot animation.

Platform-agnostic SCSI protocol logic. OS-specific SCSI I/O is injected via
the ScsiTransport ABC — Linux (SG_IO ioctl), Windows (DeviceIoControl),
macOS / BSD (pyusb USB BOT).
"""
from __future__ import annotations

import binascii
import logging
import struct
import time
import zlib
from abc import ABC, abstractmethod

from trcc.legacy.adapters.device.frame import FrameDevice
from trcc.legacy.core.models import HandshakeResult, fbl_to_resolution

log = logging.getLogger(__name__)


# =========================================================================
# SCSI transport ABC — OS-specific implementations injected via DI
# =========================================================================


class ScsiTransport(ABC):
    """Abstract SCSI transport — platform implementations injected via DI.

    Same pattern as UsbTransport in hid.py. Each OS provides a concrete
    implementation; the protocol logic never knows which OS it's on.
    """

    @abstractmethod
    def open(self) -> bool:
        """Open the device for SCSI I/O."""

    @abstractmethod
    def close(self) -> None:
        """Release device resources."""

    @abstractmethod
    def send_cdb(self, cdb: bytes, data: bytes) -> bool:
        """Send a SCSI CDB with data payload. Returns True on success."""

    @abstractmethod
    def read_cdb(self, cdb: bytes, length: int) -> bytes:
        """Send a SCSI CDB and read response. Returns response bytes."""


# =========================================================================
# Protocol constants
# =========================================================================

# Boot signature: device still initializing its display controller
_BOOT_SIGNATURE = b'\xa1\xa2\xa3\xa4'
_BOOT_WAIT_SECONDS = 3.0
_BOOT_MAX_RETRIES = 5
# Brief pause after init before first frame (lets controller settle)
_POST_INIT_DELAY = 0.1

# Base command for frame data chunks; chunk index goes in bits [27:24]
_FRAME_CMD_BASE = 0x101F5
_CHUNK_SIZE_LARGE = 0x10000  # 64 KiB per chunk for large displays (320x320+)
_CHUNK_SIZE_SMALL = 0xE100   # 57,600 bytes per chunk for small displays (≤320x240)
# Threshold: displays with ≤76,800 pixels (320x240=76,800) use small chunks.
# USBLCD.exe Mode 1 (240x240) and Mode 2 (320x240) use 0xE100 chunks;
# Mode 3 (320x320+) uses 0x10000 chunks.
_SMALL_DISPLAY_PIXELS = 76800

# Boot animation SCSI commands (from USBLCD.exe reverse engineering)
_ANIM_FIRST_FRAME = 0x000201F5   # Compressed first frame (CDB[8]=frame_count)
_ANIM_CAROUSEL = 0x000301F5      # Compressed carousel frame (CDB[3]=delay, CDB[8]=frame_index)
_ANIM_COMPRESS_LEVEL = 3         # zlib compression level (fast, matches USBLCD.exe)
_ANIM_FIRST_DELAY_S = 0.5       # 500ms sleep after first frame
_ANIM_FRAME_DELAY_S = 0.01      # 10ms sleep between carousel frames
_ANIM_MAX_FRAMES = 249           # C# rejects >= 250

# Boot animation supported resolutions (C# returns immediately for others)
_BOOT_ANIM_RESOLUTIONS = {
    (240, 240), (240, 320), (320, 240), (320, 320),
}


# =========================================================================
# SCSI device class — protocol logic, transport-agnostic
# =========================================================================


class ScsiDevice(FrameDevice):
    """SCSI LCD device — protocol logic with injected transport."""

    def __init__(
        self,
        device_path: str,
        transport: ScsiTransport,
        width: int = 0,
        height: int = 0,
        vid: int = 0,
        pid: int = 0,
    ):
        self.device_path = device_path
        self._transport = transport
        self.width = width
        self.height = height
        self._vid = vid
        self._pid = pid
        self._initialized = False

    # --- Pure helpers (no transport, used by all platforms) ---

    @staticmethod
    def _get_frame_chunks(width: int, height: int) -> list:
        """Calculate frame chunk commands for a given resolution.

        USBLCD.exe uses different chunk sizes per resolution mode:
          Mode 1/2 (≤320x240): 0xE100 (57,600) byte chunks
          Mode 3   (320x320+): 0x10000 (65,536) byte chunks
        """
        pixels = width * height
        chunk_size = (_CHUNK_SIZE_SMALL if pixels <= _SMALL_DISPLAY_PIXELS
                      else _CHUNK_SIZE_LARGE)
        total = pixels * 2  # RGB565: 2 bytes per pixel
        chunks = []
        offset = 0
        idx = 0
        while offset < total:
            size = min(chunk_size, total - offset)
            cmd = _FRAME_CMD_BASE | (idx << 24)
            chunks.append((cmd, size))
            offset += size
            idx += 1
        return chunks

    @staticmethod
    def _crc32(data: bytes) -> int:
        return binascii.crc32(data) & 0xFFFFFFFF

    @staticmethod
    def _build_header(cmd: int, size: int) -> bytes:
        """Build 20-byte SCSI command header: cmd(4) + zeros(8) + size(4) + crc32(4)."""
        header_16 = struct.pack('<I', cmd) + b'\x00' * 8 + struct.pack('<I', size)
        crc = ScsiDevice._crc32(header_16)
        return header_16 + struct.pack('<I', crc)

    @staticmethod
    def send_frame_via_transport(transport, image_data: bytes, width: int, height: int) -> bool:
        """Send one RGB565 frame via any ScsiTransport. OS-agnostic."""
        chunks = ScsiDevice._get_frame_chunks(width, height)
        total_size = sum(size for _, size in chunks)
        if len(image_data) < total_size:
            image_data += b'\x00' * (total_size - len(image_data))

        offset = 0
        for cmd, size in chunks:
            header = ScsiDevice._build_header(cmd, size)
            ok = transport.send_cdb(header[:16], image_data[offset:offset + size])
            if not ok:
                return False
            offset += size
        return True

    @staticmethod
    def _build_anim_header(cmd: int, word2: int, compressed_size: int) -> bytes:
        """Build 20-byte CDB for compressed animation commands (no CRC).

        Layout: [cmd:4][0:4][word2:4][compressed_size:4][0:4]
        """
        return struct.pack('<IIIII', cmd, 0, word2, compressed_size, 0)

    # --- Transport-backed I/O ---

    def _scsi_read(self, cdb: bytes, length: int) -> bytes:
        """Read via injected transport."""
        return self._transport.read_cdb(cdb, length)

    def _scsi_write(self, header: bytes, data: bytes) -> bool:
        """Write via injected transport."""
        return self._transport.send_cdb(header[:16], data)

    # --- Protocol sequences ---

    def _init_device(self) -> tuple[int, bytes]:
        """Poll + init handshake (must be called before first frame send).

        Matches USBLCD.exe initialization sequence:
        1. Poll (cmd=0xF5) -> read 0xE100 bytes
        2. If bytes[4:8] == 0xA1A2A3A4, device is still booting -> wait 3s, re-poll
        3. Init (cmd=0x1F5) -> write 0xE100 zeros
        4. Brief delay to let display controller settle before first frame

        Returns:
            (FBL byte, raw poll response first 64 bytes).
        """
        poll_header = ScsiDevice._build_header(0xF5, 0xE100)

        # Step 1: Poll with boot state check
        response = b''
        for attempt in range(_BOOT_MAX_RETRIES):
            response = self._scsi_read(poll_header[:16], 0xE100)
            if len(response) >= 8 and response[4:8] == _BOOT_SIGNATURE:
                log.info("Device %s still booting (attempt %d/%d), waiting %.0fs...",
                         self.device_path, attempt + 1, _BOOT_MAX_RETRIES, _BOOT_WAIT_SECONDS)
                time.sleep(_BOOT_WAIT_SECONDS)
            else:
                break

        # Extract FBL from poll response byte[0], or fall back to registry
        if response:
            fbl = response[0]
            log.debug("SCSI poll byte[0] = %d (FBL)", fbl)
        else:
            fbl = self._fbl_from_registry()
            log.warning("SCSI poll returned empty on %s — using registry FBL %d",
                        self.device_path, fbl)

        # Step 2: Init
        init_header = ScsiDevice._build_header(0x1F5, 0xE100)
        self._scsi_write(init_header, b'\x00' * 0xE100)

        # Step 3: Brief delay to let display controller settle
        time.sleep(_POST_INIT_DELAY)

        return fbl, response[:64]

    def _fbl_from_registry(self) -> int:
        """Look up FBL from device registry when poll returns empty."""
        from trcc.legacy.core.models import SCSI_DEVICES
        entry = SCSI_DEVICES.get((self._vid, self._pid))
        if entry is not None:
            return entry.fbl
        log.warning("Device %04X:%04X not in SCSI registry, defaulting to FBL 100",
                    self._vid, self._pid)
        return 100  # Default: 320x320 RGB565

    def _send_frame_data(self, rgb565_data: bytes) -> None:
        """Send one RGB565 frame in SCSI chunks sized for the resolution."""
        chunks = ScsiDevice._get_frame_chunks(self.width, self.height)
        total_size = sum(size for _, size in chunks)
        if len(rgb565_data) < total_size:
            rgb565_data += b'\x00' * (total_size - len(rgb565_data))

        offset = 0
        for cmd, size in chunks:
            header = ScsiDevice._build_header(cmd, size)
            self._scsi_write(header, rgb565_data[offset:offset + size])
            offset += size

    def _send_boot_animation(
        self,
        frames: list[bytes],
        delays: list[int],
    ) -> bool:
        """Send multi-frame boot animation to device flash via SCSI.

        Matches USBLCD.exe compressed animation upload protocol:
        1. Compress first frame with zlib, send with 0x201F5 (frame_count in CDB[8])
        2. For each subsequent frame, compress and send with 0x301F5
           (delay in CDB[3], frame_index in CDB[8])
        """
        if (self.width, self.height) not in _BOOT_ANIM_RESOLUTIONS:
            log.warning("Boot animation not supported for %dx%d", self.width, self.height)
            return False

        n = len(frames)
        if n == 0 or n >= _ANIM_MAX_FRAMES:
            log.warning("Boot animation frame count %d out of range (1-%d)", n, _ANIM_MAX_FRAMES - 1)
            return False

        # Phase 1: Compress and send first frame
        compressed = zlib.compress(frames[0], _ANIM_COMPRESS_LEVEL)
        header = ScsiDevice._build_anim_header(_ANIM_FIRST_FRAME, n, len(compressed))
        if not self._scsi_write(header, compressed):
            log.error("Boot animation: failed to send first frame")
            return False
        log.info("Boot animation: sent first frame (%d bytes compressed, %d frames total)",
                 len(compressed), n)
        time.sleep(_ANIM_FIRST_DELAY_S)

        # Phase 2: Send each carousel frame
        for i in range(n):
            compressed = zlib.compress(frames[i], _ANIM_COMPRESS_LEVEL)
            delay_raw = delays[i] if i < len(delays) else 10
            delay_byte = min(delay_raw * 10, 250) & 0xFF
            cmd = _ANIM_CAROUSEL | (delay_byte << 24)
            header = ScsiDevice._build_anim_header(cmd, i, len(compressed))
            if not self._scsi_write(header, compressed):
                log.error("Boot animation: failed to send frame %d", i)
                return False
            time.sleep(_ANIM_FRAME_DELAY_S)

        log.info("Boot animation: all %d frames sent successfully", n)
        return True

    # --- FrameDevice interface ---

    def handshake(self) -> HandshakeResult:
        """Poll + init the SCSI device.

        Reads FBL from poll response byte[0] and resolves
        the actual LCD resolution via fbl_to_resolution().
        """
        fbl, raw = self._init_device()
        resolution = fbl_to_resolution(fbl)
        self.width, self.height = resolution
        self._initialized = True
        log.info("SCSI handshake OK: FBL=%d, resolution=%s", fbl, resolution)
        return HandshakeResult(
            resolution=resolution, model_id=fbl,
            pm_byte=fbl, sub_byte=0,
            raw_response=raw,
        )

    def send_frame(self, rgb565_data: bytes) -> bool:
        """Send one RGB565 frame."""
        if not self._initialized:
            self.handshake()
        self._send_frame_data(rgb565_data)
        return True

    def send_boot_animation(
        self, frames: list[bytes], delays: list[int],
    ) -> bool:
        """Send boot animation to device flash."""
        if not self._initialized:
            self.handshake()
        return self._send_boot_animation(frames, delays)

    def close(self) -> None:
        """Close transport and mark as uninitialized."""
        self._initialized = False
        self._transport.close()
