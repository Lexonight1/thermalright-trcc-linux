"""Orientation geometry — the single pure decision for compose canvas + rotation.

Restores the legacy ``DisplayService.set_rotation`` / ``has_portrait_themes``
model the cutover fragmented (#136): a display angle is two orthogonal bits —
**which orientation folder** the content came from (landscape ``theme{w}{h}`` vs
portrait ``theme{h}{w}``) and **whether it needs a 180° flip**.  Because a 180°
flip is dimension-preserving, portrait content never has to be pixel-rotated into
a landscape buffer (which is what clips); the 90° turn is a *folder switch*, not a
spin.  The only 90/270 pixel-spin left is the fallback for a rotate panel whose
portrait variant is absent on disk (a local theme saved landscape-only).

This module owns ONLY the decision — pure, Qt-free, adapter-free, so every UI
(GUI preview bezel, CLI, API) and the wire path key on the same answer.  Applying
the rotation and encoding stays in ``services/display.py``; the device-mount
encode rotation (``DeviceProfile.encode_baseline``) is a *separate* concern
applied only at wire encode, never here.

Phase A: additive + behavior-neutral.  This is a faithful extraction of
``DisplayService._compose_geometry``; nothing calls it yet.  ``tests/
test_geometry.py`` pins the truth table.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .logs import per_frame
from .models import FitMode, Theme, oriented_resolution
from .protocol import DeviceProfile

log = logging.getLogger(__name__)
#: ``plan_orientation`` answers once per composed frame.
frame_log = per_frame(__name__)


def content_is_portrait(
    theme: Theme, profile: DeviceProfile,
    mask_path: str | None, mask_visible: bool,
) -> bool:
    """True when the ACTUALLY-LOADED content is portrait-oriented.

    A SUPERSET of three signals — True whenever ANY says portrait, so it never
    contradicts the old DC-only read (no regression) yet catches what that one
    missed:

    1. **Active mask under ``web/zt{h}{w}``** — an explicitly-applied portrait
       mask makes the frame portrait even over a landscape base theme (the bug
       where a portrait mask got SPUN instead of switched).
    2. **Theme loaded from ``theme{h}{w}``** — disk truth.  Shipped portrait
       folders ship the landscape DC with ``rotation=0``, so signal 3 alone
       reports landscape for a genuinely-portrait folder; the path doesn't lie.
    3. **Theme DC ``rotation`` ∈ {90,270}** — the legacy signal.  Real cloud
       portrait themes carry it; kept so nothing that worked before breaks.

    The single source of the portrait decision — shared by ``DisplayService``
    (render) and ``SaveTheme`` (which folder to persist into), so a saved theme
    always reloads into the orientation it was composed in.  Only meaningful for
    a non-square rotate panel; squares / non-rotate never compose portrait.
    """
    w, h = profile.resolution
    if not (profile.rotate and w != h):
        return False
    portrait_mask = f"zt{h}{w}"
    if mask_visible and mask_path and portrait_mask in Path(mask_path).parts:
        log.debug("content_is_portrait: active mask under %s → portrait",
                  portrait_mask)
        return True
    if f"theme{h}{w}" in theme.path.parts:
        log.debug("content_is_portrait: theme %s under portrait dir → portrait",
                  theme.name)
        return True
    if theme.config.get("rotation", 0) in (90, 270):
        log.debug("content_is_portrait: theme %s DC rotation portrait", theme.name)
        return True
    return False


@dataclass(frozen=True, slots=True)
class OrientationPlan:
    """How to compose + rotate one frame for a given device angle.

    * ``canvas`` — the compose canvas size (portrait content transposes to
      ``(h, w)``; a landscape-only fallback stays ``(w, h)`` and rotates whole).
    * ``is_portrait_content`` — the loaded artwork is authored/loaded portrait
      (from a portrait catalog), so the orientation is already baked into the
      pixels and must NOT be re-rotated.
    * ``post_rotate`` — degrees to rotate the FINISHED composite as one unit.
      Non-zero only for the fallback: a non-widescreen rotate panel at 90/270
      whose portrait variant is absent, so a landscape composite is spun into
      the portrait buffer (legacy ``has_portrait_themes=False``).  0 for every
      other case, so all non-fallback panels stay byte-identical.
    """
    canvas: tuple[int, int]
    is_portrait_content: bool
    post_rotate: int


def catalog_spellings(
    resolution: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    """The (landscape, portrait) spellings of *resolution*.

    Landscape is ``(long, short)`` and portrait ``(short, long)`` — the rule
    the C# theme catalogs follow without exception, verified across all ten
    ``SetThemeInfo_ThemeML`` token pairs (``854480``/``480854`` … and
    ``320176``/``176320``).

    Expressed as long/short rather than as ``(w, h)``/``(h, w)`` because those
    two coincide only while every catalogued resolution is stored (long,
    short).  The C# spells one family short-first (``is176x320``), and our row
    for it did too until 2026-09-30; written the old way, that panel selected
    its landscape catalog when it wanted portrait.  Measured: 0 differences over 480 combinations of the
    live resolutions, 8 over the same sweep once 176x320 is included.
    """
    w, h = resolution
    short, long_ = min(w, h), max(w, h)
    frame_log.debug("catalog_spellings: %dx%d -> landscape=%s portrait=%s",
                    w, h, (long_, short), (short, long_))
    return (long_, short), (short, long_)


def plan_orientation(
    profile: DeviceProfile, orientation: int, content_is_portrait: bool,
) -> OrientationPlan:
    """Compose canvas + portrait flag + whole-composite rotation for an angle.

    Faithful port of ``DisplayService._compose_geometry``.  Three cases of the
    C# oriented-output model (``SetMyUCScreenImage``):

    * **Landscape-only content @ 90/270** on a non-widescreen rotate panel — the
      portrait variant is absent on disk, so compose on the native LANDSCAPE
      canvas (bg + mask + text aligned, nothing clipped) and rotate the WHOLE
      composite by ``orientation`` into the portrait buffer.  Legacy's
      ``has_portrait_themes=False`` branch — the ONLY non-zero ``post_rotate``.
    * **Any other rotate panel @ 90/270** — portrait content, or a widescreen
      panel regardless of content — compose UPRIGHT on the transposed portrait
      canvas (``post_rotate=0``) and let the WIRE own all rotation.
      ``wire_angle`` (= ``base − orientation``) then does the right thing per
      panel: a base-90 RGB565 panel gets 0 @90 / 180 @270 (net-identical to the
      old compose-time 180° flip), while a base-0 panel gets 270 @90 / 90 @270,
      transposing the portrait canvas onto the device's fixed LANDSCAPE buffer —
      the #234 640×480 squeeze fix and the #169 widescreen 1600×720 fix, one
      rule.  A ``post_rotate`` here would double-rotate on top of the wire angle.
    * **Everything else** — 0/180, squares, non-rotate — the user-orientation
      dimension swap, no whole-composite rotation.

    On-glass handedness (90 vs 270) rides ``wire_angle``/the C# oracle; a
    reporter photo confirms it (#234 Chiefbot, #169/#203 widescreen).
    """
    w, h = profile.resolution
    landscape, portrait = catalog_spellings((w, h))
    rotate_panel = profile.rotate and w != h and orientation in (90, 270)
    if rotate_panel and not content_is_portrait and not profile.widescreen:
        frame_log.debug("plan_orientation: landscape-only content on rotate "
                        "panel %dx%d @ %d° -> compose landscape %dx%d, "
                        "post_rotate=%d", w, h, orientation, *landscape,
                        orientation)
        return OrientationPlan(landscape, False, orientation)
    if rotate_panel:
        frame_log.debug("plan_orientation: rotate panel %dx%d @ %d° "
                        "(widescreen=%s) -> compose upright %dx%d, "
                        "post_rotate=0, the wire owns rotation",
                        w, h, orientation, profile.widescreen, *portrait)
        return OrientationPlan(portrait, True, 0)
    plan = OrientationPlan(oriented_resolution((w, h), orientation), False, 0)
    frame_log.debug("plan_orientation: %dx%d @ %d° rotate=%s -> canvas %dx%d, "
                    "post_rotate=0", w, h, orientation, profile.rotate,
                    *plan.canvas)
    return plan


def save_folder_resolution(
    profile: DeviceProfile, orientation: int, content_is_portrait: bool,
) -> tuple[int, int]:
    """Resolution key for a saved theme's FOLDER (``theme{w}{h}`` vs ``{h}{w}``).

    This is the **asset-catalog** concern — portrait content saves into the
    portrait folder, landscape into the landscape folder — kept deliberately
    separate from the render **compose canvas** (which is purely
    :func:`oriented_resolution`, content-independent).  The two coincide today
    but diverge for landscape content at a portrait angle, so ``SaveTheme`` owns
    this predicate rather than borrowing the renderer's canvas.  Mirrors the
    v9.8.0 folder-switch (Phase D): a portrait selection → ``theme{h}{w}``.
    """
    log.debug("save_folder_resolution: profile=%s orientation=%s", profile, orientation)
    w, h = profile.resolution
    landscape, portrait = catalog_spellings((w, h))
    rotate_panel = profile.rotate and w != h and orientation in (90, 270)
    if rotate_panel and (content_is_portrait or profile.widescreen):
        return portrait
    if rotate_panel:
        return landscape                    # landscape content at a portrait angle
    return oriented_resolution((w, h), orientation)


def oriented_canvas(profile: DeviceProfile, orientation: int) -> tuple[int, int]:
    """The canvas a supplied picture fills at *orientation* -- the C#'s GIFSize.

    A solid colour, a single image and a screen cast compose here; for a cast
    it is also the aspect a region locks to.  The C# sizes it to the
    orientation, portrait at 90/270 on every non-square panel
    (FormCZTV.cs:3100-3557), and puts the picture in the background slot, so
    it takes the route an authored portrait theme takes -- upright, the wire
    owning the rotation.  It used to take the landscape-only route, which on
    the base-90 panels (50 51 52 53 58 64) sent a cast at 90/270 sideways.
    """
    canvas = plan_orientation(profile, orientation, True).canvas
    frame_log.debug("oriented_canvas: %s @ %d -> %s", profile.resolution,
                    orientation, canvas)
    return canvas


def screencast_axes(
    rect: tuple[int, int, int, int], native: tuple[int, int], orientation: int,
) -> tuple[int, int, int, int]:
    """Between a theme's stored screencast region and the box on screen.

    The C# stores JpX, JpY, JpW, JpH with W the panel's SHORT side, and captures
    ``(JpH, JpW)`` -- long side across -- on every square panel and on every
    other panel at 0 / 180 degrees; only a non-square panel turned to 90 / 270
    captures ``(JpW, JpH)`` (FormCZTV.cs:3100-3545).  It keys on the panel being
    square and on the rotation, and the canvas (``oriented_canvas``) follows
    the same rule, so the box and the canvas it fills always share a shape.

    Swapping is its own inverse, so one call converts either way.
    """
    x, y, a, b = rect
    swap = native[0] == native[1] or orientation in (0, 180)
    out = (x, y, b, a) if swap else (x, y, a, b)
    log.debug("screencast_axes: %s on %dx%d @ %d° -> %s",
              rect, *native, orientation, out)
    return out


def lock_region_to_panel(
    resolution: tuple[int, int] | None,
    x: int, y: int, width: int, height: int,
) -> tuple[int, int, int, int]:
    """Fit a screen region to the panel's aspect, keeping its top-left.

    **The constraint is part of the FUNCTION, not the mechanism.**  The C#
    oracle's ``FormScreenshot`` is a borderless *viewfinder*: ``MouseMove``
    moves the whole form and ``MouseUp`` reports only ``Left``/``Top``, its
    width and height coming from the panel.  The Windows user picks POSITION
    and never size, so a region that does not match the panel's shape is not a
    thing that release can express.  Capturing a free-form rectangle and
    letting the render pipeline squash it loses the guarantee the original
    gave: what you framed is what appears.

    Ratio is ``height / width``.  Height follows width -- the width the user
    dragged is the intent, and honouring it keeps the gesture's horizontal
    extent, which is what a viewfinder's edges are read against.

    ``resolution`` of ``None`` means "nobody has told us which panel yet" --
    the same meaning it carries on ``DeviceStateResult.resolution`` -- and the
    region is returned UNCHANGED.  Constraining to a panel we have not met
    would shrink a user's region to a guess; a UI that answered with a default
    ratio would be wrong by 2.8x on a 640x172.

    Lives here rather than in a panel because all four faces want it: the gui
    locked it, qtgui dragged free-form, and cli/api could not express it at
    all.  One derivation, four callers.
    """
    if resolution is None:
        log.debug("lock_region_to_panel: no panel known — region unchanged")
        return x, y, width, height
    pw, ph = resolution
    if pw <= 0 or ph <= 0 or width <= 0:
        log.debug("lock_region_to_panel: degenerate panel %sx%s or width %s — "
                  "region unchanged", pw, ph, width)
        return x, y, width, height
    locked = max(1, round(width * (ph / pw)))
    log.debug("lock_region_to_panel: %sx%s on a %sx%s panel -> %sx%s",
              width, height, pw, ph, width, locked)
    return x, y, width, locked


@dataclass(frozen=True, slots=True)
class FitRect:
    """Where a source frame lands on a panel-sized canvas.

    ``width``/``height`` is the rect the video is scaled to; ``x``/``y`` is
    where that rect is painted.  The C# calls these ``wVal/hVal`` and
    ``xVal/yVal``.
    """

    width: int
    height: int
    x: int
    y: int


def fit_source_to_panel(
    source: tuple[int, int], panel: tuple[int, int],
) -> FitRect:
    """Fit a video's frame inside a panel, preserving its aspect.

    **The oracle never scales a video to the panel rectangle.**  ffmpeg is
    given a *fitted rect* derived from the SOURCE's aspect, and the result is
    composited onto a panel-sized canvas at an offset (``UCVideoCut.cs``
    ``ZhuanMaPanDuan``: ``new Bitmap(wValSub, hValSub)`` then
    ``DrawImage(frame, xVal, yVal)``).  Handing ffmpeg the panel size instead
    is what squashed a 1920x1080 clip into 480x480.

    **The 51-branch cascade is one formula.**  ``UCVideoCut`` spells this out
    three times — ``buttonTPJCH_Click`` (force height), ``buttonTPJCW_Click``
    (force width) and ``SetImage`` (auto) — once per resolution, and the audit
    called the per-panel constants "raw magic doubles".  They are not magic:
    every one is the panel's own aspect ratio.  Measured over all 14 branches
    plus the 0.75 default, 16 for 16::

        1920x440 -> 11/48   = 0.229167 = 440/1920
        1920x462 -> 77/320  = 0.240625 = 462/1920
         640x172 -> 43/160  = 0.268750 = 172/640
         960x320 -> (x3)    = 0.333333 = 320/960
        1280x480 -> 0.375   =            480/1280
        1600x720 -> 0.45    =            720/1600
         176x320 -> 0.55    =            176/320
         854x480 -> 0.56206 ~            480/854
         960x540 -> 0.5625  =            540/960
         800x480 -> 0.6     =            480/800
        320x240 / 640x480 -> 0.75 (the default arm) = 240/320 = 480/640
        240x240 / 320x320 / 360x360 / 480x480 -> plain ``h > w`` = 1.0

    And every one of those comparisons is the same question written per panel:
    *is the source relatively wider or taller than the panel?*  So the whole
    cascade is plain **fit-inside** — scale by whichever axis runs out first,
    centre on the other.  It is derived here rather than tabulated: a panel
    added to the catalog needs no new row, and no table can drift from it.

    **The auto path never crops.**  ``buttonTPJCH_Click`` / ``buttonTPJCW_Click``
    FORCE an axis and can therefore overflow the canvas (a negative ``xVal``,
    which the canvas then clips) — but that is the user's explicit override,
    not what loading a clip does.  ``SetImage`` always fits inside.  Reading a
    forced-axis arm as if it were the default is an easy mistake: it says the
    oracle crops on wide panels, and it does not.

    ``source`` is the frame size AFTER any rotation — the C# swaps
    ``bitAngleW``/``bitAngleH`` at 90/270 before the cascade reads them
    (``buttonXuanzhuan_Click``), so a caller rotating the video must rotate
    first and pass the rotated dimensions.

    Degenerate input (a zero side) returns the panel rect unchanged, which is
    the pre-fit behaviour: better a stretched frame than a division by zero.
    """
    sw, sh = source
    pw, ph = panel
    log.debug("fit_source_to_panel: source=%sx%s panel=%sx%s", sw, sh, pw, ph)
    if sw <= 0 or sh <= 0 or pw <= 0 or ph <= 0:
        log.warning(
            "fit_source_to_panel: degenerate source %sx%s or panel %sx%s — "
            "filling the panel, which stretches a mismatched aspect",
            sw, sh, pw, ph,
        )
        return FitRect(pw, ph, 0, 0)
    # FIT INSIDE — scale by whichever axis runs out first.  Every per-panel
    # threshold in the cascade is the algebraic form of this same comparison
    # (source aspect vs panel aspect), which is why the arms differ per panel
    # and the OUTCOME does not.
    if sw * ph <= sh * pw:          # source relatively taller -> height-led
        width, height = max(1, sw * ph // sh), ph
    else:                           # source relatively wider  -> width-led
        width, height = pw, max(1, sh * pw // sw)
    rect = FitRect(width, height, (pw - width) // 2, (ph - height) // 2)
    log.debug("fit_source_to_panel: %sx%s in %sx%s -> %s", sw, sh, pw, ph, rect)
    return rect


def fit_rect_for_mode(
    source: tuple[int, int], panel: tuple[int, int],
    mode: FitMode | None = None,
) -> FitRect:
    """Where a source frame lands, for the fit the user actually chose.

    ONE implementation for both consumers.  The render path (``services/
    display._fit``) and the ``Theme.zt`` exporter (``services/video_export``)
    each composed this themselves, which is why the trimmer's W/H buttons
    could letterbox on screen and do something else on the panel — and why the
    exporter ignored the choice entirely (#291).

    ``mode=None`` is the LOAD path: fit inside, never crop
    (:func:`fit_source_to_panel`).  ``WIDTH`` / ``HEIGHT`` are the trimmer's
    two buttons and they are the C# forced-axis arms ``buttonTPJCW_Click`` /
    ``buttonTPJCH_Click`` — pin the chosen axis to the panel, scale the other
    by the source's aspect, centre it.  When that free axis OVERFLOWS, the
    content is cropped; that is the whole point of an override, and the reason
    the auto path stays a separate function instead of becoming a fourth arm.

    ``UCVideoCut.cs`` writes each arm once per resolution::

        wVal = 480; hVal = bitAngleH * 480 / bitAngleW;
        yVal += (480 - hVal) / 2;                       // buttonTPJCW, 480x480

    Every one of those copies is this formula with the panel substituted, so
    a panel added to the catalog needs no new arm here.
    """
    sw, sh = source
    pw, ph = panel
    if mode is None:
        return fit_source_to_panel(source, panel)
    # Guard matches the render path's exactly, so delegating changed nothing:
    # without a source shape there is no aspect to preserve.
    if mode is FitMode.STRETCH or sw <= 0 or sh <= 0:
        log.debug("fit_rect_for_mode: %s -> fill %sx%s", mode, pw, ph)
        return FitRect(pw, ph, 0, 0)
    if mode is FitMode.WIDTH:
        height = max(1, sh * pw // sw)
        rect = FitRect(pw, height, 0, (ph - height) // 2)
    else:
        width = max(1, sw * ph // sh)
        rect = FitRect(width, ph, (pw - width) // 2, 0)
    log.debug("fit_rect_for_mode: %s %sx%s in %sx%s -> %s",
              mode, sw, sh, pw, ph, rect)
    return rect
