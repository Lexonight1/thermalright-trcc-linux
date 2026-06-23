"""Pure tests for theme-directory resolution (no Qt).

Pins the catalog-dims selection + the #136 portrait-fallback rule that used to
be inline in ``LCDHandler._update_theme_directories``.  Driven against the
conftest ``FakePaths`` over a tmp dir — zero QApplication.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import FakePaths
from trcc.ui.presentation.theme_directories import (
    ThemeDirectories,
    resolve_theme_directories,
)

_CANVAS = (854, 480)        # pre-rotation landscape
_PORTRAIT = (480, 854)      # post-rotation portrait (lcd_size when rotated)


def _resolve(root: Path, *, lcd: tuple[int, int], rotated: bool) -> ThemeDirectories:
    return resolve_theme_directories(
        FakePaths(root), canvas_size=_CANVAS, lcd_size=lcd, is_rotated=rotated,
    )


def test_landscape_uses_canvas_dims(tmp_path: Path) -> None:
    """Not rotated → every catalog + the local browser size to the canvas."""
    dirs = _resolve(tmp_path, lcd=_CANVAS, rotated=False)

    assert dirs.catalog_size == (854, 480)
    assert dirs.portrait_fallback is False
    assert dirs.theme_dir == tmp_path / "data" / "theme854480"
    assert dirs.user_theme_dir == tmp_path / "user" / "data" / "theme854480"
    assert dirs.web_dir == tmp_path / "data" / "web" / "854480"
    assert dirs.masks_dir == tmp_path / "data" / "web" / "zt854480"


def test_rotated_with_portrait_theme_dir_stays_portrait(tmp_path: Path) -> None:
    """Rotated + a portrait theme dir on disk → browser + catalogs all portrait."""
    (tmp_path / "data" / "theme480854").mkdir(parents=True)

    dirs = _resolve(tmp_path, lcd=_PORTRAIT, rotated=True)

    assert dirs.catalog_size == (480, 854)
    assert dirs.portrait_fallback is False
    assert dirs.theme_dir == tmp_path / "data" / "theme480854"
    assert dirs.user_theme_dir == tmp_path / "user" / "data" / "theme480854"
    assert dirs.web_dir == tmp_path / "data" / "web" / "480854"
    assert dirs.masks_dir == tmp_path / "data" / "web" / "zt480854"


def test_rotated_without_portrait_theme_dir_falls_back_local_only(tmp_path: Path) -> None:
    """The #136 rule: no portrait theme dir → the LOCAL browser falls back to
    the landscape dir, but the cloud/mask catalogs + preview STAY portrait."""
    # No data/theme480854 created → fallback path.
    dirs = _resolve(tmp_path, lcd=_PORTRAIT, rotated=True)

    assert dirs.catalog_size == (480, 854)        # catalogs still portrait
    assert dirs.portrait_fallback is True
    # Local browser dropped to landscape dims…
    assert dirs.theme_dir == tmp_path / "data" / "theme854480"
    assert dirs.user_theme_dir == tmp_path / "user" / "data" / "theme854480"
    # …while cloud + mask catalogs remain portrait.
    assert dirs.web_dir == tmp_path / "data" / "web" / "480854"
    assert dirs.masks_dir == tmp_path / "data" / "web" / "zt480854"
