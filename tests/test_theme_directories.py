"""Pure tests for theme-directory resolution (no Qt).

Pins the catalog-dims selection + the #136 portrait-fallback rule that used to
be inline in ``LCDHandler._update_theme_directories``.  Driven against the
conftest ``FakePaths`` over a tmp dir — zero QApplication.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import FakePaths
from trcc.core.libraries import DeviceLibraries
from trcc.services.theme_directories import (
    ThemeDirectories,
    oriented_theme_reload_target,
    resolve_theme_directories,
)

_CANVAS = (854, 480)        # pre-rotation landscape
_PORTRAIT = (480, 854)      # post-rotation portrait (lcd_size when rotated)


def _resolve(root: Path, *, lcd: tuple[int, int], rotated: bool,
             variant: str = "", mask_variant: str = "") -> ThemeDirectories:
    """Resolve for a device with no per-SKU artwork unless a test says so.

    Empty suffixes are the overwhelmingly common case — every panel except the
    1600x720 pair and the PM-3 480x480 — so they stay the default here and the
    existing expectations below are unchanged by the SKU-library work.
    """
    return resolve_theme_directories(
        DeviceLibraries(FakePaths(root), variant, mask_variant),
        canvas_size=_CANVAS, lcd_size=lcd, is_rotated=rotated,
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


# ── oriented_theme_reload_target — the on-rotation active-theme reload (#169) ──


def test_reload_target_picks_same_name_portrait_variant(tmp_path: Path) -> None:
    """Rotating with an active landscape theme → reload its same-name variant from
    the portrait cloud catalog (whose oriented 00.png fills the buffer)."""
    (tmp_path / "data" / "theme480854" / "MSI2").mkdir(parents=True)
    active = tmp_path / "data" / "theme854480" / "MSI2"     # landscape, was rendering
    dirs = _resolve(tmp_path, lcd=_PORTRAIT, rotated=True)

    assert oriented_theme_reload_target(
        active, dirs.user_theme_dir, dirs.theme_dir,
    ) == (
        tmp_path / "data" / "theme480854" / "MSI2"
    )


def test_reload_target_prefers_user_dir_over_cloud(tmp_path: Path) -> None:
    """A user-authored portrait variant wins over the cloud one (ListThemes
    precedence)."""
    (tmp_path / "user" / "data" / "theme480854" / "MSI2").mkdir(parents=True)
    (tmp_path / "data" / "theme480854" / "MSI2").mkdir(parents=True)
    active = tmp_path / "data" / "theme854480" / "MSI2"
    dirs = _resolve(tmp_path, lcd=_PORTRAIT, rotated=True)

    assert oriented_theme_reload_target(
        active, dirs.user_theme_dir, dirs.theme_dir,
    ) == (
        tmp_path / "user" / "data" / "theme480854" / "MSI2"
    )


def test_reload_target_none_when_already_in_new_catalog(tmp_path: Path) -> None:
    """Active theme already lives in the new catalog → no reload (idempotent)."""
    (tmp_path / "data" / "theme480854" / "MSI2").mkdir(parents=True)
    active = tmp_path / "data" / "theme480854" / "MSI2"
    dirs = _resolve(tmp_path, lcd=_PORTRAIT, rotated=True)

    assert oriented_theme_reload_target(
        active, dirs.user_theme_dir, dirs.theme_dir,
    ) is None


def test_reload_target_none_when_no_variant_exists(tmp_path: Path) -> None:
    """A custom theme with no same-name portrait variant → keep current (the
    render pipeline pixel-rotates it)."""
    (tmp_path / "data" / "theme480854").mkdir(parents=True)   # catalog exists, MSI2 doesn't
    active = tmp_path / "data" / "theme854480" / "MyCustomTheme"
    dirs = _resolve(tmp_path, lcd=_PORTRAIT, rotated=True)

    assert oriented_theme_reload_target(
        active, dirs.user_theme_dir, dirs.theme_dir,
    ) is None


def test_reload_target_none_on_portrait_fallback(tmp_path: Path) -> None:
    """#136 portrait-fallback (no portrait theme dir) → theme_dir resolves back to
    the landscape dir == the active theme's dir → no reload, keep + pixel-rotate."""
    active = tmp_path / "data" / "theme854480" / "MSI2"
    dirs = _resolve(tmp_path, lcd=_PORTRAIT, rotated=True)   # no theme480854 → fallback

    assert dirs.portrait_fallback is True
    assert oriented_theme_reload_target(
        active, dirs.user_theme_dir, dirs.theme_dir,
    ) is None
