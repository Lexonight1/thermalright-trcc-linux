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

import dataclasses
import logging
from dataclasses import dataclass

from .logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

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
    # Widescreen "bili" panels (C# isBiliPingmu — 854×480, 1280×480, 1600×720,
    # 1920×462).  Their user-orientation folds into the per-resolution encode
    # TABLE (a single wire angle), NOT the whole-composite rotation the simple
    # 320×240 / 640×480 rotate panels use.  This is the correct discriminator for
    # the two rotation models — NOT ``jpeg``: a bulk 320×240 panel (FBL 50, PM=5
    # Mjolnir) negotiates JPEG yet rotates like its RGB565 siblings, not like a
    # widescreen panel.  (#176 — was conflated with ``jpeg`` pre-fix.)
    widescreen: bool = False
    # The panel is physically mounted PORTRAIT in its cooler, so its content
    # catalog is the transposed one at user-orientation 0.  Resolved at
    # handshake from the SUB byte — the C# tests ``pmSub < 5`` for exactly
    # three resolutions and picks the transposed theme directory when it fails
    # (FormCZTV.cs FormCZTVInit: 854x480 -> 480854\\, 960x540 -> 540960\\,
    # 800x480 -> 480800\\).  Every other resolution gets one unconditional
    # catalog with no sub test, which is why this is scoped, not general.
    #
    # This is a CONTENT/CATALOG property, not a wire one: the C# rotation
    # tables never read the sub byte, and the device's framebuffer is still
    # its native landscape size, so ``resolution`` and the wire header are
    # unchanged.  (#262, #203)
    portrait_mounted: bool = False
    # Resolved device-only encode rotation, applied to the WIRE frame only (not
    # the preview) in _encode_for_wire.  Resolved at handshake from
    # encode_pm_bases via resolve_encode_base() once the PM byte is known.  0 =
    # no baseline.  (#137 — FW360 Ultra PM=6 mounts 180° rotated.)
    encode_baseline: int = 0
    # encode_pm_bases is the LIVE source for encode_baseline: a PM (PingMu)
    # keyed hardware-mount rotation (e.g. PM=6 → 180° for the FW360 Ultra).
    encode_pm_bases: tuple[tuple[int, int], ...] = ()   # ((pm, base), ...) LIVE
    # The RESOLVED wire rotation — an answer, not a rule.  Both come from
    # ENCODE_ROTATIONS, keyed on the panel's resolution + encoder (+ SUB byte
    # where the C# has an arm for it), and are set by get_profile or, for a
    # device whose encoder is only known at handshake, by its wire adapter.
    # resolve_encode_angle reads them per frame and asks nothing further.
    #
    # They used to be written per FBL in FBL_PROFILES, alongside an
    # ``encode_sub_bases`` rule that was documented as a phantom and left empty.
    # It was not a phantom: the C# does vary the base by SUB in six families,
    # and FBL cannot key any of it — FBL 224 alone spans five resolutions whose
    # invert flags disagree.  That mismatch is #203/#169/#171.
    encode_base: int = 0
    encode_invert: bool = True
    # Largest JPEG the firmware will actually display, in bytes.
    #
    # TRCC 2.1.6 ``ImageToJpg`` never sends a payload of 450000 bytes or more:
    # it drops the encoder quality by 5 and discards the frame.  That test sits
    # in the JPEG path with no device condition on it, so it applies to EVERY
    # JPEG panel — it is a firmware ceiling, not a per-device quirk.
    #
    # We first met it as #251 (LY silently dropping frames over ~0.5 MB) and
    # ported it as an LY-only field, leaving every other JPEG panel uncapped.
    # That is the same silent frame loss waiting to happen on bulk and HID, and
    # it is invisible: send() completes, the ACK reads back fine, the glass
    # keeps the previous image, and nothing appears in the log.  Nobody reports
    # it because there is nothing to see.
    #
    # Renderer.encode_payload feeds this to encode_jpeg's shrink-quality loop.
    # DELIBERATE DIVERGENCE: the C# discards the frame and leaves the quality
    # lowered until reconnect; we shrink and send, and re-evaluate per frame.
    # A degraded frame beats a frozen panel, which is what #251 asked for.
    # Only the ceiling and its universality are ported, not the drop.
    max_frame_bytes: int = 450_000

    @property
    def resolution(self) -> tuple[int, int]:
        frame_log.debug("DeviceProfile.resolution: %dx%d", self.width, self.height)
        return (self.width, self.height)

    @property
    def byte_order(self) -> str:
        order = ">" if self.big_endian else "<"
        frame_log.debug("DeviceProfile.byte_order: %s (big_endian=%s)",
                        order, self.big_endian)
        return order


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
    # No encode rotation is written here.  These four share their FBL with
    # other resolutions (224 with five, 192 with three) whose bases and invert
    # flags differ, so the rotation is resolved from ENCODE_ROTATIONS by the
    # resolution get_profile lands on — see that table's header.
    114: DeviceProfile(1600, 720,  jpeg=True, rotate=True, widescreen=True),
    128: DeviceProfile(1280, 480,  jpeg=True, rotate=True, widescreen=True),
    129: DeviceProfile(480,  480,
                       encode_pm_bases=((6, 180),)),                # alias for 72
    192: DeviceProfile(1920, 462,  jpeg=True, rotate=True, widescreen=True),
    224: DeviceProfile(854,  480,  jpeg=True, rotate=True, widescreen=True),
}
# fmt: on

# FBL 59 is the SECOND route to the 640x172 panel.  The C# reaches that screen
# two ways -- ``mode == 2 && pm == 15`` (which we serve through
# ``_FBL_224_BY_PM``) and ``mode == 3 && pm == 100 && fbl == 59``, the HID
# discovery path, where FormCZTV.cs:1036 records the screen and then rewrites
# the code it will encode under:
#
#     else if (myDeviceMode == 3 && myDevicePingMu == 100 && fbl == 59)
#     { is640x172 = true; fbl = 224; }
#
# Without this row that handshake fell to ``_DEFAULT_PROFILE`` -- 320x320
# RGB565 for a 640x172 JPEG panel, wrong on both axes and wrong encoder.
#
# Derived from the 224 base rather than written out, because the two routes
# describe ONE panel: a flag added to 224 that this row spelled by hand would
# silently apply to the PM-15 device and not this one.
FBL_PROFILES[59] = dataclasses.replace(
    FBL_PROFILES[224], width=640, height=172)


_DEFAULT_PROFILE = DeviceProfile(320, 320, big_endian=True)


# =============================================================================
# ENCODE_ROTATIONS — the ONE authority for wire-frame rotation
# =============================================================================


@dataclass(frozen=True, slots=True)
class EncodeRotation:
    """The C# dir-0 mount offset for one (resolution, encoder) pair.

    The wire frame is turned clockwise by ``(base ± orientation) mod 360``
    immediately before encoding — ``+`` when :attr:`invert` is False, ``−`` when
    it is True.  That is the whole of the C# ``directionB`` switch in
    ``ImageToJpg`` / ``ImageTo565``; :func:`resolve_encode_angle` is the formula.

    ``alt_base`` applies instead of :attr:`base` when the handshake SUB byte is
    in :attr:`alt_subs`.  The C# spells this as a ``mySubMode`` test guarding a
    second ``directionB`` switch, and ``mySubMode`` is DERIVED from the SUB byte
    in ``FormCZTVInit`` — assigned for some resolutions and never assigned (so
    permanently 0) for others.  A resolution the C# never assigns it for simply
    carries no ``alt_subs`` here, which is why the derivation needs no second
    mechanism: a rule that can never fire is a rule that is not written down.

    ``invert`` is constant per resolution — verified across all seven families
    that have a sub rule, both arms agree on the sign — so only the base varies.
    """
    base: int
    invert: bool = True
    alt_base: int | None = None
    alt_subs: frozenset[int] = frozenset()

    def for_sub(self, sub: int) -> EncodeRotation:
        """This rotation with the SUB-byte arm applied.

        Returns ``self`` unchanged when the panel has no sub rule or the byte
        is not in it, so a caller never has to ask whether one exists.
        """
        if self.alt_base is None or sub not in self.alt_subs:
            log.debug("EncodeRotation.for_sub: sub=%d → base %d° (no alt arm)",
                      sub, self.base)
            return self
        log.debug("EncodeRotation.for_sub: sub=%d in %s → alt base %d° "
                  "(was %d°)", sub, sorted(self.alt_subs), self.alt_base,
                  self.base)
        return EncodeRotation(self.alt_base, self.invert)


# (width, height, jpeg) → the C# rotation for that panel.
#
# Source: TRCC 2.1.6, TRCC.CZTV/FormCZTV.cs — ``ImageToJpg`` (the jpeg=True
# rows) and ``ImageTo565`` (the jpeg=False rows).  The encoder is part of the
# key because the two switches disagree on the same resolution: 320×240 is
# base 0 under JPEG (the ``myDevicePingMu == 5`` Mjolnir arm) and base 90 under
# RGB565 (the default arm), and 640×172 is base 0 under JPEG and base 270 under
# RGB565.  ``pm == 5`` and ``pm == 50`` both collapse to FBL 50 yet rotate
# oppositely, so FBL cannot key this and neither can resolution alone.
#
# Keyed on RESOLUTION, never on FBL: FBL 224 spans five resolutions whose
# invert flags differ (854/800 do not invert; 960×540 and 960×320 do) and
# FBL 192 spans three whose bases differ.  One row per FBL cannot express
# that, and trying to is how these values were lost (see the alt_subs note
# on 854/800 below).
#
# The PM=6 mount offset (FW360 Ultra, #137) is deliberately NOT here.  It is a
# physical-mount baseline, not an encoder switch: it lives in
# ``DeviceProfile.encode_pm_bases`` → ``encode_baseline`` and is applied once
# in ``Renderer.encode_payload``.  Writing it in both places is what made the
# square ``pm == 6`` arm of ``wire_rotation`` a second, unreachable copy.
_JPEG = True
_565 = False

ENCODE_ROTATIONS: dict[tuple[int, int, bool], EncodeRotation] = {
    # ── ImageToJpg ────────────────────────────────────────────────────────
    # Squares: `is320x320 || is480x480` → 0/270/180/90.
    (320, 320, _JPEG): EncodeRotation(0),
    (480, 480, _JPEG): EncodeRotation(0),
    # The C# reaches this arm by `myDevicePingMu == 5`, tested before any
    # resolution guard.  Keying it on (320x240, JPEG) instead is faithful, not
    # a generalisation: enumerating every PM byte shows exactly six resolve to
    # 320x240 — 5, 50, 51, 52, 53, 58 — and only PM 5 is a bulk PM, so PM 5 is
    # the only one that ever reaches an encoder with jpeg=True (bulk sets
    # `jpeg = pm not in _RGB565_PMS`, and _RGB565_PMS is {32}).  The other five
    # are SCSI/HID panels that stay RGB565 and take the default arm below.
    # `pm == 5` and `320x240 JPEG` therefore pick out the same single panel.
    (320, 240, _JPEG): EncodeRotation(0),
    # `is1600x720`: mySubMode == 3 → 0, else 180.  mySubMode is set from the
    # SUB byte by SetThemeInfo_ThemeML, and only for SUB in {2,3,4}; the arm
    # tests 3, so {3} is the whole rule.
    (1600, 720, _JPEG): EncodeRotation(180, alt_base=0, alt_subs=frozenset({3})),
    # `is854x480 || is800x480`: NON-INVERTED, and no sub arm.
    #
    # The C# arm reads `mySubMode == 2 → base 180`, but FormCZTVInit never
    # assigns mySubMode on the branches that set these resolutions (pm 9/11 →
    # is854x480, pm 12 → is800x480 — every sibling branch assigns it, these two
    # do not), so it is permanently 0 and that arm is unreachable.  Both
    # therefore always take the else arm: 0/90/180/270, which is base 0 with
    # the sign NOT inverted — the only two families in either switch that count
    # up with the display angle instead of down.
    #
    # Shipping this inverted is #203/#169/#171: 180° off at 90° and 270°.
    (854, 480, _JPEG): EncodeRotation(0, invert=False),
    (800, 480, _JPEG): EncodeRotation(0, invert=False),
    # `is1280x480`: mySubMode == 2 → 90, else 0.  Reached as FBL 192 (pm 68)
    # and as FBL 128 (mode 3); both assign mySubMode = SUB, and keying on the
    # resolution is what makes one row serve both.
    (1280, 480, _JPEG): EncodeRotation(0, alt_base=90, alt_subs=frozenset({2})),
    # `is960x320`: mySubMode < 5 → 0, else 180.  Written with the polarity
    # reversed so the set stays finite — SUB is a byte, so "not < 5" is every
    # value the panel can send that is not listed.
    (960, 320, _JPEG): EncodeRotation(
        180, alt_base=0, alt_subs=frozenset({0, 1, 2, 3, 4})),
    # `is960x540`: mySubMode == 5 || == 7 → 180, else 0.
    (960, 540, _JPEG): EncodeRotation(
        0, alt_base=180, alt_subs=frozenset({5, 7})),
    # `is1920x462 || is1920x440`: mySubMode < 2 || > 4 → 180, else 0.
    (1920, 462, _JPEG): EncodeRotation(
        180, alt_base=0, alt_subs=frozenset({2, 3, 4})),
    (1920, 440, _JPEG): EncodeRotation(
        180, alt_base=0, alt_subs=frozenset({2, 3, 4})),
    # `is640x480 || is360x360 || is640x172` — one arm, all base 0.
    #
    # 360×360 being NAMED here is what retires the long-standing "360 fan-hub
    # diverges from the C#" tripwire: it was read off a decompile that predated
    # this build, where 360×360 matched no guard and fell to the base-90
    # default.  2.1.6 gives it an arm of its own and we already ship base 0.
    (640, 480, _JPEG): EncodeRotation(0),
    (360, 360, _JPEG): EncodeRotation(0),
    (640, 172, _JPEG): EncodeRotation(0),
    # ── ImageTo565 ────────────────────────────────────────────────────────
    # `is240x240 || is320x320 || is480x480` → 0/270/180/90.
    (240, 240, _565): EncodeRotation(0),
    (320, 320, _565): EncodeRotation(0),
    (480, 480, _565): EncodeRotation(0),
    # `is640x172` → 270/180/90/0.  No panel reaches this today (PM 15 hands us
    # a JPEG 640×172), but the switch has the arm and leaving it out would make
    # the next RGB565 640×172 silently take the base-90 default.
    (640, 172, _565): EncodeRotation(270),
}

# Both switches end in the same default arm: 90/0/270/180.  It is what the
# 320×240 RGB565 panels (the Frozen Warframe family) actually use, so this is a
# live value, not a safety net.
_DEFAULT_ENCODE_ROTATION = EncodeRotation(90)


def resolve_encode_rotation(
    resolution: tuple[int, int], jpeg: bool, sub: int = 0,
) -> EncodeRotation:
    """The wire rotation for a panel, with any SUB-byte arm already applied.

    The single entry point to :data:`ENCODE_ROTATIONS`.  Callers pass the
    resolution and encoder they will actually ship with — for a bulk device
    that is the PM-derived JPEG/RGB565 override, not whatever ``FBL_PROFILES``
    defaulted to — and get back the resolved rotation to store on the profile.
    """
    w, h = resolution
    rotation = ENCODE_ROTATIONS.get((w, h, jpeg))
    if rotation is None:
        log.debug("resolve_encode_rotation: %dx%d jpeg=%s not in the C# "
                  "switch → default base %d°", w, h, jpeg,
                  _DEFAULT_ENCODE_ROTATION.base)
        return _DEFAULT_ENCODE_ROTATION
    resolved = rotation.for_sub(sub)
    log.debug("resolve_encode_rotation: %dx%d jpeg=%s sub=%d → base=%d "
              "invert=%s", w, h, jpeg, sub, resolved.base, resolved.invert)
    return resolved


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


# FBL 224 is shared by 5 resolutions — PM byte disambiguates.
#
# 9 and 11 are stated even though they equal the fallback below.  The C#
# names them outright — `pm == 9 || pm == 11 → is854x480` (FormCZTV.cs:729)
# — so 854x480 is the KNOWN answer here, not a coincidence that the default
# happened to match.  Left out, the code cannot tell "catalogued" from
# "guessed", and tells a user whose cooler we fully support that it is
# unrecognised (#248).
_FBL_224_BY_PM: dict[int, tuple[int, int]] = {
    9:  (854, 480),
    10: (960, 540),
    11: (854, 480),
    12: (800, 480),
    13: (960, 320),
    15: (640, 172),
    16: (960, 540),
    17: (960, 320),
}


# FBL 192 is shared by 3 resolutions — PM byte disambiguates.
#
# PM 1 and PM 65 are one branch in the C#, not two:
# `pm == 65 || (pm == 1 && pmSub == 49)` (FormCZTV.cs:715).  PM 1 reaches
# this table through _PM_SUB_TO_FBL[(1, 49)]; PM 65 is Trofeo Vision 9.16
# (0416:5408, SUB=5).  Both are stated for the reason given above.
#
# PM 66 is deliberately ABSENT.  The variant table names it (ELITE VISION /
# LF14 / LD7) and _PM_TO_FBL_OVERRIDES routes it here on inherited
# authority, but FormCZTV.cs has no `pm == 66` branch anywhere — the vendor
# app never drives that byte, so 1920x462 would be OUR guess wearing a
# catalogued row's clothing.  Omitting it is what lets the warning tell the
# truth: we route PM 66 to this FBL and do not know its geometry.
_FBL_192_BY_PM: dict[int, tuple[int, int]] = {
    1:  (1920, 462),
    65: (1920, 462),
    68: (1280, 480),
    69: (1920, 440),
}


# Compound (PM, SUB) keys where sub byte changes the FBL mapping
_PM_SUB_TO_FBL: dict[tuple[int, int], int] = {
    (1, 48): 114,   # 1600x720
    (1, 49): 192,   # 1920x462
}


# The FBL codes shared by several resolutions → (PM table, fallback).
#
# One row each rather than one ``if fbl == …`` branch each: the two branches
# were byte-identical apart from these two values, and each rebuilt the whole
# DeviceProfile field by field — so a field added to the dataclass was silently
# dropped for exactly the two FBLs that need the most care.
_SHARED_FBLS: dict[int, tuple[dict[int, tuple[int, int]], tuple[int, int]]] = {
    192: (_FBL_192_BY_PM, (1920, 462)),
    224: (_FBL_224_BY_PM, (854, 480)),
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
    # DEBUG, not INFO: a pure table lookup called in loops during catalog
    # enumeration.  At INFO it was one of the two largest emitters in the
    # whole log, crowding real actions out of the report's action history.
    log.debug("pm_to_fbl: pm=%d sub=%d", pm, sub)
    if (pm, sub) in _PM_SUB_TO_FBL:
        return _PM_SUB_TO_FBL[(pm, sub)]
    return _PM_TO_FBL_OVERRIDES.get(pm, pm)


def _warn_unknown(
    what: str, value: int, context: str, assumed: tuple[int, int],
) -> None:
    """Announce that the catalog did not recognise a device and guessed.

    The tables map a handshake byte to a resolution, and every lookup used a
    plain ``.get(key, default)`` — so an unrecognised panel rendered at a
    plausible-but-invented size, forever, with nothing in the log admitting
    a guess had been made.  The user then sees a picture that is subtly the
    wrong shape and has no way to attribute it; one reporter resorted to
    photographing 1px test gratings and running an FFT on them to work out
    what our own log could have told him (#248).

    WARNING because the whole diagnostic loop rests on ``trcc report``: this
    line makes an unsupported device SELF-diagnosing — the reporter's own
    paste names the byte we did not know and the size we assumed.
    """
    log.warning(
        "get_profile: UNKNOWN %s=%d (%s) — no catalog entry, assuming "
        "%dx%d.  The panel may render at the wrong size; please report this "
        "line at https://github.com/Lexonight1/thermalright-trcc-linux/issues "
        "so the device can be added.",
        what, value, context, assumed[0], assumed[1],
    )


def _resolution_by_pm(
    table: dict[int, tuple[int, int]],
    pm: int,
    fallback: tuple[int, int],
    fbl: int,
) -> tuple[int, int]:
    """Resolution for *pm* within a shared FBL, warning when it is a guess."""
    known = table.get(pm)
    if known is not None:
        log.debug("get_profile: fbl=%d pm=%d → %dx%d (catalogued)",
                  fbl, pm, known[0], known[1])
        return known
    _warn_unknown("PM", pm, f"fbl={fbl}", fallback)
    return fallback


def get_profile(fbl: int, pm: int = 0) -> DeviceProfile:
    """Look up the device profile for an FBL code.

    For FBL 192/224, the PM byte disambiguates among multiple resolutions
    that share the FBL. All other FBL values map 1:1 to a profile.
    Unknown FBLs fall back to the 320×320 big-endian default.
    """
    log.debug("get_profile: fbl=%d pm=%d", fbl, pm)
    profile = FBL_PROFILES.get(fbl, _DEFAULT_PROFILE)
    if fbl not in FBL_PROFILES:
        _warn_unknown("FBL", fbl, f"pm={pm}", (profile.width, profile.height))
    shared = _SHARED_FBLS.get(fbl)
    if shared is not None:
        by_pm, fallback = shared
        w, h = _resolution_by_pm(by_pm, pm, fallback, fbl)
        profile = dataclasses.replace(profile, width=w, height=h)
    rotation = resolve_encode_rotation(profile.resolution, profile.jpeg)
    return dataclasses.replace(
        profile, encode_base=rotation.base, encode_invert=rotation.invert)


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


def resolve_encode_angle(profile: DeviceProfile, orientation: int) -> int:
    """Wire rotation for a panel = its encode base ± the user orientation.

    ``send = (encode_base + (orientation if not invert else -orientation)) % 360``
    — the whole of the C# ``directionB`` switch in ``ImageToJpg`` /
    ``ImageTo565``.  Both terms are already resolved on the profile (see
    :data:`ENCODE_ROTATIONS`), so this branches on nothing and is safe to call
    per frame.

    There used to be a second function beside this one, ``wire_rotation``,
    computing the same angle from its own inline resolution table.  It carried
    no invert term and no SUB term, so the two disagreed on exactly the panels
    that need both: 854x480 and 800x480 count UP with the display angle
    (``invert=False``) and every other family counts down.  One formula, one
    table.
    """
    signed = orientation if not profile.encode_invert else -orientation
    angle = (profile.encode_base + signed) % 360
    frame_log.debug("resolve_encode_angle: base=%d invert=%s orient=%d → %d°",
              profile.encode_base, profile.encode_invert, orientation, angle)
    return angle


# The three resolutions whose SUB byte encodes a portrait physical mount.
# From FormCZTVInit: every OTHER resolution assigns its catalog
# unconditionally, so this is a scoped rule and must not be generalised.
_PORTRAIT_MOUNT_RESOLUTIONS = frozenset({(854, 480), (960, 540), (800, 480)})
_PORTRAIT_MOUNT_MIN_SUB = 5


def is_portrait_mounted(resolution: tuple[int, int], sub: int) -> bool:
    """Whether a panel of *resolution* reporting *sub* is mounted portrait.

    The C# asks ``pmSub < 5`` for exactly three resolutions and, when that
    fails, loads the transposed theme catalog — ``854480\\`` becomes
    ``480854\\``, and likewise for 960x540 and 800x480.  So a sub of 5 or
    more on one of those panels means the screen is turned in its cooler and
    its content is authored portrait from the start, at user-orientation 0.

    Pure function of the handshake bytes so an auditor can resolve it with no
    USB, exactly like ``bulk_profile`` around it.  (#262, #203)
    """
    mounted = (resolution in _PORTRAIT_MOUNT_RESOLUTIONS
               and sub >= _PORTRAIT_MOUNT_MIN_SUB)
    log.debug("is_portrait_mounted: %s sub=%d → %s", resolution, sub, mounted)
    return mounted


def wire_angle(
    profile: DeviceProfile, orientation: int, portrait_content: bool,
) -> int:
    """The single wire-frame rotation a device gets at a user *orientation*.

    Collapses ``DisplayService.build_frame``'s three-way selection into one
    pure decision so the render path and any auditor share it (DRY):

      * rotate panels → :func:`resolve_encode_angle` (the C#-source-verified
        per-resolution encode base, #203/#169)
      * squares / non-rotate panels → user orientation only (``360 − orient``)

    The first bullet used to be two, split on ``widescreen``: non-widescreen
    panels went to a ``wire_rotation`` that read its own resolution table and
    widescreen JPEG ones came here.  Both were the same C# switch read twice,
    and only one of the two copies knew that 854/800 do not invert.

    Portrait content on a rotate panel is composed UPRIGHT (``post_rotate=0``)
    and rides the SAME angle as landscape content — it is NOT opted out.  This is the #234 fix: gating the wire rotation on ``not
    portrait_content`` sent a base-0 panel's upright portrait canvas (480×640) to
    the device unrotated, which the fixed 640×480 panel then squeezed.  Letting
    portrait content ride ``base − orientation`` transposes it (270° @90 / 90°
    @270) onto the device's landscape buffer — identical to how widescreen JPEG
    panels already reached their fixed 1600×720 wire dims (#169).  A base-90
    RGB565 panel gets 0°/180°, net-identical to the old compose-time flip.
    ``portrait_content`` now only distinguishes the square / non-rotate fallback
    (where content is never portrait, so it is a no-op in practice).
    """
    if profile.rotate:
        angle = resolve_encode_angle(profile, orientation)
        frame_log.debug("wire_angle: rotate panel %dx%d @ %d° -> %d° "
                        "(per-resolution encode base)",
                        profile.width, profile.height, orientation, angle)
        return angle
    if orientation and not portrait_content:
        angle = (360 - orientation) % 360
        frame_log.debug("wire_angle: non-rotate panel %dx%d @ %d° -> %d° "
                        "(user orientation only)",
                        profile.width, profile.height, orientation, angle)
        return angle
    frame_log.debug("wire_angle: %dx%d @ %d° portrait=%s -> 0° (no rotation)",
                    profile.width, profile.height, orientation, portrait_content)
    return 0


# ── Per-SKU artwork variants ──────────────────────────────────────────────
#
# One resolution can have MORE THAN ONE artwork library, chosen by a handshake
# byte, because two coolers with the same panel are different products with
# different chrome.  The C# picks the directory, not just the resolution:
#
#     is1600x720 → pmSub 2 → 1600720u / 7201600u   (FormCZTV.cs:1290-1353)
#                  pmSub 3 → 1600720l / 7201600l
#                  pmSub 4 → 1600720u / 7201600u
#                  else    → 1600720  / 7201600
#     is480x480  → PM 3    → zt480480y             (FormCZTV.cs:5746, masks only)
#                  else    → zt480480
#
# The landscape/portrait half of that choice is already ours: callers pass the
# ORIENTED resolution.  What is missing is only the trailing letter, which is
# why these return one and not a directory name.
_VARIANT_RESOLUTIONS = frozenset({(1600, 720), (720, 1600)})
_VARIANT_BY_SUB: dict[int, str] = {2: "u", 3: "l", 4: "u"}


def artwork_variant(resolution: tuple[int, int], sub: int = 0) -> str:
    """The theme/background library suffix for this panel: ``""``, ``u``, ``l``.

    ``""`` means the unsuffixed library, which is every panel except the
    1600x720 pair at SUB 2/3/4 — so passing a SUB we have no rule for is not a
    guess, it is the C#'s own ``default:`` arm.
    """
    if resolution not in _VARIANT_RESOLUTIONS:
        return ""
    variant = _VARIANT_BY_SUB.get(sub, "")
    log.debug("artwork_variant: %dx%d sub=%d → %r",
              resolution[0], resolution[1], sub, variant)
    return variant


def mask_variant(resolution: tuple[int, int], sub: int = 0, pm: int = 0) -> str:
    """The mask library suffix — the theme rule plus one PM-keyed arm.

    Masks carry a variant the theme and background libraries do not: 480x480 at
    PM 3 has its own ``zt480480y``.  It is keyed on PM rather than SUB, which is
    why this is a second function and not a flag on the first — the two arms
    read different handshake bytes.
    """
    if resolution == (480, 480) and pm == 3:
        log.debug("mask_variant: 480x480 pm=3 → 'y'")
        return "y"
    return artwork_variant(resolution, sub)
