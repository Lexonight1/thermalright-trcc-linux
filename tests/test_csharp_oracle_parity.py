"""Our device tables must match the C# oracle — asserted, not remembered.

The C# app is the source of truth for device geometry (see METHOD.md).  Its
tables were transcribed into ``core/protocol.py`` at the cutover, and nothing
has ever checked them since.  Every divergence found so far was data, not
logic: a wrong tuple, a wrong resolution, a base folded in that should not be.

This file is the diff, as a test.  It exists for two reasons, both learned the
hard way on 2026-07-15:

1. **Real drift fails the build.**  #207 shipped for two releases with rotation
   maths that was already correct and a registry row that withheld it.  Nobody
   re-checked because re-checking meant hand-diffing a decompile.
2. **Phantom bugs die in one second instead of twenty minutes.**  I "found" an
   FBL 224 bug by reading ``_PM_TO_FBL_OVERRIDES`` (10 → 224) and
   ``FBL_PROFILES[224]`` (854×480) and concluding PM 10 panels get the wrong
   shape — never reading ``get_profile``, which takes the PM precisely to
   disambiguate and was correct all along.  I proposed building a fix for
   nothing.  A green test says "already right" before the hunt starts.

Every constant below is transcribed from the decompile with its line cited, so
the next reader verifies against the C# rather than trusting this file.
Source: /home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.CZTV/FormCZTV.cs
(TRCC 2.1.6 — see memory ``reference_cs_decompile_path``.)

If a test here fails, ONE of these is true, in likelihood order:
  * our table drifted (fix the table);
  * the C# was re-decompiled and genuinely differs (update the constant + cite
    the new line);
  * the transcription below is wrong (fix it — and say so).
Never "fix" a failure by loosening the assertion.
"""
from __future__ import annotations

import pytest

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.core.protocol import (
    fbl_to_resolution,
    get_profile,
    pm_to_fbl,
    resolve_encode_angle,
    wire_angle,
)

# ── FormCZTV.cs::ImageToJpg — the `directionB` switch, per resolution ──
#
# Each branch maps the user's display angle to the angle the frame is rotated
# by before it goes on the wire.  Transcribed verbatim; the C# writes
# 180.00002f / 90.00002f (float nudges), which are 180 / 90.
#
#   :2677  is1600x720                                    → 0:180 90:90 180:0 270:270
#   :2683  is1280x480 || is800x480 || is854x480 || is960x540
#                                                        → 0:0 90:270 180:180 270:90
#   :2690  is1920x462                                    → 0:180 90:90 180:0 270:270
#   :2697  is640x480                                     → 0:0 90:270 180:180 270:90
_CSHARP_ENCODE_ANGLES: dict[tuple[int, int], dict[int, int]] = {
    (1600, 720): {0: 180, 90: 90, 180: 0, 270: 270},
    (1920, 462): {0: 180, 90: 90, 180: 0, 270: 270},
    (1280, 480): {0: 0, 90: 270, 180: 180, 270: 90},
    (854, 480):  {0: 0, 90: 270, 180: 180, 270: 90},
    (640, 480):  {0: 0, 90: 270, 180: 180, 270: 90},
}

# ── FormCZTV.cs:697-762 — myDeviceMode == 2: the PM byte → panel geometry ──
#
# FBL is NOT the geometry key in the C#: 9/10/11/12 all set `fbl = 224` and the
# resolution comes from the is<W>x<H> flag.  We key profiles by FBL and
# disambiguate on PM inside get_profile() — same outcome, different shape.
#
#   :706  pm == 64 || (pm == 1 && pmSub == 48)  → is1600x720, fbl 114
#   :720  pm == 65 || (pm == 1 && pmSub == 49)  → is1920x462, fbl 192
#   :734  pm == 9  || pm == 11                  → is854x480,  fbl 224
#   :748  pm == 10                              → is960x540,  fbl 224
#   :762  pm == 12                              → is800x480,  fbl 224
_CSHARP_PM_TO_RESOLUTION: dict[int, tuple[int, int]] = {
    9:  (854, 480),
    10: (960, 540),
    11: (854, 480),
    12: (800, 480),
    64: (1600, 720),
    65: (1920, 462),
}


@pytest.mark.parametrize("resolution,angles", sorted(_CSHARP_ENCODE_ANGLES.items()))
def test_encode_angles_match_the_csharp_switch(
    resolution: tuple[int, int], angles: dict[int, int],
) -> None:
    """Wire rotation must equal the C# ImageToJpg directionB switch.

    #169/#203 were exactly this drifting: a sub-byte base folded into the
    widescreen encode that the C# does not have, putting 90/270 out by 180.
    """
    fbls = [f for f in (64, 114, 128, 192, 224)
            if fbl_to_resolution(f, _pm_for(f)) == resolution]
    assert fbls, f"no FBL profile resolves to {resolution} — panel unsupported?"
    for fbl in fbls:
        profile = get_profile(fbl, _pm_for(fbl))
        got = {deg: resolve_encode_angle(profile, deg) for deg in (0, 90, 180, 270)}
        assert got == angles, (
            f"FBL {fbl} ({resolution[0]}x{resolution[1]}) encode angles drifted "
            f"from the C#: ours={got} C#={angles}"
        )


def _pm_for(fbl: int) -> int:
    """A PM that selects this FBL's DEFAULT resolution (get_profile takes PM)."""
    return {224: 9, 192: 65, 114: 64}.get(fbl, 0)


@pytest.mark.parametrize("pm,resolution", sorted(_CSHARP_PM_TO_RESOLUTION.items()))
def test_pm_resolves_to_the_csharp_resolution(
    pm: int, resolution: tuple[int, int],
) -> None:
    """PM → panel geometry must match the C#'s mode-2 branch.

    FBL 224 is shared by 854x480 / 960x540 / 800x480 and FBL 192 by several —
    the PM disambiguates.  Reading only the FBL table makes 960x540 and 800x480
    look broken when they are not; this pins the resolved answer instead.
    """
    fbl = pm_to_fbl(pm, 0)
    got = fbl_to_resolution(fbl, pm)
    assert got == resolution, (
        f"PM {pm} → fbl {fbl} → {got} but the C# says {resolution}"
    )


def test_every_csharp_resolution_has_a_profile() -> None:
    """A panel the C# drives that we have no profile for is an unsupported cooler."""
    missing = {
        res for res in _CSHARP_PM_TO_RESOLUTION.values()
        if not any(fbl_to_resolution(f, p) == res
                   for p, f in ((p, pm_to_fbl(p, 0))
                                for p in _CSHARP_PM_TO_RESOLUTION))
    }
    assert not missing, (
        f"the official app drives these panels and we have no profile: {missing}"
    )


def test_oracle_tables_are_not_silently_empty() -> None:
    """Guard the guard: an emptied table would make every test above vacuous."""
    assert len(_CSHARP_ENCODE_ANGLES) >= 5
    assert len(_CSHARP_PM_TO_RESOLUTION) >= 6
    for angles in _CSHARP_ENCODE_ANGLES.values():
        assert sorted(angles) == [0, 90, 180, 270]
    assert len(_CSHARP_BULK_WIRE_ANGLES) >= 6
    for _, _, angles in _CSHARP_BULK_WIRE_ANGLES:
        assert sorted(angles) == [0, 90, 180, 270]
    assert sorted(_CSHARP_360_FAN_ANGLES) == [0, 90, 180, 270]


# ── The COMPOSED wire angle, per real bulk handshake fingerprint ──────────
#
# The tests above check the rotation tables in isolation.  This one checks what
# a panel actually gets: the whole PM/SUB → FBL → profile → angle chain, summed
# the way ``DisplayService`` sums it.  That composition is where the maths hid
# its last bug, and where an auditor hallucinated two more (2026-07-15):
#
#   * ``wire_angle`` (build_frame, display.py:384) — the encoder/resolution base
#   * ``+ encode_baseline`` (_encode_for_wire, display.py:1321) — the PM-keyed
#     hardware-mount offset (#137)
#
# The C# folds both into ONE base; we split them.  So only the SUM is comparable
# — check either half alone and the FW360 Ultra reads 180° "wrong" while being
# provably right on satoru8's glass (#137, confirmed on commit 3a3b9ea1).
#
# Expected angles transcribed from FormCZTV.cs with the encoder that
# ``myDeviceMode == 2`` selects at the call site (:2178 → ImageToJpg, else
# ImageTo565); USBLCDNew sends JPEG for every bulk PM except 32:
#
#   :2655  is320x320 || is480x480, myDevicePingMu == 6  → 0:180 90:90 180:0 270:270
#   :2661  is320x320 || is480x480, otherwise            → 0:0 90:270 180:180 270:90
#   :2669  myDevicePingMu == 5 (Mjolnir 320x240)        → 0:0 90:270 180:180 270:90
#   :2677  is1600x720                                   → 0:180 90:90 180:0 270:270
#   :2683  is854x480 || is960x540                       → 0:0 90:270 180:180 270:90
#
# (pm, sub) are the bytes the panel reports at handshake — resp[24]/resp[36].
_CSHARP_BULK_WIRE_ANGLES: tuple[tuple[str, tuple[int, int], dict[int, int]], ...] = (
    # FW360 Ultra: the PM-keyed 180° mount offset.  DO NOT "fix" this to 0 on a
    # reading of ImageTo565 — it is JPEG (PM 6 != 32), so ImageToJpg's pm==6
    # branch applies, and satoru8 confirmed it upright on the panel (#137).
    ("FW360 Ultra",         (6, 0),   {0: 180, 90: 90, 180: 0, 270: 270}),
    ("Mjolnir",             (5, 1),   {0: 0, 90: 270, 180: 180, 270: 90}),
    # Unknown bulk PM → stays on the 480x480 base, never echoes PM as FBL (#176).
    ("GrandVision 360",     (50, 0),  {0: 0, 90: 270, 180: 180, 270: 90}),
    ("widescreen 854x480",  (11, 5),  {0: 0, 90: 270, 180: 180, 270: 90}),
    ("widescreen 960x540",  (10, 0),  {0: 0, 90: 270, 180: 180, 270: 90}),
    ("bulk 1600x720",       (1, 48),  {0: 180, 90: 90, 180: 0, 270: 270}),
)


@pytest.mark.parametrize("label,fingerprint,expected",
                         _CSHARP_BULK_WIRE_ANGLES, ids=lambda v: v if isinstance(v, str) else "")
def test_bulk_wire_angle_matches_the_csharp(
    label: str, fingerprint: tuple[int, int], expected: dict[int, int],
) -> None:
    """The angle a real bulk fingerprint puts on the wire == the C#'s.

    Resolves through the SHIPPING ``bulk_profile`` — never a copy of it.  A
    hand-rolled copy in ``dev/decompiler/audit_rotation.py`` dropped the
    ``_BULK_KNOWN_PMS`` guard, invented a phantom FBL 6, and reported this very
    device as a 180° bug.  An oracle that re-implements the code it audits
    proves nothing about what ships.
    """
    pm, sub = fingerprint
    fbl, profile = bulk_profile(pm, sub)
    for orientation, want in expected.items():
        got = (wire_angle(profile, orientation, portrait_content=False)
               + profile.encode_baseline) % 360
        assert got == want, (
            f"{label} (pm={pm} sub={sub} → fbl={fbl}, {profile.width}x"
            f"{profile.height} {'JPEG' if profile.jpeg else 'RGB565'}) at "
            f"display angle {orientation}°: we send {got}°, the C# sends {want}°"
        )


# ── FBL 54 360×360 fan-hub LCD — the ONE family that diverges from the C# ──
#
# 360×360 matches NO resolution guard in either C# rotation switch:
#   * ImageToJpg (isFanLcd → mode 2 → JPEG) tests `is320x320 || is480x480`
#   * ImageTo565 tests `is240x240 || is320x320 || is480x480`
# so it falls through to the DEFAULT branch → BASE 90 (FormCZTV.cs:2706-2710,
# 0:90 90:0 180:270 270:180).  Our ``wire_rotation`` lumps 360 with the other
# squares (BASE 0), so we send every frame 90° rotated from the C#.  Confirmed
# the sole divergence by ``dev/decompiler/rotation_trace.py`` (18/19 devices
# agree).
#
# xfail(strict) — NOT a hard failure and NOT a loosened assertion.  The assertion
# below is the true C# value; the marker records that our shipping code does not
# match it *yet*, deliberately: there is no 360×360 reporter to confirm which way
# is upright on glass, and flipping a device path blind breaks "no definites
# without hardware".  When ``wire_rotation`` is corrected to base 90 (or a 360
# owner confirms it), this XPASSES → strict turns that into a failure → delete the
# marker and the drift is closed.  Do not remove this test to make CI quiet.
_CSHARP_360_FAN_ANGLES: dict[int, int] = {0: 90, 90: 0, 180: 270, 270: 180}


@pytest.mark.xfail(
    strict=True,
    reason="360x360 fan-hub (FBL 54) hits the C# ImageToJpg DEFAULT branch "
    "(base 90, FormCZTV.cs:2706); wire_rotation sends base 0. No 360 reporter "
    "to confirm on glass — see dev/decompiler/rotation_trace.py. Fix = base 90 "
    "in wire_rotation, then drop this marker.",
)
def test_360_fan_hub_matches_the_csharp_default_branch() -> None:
    """The 360×360 fan-hub wire angle must equal the C# default-branch switch.

    FBL 54 is square + JPEG but is excluded from BOTH rotation switches' square
    guards, so the C# rotates it via the default branch (base 90).  Currently
    xfailing: our table gives base 0.  This is the CI tripwire for that drift.
    """
    profile = get_profile(54, 54)
    got = {deg: wire_angle(profile, deg, portrait_content=False)
           for deg in (0, 90, 180, 270)}
    assert got == _CSHARP_360_FAN_ANGLES
