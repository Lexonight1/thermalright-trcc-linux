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
from typing import List, Tuple

from ...core.errors import HandshakeError, TransportError
from ...core.models import HandshakeResult, ProductInfo
from ...core.ports import Device, ScsiTransport

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


# =========================================================================
# ScsiLcd
# =========================================================================


class ScsiLcd(Device[ScsiTransport]):
    """SCSI LCD device.

    connect():   poll (with boot-retry) + init handshake → FBL
    send(data):  RGB565 frame in 16-byte-CDB chunked writes
    disconnect(): close transport
    """

    def __init__(self, info: ProductInfo, transport: ScsiTransport) -> None:
        super().__init__(info, transport)

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        """Open transport, perform poll + init handshake, return FBL."""
        if not self._transport.open():
            raise HandshakeError(
                f"Failed to open SCSI transport for {self.info.key}"
            )

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

        result = HandshakeResult(
            resolution=self.info.native_resolution,
            model_id=fbl,
            pm_byte=fbl,
            sub_byte=0,
            fbl=fbl,
            raw_response=bytes(response[:64]),
        )
        self._handshake = result
        log.info("SCSI handshake OK: FBL=%d, resolution=%s",
                 fbl, result.resolution)
        return result

    def send(self, payload: bytes) -> bool:
        """Send one RGB565 frame, chunked by resolution class."""
        if not self._transport.is_open:
            raise TransportError(
                f"ScsiLcd {self.info.key} not connected — call connect() first"
            )

        width, height = (self._handshake.resolution if self._handshake
                         else self.info.native_resolution)
        chunks = self._frame_chunks(width, height)
        total = sum(size for _, size in chunks)

        data = payload
        if len(data) < total:
            data = data + b"\x00" * (total - len(data))

        offset = 0
        for cmd, size in chunks:
            cdb = self._build_cdb(cmd, size)
            ok = self._transport.send_cdb(
                cdb, data[offset:offset + size], _FRAME_TIMEOUT_MS,
            )
            if not ok:
                log.warning("SCSI frame chunk failed at offset %d", offset)
                return False
            offset += size
        return True

    def disconnect(self) -> None:
        self._transport.close()
        self._handshake = None

    # ── SCSI framing ──────────────────────────────────────────────────

    @staticmethod
    def _build_cdb(cmd: int, size: int) -> bytes:
        """Build the 16-byte SCSI CDB: cmd(4) + zeros(8) + size(4) + crc32(4)."""
        header_16 = struct.pack("<I", cmd) + b"\x00" * 8 + struct.pack("<I", size)
        crc = binascii.crc32(header_16) & 0xFFFFFFFF
        full = header_16 + struct.pack("<I", crc)
        return full[:16]

    @staticmethod
    def _frame_chunks(width: int, height: int) -> List[Tuple[int, int]]:
        """Compute (cmd, size) pairs for chunked frame send."""
        pixels = width * height
        chunk_size = (_CHUNK_SIZE_SMALL if pixels <= _SMALL_DISPLAY_PIXELS
                      else _CHUNK_SIZE_LARGE)
        total = pixels * 2  # RGB565 = 2 bytes per pixel
        chunks: List[Tuple[int, int]] = []
        offset = 0
        idx = 0
        while offset < total:
            size = min(chunk_size, total - offset)
            cmd = _FRAME_CMD_BASE | (idx << 24)
            chunks.append((cmd, size))
            offset += size
            idx += 1
        return chunks
