"""Tests for DisplayService geometry — was Orientation, now folded in.

Two test surfaces:
1. ``output_resolution(w, h, rotation)`` — standalone function in core.paths
2. DisplayService geometry properties — rotation → resolutions → dirs
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.core.paths import output_resolution
from trcc.legacy.services.display import DisplayService

# =========================================================================
# output_resolution (standalone function)
# =========================================================================


class TestOutputResolution:
    """output_resolution(w, h, rotation) — swaps dims for non-square at 90/270."""

    @pytest.mark.parametrize("rot,expected", [
        (0, (1280, 480)),
        (90, (480, 1280)),
        (180, (1280, 480)),
        (270, (480, 1280)),
    ])
    def test_non_square_1280x480(self, rot, expected):
        assert output_resolution(1280, 480, rot) == expected

    @pytest.mark.parametrize("rot,expected", [
        (0, (800, 480)),
        (90, (480, 800)),
        (180, (800, 480)),
        (270, (480, 800)),
    ])
    def test_non_square_800x480(self, rot, expected):
        assert output_resolution(800, 480, rot) == expected

    @pytest.mark.parametrize("rot,expected", [
        (0, (1600, 720)),
        (90, (720, 1600)),
        (180, (1600, 720)),
        (270, (720, 1600)),
    ])
    def test_non_square_1600x720(self, rot, expected):
        assert output_resolution(1600, 720, rot) == expected

    @pytest.mark.parametrize("rot", [0, 90, 180, 270])
    def test_square_320x320_never_swaps(self, rot):
        assert output_resolution(320, 320, rot) == (320, 320)

    @pytest.mark.parametrize("rot", [0, 90, 180, 270])
    def test_square_480x480_never_swaps(self, rot):
        assert output_resolution(480, 480, rot) == (480, 480)

    @pytest.mark.parametrize("rot", [0, 90, 180, 270])
    def test_square_240x240_never_swaps(self, rot):
        assert output_resolution(240, 240, rot) == (240, 240)

    def test_zero_resolution(self):
        assert output_resolution(0, 0, 90) == (0, 0)


# =========================================================================
# DisplayService geometry surface
# =========================================================================


def _make_disp(
    w: int,
    h: int,
    *,
    has_portrait: bool = False,
    data_root: Path | None = None,
    user_root: Path | None = None,
) -> DisplayService:
    """Build a DisplayService wired with noop sub-services for geometry tests."""
    overlay = MagicMock()
    overlay.width = w
    overlay.height = h
    disp = DisplayService(
        devices=MagicMock(),
        overlay=overlay,
        media=MagicMock(),
    )
    disp._native = (w, h)
    disp._data_root = data_root if data_root is not None else Path('/data')
    disp._user_root = user_root
    disp._has_portrait_themes = has_portrait
    return disp


class TestSquareGeometry:
    """Square device — dirs and resolutions never swap."""

    def test_square_canvas_never_swaps(self):
        disp = _make_disp(320, 320)
        disp.rotation = 90
        assert disp.canvas_size == (320, 320)

    def test_square_image_rotation_returns_actual(self):
        disp = _make_disp(320, 320)
        disp.rotation = 90
        # Square is_rotated()=False — image_rotation_for returns rotation as-is.
        assert disp.image_rotation_for(320, 320) == 90

    def test_square_is_rotated_false(self):
        disp = _make_disp(320, 320)
        disp.rotation = 90
        assert disp.is_rotated() is False


class TestNonSquareGeometry:
    """Non-square device — canvas always swaps for non-square at 90/270.

    Phase 8 collapse: canvas_size is the single name; output_resolution /
    canvas_resolution / effective_resolution all deleted.
    has_portrait_themes only affects which theme dir we pull from and
    whether image_rotation needs to fire pixel rotation.
    """

    # Without portrait themes — canvas swaps, theme stays landscape, pixel rotate
    def test_no_portrait_canvas_swaps(self):
        disp = _make_disp(1280, 480, has_portrait=False)
        disp.rotation = 90
        assert disp.canvas_size == (480, 1280)

    def test_no_portrait_image_rotation_is_actual(self):
        disp = _make_disp(1280, 480, has_portrait=False)
        disp.rotation = 90
        # Theme is landscape; pixel rotation fires onto rotated canvas.
        assert disp.image_rotation_for(0, 0) == 90

    def test_no_portrait_theme_dir_is_landscape(self):
        disp = _make_disp(1280, 480, has_portrait=False)
        disp.rotation = 90
        # No portrait theme dir exists — theme stays landscape.
        assert 'theme1280480' in str(disp.theme_dir.path)

    # With portrait themes — canvas swaps, theme uses portrait dir, no pixel rotate
    def test_portrait_canvas_swaps(self):
        disp = _make_disp(1280, 480, has_portrait=True)
        disp.rotation = 90
        assert disp.canvas_size == (480, 1280)

    def test_portrait_image_rotation_is_zero(self):
        disp = _make_disp(1280, 480, has_portrait=True)
        disp.rotation = 90
        # Theme already portrait — no further pixel rotation.
        assert disp.image_rotation_for(0, 0) == 0

    def test_portrait_theme_dir_is_portrait(self):
        disp = _make_disp(1280, 480, has_portrait=True)
        disp.rotation = 90
        assert 'theme4801280' in str(disp.theme_dir.path)

    # Phase 8: canvas_resolution / output_resolution / effective_resolution
    # all collapsed into canvas_size — no equivalence test needed because
    # there's only one name.

    @patch('pathlib.Path.exists', return_value=True)
    def test_web_dir_swaps_on_rotation(self, _):
        disp = _make_disp(1280, 480, has_portrait=False)
        disp.rotation = 90
        assert '4801280' in str(disp.web_dir)

    @patch('pathlib.Path.exists', return_value=True)
    def test_masks_dir_swaps_on_rotation(self, _):
        disp = _make_disp(1280, 480, has_portrait=False)
        disp.rotation = 90
        assert 'zt4801280' in str(disp.masks_dir)

    def test_zero_rotation_uses_landscape(self):
        disp = _make_disp(1280, 480, has_portrait=True)
        disp.rotation = 0
        assert 'theme1280480' in str(disp.theme_dir.path)

    # User content dirs
    def test_user_theme_dir_from_user_root(self, tmp_path):
        disp = _make_disp(1280, 480, user_root=tmp_path)
        user_td = tmp_path / 'theme1280480'
        user_td.mkdir()
        assert disp.user_theme_dir == user_td

    def test_user_theme_dir_none_when_missing(self, tmp_path):
        disp = _make_disp(1280, 480, user_root=tmp_path)
        assert disp.user_theme_dir is None

    def test_user_masks_dir_from_user_root(self, tmp_path):
        disp = _make_disp(1280, 480, user_root=tmp_path)
        user_md = tmp_path / 'web' / 'zt1280480'
        user_md.mkdir(parents=True)
        assert disp.user_masks_dir == user_md
