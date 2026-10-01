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
    FitRect,
    OrientationPlan,
    catalog_spellings,
    content_is_portrait,
    fit_rect_for_mode,
    fit_source_to_panel,
    lock_region_to_panel,
    plan_orientation,
)
from trcc.core.models import FitMode, Theme
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
    # Portrait content → PORTRAIT canvas, composed UPRIGHT (post_rotate=0 at
    # every angle) — the same model as the widescreen panels below.  The WIRE
    # owns all rotation via wire_angle (= base − orientation): a base-90 RGB565
    # panel gets 0 @90 / 180 @270 (net-identical to the old post_rotate=180),
    # while a base-0 panel gets 270 @90 / 90 @270 (transposes the portrait canvas
    # to the device's landscape buffer — the #234 640×480 squeeze fix).  Keeping
    # a post_rotate here would double-rotate on top of the wire angle.
    ("s565@90P",   _SMALL_RGB565, 90,  True,  OrientationPlan((240, 320), True, 0)),
    ("s565@270P",  _SMALL_RGB565, 270, True,  OrientationPlan((240, 320), True, 0)),

    # Small rotate JPEG (Mjolnir) behaves identically — rotate, not widescreen.
    ("sjpg@90L",   _SMALL_JPEG, 90,  False, OrientationPlan((320, 240), False, 90)),
    ("sjpg@90P",   _SMALL_JPEG, 90,  True,  OrientationPlan((240, 320), True, 0)),
    ("sjpg@270P",  _SMALL_JPEG, 270, True,  OrientationPlan((240, 320), True, 0)),

    # Widescreen JPEG (#169/#203) — always composes portrait at 90/270 (rides the
    # widescreen rotate_panel branch), regardless of content flag, with
    # post_rotate=0.  The WIRE rotation is owned entirely by resolve_encode_angle
    # (via wire_angle), which the C# ImageToJpg applies to the whole composite at
    # every angle — a post_rotate here would double-rotate on top of it (#169).
    # 0/180 = oriented landscape.
    ("wide@0",     _WIDE_JPEG, 0,   False, OrientationPlan((854, 480), False, 0)),
    ("wide@180",   _WIDE_JPEG, 180, False, OrientationPlan((854, 480), False, 0)),
    ("wide@90L",   _WIDE_JPEG, 90,  False, OrientationPlan((480, 854), True, 0)),
    ("wide@90P",   _WIDE_JPEG, 90,  True,  OrientationPlan((480, 854), True, 0)),
    ("wide@270L",  _WIDE_JPEG, 270, False, OrientationPlan((480, 854), True, 0)),
    ("wide@270P",  _WIDE_JPEG, 270, True,  OrientationPlan((480, 854), True, 0)),
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
    # The two canvases by the CATALOG rule -- (long, short) and (short, long).
    # Spelling them (w, h) / (h, w) reads the same for every panel stored
    # (long, short) and inverts for 176x320, the one stored the other way.
    landscape_canvas, portrait_canvas = catalog_spellings((w, h))
    assert plan.canvas in {(w, h), (h, w)}
    assert plan.canvas[0] * plan.canvas[1] == w * h

    # post_rotate is 0, the raw orientation (landscape-fallback spin), or 180
    # (non-widescreen portrait content at 270 — the dimension-preserving flip).
    # Widescreen panels take post_rotate=0 at every angle: their whole-composite
    # rotation is owned by resolve_encode_angle at wire time (#169), never here.
    assert plan.post_rotate in {0, orientation, 180}

    # Any non-zero spin is a rotate panel at 90/270.
    if plan.post_rotate:
        assert profile.rotate and w != h
        assert orientation in (90, 270)
        if plan.post_rotate == 180:
            # The 270 flip on non-widescreen portrait-composed content (widescreen
            # never reaches here — it composes portrait with post_rotate=0).
            assert orientation == 270
            assert not profile.widescreen
            assert plan.canvas == portrait_canvas
            assert plan.is_portrait_content is True
        else:
            # Landscape-only fallback: composed on the landscape canvas, spun
            # whole.  Non-widescreen only — widescreen always composes portrait.
            assert plan.post_rotate == orientation
            assert not content_portrait
            assert not profile.widescreen
            assert plan.canvas == landscape_canvas
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


# =========================================================================
# lock_region_to_panel — TASK 4: the fit constraint, made universal
# =========================================================================


def test_a_dragged_region_takes_the_panel_shape() -> None:
    """854x480 is 0.562 high per wide, so 200 across is 112 down.

    Anchored to the number the gui panel's own comment records: on an 854x480
    panel a width of 201 gives 113, and 200 gives 112.  The ratio is
    height/width and height follows WIDTH — the width the user dragged is the
    intent, so the gesture's horizontal extent is what survives.
    """
    assert lock_region_to_panel((854, 480), 10, 20, 200, 999) == (10, 20, 200, 112)


def test_a_square_panel_locks_too() -> None:
    """The old hardcoded table skipped squares behind a ``ratio != 1.0`` guard,
    so a 320x320 device never locked at all."""
    assert lock_region_to_panel((320, 320), 0, 0, 200, 999) == (0, 0, 200, 200)


def test_the_panel_the_old_table_forgot() -> None:
    """640x172 was missing from the tabulated ratios entirely and fell back to
    0.75 — 2.8x wrong.  Derived, it is 0.269."""
    assert lock_region_to_panel((640, 172), 0, 0, 200, 999) == (0, 0, 200, 54)


def test_an_unknown_panel_leaves_the_region_alone() -> None:
    """``None`` means "nobody has told us which panel yet".

    Constraining to a panel we have not met would silently shrink the user's
    region to a guess.
    """
    assert lock_region_to_panel(None, 10, 20, 200, 999) == (10, 20, 200, 999)


@pytest.mark.parametrize("bad", [(0, 480), (854, 0), (-1, -1)])
def test_a_degenerate_panel_leaves_the_region_alone(bad) -> None:
    """A zero or negative dimension must not divide by zero or invert."""
    assert lock_region_to_panel(bad, 5, 6, 100, 200) == (5, 6, 100, 200)


def test_every_panel_in_the_catalog_gives_a_usable_height() -> None:
    """Parametrized over the REAL catalog, not invented sizes.

    ``FBL_PROFILES`` is the single source of truth for panel geometry, so a
    panel added there cannot silently produce a zero-height region.
    """
    from trcc.core.protocol import FBL_PROFILES

    seen = 0
    for profile in FBL_PROFILES.values():
        res = getattr(profile, "resolution", None)
        if not res or res[0] <= 0 or res[1] <= 0:
            continue
        seen += 1
        _, _, w, h = lock_region_to_panel(res, 0, 0, 200, 999)
        assert w == 200, "the dragged width is the intent and must survive"
        assert h >= 1, f"{res} produced a zero-height region"
    assert seen > 5, f"only {seen} panels checked — is FBL_PROFILES wired?"


@pytest.mark.parametrize(("panel", "height", "width"), [
    ((854, 480), 112, 199),    # 112 * 854/480 = 199.27
    ((640, 172), 54, 201),     # 54 * 640/172 = 200.93
    ((320, 320), 200, 200),
])
def test_a_typed_height_leads_and_the_width_follows(
    panel: tuple[int, int], height: int, width: int,
) -> None:
    """``keep="height"``: the edge the user typed survives, the other locks.

    The C#'s ``textBoxH_TextChanged`` is this direction
    (``UCTouPingXianShi.cs:378``).  The gui did it with inline arithmetic and
    no floor, a second copy of the rule beside the helper.
    """
    assert lock_region_to_panel(panel, 3, 4, 999, height, keep="height") == (
        3, 4, width, height)


def test_a_short_height_on_a_tall_canvas_never_collapses_to_zero() -> None:
    """A portrait 172x640 canvas is 0.269 wide per high: 1px high rounds to 0."""
    for height in (1, 2, 3):
        _, _, w, h = lock_region_to_panel((172, 640), 0, 0, 999, height,
                                          keep="height")
        assert h == height
        assert w >= 1, f"a {height}px height collapsed to {w}px wide"


def test_a_zero_leading_edge_leaves_the_region_alone() -> None:
    """The edge that leads is the one checked: a 0 height cannot lock a width."""
    assert lock_region_to_panel((854, 480), 1, 2, 300, 0, keep="height") == (
        1, 2, 300, 0)


def test_a_narrow_drag_on_a_wide_panel_never_collapses_to_zero() -> None:
    """A 640x172 panel is 0.269 high per wide, so a 1px drag rounds to 0.

    A zero-height region is not a small capture, it is a capture of nothing —
    and it reaches the wire as a degenerate rectangle.  The ``max(1, ...)``
    floor exists for this, and the catalog sweep at width 200 never reaches
    it, so nothing tested it until a mutation removed the floor and every
    test stayed green.
    """
    for width in (1, 2, 3):
        _, _, w, h = lock_region_to_panel((640, 172), 0, 0, width, 999)
        assert w == width
        assert h >= 1, f"a {width}px drag collapsed to {h}px high"


# =========================================================================
# fit_source_to_panel — the 51-branch cascade, derived
# =========================================================================

#: Every resolution UCVideoCut.cs branches on, plus the two our catalog has
#: that it leaves to the 0.75 default arm (320x240, 640x480).
_CASCADE_PANELS = [
    (176, 320), (240, 240), (320, 240), (320, 320), (360, 360), (480, 480),
    (640, 172), (640, 480), (800, 480), (854, 480), (960, 320), (960, 540),
    (1280, 480), (1600, 720), (1920, 440), (1920, 462),
]

#: The constant each branch actually spells, read off the decompile.  The
#: claim under test is that every one of them IS the panel's aspect ratio —
#: if that is false the derivation is wrong for that panel and the table has
#: to come back.
_CS_THRESHOLDS = {
    (176, 320): 0.55, (240, 240): 1.0, (320, 240): 0.75, (320, 320): 1.0,
    (360, 360): 1.0, (480, 480): 1.0, (640, 172): 43 / 160, (640, 480): 0.75,
    (800, 480): 0.6, (854, 480): 0.56206, (960, 320): 1 / 3,
    (960, 540): 0.5625, (1280, 480): 0.375, (1600, 720): 0.45,
    (1920, 440): 11 / 48, (1920, 462): 77 / 320,
}


@pytest.mark.parametrize("panel", _CASCADE_PANELS)
def test_the_cs_threshold_is_the_panels_own_aspect(panel) -> None:
    """The "raw magic doubles" are derivable, which is why there is no table.

    ``AUDIT_VIDEO`` records these as hand-tuned constants per resolution.  They
    are not: 0.56206 is 480/854, 77/320 is 462/1920, and the 0.75 default arm
    serves exactly the two panels whose aspect is 0.75.  If this fails for a
    panel, ``fit_source_to_panel`` must not be derived for it.
    """
    w, h = panel
    assert _CS_THRESHOLDS[panel] == pytest.approx(min(w, h) / max(w, h), abs=1e-4)


@pytest.mark.parametrize("panel", _CASCADE_PANELS)
@pytest.mark.parametrize("source", [(1920, 1080), (1080, 1920), (640, 640),
                                    (3840, 1080), (500, 2000)])
def test_the_fitted_rect_never_distorts_and_always_fits(source, panel) -> None:
    """Aspect preserved, inside the panel, centred on the free axis.

    These three together are what "letterbox" means, and they are asserted
    over every panel x a spread of source shapes rather than the one case a
    bug report happened to name.
    """
    rect = fit_source_to_panel(source, panel)
    pw, ph = panel

    # FITS INSIDE.  The auto path never crops -- only the forced-axis buttons
    # can, and we do not port those.  A first draft of this gate asserted the
    # opposite, having read buttonTPJCH_Click as if it were SetImage.
    assert rect.width <= pw and rect.height <= ph, "rect overflows the panel"
    assert rect.width > 0 and rect.height > 0

    # EXACTLY ONE axis is pinned to the panel -- the one that ran out.  A rect
    # smaller on both axes would fit and still be wrong (needlessly small).
    assert rect.width == pw or rect.height == ph, "not scaled up to touch"

    # Aspect preserved, to integer rounding.
    assert rect.width / rect.height == pytest.approx(source[0] / source[1],
                                                     rel=0.02)
    # Centred on whichever axis has slack.
    assert rect.x == (pw - rect.width) // 2
    assert rect.y == (ph - rect.height) // 2


def test_it_matches_the_numbers_the_cs_computes() -> None:
    """Hand-computed from ``UCVideoCut.cs`` is480x480 (line 2049).

        else { wVal = 480; hVal = bitAngleH * 480 / bitAngleW;
               yVal += (480 - hVal) / 2; }

    1920x1080 -> hVal = 1080*480/1920 = 270, yVal = (480-270)/2 = 105.
    """
    assert fit_source_to_panel((1920, 1080), (480, 480)) == FitRect(480, 270, 0, 105)
    assert fit_source_to_panel((1080, 1920), (480, 480)) == FitRect(270, 480, 105, 0)
    assert fit_source_to_panel((480, 480), (480, 480)) == FitRect(480, 480, 0, 0)

    # And a WIDE panel, where the arms are written the other way round --
    # is1920x462 landscape: ``hVal = 116; wVal = bitAngleW * 116 / bitAngleH;
    # xVal += (480 - wVal) / 2`` on its 480x116 display rect.
    # 1920x1080 -> wVal = 1920*116/1080 = 206, xVal = (480-206)/2 = 137.
    assert fit_source_to_panel((1920, 1080), (480, 116)) == FitRect(206, 116, 137, 0)
    assert fit_source_to_panel((3840, 1080), (480, 116)) == FitRect(412, 116, 34, 0)


def test_a_degenerate_source_fills_rather_than_divides_by_zero() -> None:
    """An unprobeable source must not crash the export."""
    assert fit_source_to_panel((0, 0), (480, 480)) == FitRect(480, 480, 0, 0)
    assert fit_source_to_panel((1920, 1080), (0, 0)) == FitRect(0, 0, 0, 0)


# =========================================================================
# fit_rect_for_mode — the trimmer's forced-axis arms (#291)
# =========================================================================


@pytest.mark.parametrize("panel", _CASCADE_PANELS)
@pytest.mark.parametrize("source", [(1920, 1080), (1080, 1920), (640, 640),
                                    (3840, 1080), (500, 2000)])
def test_no_mode_is_exactly_the_auto_path(source, panel) -> None:
    """``mode=None`` must BE ``fit_source_to_panel``, not merely resemble it.

    Every export that existed before the W/H buttons could be carried takes
    this arm, so if it drifts by a pixel it is a silent regression for
    everyone who never touches a fit button.
    """
    assert fit_rect_for_mode(source, panel) == fit_source_to_panel(source,
                                                                   panel)


@pytest.mark.parametrize("panel", _CASCADE_PANELS)
@pytest.mark.parametrize("source", [(1920, 1080), (1080, 1920), (640, 640),
                                    (3840, 1080), (500, 2000)])
def test_the_forced_arms_are_the_cs_formula(source, panel) -> None:
    """Hand-computed from ``UCVideoCut.cs`` rather than from our own code.

    ``buttonTPJCW_Click``  ::  wVal = 480;
                               hVal = bitAngleH * 480 / bitAngleW;
                               yVal += (480 - hVal) / 2;
    ``buttonTPJCH_Click``  ::  hVal = 480;
                               wVal = bitAngleW * 480 / bitAngleH;
                               xVal += (480 - wVal) / 2;

    The C# writes each once per resolution; the claim under test is that all
    of those copies are this one formula with the panel substituted.
    """
    sw, sh = source
    pw, ph = panel

    w = fit_rect_for_mode(source, panel, FitMode.WIDTH)
    assert w.width == pw, "the forced axis must be pinned to the panel"
    assert w.height == sh * pw // sw
    assert w.x == 0
    assert w.y == (ph - w.height) // 2, "the free axis is centred"

    h = fit_rect_for_mode(source, panel, FitMode.HEIGHT)
    assert h.height == ph
    assert h.width == sw * ph // sh
    assert h.y == 0
    assert h.x == (pw - h.width) // 2


@pytest.mark.parametrize("panel", _CASCADE_PANELS)
def test_a_forced_axis_may_overflow_and_the_auto_arm_may_not(panel) -> None:
    """The crop IS the feature — it is the whole difference between the arms.

    A gate that only asserted "fits inside" would pass on an implementation
    that quietly letterboxed both buttons, which is precisely the bug: two
    controls, one behaviour.  So this asserts the overflow happens, on a
    source whose aspect cannot fit the forced axis.
    """
    pw, ph = panel
    # A source far taller than the panel: forcing WIDTH must overflow height.
    tall = (pw, ph * 4)
    forced = fit_rect_for_mode(tall, panel, FitMode.WIDTH)
    assert forced.height > ph, "forcing width must overflow the short axis"
    assert forced.y < 0, "the overflow is centred, so the offset goes negative"
    # ...while the auto arm on the same source never does.
    auto = fit_rect_for_mode(tall, panel)
    assert auto.height <= ph and auto.width <= pw
    assert auto.x >= 0 and auto.y >= 0


def test_stretch_fills_both_axes() -> None:
    assert fit_rect_for_mode((1920, 1080), (480, 480),
                             FitMode.STRETCH) == FitRect(480, 480, 0, 0)


@pytest.mark.parametrize("mode", [FitMode.WIDTH, FitMode.HEIGHT])
def test_a_degenerate_source_fills_rather_than_dividing_by_zero(mode) -> None:
    """No source shape means no aspect to preserve — say so by filling.

    Matches the render path's guard exactly, which is what let ``_fit``
    delegate here without changing a pixel.
    """
    assert fit_rect_for_mode((0, 0), (480, 480), mode) == FitRect(480, 480,
                                                                  0, 0)


@pytest.mark.parametrize("mode", [FitMode.WIDTH, FitMode.HEIGHT,
                                  FitMode.STRETCH])
@pytest.mark.parametrize("panel", _CASCADE_PANELS)
@pytest.mark.parametrize("source", [(1920, 1080), (1080, 1920), (640, 640)])
def test_the_render_path_delegates_without_changing_a_pixel(
    source, panel, mode,
) -> None:
    """``services/display._fit`` must stay byte-identical to the shared one.

    The exporter and the render path each had their own copy of this
    arithmetic, so the same button could mean one thing on screen and
    another on the panel.  They are one implementation now; this is the
    gate that says the consolidation changed no behaviour.
    """
    from trcc.services.display import _fit

    rect = fit_rect_for_mode(source, panel, mode)
    assert _fit(mode, *source, *panel) == (rect.width, rect.height,
                                           rect.x, rect.y)


# ── The catalog spelling rule (long, short) ────────────────────────────────


def test_catalog_spellings_matches_every_csharp_token_pair() -> None:
    """``(long, short)`` / ``(short, long)`` IS the C#'s catalog rule.

    Read off ``SetThemeInfo_ThemeML``'s own token table rather than retyped
    here, so the pairs cannot drift from the transcription they came from.

    This is the rule ``save_folder_resolution`` and ``plan_orientation`` used
    to express as ``(w, h)`` / ``(h, w)``.  Those coincide only while every
    catalogued resolution is stored (long, short) — true of all 15 live ones,
    and NOT true of 176x320, the single family the C# stores (short, long).
    Written the old way that panel picks its LANDSCAPE catalog when it wants
    portrait.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dev"
                           / "decompiler"))
    from formcztv_init import (  # pyright: ignore[reportMissingImports]
        _THEME_TOKENS,
    )

    from trcc.core.geometry import catalog_spellings

    assert _THEME_TOKENS, "the transcription's token table is empty"
    for flag, land, port, _portrait_from, _line in _THEME_TOKENS:
        width, height = (int(n) for n in flag[2:].split("x"))
        landscape, portrait = catalog_spellings((width, height))
        assert f"{landscape[0]}{landscape[1]}" == land, (
            f"{flag}: landscape catalog is {land!r}, we spell "
            f"{landscape[0]}{landscape[1]}"
        )
        assert f"{portrait[0]}{portrait[1]}" == port, (
            f"{flag}: portrait catalog is {port!r}, we spell "
            f"{portrait[0]}{portrait[1]}"
        )


def test_the_spelling_rule_is_order_independent() -> None:
    """A panel stored (short, long) gets the same pair as one stored (long, short).

    The whole point of the reformulation: 176x320 and 320x176 are the same
    physical screen and must name the same two catalogs.
    """
    from trcc.core.geometry import catalog_spellings

    assert catalog_spellings((176, 320)) == catalog_spellings((320, 176))
    assert catalog_spellings((176, 320)) == ((320, 176), (176, 320))
