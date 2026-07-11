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
    resolve_encode_base,
    resolve_encode_sub,
)
from . import DeviceFactory

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
_RGB565_PMS: set[int] = {32}

# Bulk base FBL is 72 (480x480, hardcoded by USBLCDNew.exe).  The C# resolves
# every bulk panel through ``FormCZTVInit(fbl=72, m=2, pm, pmSub)``
# (TRCC.decompiled.cs:742) — base 480x480, overridden ONLY for the PM values
# its ``switch(pm)`` handles.  This set is exactly those values:
#     case 5 → 50 (320x240)   case 7 → 64 (640x480)
#     m==2 default: pm==32 → 100 (320x320);  pm==64 → 114 (1600x720);
#     pm==65 → 192 (1920x462);  pm∈{9,11} → 224 (854x480);
#     pm==10 → 224 (960x540);   pm==12 → 224 (800x480)
# (PM=1 with SUB 48/49 is handled by the separate SUB guard in ``connect()``.)
# EVERY other PM stays 480x480 — including PM=50 (a poll-byte "SPI mode 2"
# value the GrandVision 360 reports, #176) and the 224/192-by-PM poll-byte
# values (13-17, 63, 66, 68, 69) that HID/LY resolve via ``pm_to_fbl`` but
# ``FormCZTVInit`` never maps on the bulk path.  Keep this set == FormCZTVInit's
# branches; do not re-add poll-byte PMs, or bulk panels misread again. (#176/#169)
_BULK_BASE_FBL = 72
_BULK_KNOWN_PMS: frozenset[int] = frozenset({5, 7, 9, 10, 11, 12, 32, 64, 65})


@DeviceFactory.register(Wire.BULK)
class BulkLcd(Device[BulkTransport]):
    """Raw USB bulk LCD device (USBLCDNew protocol)."""

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._pm: int = 0
        self._sub: int = 0
        # Cached at handshake. Carries PM-derived resolution from the FBL
        # tables, with the Bulk-specific JPEG-vs-RGB565 override:
        # USBLCDNew uses JPEG (cmd=2) for every PM except 32, which forces
        # RGB565 (cmd=3). FBL_PROFILES alone wouldn't capture that.
        self._profile: DeviceProfile | None = None

    @property
    def profile(self) -> DeviceProfile | None:
        """Handshake-derived profile; None pre-handshake."""
        return self._profile

    # ── Device ABC ────────────────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        log.info("BulkLcd %s: opening transport", self.info.key)
        if not self._transport.open():
            log.error("BulkLcd %s: transport open failed", self.info.key)
            raise HandshakeError(f"Failed to open USB transport for {self.info.key}")

        try:
            self._transport.write(_EP_WRITE, _HANDSHAKE_PAYLOAD, _HANDSHAKE_TIMEOUT_MS)
            resp = self._transport.read(_EP_READ, _HANDSHAKE_READ_SIZE, _HANDSHAKE_TIMEOUT_MS)
        except TransportError as e:
            log.error("BulkLcd %s: handshake I/O failed: %s", self.info.key, e)
            raise HandshakeError(f"BulkLcd handshake I/O failed: {e}") from e

        if len(resp) < 41 or resp[24] == 0:
            log.error(
                "BulkLcd %s: handshake validation failed (len=%d, resp[24]=%s)",
                self.info.key, len(resp),
                resp[24] if len(resp) > 24 else "N/A",
            )
            raise HandshakeError(
                f"BulkLcd handshake validation failed "
                f"(len={len(resp)}, resp[24]={resp[24] if len(resp) > 24 else 'N/A'})"
            )

        self._pm = resp[24]
        self._sub = resp[36]

        # Build the cached profile. Resolution comes from the FBL tables
        # (mirrors legacy ``_bulk_resolution``); the jpeg flag is the
        # Bulk-specific override (USBLCDNew → JPEG except PM=32).  Unknown
        # bulk PMs stay on the 480x480 base rather than letting pm_to_fbl
        # echo the PM into get_profile as a bogus FBL (C# parity, #169).
        if self._pm in _BULK_KNOWN_PMS or (
            self._pm == 1 and self._sub in (48, 49)
        ):
            fbl = pm_to_fbl(self._pm, self._sub)
        else:
            log.info(
                "BulkLcd %s: PM=%d SUB=%d not a known bulk model — "
                "defaulting to FBL %d (480x480)",
                self.info.key, self._pm, self._sub, _BULK_BASE_FBL,
            )
            fbl = _BULK_BASE_FBL
        base = get_profile(fbl, self._pm)
        # Resolve the device-only encode baseline now that PM is known — e.g.
        # the FW360 Ultra (PM=6) mounts 180° rotated and needs its wire frame
        # pre-rotated so it reads upright on the glass. (#137)
        encode_baseline = resolve_encode_base(base, self._pm)
        if encode_baseline:
            log.info("BulkLcd %s: PM=%d encode baseline %d° (wire-only)",
                     self.info.key, self._pm, encode_baseline)
        # Fold the sub-byte override into encode_base now that SUB is known, so
        # the render-time resolve_encode_angle only needs the user orientation
        # (C# ImageToJpg mySubMode branch — e.g. FBL 224 sub=2 → 180°). (#169)
        encode_base = resolve_encode_sub(base, self._sub)
        self._profile = DeviceProfile(
            width=base.width, height=base.height,
            jpeg=(self._pm not in _RGB565_PMS),
            big_endian=base.big_endian, rotate=base.rotate,
            widescreen=base.widescreen,
            encode_baseline=encode_baseline,
            encode_base=encode_base, encode_invert=base.encode_invert,
            encode_sub_bases=(),  # folded into encode_base above
            encode_pm_bases=base.encode_pm_bases,
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
        log.info("BulkLcd handshake OK: PM=%d SUB=%d resolution=%s (%s)",
                 self._pm, self._sub, result.resolution,
                 "JPEG" if self._profile.jpeg else "RGB565")
        return result

    def send(self, payload: bytes) -> bool:
        if not self._transport.is_open or self._profile is None:
            log.error("BulkLcd %s: send() called before connect()", self.info.key)
            raise TransportError(
                f"BulkLcd {self.info.key} not connected — call connect() first"
            )

        # Resolution + encoding come from the handshake-derived profile.
        # USBLCDNew header carries actual w/h (used by the device firmware
        # for de-blocking JPEG / interpreting the RGB565 buffer).
        width, height = self._profile.resolution
        cmd = 2 if self._profile.jpeg else 3

        header = bytearray(64)
        header[0:4] = _HANDSHAKE_PAYLOAD[0:4]
        struct.pack_into("<I", header, 4, cmd)
        struct.pack_into("<I", header, 8, width)
        struct.pack_into("<I", header, 12, height)
        struct.pack_into("<I", header, 56, 2)
        struct.pack_into("<I", header, 60, len(payload))

        frame = bytes(header) + payload
        log.debug("BulkLcd %s: sending %d-byte frame (%s, %dx%d)",
                  self.info.key, len(frame),
                  "JPEG" if self._profile.jpeg else "RGB565", width, height)

        def _write_frame() -> bool:
            for offset in range(0, len(frame), _WRITE_CHUNK_SIZE):
                self._transport.write(
                    _EP_WRITE, frame[offset:offset + _WRITE_CHUNK_SIZE],
                    _WRITE_TIMEOUT_MS,
                )
            # Zero-length packet on 512-byte alignment (frame delimiter)
            if len(frame) % 512 == 0:
                self._transport.write(_EP_WRITE, b"", _WRITE_TIMEOUT_MS)
            return True

        # Shared reconnect + recovery policy (base Device): one in-place
        # close→open→handshake retry absorbs the intermittent NAKs that KVM
        # USB passthrough / slow hubs produce (legacy ``BulkDevice.send_frame``
        # did the same) and heals a stale handle after resume, before
        # escalating the final error to the recovery tracker.
        return self._send_with_recovery(_write_frame)

    def disconnect(self) -> None:
        log.info("BulkLcd %s: disconnecting", self.info.key)
        self._transport.close()
        self._handshake = None
        self._profile = None
