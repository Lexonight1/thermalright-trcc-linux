"""
USB bulk device handler for Trofeo Vision 9.16 LCD (0x0416:0x5408 / 0x5409).

Protocol reverse-engineered from TRCC v2.1.2 USBLCDNEW.dll:
  ThreadSendDeviceDataLY  (PID 0x5408, EP9 OUT)
  ThreadSendDeviceDataLY1 (PID 0x5409, EP2 OUT)

Protocol:
  1. Handshake: write 2048 bytes {0x02, 0xFF, zeros...},
     read 512 bytes.  Validate resp[0]==3, resp[1]==0xFF, resp[8]==1.
  2. PM extraction:
     - PID 0x5408: PM = 64 + resp[20]  (clamp resp[20] min=1)
     - PID 0x5409: PM = 50 + resp[36]
  3. Frame send: image data chunked into 512-byte blocks
     (16-byte header + 496 bytes payload), sent in 4096-byte
     USB bulk writes, then read 512-byte ACK.
"""

from __future__ import annotations

import logging
import struct
from typing import TYPE_CHECKING

from trcc.legacy.core.models import (
    FRAME_WRITE_TIMEOUT_MS,
    HANDSHAKE_TIMEOUT_LY_MS,
    TRACE_LEVEL,
    HandshakeResult,
    fbl_to_resolution,
    pm_to_fbl,
)

from ._usb_helpers import BulkFrameDevice
from .frame import FrameDevice

if TYPE_CHECKING:
    from trcc.legacy.core.models import UsbAddress

log = logging.getLogger(__name__)

# -- Constants ----------------------------------------------------------------

_PID_LY = 0x5408
_PID_LY1 = 0x5409

# Handshake: 16-byte header + 2032 zero padding = 2048 bytes total
_HANDSHAKE_HEADER = bytes([
    0x02, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
])
_HANDSHAKE_PAYLOAD = _HANDSHAKE_HEADER + bytes(2032)

_HANDSHAKE_READ_SIZE = 512
# Handshake + frame-write timeouts live in core/models/protocol.py — see
# the comment block there for the per-transport calibration rationale.
_HANDSHAKE_TIMEOUT_MS = HANDSHAKE_TIMEOUT_LY_MS
_WRITE_TIMEOUT_MS = FRAME_WRITE_TIMEOUT_MS
_READ_TIMEOUT_MS = HANDSHAKE_TIMEOUT_LY_MS  # same calibration as handshake

_CHUNK_SIZE = 512       # Total bytes per chunk (header + data)
_CHUNK_HEADER_SIZE = 16
_CHUNK_DATA_SIZE = 496  # _CHUNK_SIZE - _CHUNK_HEADER_SIZE
_USB_WRITE_SIZE = 4096  # Bytes per USB bulk write


def _ly_resolution(pm: int, sub: int = 0) -> tuple[int, int]:
    """Map LY device PM+SUB to (width, height) via shared pipeline."""
    fbl = pm_to_fbl(pm, sub)
    return fbl_to_resolution(fbl, pm)


# -- LyDevice ----------------------------------------------------------------

class LyDevice(BulkFrameDevice, FrameDevice):
    """USB bulk device handler for LY-type LCDs (0416:5408 / 0416:5409).

    Uses pyusb for raw bulk endpoint I/O.  The kernel must not have
    claimed the interface (no usb-storage, no usbhid).
    """

    def __init__(
        self, vid: int, pid: int, usb_path: str = "",
        *, usb_address: UsbAddress | None = None,
    ):
        super().__init__(vid, pid, usb_path, usb_address=usb_address)
        # LY uses chunk header byte[8]=1, LY1 uses 2
        self._chunk_cmd: int = 1 if pid == _PID_LY else 2

    def handshake(self) -> HandshakeResult:
        """Send 2048-byte handshake, read 512-byte response.

        C# ThreadSendDeviceDataLY / ThreadSendDeviceDataLY1:
          Write: 02 FF 00 00 00 00 00 00 01 00 00 00 00 00 00 00 + 2032 zeros
          Read:  512 bytes, validate [0]==3, [1]==FF, [8]==1
        """
        if self._dev is None:
            self._open()

        assert self._ep_out is not None
        assert self._ep_in is not None

        self._ep_out.write(_HANDSHAKE_PAYLOAD, timeout=_HANDSHAKE_TIMEOUT_MS)
        log.debug("LY handshake sent (%d bytes)", len(_HANDSHAKE_PAYLOAD))

        resp = bytes(self._ep_in.read(
            _HANDSHAKE_READ_SIZE, timeout=_HANDSHAKE_TIMEOUT_MS
        ))
        self._raw_handshake = resp
        log.info("LY handshake response: %d bytes", len(resp))
        log.debug("Response hex (first 48): %s",
                  " ".join(f"{b:02x}" for b in resp[:48]))

        # Validate: C# checks array[0]==3 && array[1]==0xFF && array[8]==1
        if len(resp) < 37 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1:
            log.warning("LY handshake validation failed: [0]=%s [1]=%s [8]=%s",
                        resp[0] if len(resp) > 0 else "N/A",
                        resp[1] if len(resp) > 1 else "N/A",
                        resp[8] if len(resp) > 8 else "N/A")
            return HandshakeResult(raw_response=resp)

        # Extract PM based on PID variant
        if self.pid == _PID_LY:
            # C#: if (array[20] <= 3) { array[20] = 1; }
            # PM = 64 + array[20]
            raw_pm_byte = resp[20]
            if raw_pm_byte <= 3:
                raw_pm_byte = 1
            self.pm = 64 + raw_pm_byte
            self.sub_type = resp[22] + 1 if len(resp) > 22 else 0
        else:
            # LY1: PM = 50 + resp[36], SUB = resp[22]
            self.pm = 50 + resp[36]
            self.sub_type = resp[22] if len(resp) > 22 else 0

        self.use_jpeg = True  # LY devices use JPEG (FBL 192 is in JPEG_MODE_FBLS)

        resolution = _ly_resolution(self.pm, self.sub_type)
        self.width, self.height = resolution

        fbl = pm_to_fbl(self.pm, self.sub_type)
        log.info("LY handshake OK: PM=%d, SUB=%d, FBL=%d, resolution=%s, pid=0x%04x",
                 self.pm, self.sub_type, fbl, resolution, self.pid)

        return HandshakeResult(
            resolution=resolution,
            model_id=fbl,
            pm_byte=self.pm,
            sub_byte=self.sub_type,
            raw_response=resp,
        )

    def send_frame(self, image_data: bytes) -> bool:
        """Send one frame via chunked bulk write.

        C# ThreadSendDeviceDataLY frame protocol:
          1. Split payload into 512-byte chunks (16-byte header + 496 data).
          2. Chunk header:
               [0]    = 0x01
               [1]    = 0xFF
               [2-5]  = total payload size (LE32)
               [6-7]  = this chunk's data length (LE16)
               [8]    = cmd (1 for LY, 2 for LY1)
               [9-10] = total number of chunks (LE16)
               [11-12]= chunk index (LE16)
               [13-15]= padding zeros
          3. Pad chunk count to multiple of 4 (LY) or 1 (LY1).
          4. Send all chunks in 4096-byte USB bulk writes.
          5. Read 512-byte ACK from device.
        """
        if self._dev is None or self._ep_out is None:
            self.handshake()

        assert self._ep_out is not None
        assert self._ep_in is not None

        total_size = len(image_data)
        num_chunks = total_size // _CHUNK_DATA_SIZE + 1
        last_chunk_data = total_size % _CHUNK_DATA_SIZE

        # Build all 512-byte chunks
        chunks = bytearray(num_chunks * _CHUNK_SIZE)
        for i in range(num_chunks):
            offset = i * _CHUNK_SIZE
            is_last = (i == num_chunks - 1)
            data_len = last_chunk_data if is_last else _CHUNK_DATA_SIZE

            # 16-byte header
            chunks[offset] = 0x01
            chunks[offset + 1] = 0xFF
            struct.pack_into("<I", chunks, offset + 2, total_size)
            struct.pack_into("<H", chunks, offset + 6, data_len)
            chunks[offset + 8] = self._chunk_cmd
            struct.pack_into("<H", chunks, offset + 9, num_chunks)
            struct.pack_into("<H", chunks, offset + 11, i)
            # bytes 13-15 stay zero

            # Copy data payload
            src_offset = i * _CHUNK_DATA_SIZE
            # C# copies from shared memory at offset 64 (LY) or 20 (LY1).
            # For us, image_data IS the payload — no shared memory offset.
            chunks[offset + _CHUNK_HEADER_SIZE:offset + _CHUNK_HEADER_SIZE + data_len] = (
                image_data[src_offset:src_offset + data_len]
            )

        # Pad chunk count to multiple of 4 (LY) or 1 (LY1)
        pad_multiple = 4 if self.pid == _PID_LY else 1
        padded_chunks = num_chunks
        remainder = padded_chunks % pad_multiple
        if remainder != 0:
            padded_chunks += pad_multiple - remainder
        total_bytes = padded_chunks * _CHUNK_SIZE

        # Extend buffer with zero-padded chunks if needed
        send_buf = bytes(chunks) + bytes(total_bytes - len(chunks))

        for attempt in range(2):
            try:
                # Send in 4096-byte batches
                pos = 0
                while pos < total_bytes:
                    remaining = total_bytes - pos
                    if remaining >= _USB_WRITE_SIZE:
                        write_size = _USB_WRITE_SIZE
                    else:
                        # C#: sends 2048 for the tail (LY), variable for LY1
                        write_size = min(2048, remaining) if self.pid == _PID_LY else remaining
                    self._ep_out.write(
                        send_buf[pos:pos + write_size], timeout=_WRITE_TIMEOUT_MS
                    )
                    pos += _USB_WRITE_SIZE  # C# always advances by 4096

                # Read ACK
                self._ep_in.read(_HANDSHAKE_READ_SIZE, timeout=_READ_TIMEOUT_MS)

                log.log(TRACE_LEVEL,
                        "LY frame sent: %dx%d, %d bytes, %d chunks",
                        self.width, self.height, total_size, num_chunks)
                return True
            except OSError:
                if attempt == 0:
                    log.warning("LY send failed, reconnecting and retrying")
                    self.close()
                    self.handshake()
                else:
                    log.exception("LY frame send failed after retry (%d bytes, %d chunks)",
                                  total_size, num_chunks)
        return False

    # close() inherited from BulkFrameDevice
