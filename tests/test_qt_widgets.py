"""
Tests for qt_components widgets – UCPreview, UCDevice, UCThemeLocal, UCAbout, assets.

Uses QT_QPA_PLATFORM=offscreen for headless testing.

Tests cover:
- assets: get_asset_path, load_pixmap, asset_exists, Assets class methods
- UCPreview: init, resolution offsets, set_status, show_video_controls, set_resolution
- UCDevice: init, device button creation, selection, about/home signals, DEVICE_IMAGE_MAP
- UCThemeLocal: init, filter modes, slideshow toggle, theme loading from directory
- UCAbout: init, autostart helpers, signals
- qt_app_mvc: detect_language helper
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Must set before ANY Qt import
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from PIL import Image  # noqa: E402

# ============================================================================
# Assets
# ============================================================================
from trcc.qt_components.assets import (  # noqa: E402
    ASSETS_DIR,
    Assets,
    asset_exists,
    get_asset_path,
    load_pixmap,
)


class TestAssets(unittest.TestCase):
    """Test asset loader functions."""

    def test_assets_dir_exists(self):
        self.assertTrue(ASSETS_DIR.exists(), f"ASSETS_DIR missing: {ASSETS_DIR}")

    def test_get_asset_path(self):
        path = get_asset_path('P0CZTV.png')
        self.assertIsInstance(path, Path)
        self.assertTrue(str(path).endswith('P0CZTV.png'))

    def test_load_pixmap_missing(self):
        """Missing asset returns empty QPixmap."""
        pix = load_pixmap.__wrapped__('definitely_not_a_file.png')
        self.assertTrue(pix.isNull())

    def test_load_pixmap_existing(self):
        """Loading a real asset returns valid pixmap."""
        # Use a known asset
        if asset_exists('P0CZTV.png'):
            pix = load_pixmap.__wrapped__('P0CZTV.png')
            self.assertFalse(pix.isNull())
        else:
            self.skipTest('P0CZTV.png not found')

    def test_load_pixmap_scaled(self):
        """Scaling produces correct dimensions."""
        if asset_exists('P0CZTV.png'):
            pix = load_pixmap.__wrapped__('P0CZTV.png', 100, 80)
            self.assertEqual(pix.width(), 100)
            self.assertEqual(pix.height(), 80)
        else:
            self.skipTest('P0CZTV.png not found')

    def test_asset_exists(self):
        self.assertFalse(asset_exists('nonexistent_file_xyz.png'))

    def test_assets_preview_for_resolution(self):
        name = Assets.get_preview_for_resolution(320, 320)
        self.assertIsInstance(name, str)
        self.assertTrue(name.endswith('.png'))

    def test_assets_preview_fallback(self):
        """Unknown resolution falls back to 320x320."""
        name = Assets.get_preview_for_resolution(9999, 9999)
        self.assertEqual(name, Assets.PREVIEW_320X320)

    def test_assets_get_localized_cn(self):
        """Chinese (default) returns base name."""
        self.assertEqual(Assets.get_localized('P0CZTV.png', 'cn'), 'P0CZTV.png')
        self.assertEqual(Assets.get_localized('P0CZTV.png', ''), 'P0CZTV.png')

    def test_assets_get_localized_en(self):
        """English returns en-suffixed name if it exists."""
        result = Assets.get_localized('P0CZTV.png', 'en')
        # Should be P0CZTVen.png if that file exists, else P0CZTV.png
        self.assertIsInstance(result, str)


# ============================================================================
# UCPreview
# ============================================================================

from trcc.qt_components.uc_preview import UCPreview  # noqa: E402


class TestUCPreview(unittest.TestCase):
    """Test UCPreview widget."""

    def test_init_default_resolution(self):
        preview = UCPreview(320, 320)
        self.assertEqual(preview.get_lcd_size(), (320, 320))

    def test_resolution_offsets(self):
        """All standard resolutions have offset entries."""
        for res in [(320, 320), (480, 480), (240, 240)]:
            self.assertIn(res, UCPreview.RESOLUTION_OFFSETS)

    def test_set_status(self):
        preview = UCPreview(320, 320)
        preview.set_status('Testing...')
        self.assertEqual(preview.status_label.text(), 'Testing...')

    def test_show_video_controls(self):
        """show_video_controls toggles the hidden flag."""
        preview = UCPreview(320, 320)
        self.assertTrue(preview.progress_container.isHidden())
        preview.show_video_controls(True)
        self.assertFalse(preview.progress_container.isHidden())
        preview.show_video_controls(False)
        self.assertTrue(preview.progress_container.isHidden())

    def test_set_resolution(self):
        preview = UCPreview(320, 320)
        preview.set_resolution(480, 480)
        self.assertEqual(preview.get_lcd_size(), (480, 480))

    def test_set_progress(self):
        preview = UCPreview(320, 320)
        preview.set_progress(50.0, '01:30', '03:00')
        self.assertEqual(preview.progress_slider.value(), 50)
        self.assertIn('01:30', preview.time_label.text())

    def test_set_playing_toggle(self):
        preview = UCPreview(320, 320)
        preview.set_playing(True)
        preview.set_playing(False)
        # Should not crash — tests both icon and text fallback paths

    def test_set_image(self):
        preview = UCPreview(320, 320)
        img = Image.new('RGB', (320, 320), (128, 128, 128))
        preview.set_image(img)
        self.assertFalse(preview.preview_label.pixmap().isNull())

    def test_delegate_play_pause(self):
        """Play button emits delegate signal."""
        preview = UCPreview(320, 320)
        received = []
        preview.delegate.connect(lambda c, i, d: received.append(c))
        preview.play_btn.click()
        self.assertIn(UCPreview.CMD_VIDEO_PLAY_PAUSE, received)


# ============================================================================
# UCDevice
# ============================================================================

from trcc.qt_components.uc_device import (  # noqa: E402
    DEVICE_IMAGE_MAP,
    DeviceButton,
    UCDevice,
    _get_device_images,
)


class TestDeviceImageMap(unittest.TestCase):
    """Test device image name mapping."""

    def test_map_not_empty(self):
        self.assertGreater(len(DEVICE_IMAGE_MAP), 0)

    def test_all_values_are_strings(self):
        for model, base in DEVICE_IMAGE_MAP.items():
            self.assertIsInstance(base, str)

    def test_get_device_images_unknown(self):
        """Unknown device falls back to CZTV or None."""
        normal, active = _get_device_images({'name': 'Unknown Device XYZ'})
        # Either CZTV fallback or None if no assets
        if normal:
            self.assertTrue(normal.endswith('.png'))


class TestDeviceButton(unittest.TestCase):
    """Test DeviceButton widget."""

    def test_init(self):
        info = {'name': 'Test LCD', 'path': '/dev/sg0', 'model': 'UNKNOWN'}
        btn = DeviceButton(info)
        self.assertEqual(btn.device_info, info)
        self.assertFalse(btn.selected)

    def test_set_selected(self):
        info = {'name': 'LCD', 'path': '/dev/sg0'}
        btn = DeviceButton(info)
        btn.set_selected(True)
        self.assertTrue(btn.selected)

    def test_device_clicked_signal(self):
        info = {'name': 'LCD', 'path': '/dev/sg0'}
        btn = DeviceButton(info)
        received = []
        btn.device_clicked.connect(lambda d: received.append(d))
        btn.click()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['path'], '/dev/sg0')


class TestUCDevice(unittest.TestCase):
    """Test UCDevice sidebar widget."""

    def test_init(self):
        """UCDevice initializes without crashing."""
        with patch('trcc.qt_components.uc_device.find_lcd_devices', return_value=[]):
            sidebar = UCDevice()
        self.assertEqual(sidebar.width(), 180)
        self.assertEqual(sidebar.height(), 800)

    def test_no_devices(self):
        with patch('trcc.qt_components.uc_device.find_lcd_devices', return_value=[]):
            sidebar = UCDevice()
        self.assertEqual(sidebar.get_devices(), [])
        self.assertIsNone(sidebar.get_selected_device())

    def test_update_devices(self):
        """update_devices creates device buttons."""
        with patch('trcc.qt_components.uc_device.find_lcd_devices', return_value=[]):
            sidebar = UCDevice()

        devices = [
            {'name': 'LCD1', 'path': '/dev/sg0'},
            {'name': 'LCD2', 'path': '/dev/sg1'},
        ]
        sidebar.update_devices(devices)
        self.assertEqual(len(sidebar.device_buttons), 2)

    def test_about_signal(self):
        with patch('trcc.qt_components.uc_device.find_lcd_devices', return_value=[]):
            sidebar = UCDevice()
        fired = []
        sidebar.about_clicked.connect(lambda: fired.append(True))
        sidebar.about_btn.click()
        self.assertTrue(fired)

    def test_home_signal(self):
        with patch('trcc.qt_components.uc_device.find_lcd_devices', return_value=[]):
            sidebar = UCDevice()
        fired = []
        sidebar.home_clicked.connect(lambda: fired.append(True))
        sidebar.sensor_btn.click()
        self.assertTrue(fired)


# ============================================================================
# UCThemeLocal
# ============================================================================

from trcc.qt_components.uc_theme_local import UCThemeLocal  # noqa: E402


class TestUCThemeLocal(unittest.TestCase):
    """Test UCThemeLocal browser widget."""

    def test_init(self):
        panel = UCThemeLocal()
        self.assertEqual(panel.filter_mode, UCThemeLocal.MODE_ALL)
        self.assertFalse(panel.is_slideshow())

    def test_load_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel = UCThemeLocal()
            panel.set_theme_directory(tmp)
            self.assertIsNone(panel.get_selected_theme())

    def test_load_themes_from_directory(self):
        """Themes with Theme.png are discovered."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create two fake theme dirs
            for name in ('Theme1', 'Theme2'):
                d = Path(tmp) / name
                d.mkdir()
                (d / 'Theme.png').write_bytes(b'\x89PNG_FAKE')

            panel = UCThemeLocal()
            panel.set_theme_directory(tmp)
            self.assertEqual(len(panel.item_widgets), 2)

    def test_filter_user_themes(self):
        """User filter shows only Custom_/User_ prefixed themes."""
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('DefaultTheme', 'Custom_Mine'):
                d = Path(tmp) / name
                d.mkdir()
                (d / 'Theme.png').write_bytes(b'PNG')

            panel = UCThemeLocal()
            panel.set_theme_directory(tmp)
            # Switch to user filter
            panel._set_filter(UCThemeLocal.MODE_USER)
            user_names = [w.item_info['name'] for w in panel.item_widgets
                          if hasattr(w, 'item_info')]
            self.assertEqual(user_names, ['Custom_Mine'])

    def test_slideshow_interval(self):
        panel = UCThemeLocal()
        panel.timer_input.setText('5')
        panel._on_timer_changed()
        self.assertEqual(panel.get_slideshow_interval(), 5)

    def test_slideshow_interval_minimum(self):
        """Interval below 3 is clamped to 3."""
        panel = UCThemeLocal()
        panel.timer_input.setText('1')
        panel._on_timer_changed()
        self.assertEqual(panel.get_slideshow_interval(), 3)

    def test_slideshow_toggle(self):
        panel = UCThemeLocal()
        self.assertFalse(panel.is_slideshow())
        panel._on_slideshow_clicked()
        self.assertTrue(panel.is_slideshow())
        panel._on_slideshow_clicked()
        self.assertFalse(panel.is_slideshow())


# ============================================================================
# UCAbout helpers
# ============================================================================

from trcc.qt_components.uc_about import (  # noqa: E402
    _get_trcc_exec,
    _is_autostart_enabled,
    _make_desktop_entry,
    _set_autostart,
    ensure_autostart,
)


class TestAutostart(unittest.TestCase):
    """Comprehensive autostart tests matching Windows KaijiQidong behavior."""

    # --- _is_autostart_enabled ---

    def test_is_autostart_enabled_returns_bool(self):
        """_is_autostart_enabled returns bool based on .desktop file existence."""
        result = _is_autostart_enabled()
        self.assertIsInstance(result, bool)

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    def test_is_autostart_enabled_true(self, mock_file):
        """Returns True when .desktop file exists."""
        mock_file.exists.return_value = True
        self.assertTrue(_is_autostart_enabled())

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    def test_is_autostart_enabled_false(self, mock_file):
        """Returns False when .desktop file missing."""
        mock_file.exists.return_value = False
        self.assertFalse(_is_autostart_enabled())

    # --- _set_autostart ---

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    @patch('trcc.qt_components.uc_about._AUTOSTART_DIR')
    def test_set_autostart_enable(self, mock_dir, mock_file):
        """Enabling creates dir and writes .desktop file."""
        _set_autostart(True)
        mock_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_file.write_text.assert_called_once()
        content = mock_file.write_text.call_args[0][0]
        self.assertIn('[Desktop Entry]', content)
        self.assertIn('--last-one', content)

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    def test_set_autostart_disable_removes_file(self, mock_file):
        """Disabling removes .desktop file when it exists."""
        mock_file.exists.return_value = True
        _set_autostart(False)
        mock_file.unlink.assert_called_once()

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    def test_set_autostart_disable_noop_when_missing(self, mock_file):
        """Disabling does nothing when .desktop file already gone."""
        mock_file.exists.return_value = False
        _set_autostart(False)
        mock_file.unlink.assert_not_called()

    def test_set_autostart_real_filesystem(self):
        """Integration test with real temp dir."""
        import trcc.qt_components.uc_about as mod
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            orig_dir = mod._AUTOSTART_DIR
            orig_file = mod._AUTOSTART_FILE
            try:
                mod._AUTOSTART_DIR = tmp_path / 'autostart'
                mod._AUTOSTART_FILE = mod._AUTOSTART_DIR / 'trcc.desktop'
                # Enable
                _set_autostart(True)
                self.assertTrue(mod._AUTOSTART_FILE.exists())
                content = mod._AUTOSTART_FILE.read_text()
                self.assertIn('TRCC Linux', content)
                self.assertIn('--last-one', content)
                # Disable
                _set_autostart(False)
                self.assertFalse(mod._AUTOSTART_FILE.exists())
            finally:
                mod._AUTOSTART_DIR = orig_dir
                mod._AUTOSTART_FILE = orig_file

    # --- _get_trcc_exec ---

    @patch('shutil.which', return_value='/usr/bin/trcc')
    def test_get_trcc_exec_pip_installed(self, mock_which):
        """Uses pip-installed entry point when found on PATH."""
        result = _get_trcc_exec()
        self.assertEqual(result, '/usr/bin/trcc')

    @patch('shutil.which', return_value=None)
    def test_get_trcc_exec_fallback(self, mock_which):
        """Falls back to PYTHONPATH + python -m trcc.cli."""
        result = _get_trcc_exec()
        self.assertIn('python', result)
        self.assertIn('trcc.cli', result)
        self.assertIn('PYTHONPATH=', result)

    # --- _make_desktop_entry ---

    @patch('trcc.qt_components.uc_about._get_trcc_exec', return_value='/usr/bin/trcc')
    def test_make_desktop_entry_format(self, mock_exec):
        """Desktop entry has correct XDG fields."""
        entry = _make_desktop_entry()
        self.assertIn('[Desktop Entry]', entry)
        self.assertIn('Type=Application', entry)
        self.assertIn('Name=TRCC Linux', entry)
        self.assertIn('Exec=/usr/bin/trcc --last-one', entry)
        self.assertIn('Terminal=false', entry)
        self.assertIn('X-GNOME-Autostart-enabled=true', entry)

    # --- ensure_autostart (first launch) ---

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    @patch('trcc.qt_components.uc_about._AUTOSTART_DIR')
    @patch('trcc.paths.save_config')
    @patch('trcc.paths.load_config', return_value={})
    def test_ensure_autostart_first_launch(self, mock_load, mock_save,
                                            mock_dir, mock_file):
        """First launch auto-enables autostart and marks configured."""
        mock_file.exists.return_value = False
        result = ensure_autostart()
        self.assertTrue(result)
        mock_dir.mkdir.assert_called_once()
        mock_file.write_text.assert_called_once()
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        self.assertTrue(saved['autostart_configured'])

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    @patch('trcc.qt_components.uc_about._AUTOSTART_DIR')
    @patch('trcc.paths.save_config')
    @patch('trcc.paths.load_config', return_value={'other_key': 'val'})
    def test_ensure_autostart_first_launch_preserves_config(self, mock_load,
                                                             mock_save,
                                                             mock_dir, mock_file):
        """First launch preserves existing config keys."""
        mock_file.exists.return_value = False
        ensure_autostart()
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved['other_key'], 'val')
        self.assertTrue(saved['autostart_configured'])

    # --- ensure_autostart (subsequent launch) ---

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    @patch('trcc.paths.save_config')
    @patch('trcc.paths.load_config', return_value={'autostart_configured': True})
    def test_ensure_autostart_subsequent_enabled(self, mock_load, mock_save,
                                                  mock_file):
        """Subsequent launch returns True when .desktop exists."""
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = "old content"
        result = ensure_autostart()
        self.assertTrue(result)
        mock_save.assert_not_called()

    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    @patch('trcc.paths.save_config')
    @patch('trcc.paths.load_config', return_value={'autostart_configured': True})
    def test_ensure_autostart_subsequent_disabled(self, mock_load, mock_save,
                                                   mock_file):
        """Returns False when user removed .desktop file."""
        mock_file.exists.return_value = False
        result = ensure_autostart()
        self.assertFalse(result)

    # --- ensure_autostart (path refresh) ---

    @patch('trcc.qt_components.uc_about._make_desktop_entry',
           return_value='[Desktop Entry]\nExec=/new/path\n')
    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    @patch('trcc.paths.save_config')
    @patch('trcc.paths.load_config', return_value={'autostart_configured': True})
    def test_ensure_autostart_refreshes_stale_path(self, mock_load, mock_save,
                                                    mock_file, mock_make):
        """Refreshes .desktop when Exec path changed (like Windows path check)."""
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = '[Desktop Entry]\nExec=/old/path\n'
        result = ensure_autostart()
        self.assertTrue(result)
        mock_file.write_text.assert_called_once_with(
            '[Desktop Entry]\nExec=/new/path\n')

    @patch('trcc.qt_components.uc_about._make_desktop_entry',
           return_value='[Desktop Entry]\nExec=/same/path\n')
    @patch('trcc.qt_components.uc_about._AUTOSTART_FILE')
    @patch('trcc.paths.save_config')
    @patch('trcc.paths.load_config', return_value={'autostart_configured': True})
    def test_ensure_autostart_no_refresh_when_same(self, mock_load, mock_save,
                                                    mock_file, mock_make):
        """Does not rewrite .desktop when content matches."""
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = '[Desktop Entry]\nExec=/same/path\n'
        ensure_autostart()
        mock_file.write_text.assert_not_called()


# ============================================================================
# detect_language
# ============================================================================

from trcc.qt_components.qt_app_mvc import LOCALE_TO_LANG, detect_language  # noqa: E402


class TestDetectLanguage(unittest.TestCase):
    """Test language detection from locale."""

    def test_returns_string(self):
        lang = detect_language()
        self.assertIsInstance(lang, str)

    def test_locale_mapping_keys(self):
        """All expected locales are mapped."""
        self.assertIn('en', LOCALE_TO_LANG)
        self.assertIn('zh_CN', LOCALE_TO_LANG)
        self.assertIn('de', LOCALE_TO_LANG)

    @patch('trcc.qt_components.qt_app_mvc.locale')
    def test_english_locale(self, mock_locale):
        mock_locale.getlocale.return_value = ('en_US', 'UTF-8')
        self.assertEqual(detect_language(), 'en')

    @patch('trcc.qt_components.qt_app_mvc.locale')
    def test_chinese_locale(self, mock_locale):
        mock_locale.getlocale.return_value = ('zh_CN', 'UTF-8')
        self.assertEqual(detect_language(), '')

    @patch('trcc.qt_components.qt_app_mvc.locale')
    def test_unknown_locale_defaults_to_en(self, mock_locale):
        mock_locale.getlocale.return_value = ('zz_ZZ', 'UTF-8')
        self.assertEqual(detect_language(), 'en')

    @patch.dict('os.environ', {'LANG': ''})
    @patch('trcc.qt_components.qt_app_mvc.locale')
    def test_none_locale_defaults_to_en(self, mock_locale):
        mock_locale.getlocale.return_value = (None, None)
        self.assertEqual(detect_language(), 'en')


if __name__ == '__main__':
    unittest.main()
