"""Tests for services/theme_loader.py — theme loading orchestrator.

Covers:
- load_local_theme() — reference-based and copy-based paths
- _load_reference_theme() — overlay config, mask, background loading
- _load_copy_theme() — working dir copy, content resolution
- load_cloud_theme() — video-only cloud themes
- apply_mask() — mask overlay application
- _load_static_image() — image load + resize
- _parse_mask_position() — DC position parsing, fallback
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.services.theme_loader import ThemeLoader


def _make_loader(
    overlay: MagicMock | None = None,
    media: MagicMock | None = None,
    theme_svc: MagicMock | None = None,
) -> ThemeLoader:
    return ThemeLoader(
        overlay=overlay or MagicMock(),
        media=media or MagicMock(),
        theme_svc=theme_svc,
    )


def _make_theme(path: Path, name: str = 'TestTheme') -> MagicMock:
    theme = MagicMock()
    theme.path = path
    theme.name = name
    theme.animation_path = None
    return theme


# =========================================================================
# load_local_theme — dispatch
# =========================================================================


class TestLoadLocalTheme:
    def test_resets_overlay_state(self, tmp_path):
        overlay = MagicMock()
        media = MagicMock()
        loader = _make_loader(overlay=overlay, media=media)

        # Create theme with 00.png (copy-based, no config.json)
        theme_dir = tmp_path / 'theme1'
        theme_dir.mkdir()
        (theme_dir / '00.png').write_bytes(b'fake')

        theme = _make_theme(theme_dir)

        with patch.object(loader, '_load_copy_theme', return_value={
            'image': None, 'is_animated': False,
            'status': 'ok', 'mask_source_dir': None, 'theme_path': theme_dir,
        }):
            loader.load_local_theme(theme, (320, 320), tmp_path / 'work')

        overlay.set_background.assert_called_once_with(None)
        overlay.set_mask.assert_called_once_with(None)
        overlay.set_config.assert_called_once_with({})
        media.stop.assert_called_once()

    def test_dispatches_to_reference_when_json_exists(self, tmp_path):
        overlay = MagicMock()
        loader = _make_loader(overlay=overlay)

        theme_dir = tmp_path / 'theme'
        theme_dir.mkdir()
        (theme_dir / 'config.json').write_text('{}')

        theme = _make_theme(theme_dir)

        with patch.object(loader, '_load_reference_theme',
                          return_value={'image': None}) as mock_ref:
            loader.load_local_theme(theme, (320, 320), tmp_path / 'work')
            mock_ref.assert_called_once()

    def test_dispatches_to_copy_when_no_json(self, tmp_path):
        overlay = MagicMock()
        loader = _make_loader(overlay=overlay)

        theme_dir = tmp_path / 'theme'
        theme_dir.mkdir()
        (theme_dir / '00.png').write_bytes(b'fake')

        theme = _make_theme(theme_dir)

        with patch.object(loader, '_load_copy_theme',
                          return_value={'image': None}) as mock_copy:
            loader.load_local_theme(theme, (320, 320), tmp_path / 'work')
            mock_copy.assert_called_once()


# =========================================================================
# _load_reference_theme
# =========================================================================


class TestLoadReferenceTheme:
    def test_loads_overlay_from_dc(self, tmp_path):
        overlay = MagicMock()
        overlay.load_from_dc.return_value = {}
        loader = _make_loader(overlay=overlay)

        theme_dir = tmp_path / 'theme'
        theme_dir.mkdir()
        (theme_dir / 'config.json').write_text('{}')
        (theme_dir / '00.png').write_bytes(b'img')

        theme = _make_theme(theme_dir)

        with patch.object(loader, '_load_static_image', return_value='pil_img'):
            from trcc.legacy.core.models import ThemeDir
            td = ThemeDir(theme_dir)
            result = loader._load_reference_theme(
                theme, td, (320, 320), tmp_path / 'work')

        overlay.load_from_dc.assert_called_once()
        assert result['theme_path'] == theme_dir

    def test_enables_overlay_when_config_says_so(self, tmp_path):
        overlay = MagicMock()
        overlay.load_from_dc.return_value = {'overlay_enabled': True}
        loader = _make_loader(overlay=overlay)

        theme_dir = tmp_path / 'theme'
        theme_dir.mkdir()
        (theme_dir / 'config.json').write_text('{}')

        theme = _make_theme(theme_dir)

        from trcc.legacy.core.models import ThemeDir
        td = ThemeDir(theme_dir)
        loader._load_reference_theme(theme, td, (320, 320), tmp_path / 'work')
        assert overlay.enabled is True

    def test_loads_video_background(self, tmp_path):
        overlay = MagicMock()
        overlay.load_from_dc.return_value = {
            'background_path': str(tmp_path / 'bg.mp4'),
        }
        loader = _make_loader(overlay=overlay)

        theme_dir = tmp_path / 'theme'
        theme_dir.mkdir()
        (theme_dir / 'config.json').write_text('{}')
        (tmp_path / 'bg.mp4').write_bytes(b'video')

        theme = _make_theme(theme_dir)
        from trcc.legacy.core.models import ThemeDir
        td = ThemeDir(theme_dir)
        result = loader._load_reference_theme(
            theme, td, (320, 320), tmp_path / 'work')
        assert result['is_animated'] is True


# =========================================================================
# load_cloud_theme
# =========================================================================


class TestLoadCloudTheme:
    def test_cloud_theme_result(self, tmp_path):
        media = MagicMock()
        loader = _make_loader(media=media)

        theme = MagicMock()
        theme.name = 'CloudTheme'
        theme.animation_path = str(tmp_path / 'cloud.mp4')
        (tmp_path / 'cloud.mp4').write_bytes(b'video')

        working_dir = tmp_path / 'work'
        working_dir.mkdir()

        result = loader.load_cloud_theme(theme, working_dir)
        assert result['is_animated'] is True
        assert result['theme_path'] is None
        assert result['mask_source_dir'] is None
        media.stop.assert_called_once()

    def test_cloud_theme_copies_video(self, tmp_path):
        loader = _make_loader()
        video = tmp_path / 'cloud.mp4'
        video.write_bytes(b'video')

        theme = MagicMock()
        theme.name = 'Cloud'
        theme.animation_path = str(video)

        working_dir = tmp_path / 'work'
        working_dir.mkdir()

        loader.load_cloud_theme(theme, working_dir)
        assert (working_dir / 'cloud.mp4').exists()


# =========================================================================
# apply_mask
# =========================================================================


class TestApplyMask:
    def test_apply_mask_returns_source_dir(self, tmp_path):
        overlay = MagicMock()
        overlay.load_from_dc.return_value = {}
        loader = _make_loader(overlay=overlay)

        mask_dir = tmp_path / 'mask'
        mask_dir.mkdir()
        (mask_dir / '01.png').write_bytes(b'mask')

        working_dir = tmp_path / 'work'
        working_dir.mkdir()

        result = loader.apply_mask(mask_dir, working_dir, (320, 320))
        assert result == mask_dir
        assert overlay.enabled is True

    def test_apply_mask_nonexistent_returns_none(self, tmp_path):
        loader = _make_loader()
        result = loader.apply_mask(
            tmp_path / 'nope', tmp_path / 'work', (320, 320))
        assert result is None

    def test_apply_mask_none_returns_none(self, tmp_path):
        loader = _make_loader()
        result = loader.apply_mask(None, tmp_path / 'work', (320, 320))
        assert result is None


# =========================================================================
# _load_static_image
# =========================================================================


class TestLoadStaticImage:
    @patch('trcc.legacy.services.theme_loader.ImageService')
    def test_success(self, mock_is):
        mock_is.open_and_resize.return_value = 'pil_img'
        loader = _make_loader()
        result = loader._load_static_image(Path('/fake/img.png'), (320, 320))
        assert result == 'pil_img'
        mock_is.open_and_resize.assert_called_once()

    @patch('trcc.legacy.services.theme_loader.ImageService')
    def test_failure_returns_none(self, mock_is):
        mock_is.open_and_resize.side_effect = RuntimeError("bad image")
        loader = _make_loader()
        result = loader._load_static_image(Path('/fake/img.png'), (320, 320))
        assert result is None


# =========================================================================
# _parse_mask_position
# =========================================================================


class TestParseMaskPosition:
    def test_no_theme_svc_fullsize_mask(self):
        loader = _make_loader(theme_svc=None)
        result = loader._parse_mask_position(None, 320, 320, (320, 320))
        assert result == (0, 0)

    @pytest.mark.skip(reason="Phase 9: _parse_mask_position now delegates to OverlayService.calculate_mask_position regardless of theme_svc; small-mask centering changed")
    def test_no_theme_svc_small_mask(self):
        pass

    @pytest.mark.skip(reason="Phase 9: _parse_mask_position no longer delegates to theme_svc — uses OverlayService.calculate_mask_position directly")
    def test_delegates_to_theme_svc(self):
        pass
