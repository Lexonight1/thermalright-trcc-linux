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

Source: **TRCC 2.1.6**, ``~/Downloads/TRCC_2.1.6_decompiled/``.  Verify the
tree before trusting a citation — ``grep -rh AssemblyVersion
~/Downloads/TRCC_2.1.6_decompiled/Properties/*.cs`` must say ``2.1.6.0``.  This
header used to name ``TRCCCAPEN/TRCC_decompiled`` as 2.1.6; that tree is
**2.0.3**, carved from an executable four months older than the 2.1.6
installer, and every rotation conclusion drawn from it was drawn from the wrong
release.  See memory ``project_csharp_oracle_was_the_wrong_version``.

The ROTATION constants are no longer transcribed here.  They come from
``dev/decompiler/encode_reference.py``, the one transcription, because
this file holding a second copy of them is how they drifted: it asserted a
sub-independent encode base that the real switch contradicts in six families.
What stays here is what this file uniquely checks — the PM byte to geometry
map, and the angle a real handshake fingerprint puts on the wire.

If a test here fails, ONE of these is true, in likelihood order:
  * our table drifted (fix the table);
  * the C# was re-decompiled and genuinely differs (update the constant + cite
    the new line);
  * the transcription below is wrong (fix it — and say so).
Never "fix" a failure by loosening the assertion.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.core.protocol import (
    fbl_to_resolution,
    get_profile,
    pm_to_fbl,
    resolve_encode_angle,
    wire_angle,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "decompiler"))

from encode_reference import (  # pyright: ignore[reportMissingImports]
    csharp_encode_angles,
)
from formcztv_init import (  # pyright: ignore[reportMissingImports]
    form_cztv_init,
)

# The resolutions this file checks a wire angle for.  The ANGLES come from the
# oracle; only the list of panels to sweep lives here.
_ENCODE_PANELS: tuple[tuple[int, int], ...] = (
    (1600, 720), (1920, 462), (1280, 480), (854, 480), (640, 480),
)


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


@pytest.mark.parametrize("resolution", _ENCODE_PANELS)
def test_encode_angles_match_the_csharp_switch(
    resolution: tuple[int, int],
) -> None:
    """Wire rotation must equal the C# ImageToJpg directionB switch.

    Reached through the FBL that resolves to this panel, so it checks the whole
    lookup — FBL and PM to profile to angle — rather than the table alone.
    ``tests/test_encode_rotation.py`` sweeps the table itself, at every SUB.
    """
    fbls = [f for f in (64, 114, 128, 192, 224)
            if fbl_to_resolution(f, _pm_for(f)) == resolution]
    assert fbls, f"no FBL profile resolves to {resolution} — panel unsupported?"
    for fbl in fbls:
        profile = get_profile(fbl, _pm_for(fbl))
        got = {deg: resolve_encode_angle(profile, deg) for deg in (0, 90, 180, 270)}
        angles = csharp_encode_angles(resolution, jpeg=profile.jpeg)
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
    """Guard the guard: a hollow oracle makes every test above vacuous.

    This matters MORE now that the expectations are generated rather than
    written out.  A hand-written table that goes missing fails loudly at
    import; an oracle that quietly returns an empty or constant answer would
    make every comparison above assert nothing at all, and stay green.
    """
    assert len(_CSHARP_PM_TO_RESOLUTION) >= 6
    assert len(_ENCODE_PANELS) >= 5
    assert len(_CSHARP_BULK_FINGERPRINTS) >= 6
    for resolution in _ENCODE_PANELS:
        angles = csharp_encode_angles(resolution, jpeg=True)
        assert sorted(angles) == [0, 90, 180, 270], resolution
        assert sorted(angles.values()) == [0, 90, 180, 270], (
            f"{resolution}: the oracle returned {angles} — a real switch arm "
            f"is a permutation of the four right angles, so this one is a stub"
        )
    # Not every panel answers alike: an oracle collapsed to one arm would pass
    # everything above while proving nothing.
    distinct = {tuple(sorted(csharp_encode_angles(r, jpeg=True).items()))
                for r in _ENCODE_PANELS}
    assert len(distinct) >= 2, (
        "every panel got the same arm — the oracle is not discriminating"
    )
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
# ImageTo565); USBLCDNew sends JPEG for every bulk PM except 32.
#
# Only the FINGERPRINTS are written here.  The angles come from the oracle,
# keyed by the resolution + encoder the shipping ``bulk_profile`` resolves the
# fingerprint to, so this asserts the whole handshake path without holding a
# second copy of the switch.
#
# (pm, sub) are the bytes the panel reports at handshake — resp[24]/resp[36].
_CSHARP_BULK_FINGERPRINTS: tuple[tuple[str, tuple[int, int]], ...] = (
    # FW360 Ultra: the PM-keyed 180° mount offset.  DO NOT "fix" this to 0 on a
    # reading of ImageTo565 — it is JPEG (PM 6 != 32), so ImageToJpg's pm==6
    # branch applies, and satoru8 confirmed it upright on the panel (#137).
    ("FW360 Ultra",         (6, 0)),
    ("Mjolnir",             (5, 1)),
    # Unknown bulk PM → stays on the 480x480 base, never echoes PM as FBL (#176).
    ("GrandVision 360",     (50, 0)),
    ("widescreen 854x480",  (11, 5)),
    ("widescreen 960x540",  (10, 0)),
    ("bulk 1600x720",       (1, 48)),
)


@pytest.mark.parametrize("label,fingerprint", _CSHARP_BULK_FINGERPRINTS,
                         ids=lambda v: v if isinstance(v, str) else "")
def test_bulk_wire_angle_matches_the_csharp(
    label: str, fingerprint: tuple[int, int],
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
    # Form1.cs:1071 — the bulk class enters FormCZTVInit at fbl 72, mode 2.
    my_sub_mode = form_cztv_init(72, m=2, pm=pm, pmSub=sub).mySubMode
    expected = csharp_encode_angles(
        profile.resolution, jpeg=profile.jpeg, pm=pm, my_sub_mode=my_sub_mode)
    for orientation, want in expected.items():
        got = (wire_angle(profile, orientation, portrait_content=False)
               + profile.encode_baseline) % 360
        assert got == want, (
            f"{label} (pm={pm} sub={sub} → fbl={fbl}, {profile.width}x"
            f"{profile.height} {'JPEG' if profile.jpeg else 'RGB565'}) at "
            f"display angle {orientation}°: we send {got}°, the C# sends {want}°"
        )


# ── FBL 54 360×360 fan-hub LCD — NOT a divergence.  Retired 2026-08-17. ──
#
# This carried an ``xfail(strict)`` asserting the C# rotates 360×360 by base 90
# while we send base 0, on the reading that 360×360 matches no resolution guard
# in either switch and falls to the DEFAULT branch.
#
# That reading is correct for TRCC **2.0.3** and wrong for 2.1.6, which is the
# release we port.  2.1.6's ``ImageToJpg`` has an arm naming it outright —
# ``is640x480 || is360x360 || is640x172`` → 0/270/180/90, base 0 — the same
# angles we already ship.  The tripwire was firing on a difference between two
# C# releases, not between us and the C#, and it labelled correct shipping code
# as known-broken for as long as it stood.
#
# Kept as a live assertion rather than deleted: the panel still has no reporter,
# so the value of a bench check against the oracle is unchanged.  It simply
# passes now.
_CSHARP_360_FAN_ANGLES: dict[int, int] = {0: 0, 90: 270, 180: 180, 270: 90}


def test_360_fan_hub_matches_the_csharp_default_branch() -> None:
    """The 360×360 fan-hub wire angle must equal the C# arm that names it."""
    profile = get_profile(54, 54)
    got = {deg: wire_angle(profile, deg, portrait_content=False)
           for deg in (0, 90, 180, 270)}
    assert got == _CSHARP_360_FAN_ANGLES
    assert got == csharp_encode_angles((360, 360), jpeg=profile.jpeg), (
        "the oracle and the hand-written row above disagree — one of them is "
        "a stale transcription"
    )


# The C#'s mode-3 FBL rewrites — the HID discovery path, where
# ``AddhidDeviceList`` calls ``FormCZTVInit((byte)data, 3, …, 95, 100, …)``
# (Form1.cs:1604): the FBL arrives as the FIRST argument and the PM is the
# constant 100.  ``FormCZTVInit`` then rewrites three of those codes
# (FormCZTV.cs:1032-1044):
#
#     fbl == 129 → fbl = 72                          (straight alias)
#     fbl == 59  → is640x172 = true;  fbl = 224
#     fbl == 60  → is176x320 = true;  fbl = 224
#
# ``test_pm_resolves_to_the_csharp_resolution`` above cannot see any of this:
# its table is PM-keyed and every entry is a mode-2 PM, so a mode-3 panel is
# outside its universe no matter how wrong we get it.  That is how fbl 59 sat
# resolving to 320x320 — the wrong size for a 640x172 panel — under a green
# suite.
_CSHARP_MODE3_REWRITES: dict[int, tuple[int, int]] = {
    129: (480, 480),
    59: (640, 172),
    60: (320, 176),     # is176x320, stored long side first
}


@pytest.mark.parametrize("fbl,resolution",
                         sorted(_CSHARP_MODE3_REWRITES.items()))
def test_mode3_fbl_rewrites_match_the_csharp(
    fbl: int, resolution: tuple[int, int],
) -> None:
    """A mode-3 handshake must land on the panel the C# rewrites it to."""
    got = fbl_to_resolution(fbl, 100)
    assert got == resolution, (
        f"mode-3 fbl {fbl} → {got} but the C# rewrites it to {resolution}"
    )


def test_fbl_59_is_the_pm_15_panel_on_the_rgb565_encoder() -> None:
    """Both C# routes to 640x172 are ONE panel, encoded by the ROUTE.

    ``mode == 2 && pm == 15`` and ``mode == 3 && pm == 100 && fbl == 59`` are
    the same screen; the C# proves it by rewriting the second to 224, the very
    code the first already carries.  But every encode site is
    ``myDeviceMode == 2 ? ImageToJpg : ImageTo565`` (FormCZTV.cs:2825), so the
    mode-3 route ships RGB565 with the ``is640x172`` row repack.  Asserting the
    exact set of differing fields is what stops a change to 224 from reaching
    one route only.
    """
    route3, route2 = get_profile(59, 100), get_profile(224, 15)
    differ = {f.name for f in dataclasses.fields(route3)
              if getattr(route3, f.name) != getattr(route2, f.name)}
    assert differ == {"jpeg", "wire_row_px", "encode_base"}
    assert (route3.jpeg, route3.wire_row_px) == (False, 176)


# ImageTo565's ``is640x172`` arm (2.1.8, FormCZTV.cs:4221): SUB 5 takes the
# default 90/0/270/180, every other SUB 270/180/90/0.  Written out here, not
# read from the oracle, so a wrong transcription cannot agree with itself.
_CSHARP_640x172_RGB565: dict[int, dict[int, int]] = {
    0: {0: 270, 90: 180, 180: 90, 270: 0},
    5: {0: 90, 90: 0, 180: 270, 270: 180},
}


@pytest.mark.parametrize("sub", sorted(_CSHARP_640x172_RGB565))
def test_fbl_59_wire_angle_follows_the_sub_arm(sub: int) -> None:
    """The mode-3 640x172 wire angle must be the 2.1.8 arm for its SUB."""
    profile = get_profile(59, 59, sub)
    got = {deg: resolve_encode_angle(profile, deg) for deg in (0, 90, 180, 270)}
    assert got == _CSHARP_640x172_RGB565[sub]
    my_sub_mode = form_cztv_init(59, m=3, pm=100, pmSub=sub).mySubMode
    assert got == csharp_encode_angles(
        (640, 172), jpeg=False, my_sub_mode=my_sub_mode)


def test_the_176x320_panel_is_dual_orientation_not_just_a_size() -> None:
    """fbl 60 landed; this is what it took beyond the size.

    It was pinned unsupported for a long time with the note that it "needs
    SUB-keyed geometry, not a table row" -- correct, and that geometry arrived
    separately: ``is_portrait_mounted`` now covers it (``pmSub < 5`` ->
    ``320176\\`` at 0 degrees, otherwise ``176320\\`` at 90), and
    ``catalog_spellings`` names those two catalogs by long/short rather than by
    ``(w, h)`` order.  The C# spells the flag short-first (``is176x320``) but
    composes the panel LANDSCAPE at 0 (GIFSize 320x176, FormCZTV.cs:3160), so
    the row is (320, 176) like every other; stored (176, 320) until
    2026-09-30, every angle came out transposed.

    Both catalogs already shipped: ``theme176320.7z`` and ``theme320176.7z``,
    5 themes each, backgrounds measured at 176x320 and 320x176 straight from
    the PNG headers.

    NOT verified on glass: no reporter has ever presented this panel, so the
    row is oracle- and artwork-grounded only.
    """
    from trcc.core.geometry import catalog_spellings
    from trcc.core.protocol import is_portrait_mounted

    assert fbl_to_resolution(60, 100) == (320, 176)
    # The SUB byte picks between the two shipped catalogs.
    assert is_portrait_mounted((320, 176), 4) is False
    assert is_portrait_mounted((320, 176), 5) is True
    # And they are named long/short, NOT (w, h)/(h, w).
    assert catalog_spellings((320, 176)) == ((320, 176), (176, 320))


# ── The HID type-2 / mode-3 route, swept against the C# ────────────────────
#
# test_csharp_conformance sweeps `_BULK_VARIANTS` and nothing else, so no HID
# type-2 fingerprint has ever been compared to the C#.  That blind spot is why
# the mount transcription bug lived in the oracle for as long as it did.
#
# Two axes are compared and a third deliberately is not:
#
#   resolution -- ONLY where the C# set a resolution flag.  On a fingerprint it
#     does not resolve, `st.resolution` falls back to the 20-byte header's
#     hardcoded 240x320 (FormCZTV.cs:4205 writes `240, 0, 64, 1`), which is a
#     WIRE CONSTANT, not a compose canvas.  Comparing it to profile.resolution
#     manufactures a divergence out of two different quantities -- the same
#     category error as widescreen vs isBiliPingmu.
#   mount -- always; it is a pure function of the SUB byte.
#   encode -- NOT compared YET, and that is now a gap, not a choice.  The C#
#     picks JPEG vs RGB565 on `myDeviceMode == 2` (all four call sites).  The
#     old excuse -- fbl 59 shipping jpeg=True -- is gone: it ships RGB565 since
#     438ac584.  What the gap hides, measured 2026-10-01: in 2.1.8 fbl 54 at
#     SUB 4 (CORE VISION) stays mode 3 and encodes RGB565, while we send JPEG
#     at every SUB -- and SUB 4 is not even in the list swept below.  See
#     memory/project_2_1_8_encoder_arms_unported.md.


def _mode3_fingerprints() -> list[tuple[int, int]]:
    """Every catalogued FBL against the SUB bytes a panel actually sends."""
    from trcc.core.protocol import FBL_PROFILES

    return [(fbl, sub) for fbl in sorted(FBL_PROFILES)
            for sub in (0, 1, 2, 3, 5, 6)]


@pytest.mark.parametrize(("fbl", "sub"), _mode3_fingerprints())
def test_mode3_fingerprint_matches_the_csharp(fbl: int, sub: int) -> None:
    """Our shipping profile answers what TRCC 2.1.6 answers on this route.

    No allow-list: measured at 0 divergences over all 120 fingerprints, so a
    failure here is a real disagreement, not a known gap to record.
    """
    import re
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev"
                           / "decompiler"))
    from formcztv_init import (  # pyright: ignore[reportMissingImports]
        CztvState,
        form_cztv_init,
    )

    from trcc.core.protocol import get_profile, is_portrait_mounted

    state = form_cztv_init(fbl, m=3, pm=100, pmSub=sub)
    resolved = any(getattr(state, name) for name in dir(CztvState)
                   if re.fullmatch(r"is\d+x\d+", name))
    profile = get_profile(fbl, fbl, sub)

    if not resolved:
        # The mode-3 route does not resolve this FBL -- 192 and 224 are the
        # mode-2 PM-ladder families, reached by a PM byte and not by this one.
        # `st.resolution` is then the 20-byte header's hardcoded 240x320
        # (FormCZTV.cs:4205 writes `240, 0, 64, 1`), a WIRE CONSTANT rather
        # than a panel size, and `themeDirection` never ran a mount test.
        # Neither is comparable; what IS true is that the fallback is that
        # constant, so pin it rather than skipping.
        assert state.resolution == (240, 320), (
            f"mode-3 fbl={fbl} resolves no panel, so the C# should fall back "
            f"to the header constant 240x320, not {state.resolution}"
        )
        return

    # The flag NAMES the panel's sides (``is176x320`` short-first,
    # ``is640x172`` long-first); what the C# composes at 0 degrees -- GIFSize
    # at directionB 0, FormCZTV.cs:3100-3545 -- is landscape for every one,
    # and that canvas is what ``profile.resolution`` stores.
    at_0 = (max(state.resolution), min(state.resolution))
    assert at_0 == profile.resolution, (
        f"mode-3 fbl={fbl} sub={sub}: the C# composes {at_0} at 0 degrees "
        f"(flag {state.resolution}), we resolve {profile.resolution}"
    )
    theirs = state.themeDirection == 90
    ours = is_portrait_mounted(profile.resolution, sub)
    assert theirs == ours, (
        f"mode-3 fbl={fbl} sub={sub} ({profile.resolution}): the C# mounts "
        f"portrait={theirs}, we say {ours}"
    )
