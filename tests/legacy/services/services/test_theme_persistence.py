"""Tests for theme save bug fixes (v8.0.1).

Verifies:
1. ThemePersistence.save() saves clean bg as 00.png, rendered preview as Theme.png
2. DisplayService.save_theme() passes _clean_background, not current_image
3. load_mask_standalone() wires _mask_source_dir so save writes mask to config.json
4. Cloud theme load clears stale _mask_source_dir
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from conftest import make_test_surface

from trcc.legacy.core.device.lcd import LCDDevice as Device
from trcc.legacy.services.display import DisplayService
from trcc.legacy.services.image import ImageService
from trcc.legacy.services.overlay import OverlayService
from trcc.legacy.services.theme_persistence import ThemePersistence

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def renderer() -> Any:
    return ImageService.renderer()


@pytest.fixture()
def lcd_size() -> tuple[int, int]:
    return (320, 320)


@pytest.fixture()
def blue_bg(renderer: Any) -> Any:
    """Clean background — blue."""
    return renderer.create_surface(320, 320, (0, 0, 255))


@pytest.fixture()
def green_bg(renderer: Any) -> Any:
    """Rendered overlay result — green."""
    return renderer.create_surface(320, 320, (0, 255, 0))


@pytest.fixture()
def mock_overlay(green_bg: Any) -> MagicMock:
    """OverlayService mock that returns green from render()."""
    overlay = MagicMock(spec=OverlayService)
    overlay.render.return_value = green_bg
    overlay.get_mask.return_value = (None, None)
    overlay.config = {'time': {'x': 10, 'y': 20, 'metric': 'time', 'enabled': True}}
    return overlay


@pytest.fixture()
def mask_dir(tmp_path: Path, renderer: Any) -> Path:
    """Theme mask directory with 01.png."""
    d = tmp_path / 'zt320320' / 'Theme5'
    d.mkdir(parents=True)
    mask = renderer.create_surface(320, 320, (0, 0, 0, 128))
    mask.save(str(d / '01.png'))
    return d


@pytest.fixture()
def mock_path_resolver(tmp_path: Path) -> MagicMock:
    """Mock PlatformSetup path resolver — returns dirs under tmp_path."""
    resolver = MagicMock()
    resolver.data_dir.return_value = str(tmp_path / 'data')
    resolver.user_content_dir.return_value = str(tmp_path / 'user')
    resolver.web_dir = lambda w, h: str(tmp_path / 'data' / 'web' / f'{w}{h}')
    resolver.web_masks_dir = lambda w, h: str(tmp_path / 'data' / 'web' / f'zt{w}{h}')
    resolver.user_masks_dir = lambda w, h: str(tmp_path / 'user' / 'data' / 'web' / f'zt{w}{h}')
    return resolver


@pytest.fixture()
def display_svc(renderer: Any, mock_path_resolver: MagicMock) -> DisplayService:
    """DisplayService with real OverlayService, mocked device/media, injected path_resolver."""
    devices = MagicMock()
    overlay = OverlayService(320, 320, renderer=renderer)
    media = MagicMock()
    media._frames = []
    media.has_frames = False
    media.is_playing = False
    media.source_path = None
    media.get_frame.return_value = None

    svc = DisplayService(devices, overlay, media, path_resolver=mock_path_resolver)
    svc.set_resolution(320, 320)
    svc.current_image = renderer.create_surface(320, 320, (0, 0, 255))
    svc._clean_background = svc.current_image
    return svc


@pytest.fixture()
def lcd(display_svc: DisplayService, renderer: Any) -> Device:
    """Device wired to display_svc (the GUI path)."""
    return Device(
        device_svc=display_svc.devices,
        display_svc=display_svc,
        renderer=renderer,
    )




# ── ThemePersistence.save() ───────────────────────────────────────────────────


class TestThemePersistenceSave:

    def test_saves_clean_bg_not_rendered(
        self, tmp_path: Path, lcd_size: tuple[int, int],
        blue_bg: Any, mock_overlay: MagicMock,
    ) -> None:
        """00.png must be the clean background, not the overlay-composited image."""
        ok, _ = ThemePersistence.save(
            'TestClean', tmp_path, lcd_size,
            current_image=blue_bg, overlay=mock_overlay,
            mask_source_dir=None, media_source_path=None,
            media_is_playing=False, current_theme_path=None,
        )
        assert ok is True

        theme_path = tmp_path / 'theme320320' / 'Custom_TestClean'
        bg_data = (theme_path / '00.png').read_bytes()
        thumb_data = (theme_path / 'Theme.png').read_bytes()
        assert bg_data != thumb_data, \
            "00.png (clean bg) must differ from Theme.png (rendered preview)"

    def test_overlay_render_called_for_thumbnail(
        self, tmp_path: Path, lcd_size: tuple[int, int],
        blue_bg: Any, mock_overlay: MagicMock,
    ) -> None:
        """overlay.render() must be called to produce the thumbnail preview."""
        ThemePersistence.save(
            'RenderCall', tmp_path, lcd_size,
            current_image=blue_bg, overlay=mock_overlay,
            mask_source_dir=None, media_source_path=None,
            media_is_playing=False, current_theme_path=None,
        )
        mock_overlay.render.assert_called_once_with(blue_bg)

    def test_overlay_config_saved(
        self, tmp_path: Path, lcd_size: tuple[int, int],
        blue_bg: Any, mock_overlay: MagicMock,
    ) -> None:
        """Overlay config must be written to config.json dc field."""
        ThemePersistence.save(
            'CfgSave', tmp_path, lcd_size,
            current_image=blue_bg, overlay=mock_overlay,
            mask_source_dir=None, media_source_path=None,
            media_is_playing=False, current_theme_path=None,
        )
        config = json.loads(
            (tmp_path / 'theme320320' / 'Custom_CfgSave' / 'config.json').read_text())
        assert config['dc'] == mock_overlay.config

    def test_mask_source_propagated(
        self, tmp_path: Path, lcd_size: tuple[int, int],
        blue_bg: Any, mock_overlay: MagicMock, mask_dir: Path,
    ) -> None:
        """mask_source_dir must flow through to config.json mask field."""
        mock_overlay.get_mask.return_value = (blue_bg, (0, 0))

        ThemePersistence.save(
            'MaskProp', tmp_path, lcd_size,
            current_image=blue_bg, overlay=mock_overlay,
            mask_source_dir=mask_dir, media_source_path=None,
            media_is_playing=False, current_theme_path=None,
        )
        config = json.loads(
            (tmp_path / 'theme320320' / 'Custom_MaskProp' / 'config.json').read_text())
        assert config['mask'] == str(mask_dir)

    def test_no_image_returns_false(
        self, tmp_path: Path, lcd_size: tuple[int, int],
        mock_overlay: MagicMock,
    ) -> None:
        ok, msg = ThemePersistence.save(
            'NoImg', tmp_path, lcd_size,
            current_image=None, overlay=mock_overlay,
            mask_source_dir=None, media_source_path=None,
            media_is_playing=False, current_theme_path=None,
        )
        assert ok is False
        assert 'No image' in msg


# ── DisplayService.save_theme() ──────────────────────────────────────────────


class TestDisplayServiceSaveTheme:

    def test_passes_clean_background(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """save_theme must pass _clean_background, not current_image."""
        clean = renderer.create_surface(320, 320, (0, 0, 255))
        dirty = renderer.create_surface(320, 320, (255, 0, 0))
        display_svc._clean_background = clean
        display_svc.current_image = dirty

        with patch.object(ThemePersistence, 'save', return_value=(True, 'ok')) as mock_save:
            display_svc.save_theme('Test')

        passed = mock_save.call_args.kwargs.get('current_image') or mock_save.call_args[0][3]
        assert passed is clean

    def test_falls_back_to_current_image(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """When _clean_background is None, falls back to current_image."""
        current = renderer.create_surface(320, 320, (255, 0, 0))
        display_svc._clean_background = None
        display_svc.current_image = current

        with patch.object(ThemePersistence, 'save', return_value=(True, 'ok')) as mock_save:
            display_svc.save_theme('Fallback')

        passed = mock_save.call_args.kwargs.get('current_image') or mock_save.call_args[0][3]
        assert passed is current


# ── load_mask_standalone → save round-trip ────────────────────────────────────


class TestLoadMaskStandaloneWiring:
    """The REAL bug: load_mask_standalone bypassed DisplayService.apply_mask,
    so _mask_source_dir was never set. Save then wrote mask=null."""

    def test_sets_mask_source_dir(
        self, lcd: Device, display_svc: DisplayService, mask_dir: Path,
    ) -> None:
        """load_mask_standalone must update _display_svc._mask_source_dir."""
        result = lcd.load_mask_standalone(str(mask_dir))

        assert result['success'] is True
        assert display_svc._mask_source_dir == mask_dir

    def test_save_after_mask_has_mask_in_config(
        self, lcd: Device, display_svc: DisplayService,
        mask_dir: Path, tmp_path: Path,
    ) -> None:
        """Full flow: load_mask_standalone → save → config.json has mask."""
        lcd.load_mask_standalone(str(mask_dir))

        ok, msg = display_svc.save_theme('MaskTest')
        assert ok is True

        user_dir = Path(display_svc._path_resolver.user_content_dir())
        config = json.loads(
            (user_dir / 'data' / 'theme320320' / 'Custom_MaskTest' / 'config.json').read_text())
        assert config['mask'] == str(mask_dir), \
            f"Expected mask={mask_dir}, got {config['mask']}"


# ── Cloud theme load clears stale state ──────────────────────────────────────


class TestCloudThemeStateWiring:

    def test_preserves_mask_source_dir_on_cloud_load(
        self, display_svc: DisplayService,
    ) -> None:
        """Cloud load (video-only) must preserve existing mask source dir."""
        mask_dir = Path('/applied/mask')
        display_svc._mask_source_dir = mask_dir

        cloud_result = {
            'image': None, 'is_animated': True,
            'status': 'Cloud Theme: vid',
            'mask_source_dir': None,
            'theme_path': None,
        }
        with patch.object(display_svc._loader, 'load_cloud_theme', return_value=cloud_result):
            display_svc.load_cloud_theme(MagicMock())

        assert display_svc._mask_source_dir == mask_dir

    def test_wires_theme_path(
        self, display_svc: DisplayService,
    ) -> None:
        """load_cloud_theme must set current_theme_path."""
        fake_path = Path('/fake/theme')
        cloud_result = {
            'image': None, 'is_animated': True,
            'status': 'Cloud Theme: vid',
            'mask_source_dir': None,
            'theme_path': fake_path,
        }
        with patch.object(display_svc._loader, 'load_cloud_theme', return_value=cloud_result):
            display_svc.load_cloud_theme(MagicMock())

        assert display_svc.current_theme_path is fake_path

    def test_sets_clean_background_from_first_frame(
        self, display_svc: DisplayService, renderer: Any,
    ) -> None:
        """load_cloud_theme must set _clean_background from first video frame."""
        frame = renderer.create_surface(320, 320, (50, 50, 50))
        display_svc.media.get_frame.return_value = frame

        cloud_result = {
            'image': None, 'is_animated': True,
            'status': 'Cloud Theme: vid',
            'mask_source_dir': None,
            'theme_path': None,
        }
        with patch.object(display_svc._loader, 'load_cloud_theme', return_value=cloud_result):
            display_svc.load_cloud_theme(MagicMock())

        assert display_svc._clean_background is frame

    def test_save_after_cloud_load_preserves_mask(
        self, display_svc: DisplayService,
    ) -> None:
        """Save after cloud load must preserve applied mask source dir."""
        mask_dir = Path('/applied/mask')
        display_svc._mask_source_dir = mask_dir
        display_svc.media.get_frame.return_value = make_test_surface(320, 320, (50, 50, 50))

        cloud_result = {
            'image': None, 'is_animated': True,
            'status': 'Cloud Theme: vid',
            'mask_source_dir': None,
            'theme_path': None,
        }
        with patch.object(display_svc._loader, 'load_cloud_theme', return_value=cloud_result):
            display_svc.load_cloud_theme(MagicMock())

        with patch.object(ThemePersistence, 'save', return_value=(True, 'ok')) as mock_save:
            display_svc.save_theme('CloudSave')

        assert mock_save.call_args.kwargs.get('mask_source_dir') == mask_dir


# ── ThemePersistence.export_config / import_config ───────────────────────────


class TestThemePersistenceExportImport:
    """Contracts for export_config and import_config."""

    def test_export_no_theme_path_returns_false(self, tmp_path: Path) -> None:
        """export_config with no current theme path returns (False, 'No theme loaded')."""
        p = ThemePersistence()
        ok, msg = p.export_config(tmp_path / 'out.json', None, 320, 320)
        assert ok is False
        assert 'No theme loaded' in msg

    def test_export_tr_no_theme_svc_returns_false(self, tmp_path: Path) -> None:
        """export_config for .tr file without theme_svc returns (False, 'Export not available...')."""
        p = ThemePersistence(theme_svc=None)
        ok, msg = p.export_config(
            tmp_path / 'out.tr', tmp_path / 'theme', 320, 320)
        assert ok is False
        assert 'Export not available' in msg

    def test_export_tr_delegates_to_theme_svc(self, tmp_path: Path) -> None:
        """export_config for .tr file with theme_svc calls theme_svc.export_tr."""
        theme_svc = MagicMock()
        theme_svc.export_tr.return_value = (True, 'Exported')
        p = ThemePersistence(theme_svc=theme_svc)
        current = tmp_path / 'theme'
        export_path = tmp_path / 'out.tr'
        ok, msg = p.export_config(export_path, current, 320, 320)
        theme_svc.export_tr.assert_called_once_with(current, export_path)
        assert ok is True

    def test_export_json_writes_file(self, tmp_path: Path) -> None:
        """export_config for JSON path writes config file and returns (True, ...)."""
        import json
        p = ThemePersistence()
        current = tmp_path / 'theme'
        export_path = tmp_path / 'out.json'
        ok, msg = p.export_config(export_path, current, 320, 320)
        assert ok is True
        assert export_path.exists()
        data = json.loads(export_path.read_text())
        assert data['theme_path'] == str(current)
        assert data['resolution'] == '320x320'

    def test_export_json_write_failure_returns_false(self, tmp_path: Path) -> None:
        """export_config for JSON returns (False, ...) when write fails."""
        p = ThemePersistence()
        # Use a path whose parent doesn't exist — write will fail
        bad_path = tmp_path / 'nonexistent' / 'out.json'
        ok, msg = p.export_config(bad_path, tmp_path / 'theme', 320, 320)
        assert ok is False
        assert 'Export failed' in msg

    def test_import_tr_no_theme_svc_returns_false(self, tmp_path: Path) -> None:
        """import_config for .tr file without theme_svc returns (False, 'Import not available...')."""
        p = ThemePersistence(theme_svc=None)
        ok, msg = p.import_config(tmp_path / 'in.tr', tmp_path, (320, 320))
        assert ok is False
        assert 'Import not available' in msg

    def test_import_tr_delegates_to_theme_svc(self, tmp_path: Path) -> None:
        """import_config for .tr file with theme_svc calls theme_svc.import_tr."""
        theme_svc = MagicMock()
        theme_svc.import_tr.return_value = (True, MagicMock())
        p = ThemePersistence(theme_svc=theme_svc)
        import_path = tmp_path / 'in.tr'
        ok, _ = p.import_config(import_path, tmp_path, (320, 320))
        theme_svc.import_tr.assert_called_once_with(import_path, tmp_path, (320, 320))
        assert ok is True

    def test_import_json_valid_path_returns_theme_info(
        self, tmp_path: Path, renderer: Any,
    ) -> None:
        """import_config for JSON with a valid theme_path returns (True, ThemeInfo)."""
        import json

        from trcc.legacy.core.models import ThemeInfo
        # Create a real theme directory so ThemeInfo.from_directory works
        theme_dir = tmp_path / 'MyTheme'
        theme_dir.mkdir()
        bg = renderer.create_surface(320, 320, (0, 0, 255))
        bg.save(str(theme_dir / '00.png'))

        config_file = tmp_path / 'in.json'
        config_file.write_text(json.dumps({'theme_path': str(theme_dir)}))

        p = ThemePersistence()
        ok, result = p.import_config(config_file, tmp_path, (320, 320))
        assert ok is True
        assert isinstance(result, ThemeInfo)

    def test_import_json_missing_theme_path_returns_false(
        self, tmp_path: Path,
    ) -> None:
        """import_config for JSON when theme_path does not exist returns (False, 'Theme path...')."""
        import json
        config_file = tmp_path / 'in.json'
        config_file.write_text(json.dumps({
            'theme_path': str(tmp_path / 'nonexistent'),
        }))
        p = ThemePersistence()
        ok, msg = p.import_config(config_file, tmp_path, (320, 320))
        assert ok is False
        assert 'Theme path' in msg

    def test_import_json_corrupt_returns_false(self, tmp_path: Path) -> None:
        """import_config for corrupt JSON returns (False, 'Import failed...')."""
        bad_file = tmp_path / 'bad.json'
        bad_file.write_text('NOT JSON {{{{')
        p = ThemePersistence()
        ok, msg = p.import_config(bad_file, tmp_path, (320, 320))
        assert ok is False
        assert 'Import failed' in msg
