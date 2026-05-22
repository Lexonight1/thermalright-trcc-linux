"""
Raw USB bulk device handler for USBLCDNew-type devices.

Handles devices with bInterfaceClass=255 (Vendor Specific) that use
raw USB bulk transfers instead of SCSI or HID.  Protocol reverse-engineered
from USBLCDNew.exe ThreadSendDeviceData (87AD:70DB GrandVision series).

Protocol:
  1. Handshake: write 64 bytes {0x12,0x34,0x56,0x78,...,byte[56]=0x01},
     read 1024 bytes.  resp[24]=PM, resp[36]=SUB.
  2. Frame send: 64-byte header + payload, bulk write.
     cmd=2 for JPEG (all PMs except 32), cmd=3 for raw RGB565 (PM=32).
     ZLP after payload as frame delimiter.
"""

from __future__ import annotations

import logging
import struct

from trcc.legacy.core.models import (
    FRAME_WRITE_TIMEOUT_MS,
    HANDSHAKE_TIMEOUT_BULK_MS,
    TRACE_LEVEL,
    HandshakeResult,
    fbl_to_resolution,
    pm_to_fbl,
)

from ._usb_helpers import BulkFrameDevice
from .frame import FrameDevice

log = logging.getLogger(__name__)


# Handshake payload: 64 bytes from USBLCDNew ThreadSendDeviceData
_HANDSHAKE_PAYLOAD = bytes([
    0x12, 0x34, 0x56, 0x78, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 1, 0, 0, 0,
    0, 0, 0, 0,
])

# PM values with explicit resolution overrides for bulk devices.
# All others default to FBL=72 → 480x480.
# C# v2.1.2 FormCZTVInit: all PM values that get special handling.
_BULK_KNOWN_PMS = {5, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 32, 50, 64, 65, 66, 68, 69}

# C# FormCZTVInit: myDeviceMode=2 (JPEG) for all USBLCDNew devices,
# except PM=32 which overrides to myDeviceMode=4 (RGB565, cmd=3).
_BULK_RGB565_PMS = {32}


def _parse_model_name(resp: bytes, offset: int, length: int) -> str:
    """Decode a NUL-padded ASCII model code from a handshake response slice.

    Strips NUL terminators and any other non-printable bytes. Returns "" on
    insufficient response length or pure-NUL slice.
    """
    if len(resp) < offset + length:
        return ""
    raw = resp[offset:offset + length]
    name = raw.split(b'\x00', 1)[0].decode('ascii', errors='ignore')
    return ''.join(c for c in name if c.isprintable()).strip()


def _bulk_resolution(pm: int, sub: int = 0) -> tuple[int, int]:
    """Map bulk device PM+SUB to (width, height).

    Uses the shared pm_to_fbl() + fbl_to_resolution() pipeline for
    known PM values.  Everything else defaults to 480x480 (FBL=72).
    """
    if pm in _BULK_KNOWN_PMS or (pm == 1 and sub in (48, 49)):
        fbl = pm_to_fbl(pm, sub)
        return fbl_to_resolution(fbl, pm)
    return (480, 480)


_HANDSHAKE_READ_SIZE = 1024
# Handshake + frame-write timeouts live in core/models/protocol.py — see
# the comment block there for the per-transport calibration rationale.
_HANDSHAKE_TIMEOUT_MS = HANDSHAKE_TIMEOUT_BULK_MS
_WRITE_TIMEOUT_MS = FRAME_WRITE_TIMEOUT_MS
_FRAME_HEADER_SIZE = 64
_WRITE_CHUNK_SIZE = 16 * 1024  # 16 KiB per USB bulk write


class BulkDevice(BulkFrameDevice, FrameDevice):
    """USB bulk device handler for USBLCDNew-type LCDs (87AD:70DB etc.).

    Uses pyusb for raw bulk endpoint I/O.  The kernel must not have
    claimed the interface (no usb-storage, no usbhid).
    """

    def handshake(self) -> HandshakeResult:
        """Send 64-byte handshake, read 1024-byte response.

        Extracts PM from resp[24], SUB from resp[36].
        """
        if self._dev is None:
            self._open()

        assert self._ep_out is not None
        assert self._ep_in is not None

        # Write handshake
        self._ep_out.write(_HANDSHAKE_PAYLOAD, timeout=_HANDSHAKE_TIMEOUT_MS)
        log.debug("Handshake sent (%d bytes)", len(_HANDSHAKE_PAYLOAD))

        # Read response
        resp = bytes(self._ep_in.read(
            _HANDSHAKE_READ_SIZE, timeout=_HANDSHAKE_TIMEOUT_MS
        ))
        self._raw_handshake = resp
        log.info("Handshake response: %d bytes", len(resp))
        log.debug("Response hex (first 64): %s",
                  " ".join(f"{b:02x}" for b in resp[:64]))

        # Validate: resp[24] must be non-zero (from CS code)
        if len(resp) < 41 or resp[24] == 0:
            log.warning("Handshake failed: resp[24]=%s (expected non-zero)",
                        resp[24] if len(resp) > 24 else "N/A")
            return HandshakeResult(raw_response=resp)

        # Extract PM and SUB (from USBLCDNew shared memory mapping)
        self.pm = resp[24]
        self.sub_type = resp[36]

        # C#: myDeviceMode=2 (JPEG) for all USBLCDNew, except PM=32 → mode 4 (RGB565)
        self.use_jpeg = self.pm not in _BULK_RGB565_PMS

        # Derive resolution from PM+SUB (from FormCZTVInit in FormCZTV.cs).
        # Bulk devices (87AD:70DB) get FBL=72 hardcoded by USBLCDNEW.exe,
        # then PM overrides FBL for certain device models.
        if (resolution := _bulk_resolution(self.pm, self.sub_type)):
            self.width, self.height = resolution

        # FBL must match actual resolution — unknown PMs default to FBL=72 (480x480)
        if self.pm in _BULK_KNOWN_PMS or (self.pm == 1 and self.sub_type in (48, 49)):
            fbl = pm_to_fbl(self.pm, self.sub_type)
        else:
            fbl = 72  # bulk default: 480x480 JPEG

        # ASCII product code at offset 4-12, NUL-padded. e.g. "SSCRM-V3" on
        # 87AD:70DB. Used as a handshake fingerprint to distinguish physical
        # product variants (e.g. Levita vs GrandVision 360) that share VID:PID.
        model_name = _parse_model_name(resp, 4, 8)

        log.info("Bulk handshake OK: PM=%d, SUB=%d, FBL=%d, resolution=%s, jpeg=%s, model=%r",
                 self.pm, self.sub_type, fbl, resolution, self.use_jpeg, model_name)

        return HandshakeResult(
            resolution=resolution,
            model_id=fbl,
            pm_byte=self.pm,
            sub_byte=self.sub_type,
            model_name=model_name,
            raw_response=resp,
        )

    def send_frame(self, image_data: bytes) -> bool:
        """Send one frame via bulk write.

        C# protocol (FormCZTV.cs ImageToJpg / ImageTo565):
          - JPEG mode (cmd=2): all PMs except 32.  Payload is JPEG bytes.
          - RGB565 mode (cmd=3): PM=32 only.  Payload is raw RGB565 pixels.

        Header format (64 bytes):
          offset  0: magic  12 34 56 78
          offset  4: cmd    2 = JPEG, 3 = raw RGB565
          offset  8: width  (LE u32)
          offset 12: height (LE u32)
          offset 56: mode   2
          offset 60: payload length (LE u32)
        Followed by payload in 16 KiB chunks, then a ZLP delimiter.
        """
        if self._dev is None or self._ep_out is None:
            self.handshake()

        assert self._ep_out is not None

        data_size = len(image_data)
        cmd = 2 if self.use_jpeg else 3

        # Build 64-byte header
        header = bytearray(64)
        header[0:4] = _HANDSHAKE_PAYLOAD[0:4]           # magic 12 34 56 78
        struct.pack_into("<I", header, 4, cmd)            # cmd
        struct.pack_into("<I", header, 8, self.width)    # width
        struct.pack_into("<I", header, 12, self.height)  # height
        struct.pack_into("<I", header, 56, 2)            # mode
        struct.pack_into("<I", header, 60, data_size)    # payload length

        frame = bytes(header) + image_data

        for attempt in range(2):
            try:
                # Send in chunks — a single large transfer can reset the device
                # on KVM USB passthrough and slower USB hubs.
                for offset in range(0, len(frame), _WRITE_CHUNK_SIZE):
                    self._ep_out.write(
                        frame[offset:offset + _WRITE_CHUNK_SIZE],
                        timeout=_WRITE_TIMEOUT_MS,
                    )

                # C#: ZLP when total is 512-aligned (num2 % 512 == 0)
                if len(frame) % 512 == 0:
                    self._ep_out.write(b"", timeout=_WRITE_TIMEOUT_MS)

                log.log(TRACE_LEVEL,
                        "Bulk frame sent: %dx%d, cmd=%d, %d bytes",
                        self.width, self.height, cmd, data_size)
                return True
            except OSError:
                if attempt == 0:
                    log.warning("Bulk send failed, reconnecting and retrying")
                    self.close()
                    self.handshake()
                else:
                    log.exception("Bulk frame send failed after retry (cmd=%d, %d bytes)",
                                  cmd, data_size)
        return False

    # close() inherited from BulkFrameDevice
