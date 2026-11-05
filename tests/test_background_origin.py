"""The background's origin travels with the background, not with the theme.

**Why a differential and not a byte count.**  The wire frame is a FIXED size
whether the background resolved or not, so ``loaded and sent (24987 bytes)``
prints happily next to a black screen -- the shape #245 took.  Every gate here
therefore compares two renders that must agree, or one render against the
colour it was seeded with.

What was wrong: ``_build_bg_mask`` asked whether the active THEME's directory
sat under ``user_content_dir()``, then applied that answer to a background that
may have come from somewhere else entirely -- a ``background_path`` override, a
reference theme's library asset (resolved user-root FIRST), or a playback whose
origin ``PlayVideo`` had already decided and discarded.  A user's own wallpaper
under a program theme took the canvas-sized native-or-black rule and the panel
went BLACK, while the identical file under a user theme rendered.

The fix is one predicate (``Paths.is_user_content``) asked of the path that
actually resolved, carried to the compositor on ``RenderContent``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.media.ffmpeg import FfmpegTool
from trcc.adapters.render.qt import QtRenderer
from trcc.adapters.theme.filesystem import FileContentStore
from trcc.core.models import (
    FitMode,
    Kind,
    PictureBackground,
    ProductInfo,
    RenderContent,
    Theme,
    Wire,
)
from trcc.services.background import BackgroundSlot
from trcc.services.display import DisplayService
from trcc.services.media import MediaService, Playback
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings

from .conftest import FakePaths

CANVAS = (320, 320)
#: Deliberately WIDER than the canvas -- that is the only regime where the two
#: fit rules disagree visibly (native-or-black drops it, fit_mode scales it).
WALLPAPER = (1920, 1080)
INK = (200, 40, 40)
BLACK = (0, 0, 0)


def _info() -> ProductInfo:
    return ProductInfo(
        vid=0x0402, pid=0x3922, vendor="ALi Corp", product="320x320 LCD",
        wire=Wire.SCSI, kind=Kind.LCD, device_type=1, fbl=100,
        native_resolution=CANVAS, orientations=(0, 90, 180, 270),
    )


@pytest.fixture
def renderer() -> QtRenderer:
    return QtRenderer()


@pytest.fixture
def paths(tmp_home: Path) -> FakePaths:
    return FakePaths(tmp_home)


@pytest.fixture
def display(renderer: QtRenderer, paths: FakePaths) -> DisplayService:
    return DisplayService(
        renderer=renderer, themes=FileContentStore(paths),
        overlay=OverlayService(renderer), settings=Settings(paths),
        media=MediaService(video_tool=FfmpegTool()), backgrounds=BackgroundSlot(), paths=paths,
    )


def _write_image(r: QtRenderer, target: Path,
                 size: tuple[int, int], rgb: tuple[int, int, int]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(r.encode_png(r.create_surface(*size, color=(*rgb, 255))))
    return target


def _theme(root: Path, name: str, config: dict | None = None) -> Theme:
    (root / name).mkdir(parents=True, exist_ok=True)
    return Theme(path=root / name, name=name, resolution=CANVAS,
                 config=config if config is not None else {"elements": []})


def _centre(r: QtRenderer, surface: object) -> tuple[int, int, int]:
    """The middle pixel — a letterboxed fit leaves the corners black."""
    return r.get_pixels_rgb(surface, 9, 9)[4][4]


# ── Paths.is_user_content — the one predicate ──────────────────────────────

def test_is_user_content_answers_for_each_tree(paths: FakePaths) -> None:
    assert paths.is_user_content(paths.user_data_dir() / "themes" / "t" / "00.png")
    assert paths.is_user_content(paths.user_content_dir() / "single-image" / "x.png")
    assert not paths.is_user_content(paths.data_dir() / "theme320320" / "t" / "00.png")


# ── The differential: one file, two themes, one result ─────────────────────

def test_user_background_renders_the_same_under_any_theme(
    display: DisplayService, renderer: QtRenderer, paths: FakePaths,
) -> None:
    """A user's own wallpaper must not depend on which theme is selected.

    Before the fix this asserted (200,40,40) == (0,0,0): the program-theme
    render dropped the background entirely.
    """
    wallpaper = _write_image(
        renderer, paths.user_data_dir() / "uploads" / "wall.png", WALLPAPER, INK)
    info = _info()
    display._settings.set_background(info.key, PictureBackground(str(wallpaper)))

    under_program = _centre(renderer, display._build_bg_mask(
        info, _theme(paths.data_dir() / "theme320320", "shipped"), CANVAS))
    display.invalidate_all()
    under_user = _centre(renderer, display._build_bg_mask(
        info, _theme(paths.user_data_dir() / "theme320320", "mine"), CANVAS))

    assert under_program == under_user == INK


def test_reference_theme_asset_in_the_user_root_is_fitted(
    display: DisplayService, renderer: QtRenderer, paths: FakePaths,
) -> None:
    """A theme's OWN background can live outside its directory.

    ``_resolve_asset_ref`` tries ``user_data_dir()`` before ``data_dir()``, so
    a theme shipped under the program root can reference a user asset.  The
    theme's directory answers for neither.
    """
    _write_image(renderer, paths.user_data_dir() / "web" / "320320" / "a1.png",
                 WALLPAPER, INK)
    theme = _theme(paths.data_dir() / "theme320320", "ref",
                   {"elements": [], "background": "web/320320/a1.png"})

    assert _centre(renderer, display._build_bg_mask(_info(), theme, CANVAS)) == INK


def test_playback_origin_survives_into_the_render(
    display: DisplayService, renderer: QtRenderer, paths: FakePaths,
) -> None:
    """``PlayVideo`` decided the origin when it picked a decode size.

    A video decoded at NATIVE is user content by construction, whatever theme
    is active — the render must not re-derive that from the theme directory.
    """
    info = _info()
    frame = renderer.encode_png(renderer.create_surface(*WALLPAPER,
                                                        color=(*INK, 255)))
    display._media._playbacks[info.key] = Playback(
        frames=[frame], fps=15, is_user_content=True)

    theme = _theme(paths.data_dir() / "theme320320", "shipped")
    assert _centre(renderer, display._build_bg_mask(info, theme, CANVAS)) == INK


# ── The category gate: do not over-fix ─────────────────────────────────────

def test_program_background_keeps_the_native_or_black_rule(
    display: DisplayService, renderer: QtRenderer, paths: FakePaths,
) -> None:
    """Program/cloud content wider than the canvas still goes black.

    The C# width test (``UCScreenImage.cs:824-834``) is not what was broken,
    and a fix that fitted everything would have deleted it silently.
    """
    override = _write_image(
        renderer, paths.data_dir() / "web" / "320320" / "wide.png",
        WALLPAPER, INK)
    info = _info()
    display._settings.set_background(info.key, PictureBackground(str(override)))

    theme = _theme(paths.data_dir() / "theme320320", "shipped")
    assert _centre(renderer, display._build_bg_mask(info, theme, CANVAS)) == BLACK


def test_fit_mode_still_selects_how_user_content_scales(
    display: DisplayService, renderer: QtRenderer, paths: FakePaths,
) -> None:
    """The origin decides WHETHER to fit; ``fit_mode`` still decides HOW.

    WIDTH letterboxes a 16:9 upload on a square panel (black top and bottom);
    STRETCH fills it.  Both must reach the user branch at all.
    """
    wallpaper = _write_image(
        renderer, paths.user_data_dir() / "uploads" / "w.png", WALLPAPER, INK)
    info = _info()
    display._settings.set_background(info.key, PictureBackground(str(wallpaper)))
    theme = _theme(paths.data_dir() / "theme320320", "shipped")

    display._settings.set_fit_mode(info.key, FitMode.WIDTH)
    letterboxed = renderer.get_pixels_rgb(
        display._build_bg_mask(info, theme, CANVAS), 9, 9)
    display.invalidate_all()
    display._settings.set_fit_mode(info.key, FitMode.STRETCH)
    stretched = renderer.get_pixels_rgb(
        display._build_bg_mask(info, theme, CANVAS), 9, 9)

    assert letterboxed[0][4] == BLACK and letterboxed[4][4] == INK
    assert stretched[0][4] == INK and stretched[4][4] == INK


# ── The carrier ────────────────────────────────────────────────────────────

def test_resolver_reports_the_origin_of_what_it_resolved(
    display: DisplayService, renderer: QtRenderer, paths: FakePaths,
) -> None:
    """``_resolve_background`` returns the pair, never a bare surface."""
    info = _info()
    shipped = _theme(paths.data_dir() / "theme320320", "shipped")
    _write_image(renderer, shipped.path / "00.png", CANVAS, INK)

    program = display._resolve_background(info, shipped, CANVAS)
    assert isinstance(program, RenderContent)
    assert program.background is not None and not program.background_is_user

    mine = _theme(paths.user_data_dir() / "theme320320", "mine")
    _write_image(renderer, mine.path / "00.png", CANVAS, INK)
    assert display._resolve_background(info, mine, CANVAS).background_is_user


def test_no_background_is_a_content_with_no_surface(
    display: DisplayService, paths: FakePaths,
) -> None:
    bare = _theme(paths.data_dir() / "theme320320", "bare")
    assert display._resolve_background(_info(), bare, CANVAS).background is None
