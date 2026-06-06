"""FBL / PM byte lookup machinery — single source of truth for device geometry.

`DeviceProfile` holds everything derivable from an FBL code:
    resolution, encoding (JPEG vs RGB565), byte order, pre-rotate flag, and
    device-side encode rotation logic.

The FBL byte comes from the device's handshake response. For most protocols
PM byte == FBL byte (the SCSI convention); HID Type 2 and a handful of bulk
PM values disambiguate via `_PM_TO_FBL_OVERRIDES` and `_PM_SUB_TO_FBL`.

Two FBL codes (192, 224) are shared by multiple resolutions; the PM byte
disambiguates via `_FBL_192_BY_PM` and `_FBL_224_BY_PM`.

Ported byte-for-byte from legacy ``src/trcc/core/models/protocol.py`` —
parity locked by ``tests/next/test_protocol_parity.py``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# =============================================================================
# DeviceProfile — single source of truth for FBL-derived properties
# =============================================================================


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Everything needed to render + encode for a device, derived from FBL.

    Replaces five scattered constants/functions in legacy:
        FBL_TO_RESOLUTION, JPEG_MODE_FBLS, BULK_RGB565_FBLS,
        byte_order_for(), _SQUARE_NO_ROTATE.
    """
    width: int
    height: int
    jpeg: bool = False           # JPEG encoding (vs RGB565)
    big_endian: bool = False     # RGB565 byte order (> vs <)
    rotate: bool = False         # Pre-rotate 90° CW for non-square portrait panels
    # Resolved device-only encode rotation, applied to the WIRE frame only (not
    # the preview) in _encode_for_wire.  Resolved at handshake from
    # encode_pm_bases via resolve_encode_base() once the PM byte is known.  0 =
    # no baseline.  (#137 — FW360 Ultra PM=6 mounts 180° rotated.)
    encode_baseline: int = 0
    # encode_pm_bases is the LIVE source for encode_baseline: a PM (PingMu)
    # keyed hardware-mount rotation (e.g. PM=6 → 180° for the FW360 Ultra).
    encode_pm_bases: tuple[tuple[int, int], ...] = ()   # ((pm, base), ...) LIVE
    # encode_base / encode_invert / encode_sub_bases are recorded-but-UNWIRED:
    # the C# unified per-resolution model (angle = base + direction*sign).  Our
    # rotate-flag pipeline already covers widescreen rotation, so they are NOT
    # applied — wiring them needs widescreen hardware verification.
    encode_base: int = 0
    encode_invert: bool = True   # (unwired — see above)
    encode_sub_bases: tuple[tuple[int, int], ...] = ()  # ((sub, base), ...) unwired

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def byte_order(self) -> str:
        return ">" if self.big_endian else "<"


# =============================================================================
# FBL_PROFILES — the master lookup. One entry per FBL code in the wild.
# =============================================================================


# fmt: off
FBL_PROFILES: dict[int, DeviceProfile] = {
    #          W      H     jpeg    BE      rotate   notes
    36:  DeviceProfile(240,  240),
    37:  DeviceProfile(240,  240),
    50:  DeviceProfile(320,  240,  rotate=True),
    51:  DeviceProfile(320,  240,  rotate=True),                    # HID Type 2 → SPIMode=1
    52:  DeviceProfile(320,  240,  rotate=True),                    # BA120 Vision (#100)
    53:  DeviceProfile(320,  240,  rotate=True),                    # HID Type 2 → SPIMode=1
    54:  DeviceProfile(360,  360,  jpeg=True),
    58:  DeviceProfile(320,  240,  rotate=True),                    # AussieMakerGeek's Frozen Warframe SE
    64:  DeviceProfile(640,  480,  rotate=True),
    72:  DeviceProfile(480,  480,
                       encode_pm_bases=((6, 180),)),                # FW360 Ultra PM=6 → 180° baseline (#137)
    100: DeviceProfile(320,  320,  big_endian=True),
    101: DeviceProfile(320,  320,  big_endian=True),
    102: DeviceProfile(320,  320,  big_endian=True),
    114: DeviceProfile(1600, 720,  jpeg=True, rotate=True,
                       encode_base=180, encode_sub_bases=((3, 0),)),
    128: DeviceProfile(1280, 480,  jpeg=True, rotate=True,
                       encode_sub_bases=((2, 90),)),
    129: DeviceProfile(480,  480,
                       encode_pm_bases=((6, 180),)),                # alias for 72
    192: DeviceProfile(1920, 462,  jpeg=True, rotate=True,
                       encode_base=180, encode_sub_bases=((2, 0), (3, 0), (4, 0))),
    224: DeviceProfile(854,  480,  jpeg=True, rotate=True,
                       encode_invert=False, encode_sub_bases=((2, 180),)),
}
# fmt: on


_DEFAULT_PROFILE = DeviceProfile(320, 320, big_endian=True)


# =============================================================================
# Disambiguation: PM byte → FBL byte mapping
# =============================================================================


# PM byte → FBL byte for devices where PM ≠ FBL. For all other PM values,
# PM=FBL (SCSI poll-byte convention). C# FormCZTV.cs lines 682-821.
_PM_TO_FBL_OVERRIDES: dict[int, int] = {
    5:   50,    # 320x240
    7:   64,    # 640x480
    9:   224,   # 854x480
    10:  224,   # 960x540 (disambiguated in _FBL_224_BY_PM)
    11:  224,   # 854x480
    12:  224,   # 800x480 (disambiguated in _FBL_224_BY_PM)
    13:  224,   # 960x320 (disambiguated in _FBL_224_BY_PM)
    14:  64,    # 640x480
    15:  224,   # 640x172 (disambiguated in _FBL_224_BY_PM)
    16:  224,   # 960x540 (disambiguated in _FBL_224_BY_PM)
    17:  224,   # 960x320 (disambiguated in _FBL_224_BY_PM)
    32:  100,   # 320x320
    50:  50,    # 320x240 (SPI mode 2)
    63:  114,   # 1600x720
    64:  114,   # 1600x720
    65:  192,   # 1920x462
    66:  192,   # 1920x462
    68:  192,   # 1280x480 (disambiguated in _FBL_192_BY_PM)
    69:  192,   # 1920x440 (disambiguated in _FBL_192_BY_PM)
}


# FBL 224 is shared by 5 resolutions — PM byte disambiguates
_FBL_224_BY_PM: dict[int, tuple[int, int]] = {
    10: (960, 540),
    12: (800, 480),
    13: (960, 320),
    15: (640, 172),
    16: (960, 540),
    17: (960, 320),
}


# FBL 192 is shared by 3 resolutions — PM byte disambiguates
_FBL_192_BY_PM: dict[int, tuple[int, int]] = {
    68: (1280, 480),
    69: (1920, 440),
}


# Compound (PM, SUB) keys where sub byte changes the FBL mapping
_PM_SUB_TO_FBL: dict[tuple[int, int], int] = {
    (1, 48): 114,   # 1600x720
    (1, 49): 192,   # 1920x462
}


# =============================================================================
# Public lookups
# =============================================================================


def pm_to_fbl(pm: int, sub: int = 0) -> int:
    """Map PM byte to FBL byte.

    Default: PM=FBL (SCSI poll-byte convention).
    Overrides for the few PM values where PM ≠ FBL.
    Compound (PM, SUB) key checked first for sub-dependent mappings.
    """
    log.info("pm_to_fbl: pm=%d sub=%d", pm, sub)
    if (pm, sub) in _PM_SUB_TO_FBL:
        return _PM_SUB_TO_FBL[(pm, sub)]
    return _PM_TO_FBL_OVERRIDES.get(pm, pm)


def get_profile(fbl: int, pm: int = 0) -> DeviceProfile:
    """Look up the device profile for an FBL code.

    For FBL 192/224, the PM byte disambiguates among multiple resolutions
    that share the FBL. All other FBL values map 1:1 to a profile.
    Unknown FBLs fall back to the 320×320 big-endian default.
    """
    log.info("get_profile: fbl=%d pm=%d", fbl, pm)
    profile = FBL_PROFILES.get(fbl, _DEFAULT_PROFILE)
    if fbl == 224:
        w, h = _FBL_224_BY_PM.get(pm, (854, 480))
        return DeviceProfile(
            w, h,
            jpeg=profile.jpeg,
            big_endian=profile.big_endian,
            rotate=profile.rotate,
            encode_base=profile.encode_base,
            encode_invert=profile.encode_invert,
            encode_sub_bases=profile.encode_sub_bases,
            encode_pm_bases=profile.encode_pm_bases,
        )
    if fbl == 192:
        w, h = _FBL_192_BY_PM.get(pm, (1920, 462))
        return DeviceProfile(
            w, h,
            jpeg=profile.jpeg,
            big_endian=profile.big_endian,
            rotate=profile.rotate,
            encode_base=profile.encode_base,
            encode_invert=profile.encode_invert,
            encode_sub_bases=profile.encode_sub_bases,
            encode_pm_bases=profile.encode_pm_bases,
        )
    return profile


def fbl_to_resolution(fbl: int, pm: int = 0) -> tuple[int, int]:
    """Map FBL byte (with optional PM disambiguator) to (width, height)."""
    log.info("fbl_to_resolution: fbl=%d pm=%d", fbl, pm)
    return get_profile(fbl, pm).resolution


def resolve_encode_base(profile: DeviceProfile, pm_byte: int) -> int:
    """Resolve a panel's device-only encode baseline by PM (PingMu) byte.

    Square panels carry a fixed hardware-mount rotation keyed on PM — e.g. the
    FW360 Ultra (PM=6) mounts 180° rotated, so its wire frame must be
    pre-rotated 180° to read upright on the glass.  Returns the matching
    ``encode_pm_bases`` angle, else 0.

    Resolved once at handshake (where PM is known) into
    ``DeviceProfile.encode_baseline`` and applied device-only in
    ``_encode_for_wire`` — the GUI preview is never rotated.  (#137)
    """
    for pm, base in profile.encode_pm_bases:
        if pm_byte == pm:
            log.debug("resolve_encode_base: pm=%d → %d°", pm_byte, base)
            return base
    return 0


def resolve_encode_sub(profile: DeviceProfile, sub_byte: int) -> int:
    """Resolve a widescreen panel's encode base, applying any sub-byte override.

    The widescreen JPEG panels override their base wire rotation per sub-byte —
    the C# ``ImageToJpg`` ``mySubMode`` branch (e.g. FBL 224 sub=2 → base 180°,
    FBL 128 sub=2 → 90°).  Returns the matching ``encode_sub_bases`` override,
    else the static ``encode_base``.  Resolved once at handshake (where SUB is
    known) and folded into ``DeviceProfile.encode_base`` so the render-time
    :func:`resolve_encode_angle` only needs the user orientation.
    """
    for sub, base in profile.encode_sub_bases:
        if sub_byte == sub:
            log.debug("resolve_encode_sub: sub=%d → base %d°", sub_byte, base)
            return base
    return profile.encode_base


def resolve_encode_angle(profile: DeviceProfile, orientation: int) -> int:
    """Wire rotation for a widescreen panel = base ± the user orientation.

    Ports the C# ``ImageToJpg`` ``directionB`` switch:
    ``send = (encode_base + (orientation if not invert else -orientation)) % 360``.
    ``encode_base`` must already carry any sub-byte override (folded at handshake
    via :func:`resolve_encode_sub`); ``encode_invert`` is the per-resolution sign.
    Replaces the cutover's blanket ``rotate→90°``.  FBL 224 (854×480) at
    orientation 0 → 0° (landscape, unrotated); at 90° → 90°. (#169)
    """
    signed = orientation if not profile.encode_invert else -orientation
    angle = (profile.encode_base + signed) % 360
    log.debug("resolve_encode_angle: base=%d invert=%s orient=%d → %d°",
              profile.encode_base, profile.encode_invert, orientation, angle)
    return angle
