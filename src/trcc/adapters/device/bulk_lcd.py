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
from ...core.ports import BulkTransport
from ...core.protocol import (
    DeviceProfile,
    get_profile,
    is_portrait_mounted,
    pm_to_fbl,
    resolve_encode_base,
    resolve_encode_sub,
)
from ._base import BaseBulkDevice

log = logging.getLogger(__name__)


# ── Wire constants ─────────────────────────────────────────────────────

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


# JPEG start-of-frame markers carry the image's real dimensions.  C4/C8/CC
# are Huffman/extension segments, not frames, so they are excluded.
_JPEG_SOF_MARKERS = frozenset(
    m for m in range(0xC0, 0xD0) if m not in (0xC4, 0xC8, 0xCC)
)


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read a JPEG's ``(width, height)`` from its SOF segment, or None.

    Header-only: it walks the segment markers rather than decoding pixels, so
    it is cheap enough to run on every frame and needs no imaging library at
    the wire layer.  Returns None for RGB565 payloads and anything malformed.
    """
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return None
    i, n = 2, len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in _JPEG_SOF_MARKERS:
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            log.debug("jpeg_dimensions: %dx%d", width, height)
            return (width, height)
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    log.debug("jpeg_dimensions: no SOF segment in %d bytes", n)
    return None


def bulk_profile(pm: int, sub: int, key: str = "?") -> tuple[int, DeviceProfile]:
    """Resolve a bulk handshake's PM/SUB bytes to its ``(FBL, profile)``.

    The one implementation of the bulk fingerprint → geometry rules, shared by
    the wire path (:meth:`BulkLcd.connect`) and the bench auditor
    (``dev/decompiler/audit_rotation.py``).  It is a pure function of the two
    handshake bytes precisely so an auditor can resolve a device with no USB.

    Keep it that way: a hand-copy of these rules in the auditor drifted — it
    dropped the ``_BULK_KNOWN_PMS`` guard below and invented a phantom FBL 6,
    which then reported our (reporter-confirmed, #137) FW360 Ultra rotation as
    a 180° bug.  An oracle that re-implements the thing it audits proves
    nothing about the code that ships.
    """
    # Resolution comes from the FBL tables (mirrors legacy ``_bulk_resolution``).
    # Unknown bulk PMs stay on the 480x480 base rather than letting pm_to_fbl
    # echo the PM into get_profile as a bogus FBL (C# parity, #169).
    if pm in _BULK_KNOWN_PMS or (pm == 1 and sub in (48, 49)):
        fbl = pm_to_fbl(pm, sub)
    else:
        log.info(
            "BulkLcd %s: PM=%d SUB=%d not a known bulk model — "
            "defaulting to FBL %d (480x480)", key, pm, sub, _BULK_BASE_FBL,
        )
        fbl = _BULK_BASE_FBL
    base = get_profile(fbl, pm)
    # Resolve the device-only encode baseline now that PM is known — e.g. the
    # FW360 Ultra (PM=6) mounts 180° rotated and needs its wire frame
    # pre-rotated so it reads upright on the glass. (#137)
    encode_baseline = resolve_encode_base(base, pm)
    if encode_baseline:
        log.info("BulkLcd %s: PM=%d encode baseline %d° (wire-only)",
                 key, pm, encode_baseline)
    # Fold the sub-byte override into encode_base now that SUB is known, so the
    # render-time resolve_encode_angle only needs the user orientation
    # (C# ImageToJpg mySubMode branch — e.g. FBL 224 sub=2 → 180°). (#169)
    return fbl, DeviceProfile(
        width=base.width, height=base.height,
        # The Bulk-specific override: USBLCDNew uses JPEG (cmd=2) for every PM
        # except 32, which forces RGB565 (cmd=3).  This flag is what the C#
        # rotation switches key on (ImageToJpg vs ImageTo565) — FBL_PROFILES
        # alone wouldn't capture it.
        jpeg=(pm not in _RGB565_PMS),
        big_endian=base.big_endian, rotate=base.rotate,
        widescreen=base.widescreen,
        # The SUB byte also says how the panel is MOUNTED: on three
        # resolutions a sub of 5+ means it is turned portrait in its cooler,
        # so its content catalog is the transposed one from orientation 0.
        # (#262, #203 — both PM=11 SUB=5 on 854x480.)
        portrait_mounted=is_portrait_mounted(
            (base.width, base.height), sub),
        encode_baseline=encode_baseline,
        encode_base=resolve_encode_sub(base, sub),
        encode_sub_bases=(),  # folded into encode_base above
        encode_pm_bases=base.encode_pm_bases,
        encode_invert=base.encode_invert,
    )


class BulkLcd(BaseBulkDevice, wire=Wire.BULK):
    """Raw USB bulk LCD device (USBLCDNew protocol).

    ``self._profile`` is cached at handshake.  It carries PM-derived resolution
    from the FBL tables plus the Bulk-specific JPEG-vs-RGB565 override —
    USBLCDNew uses JPEG (cmd=2) for every PM except 32, which forces RGB565
    (cmd=3), and ``FBL_PROFILES`` alone wouldn't capture that.
    """

    _EP_WRITE = 0x01

    def __init__(self, info: ProductInfo, transport: BulkTransport) -> None:
        super().__init__(info, transport)
        self._pm: int = 0
        self._sub: int = 0

    # ── Device ABC ────────────────────────────────────────────────────

    def _do_handshake(self) -> HandshakeResult:
        resp = self._exchange(_HANDSHAKE_PAYLOAD, _HANDSHAKE_READ_SIZE,
                              _HANDSHAKE_TIMEOUT_MS)

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

        fbl, self._profile = bulk_profile(self._pm, self._sub, self.info.key)

        return HandshakeResult(
            resolution=self._profile.resolution,
            model_id=self._pm,
            pm_byte=self._pm,
            sub_byte=self._sub,
            fbl=fbl,
            raw_response=bytes(resp),
        )

    def _handshake_detail(self, result: HandshakeResult) -> str:
        """Which encoder the PM byte selected, and how the panel is mounted.

        Appended to the ``handshake OK: PM=… SUB=… resolution=…`` line that
        ``dev/tools/diagnose.py`` and ``diagnostics/debug_report.py`` scrape —
        so the prefix shape is fixed and only this suffix may grow.

        The mount is worth saying out loud.  It is knowable from the SUB byte
        (see :func:`is_portrait_mounted`), it explains why an owner has to
        rotate to 90° to get an upright picture, and it was invisible in every
        report we had: #262 and #203 turned out to share ``PM=11 SUB=5`` and
        nobody could see it, including me, until I read the C# catalog rule.
        """
        if self._profile is None:
            return ""
        encoder = " (JPEG)" if self._profile.jpeg else " (RGB565)"
        if self._profile.portrait_mounted:
            return f"{encoder} portrait-mounted"
        return encoder

    def _require_connected(self) -> None:
        """Also demand the handshake profile — the header needs its geometry."""
        super()._require_connected()
        if self._profile is None:
            log.error("BulkLcd %s: send() called before connect()", self.info.key)
            raise TransportError(
                f"BulkLcd {self.info.key} not connected — call connect() first"
            )

    def _warn_if_payload_shape_disagrees(
        self, payload: bytes, width: int, height: int, *, jpeg: bool,
    ) -> None:
        """Say so when the frame we are about to send is not the shape we claim.

        The header declares the profile's resolution, and the firmware uses it
        to de-block the JPEG or interpret the RGB565 buffer — so a payload of a
        different shape is painted only where the two overlap.  That is #262:
        ``display send-image`` on an 854x480 panel emits a 480x854 JPEG under
        an 854x480 header, and roughly a third of the screen lights up.

        It failed silently, which is the worst part: the command reports
        success, the log says the frame went out, and only the glass disagrees.
        A warning here makes it self-reporting from any user's ``trcc report``,
        with both shapes named, before anyone has to reproduce it.

        Diagnostic only — the frame is still sent.  This must never be the
        thing that stops a panel drawing.
        """
        actual = jpeg_dimensions(payload) if jpeg else None
        if actual is not None and actual != (width, height):
            log.warning(
                "BulkLcd %s: frame is %dx%d but the header declares %dx%d — "
                "the panel will paint only the overlap (#262)",
                self.info.key, actual[0], actual[1], width, height,
            )
            return
        if not jpeg:
            expected = width * height * 2      # RGB565
            if payload and len(payload) != expected:
                log.warning(
                    "BulkLcd %s: RGB565 frame is %d bytes but %dx%d needs %d "
                    "— the panel will paint only part of it (#262)",
                    self.info.key, len(payload), width, height, expected,
                )
                return
        log.debug("_warn_if_payload_shape_disagrees: %s matches %dx%d",
                  self.info.key, width, height)

    def _prepare_frame(self, payload: bytes) -> bytes:
        """64-byte USBLCDNew header + payload.

        Resolution + encoding come from the handshake-derived profile; the
        header carries the actual w/h, which the firmware uses to de-block the
        JPEG or interpret the RGB565 buffer.
        """
        if self._profile is None:      # pragma: no cover — _require_connected
            raise TransportError(f"BulkLcd {self.info.key} has no profile")
        width, height = self._profile.resolution
        cmd = 2 if self._profile.jpeg else 3
        self._warn_if_payload_shape_disagrees(
            payload, width, height, jpeg=self._profile.jpeg)

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
        return frame

    def _write_frame(self, frame: bytes) -> bool:
        """16 KiB bulk writes, with a ZLP delimiter on 512-byte alignment."""
        for offset in range(0, len(frame), _WRITE_CHUNK_SIZE):
            self._transport.write(
                self._EP_WRITE, frame[offset:offset + _WRITE_CHUNK_SIZE],
                _WRITE_TIMEOUT_MS,
            )
        # Zero-length packet on 512-byte alignment (frame delimiter)
        if len(frame) % 512 == 0:
            self._transport.write(self._EP_WRITE, b"", _WRITE_TIMEOUT_MS)
        return True
