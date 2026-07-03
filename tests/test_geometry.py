"""Truth table for :func:`trcc.core.geometry.plan_orientation`.

Phase A of the folder-switch geometry restore (#136): pins the compose
canvas + portrait flag + whole-composite rotation for every panel class ×
angle × content-orientation.  This is a faithful extraction of
``DisplayService._compose_geometry`` — the table here is the contract Phase B
must preserve byte-for-byte when it routes the render path through this module.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.core.geometry import (
    OrientationPlan,
    content_is_portrait,
    plan_orientation,
)
from trcc.core.models import Theme
from trcc.core.protocol import FBL_PROFILES, DeviceProfile

ANGLES = (0, 90, 180, 270)

# Representative panels, one per structural class.
_SQUARE = DeviceProfile(360, 360, jpeg=True)                       # non-rotate square
_NON_ROTATE = DeviceProfile(320, 320, big_endian=True)            # square, rotate=False
_SMALL_RGB565 = DeviceProfile(320, 240, rotate=True)             # small rotate panel
_SMALL_JPEG = DeviceProfile(320, 240, jpeg=True, rotate=True)    # Mjolnir — still small
_WIDE_JPEG = DeviceProfile(854, 480, jpeg=True, rotate=True, widescreen=True)


# ── Explicit, hand-derived expectations ─────────────────────────────────────
# (label, profile, orientation, content_is_portrait, expected OrientationPlan)
CASES = [
    # Square / non-rotate: else-branch always — canvas = oriented(native),
    # portrait=False, post_rotate=0.  A square swaps to itself.
    ("square@0",   _SQUARE,     0,   False, OrientationPlan((360, 360), False, 0)),
    ("square@90",  _SQUARE,     90,  False, OrientationPlan((360, 360), False, 0)),
    ("square@90p", _SQUARE,     90,  True,  OrientationPlan((360, 360), False, 0)),
    ("nonrot@90",  _NON_ROTATE, 90,  False, OrientationPlan((320, 320), False, 0)),

    # Small rotate RGB565 — landscape content.
    ("s565@0",     _SMALL_RGB565, 0,   False, OrientationPlan((320, 240), False, 0)),
    ("s565@180",   _SMALL_RGB565, 180, False, OrientationPlan((320, 240), False, 0)),
    # 90/270 landscape-only → LANDSCAPE canvas + whole-composite spin (fallback).
    ("s565@90L",   _SMALL_RGB565, 90,  False, OrientationPlan((320, 240), False, 90)),
    ("s565@270L",  _SMALL_RGB565, 270, False, OrientationPlan((320, 240), False, 270)),
    # 90/270 portrait content → PORTRAIT canvas, NO spin (the fix's target).
    ("s565@90P",   _SMALL_RGB565, 90,  True,  OrientationPlan((240, 320), True, 0)),
    ("s565@270P",  _SMALL_RGB565, 270, True,  OrientationPlan((240, 320), True, 0)),

    # Small rotate JPEG (Mjolnir) behaves identically — rotate, not widescreen.
    ("sjpg@90L",   _SMALL_JPEG, 90,  False, OrientationPlan((320, 240), False, 90)),
    ("sjpg@90P",   _SMALL_JPEG, 90,  True,  OrientationPlan((240, 320), True, 0)),

    # Widescreen JPEG (#169/#203) — NEVER gets post_rotate; at 90/270 always
    # composes portrait (rides the second rotate_panel branch), regardless of
    # content flag.  0/180 = oriented landscape.
    ("wide@0",     _WIDE_JPEG, 0,   False, OrientationPlan((854, 480), False, 0)),
    ("wide@180",   _WIDE_JPEG, 180, False, OrientationPlan((854, 480), False, 0)),
    ("wide@90L",   _WIDE_JPEG, 90,  False, OrientationPlan((480, 854), True, 0)),
    ("wide@90P",   _WIDE_JPEG, 90,  True,  OrientationPlan((480, 854), True, 0)),
    ("wide@270L",  _WIDE_JPEG, 270, False, OrientationPlan((480, 854), True, 0)),
]


@pytest.mark.parametrize(
    "profile,orientation,content_portrait,expected",
    [(p, o, c, e) for _, p, o, c, e in CASES],
    ids=[label for label, *_ in CASES],
)
def test_plan_orientation_truth_table(
    profile: DeviceProfile, orientation: int,
    content_portrait: bool, expected: OrientationPlan,
) -> None:
    assert plan_orientation(profile, orientation, content_portrait) == expected


@pytest.mark.parametrize("fbl", sorted(FBL_PROFILES))
@pytest.mark.parametrize("orientation", ANGLES)
@pytest.mark.parametrize("content_portrait", [True, False])
def test_plan_invariants_over_every_profile(
    fbl: int, orientation: int, content_portrait: bool,
) -> None:
    """Structural invariants that must hold for every real FBL profile."""
    profile = FBL_PROFILES[fbl]
    plan = plan_orientation(profile, orientation, content_portrait)

    # Canvas is always a permutation of the native resolution (area preserved).
    w, h = profile.resolution
    assert plan.canvas in {(w, h), (h, w)}
    assert plan.canvas[0] * plan.canvas[1] == w * h

    # post_rotate is only ever the raw orientation (fallback spin) or 0.
    assert plan.post_rotate in {0, orientation}

    # Widescreen panels NEVER take the whole-composite spin — #169/#203 protection.
    if profile.widescreen:
        assert plan.post_rotate == 0

    # A non-zero post_rotate implies a non-widescreen rotate panel at 90/270
    # with landscape content composed on the landscape canvas.
    if plan.post_rotate:
        assert profile.rotate and w != h and not profile.widescreen
        assert orientation in (90, 270)
        assert not content_portrait
        assert plan.canvas == (w, h)
        assert plan.is_portrait_content is False


def test_landscape_angles_never_spin() -> None:
    """0/180 never produce a whole-composite spin on any panel."""
    for fbl, profile in FBL_PROFILES.items():
        for orientation in (0, 180):
            for content_portrait in (True, False):
                plan = plan_orientation(profile, orientation, content_portrait)
                assert plan.post_rotate == 0, f"fbl={fbl} @{orientation}"


# ── content_is_portrait — the shared parent-folder predicate ────────────────
# A non-square rotate panel with native (320, 240): its portrait catalogs are
# theme240320 / zt240320.  Three OR-signals + the square/non-rotate guard.

_ROTATE = DeviceProfile(320, 240, rotate=True)
_SQUARE = DeviceProfile(320, 320, big_endian=True)


def _theme(path: str, rotation: int = 0) -> Theme:
    return Theme(path=Path(path), name="t", resolution=(320, 240),
                 config={"rotation": rotation})


def test_content_portrait_active_mask_wins_over_landscape_theme() -> None:
    # Portrait mask (web/zt240320) over a landscape base theme → portrait.
    t = _theme("/x/theme320240/T", rotation=0)
    assert content_is_portrait(t, _ROTATE, "/x/web/zt240320/000d/01.png", True)


def test_content_portrait_mask_ignored_when_hidden() -> None:
    t = _theme("/x/theme320240/T", rotation=0)
    assert not content_is_portrait(t, _ROTATE, "/x/web/zt240320/000d/01.png", False)


def test_content_portrait_landscape_mask_is_not_portrait() -> None:
    t = _theme("/x/theme320240/T", rotation=0)
    assert not content_is_portrait(t, _ROTATE, "/x/web/zt320240/000d/01.png", True)


def test_content_portrait_theme_folder_signal_beats_lying_dc() -> None:
    # Shipped-bug case: portrait folder, landscape DC (rotation=0) → still portrait.
    t = _theme("/x/theme240320/T", rotation=0)
    assert content_is_portrait(t, _ROTATE, None, False)


def test_content_portrait_dc_rotation_signal_kept() -> None:
    t = _theme("/x/anywhere/T", rotation=90)
    assert content_is_portrait(t, _ROTATE, None, False)


def test_content_portrait_all_signals_false_is_landscape() -> None:
    t = _theme("/x/theme320240/T", rotation=0)
    assert not content_is_portrait(t, _ROTATE, None, False)


def test_content_portrait_square_never_portrait() -> None:
    # Every signal points portrait, but a square panel never composes portrait.
    t = _theme("/x/theme240320/T", rotation=90)
    assert not content_is_portrait(t, _SQUARE, "/x/web/zt320320/m/01.png", True)
