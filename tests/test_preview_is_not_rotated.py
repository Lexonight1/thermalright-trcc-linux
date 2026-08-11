"""The preview is returned as composed — the display angle never turns it.

The C# leaves no room for interpretation here.  Every ``RotateImg``/
``RotateImgHei``/``RotateImgBu`` call site in the decompile sits inside a wire
encoder (``ImageToJpg``, ``ImageTo565``, ``GifToJPG``, ``GifTo565``); the
compose/preview control contains no rotation at all, and ``RotateFlip`` does not
appear anywhere in the application.  ``SetMyUCScreenImage(angle)``
(``UCScreenImage.cs:109``) spends the angle purely on the preview control's
``Left/Top/Width/Height`` — 0 and 180 take the *same* branch — and
``GenerateImage(angle, …)`` (``:634``) spends it purely on the canvas SHAPE,
every branch being ``angle == 0 || angle == 180``.  ``SetUCState`` then hands
that one image both to the control that paints it and to the encoder that
rotates a copy for the glass.

We had diverged: the display angle rotated the preview too.  Three symptoms,
all from that one line:

* an owner whose panel is bolted in rotated (#224 Levita, #256) can turn the
  dial until the GLASS is right, but then the app showed them an upside-down
  picture — so the one control that fixes their hardware looked broken;
* overlay drag maps widget→LCD by scale alone with no angle term
  (``uc_preview._widget_to_lcd``), so on a rotated preview every drag moved the
  element the wrong way;
* ``SaveTheme`` snapshots this surface into ``Theme.png``, storing upside-down
  thumbnails for anyone working at 180°.

MUTATION CHECK — restore the divergence in ``build_frame``/
``build_preview_surface``::

    preview_surface = self._r.rotate(composite, 360 - s.orientation)

and ``test_the_display_angle_never_reaches_the_preview`` fails at 90/180/270
on every panel.  Do not weaken it to quiet a future refactor.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.core.geometry import plan_orientation
from trcc.core.models import Kind, ProductInfo, Wire
from trcc.core.protocol import FBL_PROFILES, get_profile
from trcc.services.display import DisplayService
from trcc.services.settings import Settings

from .test_display_rotation import (
    RecordingRenderer,
    _StubOverlay,
    _theme,
)

ANGLES = (0, 90, 180, 270)


def _rotations(renderer: RecordingRenderer) -> list[int]:
    return [args[1] for name, args in renderer.calls if name == "rotate"]


def _info(fbl: int, native: tuple[int, int]) -> ProductInfo:
    return ProductInfo(
        vid=0x87AD, pid=0x70DB,
        vendor="Thermalright", product=f"LCD {native[0]}x{native[1]}",
        wire=Wire.BULK, kind=Kind.LCD,
        device_type=2, fbl=fbl, native_resolution=native,
        orientations=ANGLES,
    )


def _display(renderer: RecordingRenderer, tmp_home: Path) -> DisplayService:
    from trcc.services.media import MediaService
    from trcc.services.theme import ThemeService

    from .conftest import FakePaths
    paths = FakePaths(tmp_home)
    return DisplayService(
        renderer=renderer, themes=ThemeService(),
        overlay=_StubOverlay(renderer), settings=Settings(paths),
        media=MediaService(), paths=paths,
    )


# ── the invariant, over every panel we ship ───────────────────────────


@pytest.mark.parametrize("fbl", sorted(FBL_PROFILES))
@pytest.mark.parametrize("orientation", ANGLES)
def test_the_preview_applies_the_orientation_plan_and_nothing_else(
    fbl: int, orientation: int, tmp_home: Path,
) -> None:
    """A preview rotates by exactly ``OrientationPlan.post_rotate`` — never by
    the display angle on its own.

    ``post_rotate`` is non-zero only for the landscape-only-theme-at-90/270
    fallback, where the whole composite is deliberately spun into the portrait
    buffer as one unit (the C# would draw solid black there and we do better on
    purpose).  The expectation is read from ``plan_orientation`` — the single
    pure source the renderer itself consults — rather than restated here, so a
    future change to the rule moves both together instead of leaving a harness
    quietly asserting a rule the app no longer follows.
    """
    profile = FBL_PROFILES[fbl]
    renderer = RecordingRenderer()
    display = _display(renderer, tmp_home)
    info = _info(fbl, profile.resolution)
    theme = _theme()

    display._settings.set_orientation(info.key, orientation)
    renderer.calls.clear()
    display.build_preview_surface(
        info=info, theme=theme, sensors={}, profile=profile,
    )

    plan = plan_orientation(
        profile, orientation,
        display._content_is_portrait(theme, profile,
                                     display._settings.for_device(info.key)),
    )
    expected = [plan.post_rotate] if plan.post_rotate else []
    assert _rotations(renderer) == expected, (
        f"fbl={fbl} {profile.resolution} at {orientation}°: preview rotated "
        f"{_rotations(renderer)}, plan allows {expected}"
    )


# ── the reported case, stated without consulting the planner ──────────


@pytest.mark.parametrize("fbl,native", [
    (114, (1600, 720)),     # Levita / Wonder Vision — #224
    (224, (854, 480)),      # widescreen, the other encode_base
    (72, (480, 480)),       # square
    (58, (320, 240)),       # small rotate panel
])
@pytest.mark.parametrize("orientation", (90, 180, 270))
def test_the_display_angle_never_reaches_the_preview(
    fbl: int, native: tuple[int, int], orientation: int, tmp_home: Path,
) -> None:
    """No preview rotation is ever equal to the display angle's own turn.

    Hardcoded on purpose — this is the mutation-sensitive half, and it must not
    be able to pass by agreeing with a planner that has itself regressed.
    ``360 − orientation`` is the exact value the removed line applied.
    """
    profile = get_profile(fbl)
    renderer = RecordingRenderer()
    display = _display(renderer, tmp_home)
    info = _info(fbl, native)

    display._settings.set_orientation(info.key, orientation)
    renderer.calls.clear()
    display.build_preview_surface(
        info=info, theme=_theme(), sensors={}, profile=profile,
    )

    assert (360 - orientation) % 360 not in _rotations(renderer), (
        f"fbl={fbl} at {orientation}°: the display angle turned the preview "
        f"({_rotations(renderer)}) — #224/#256 regression"
    )


def test_the_wire_still_rotates_in_the_same_run(tmp_home: Path) -> None:
    """Guards the guard: the tests above must not be satisfiable by breaking
    the wire instead.  FBL 114 is Levita/Wonder Vision — ``encode_base=180``,
    so at orientation 0 the frame ships turned 180° even though the preview
    does not move at all (``FormCZTV.cs:2676``, and WillVinzant's own log:
    ``base=180 invert=True orient=0 → 180°``)."""
    profile = get_profile(114, 64)
    renderer = RecordingRenderer()
    display = _display(renderer, tmp_home)
    info = _info(114, (1600, 720))

    display.build_frame(info=info, theme=_theme(), sensors={}, profile=profile)
    wire = _rotations(renderer)

    renderer.calls.clear()
    display.build_preview_surface(
        info=info, theme=_theme(), sensors={}, profile=profile,
    )

    assert 180 in wire, f"wire lost its 180° rotation: {wire}"
    assert _rotations(renderer) == [], "preview must stay composed-upright"


def test_a_saved_thumbnail_is_upright_at_every_angle(tmp_home: Path) -> None:
    """``SaveTheme`` bakes ``Theme.png`` from this surface
    (``core/commands/theme.py``), so a theme saved while working at 180° must
    not be stored upside down."""
    profile = get_profile(72)       # square 480 — no post_rotate at any angle
    renderer = RecordingRenderer()
    display = _display(renderer, tmp_home)
    info = _info(72, (480, 480))

    for orientation in ANGLES:
        display._settings.set_orientation(info.key, orientation)
        renderer.calls.clear()
        display.build_preview_surface(
            info=info, theme=_theme(), sensors={}, profile=profile,
        )
        assert _rotations(renderer) == [], (
            f"square panel at {orientation}°: thumbnail source rotated "
            f"{_rotations(renderer)}"
        )
