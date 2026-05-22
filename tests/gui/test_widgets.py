"""
Tests for gui widgets – UCPreview, UCDevice, UCThemeLocal, UCAbout, assets.

Uses QT_QPA_PLATFORM=offscreen for headless testing.

Tests cover:
- Assets: path, load_pixmap, exists, get, get_localized, auto .png resolution
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
from tempfile import TemporaryDirectory
from unittest.mock import patch

# Must set before ANY Qt import
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

from tests.conftest import make_test_surface  # noqa: E402

# ============================================================================
# Assets
# ============================================================================
from trcc.legacy.ui.gui.assets import _ASSETS_DIR, Assets  # noqa: E402


class TestAssets(unittest.TestCase):
    """Test asset loader functions."""

    def test_assets_dir_exists(self):
        self.assertTrue(_ASSETS_DIR.exists(), f"ASSETS_DIR missing: {_ASSETS_DIR}")

    def test_path(self):
        path = Assets.path('P0CZTV.png')
        self.assertIsInstance(path, Path)
        self.assertTrue(str(path).endswith('P0CZTV.png'))

    def test_load_pixmap_missing(self):
        """Missing asset returns empty QPixmap."""
        pix = Assets.load_pixmap.__wrapped__(Assets, 'definitely_not_a_file.png')
        self.assertTrue(pix.isNull())

    def test_exists(self):
        self.assertFalse(Assets.exists('nonexistent_file_xyz.png'))

    def test_auto_png_resolution(self):
        """Base names without .png resolve if .png file exists."""
        if Assets.exists('P0CZTV.png'):
            self.assertTrue(Assets.exists('P0CZTV'))
            self.assertIsNotNone(Assets.get('P0CZTV'))

    def test_assets_preview_for_resolution(self):
        name = Assets.get_preview_for_resolution(320, 320)
        self.assertIsInstance(name, str)
        self.assertTrue(name.endswith('.png'))

    def test_assets_preview_fallback(self):
        """Unknown resolution falls back to 320x320."""
        name = Assets.get_preview_for_resolution(9999, 9999)
        self.assertEqual(name, Assets.PREVIEW_320X320)

    def test_assets_get_localized_zh(self):
        """Chinese simplified returns base name (no suffix)."""
        self.assertEqual(Assets.get_localized('P0CZTV.png', 'zh'), 'P0CZTV.png')

    def test_assets_get_localized_en(self):
        """English returns en-suffixed name if it exists."""
        result = Assets.get_localized('P0CZTV.png', 'en')
        # Should be P0CZTVen.png if that file exists, else P0CZTV.png
        self.assertIsInstance(result, str)

    def test_get_localized_base_name(self):
        """Localization works with base names (no .png)."""
        result = Assets.get_localized('P0CZTV', 'en')
        self.assertIsInstance(result, str)

    def test_led_mode_button_assets_exist(self):
        """All 6 LED mode button images (normal + active) must exist."""
        for i in range(1, 7):
            normal = f"led_mode_{i}"
            active = f"led_mode_{i}_active"
            self.assertTrue(Assets.exists(normal), f"Missing {normal}")
            self.assertTrue(Assets.exists(active), f"Missing {active}")


class TestResolveAssetsDir(unittest.TestCase):
    """Platform adapters resolve assets directory correctly."""

    def test_linux_uses_package_dir(self):
        """LinuxSetup returns package dir directly."""
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        from trcc.legacy.ui.gui.assets import _PKG_ASSETS_DIR
        result = LinuxPlatform().resolve_assets_dir(_PKG_ASSETS_DIR)
        self.assertEqual(result, _PKG_ASSETS_DIR)

    def test_windows_copies_to_user_dir(self):
        """WindowsSetup copies assets to ~/.trcc/assets/gui/."""
        from trcc.legacy.adapters.system.windows_platform import WindowsPlatform
        from trcc.legacy.ui.gui.assets import _PKG_ASSETS_DIR
        with TemporaryDirectory() as tmpdir:
            with patch('trcc.legacy.adapters.system.windows_platform.Path.home',
                       return_value=Path(tmpdir)):
                result = WindowsPlatform().resolve_assets_dir(_PKG_ASSETS_DIR)
            if _PKG_ASSETS_DIR.exists():
                user_assets = Path(tmpdir) / '.trcc' / 'assets' / 'gui'
                self.assertEqual(result, user_assets)
                self.assertTrue(any(user_assets.glob('*.png')))

    def test_macos_copies_to_user_dir(self):
        """MacOSSetup copies assets to ~/.trcc/assets/gui/."""
        from trcc.legacy.adapters.system.macos_platform import MacOSPlatform
        from trcc.legacy.ui.gui.assets import _PKG_ASSETS_DIR
        with TemporaryDirectory() as tmpdir:
            with patch('trcc.legacy.adapters.system.macos_platform.Path.home',
                       return_value=Path(tmpdir)):
                result = MacOSPlatform().resolve_assets_dir(_PKG_ASSETS_DIR)
            if _PKG_ASSETS_DIR.exists():
                user_assets = Path(tmpdir) / '.trcc' / 'assets' / 'gui'
                self.assertEqual(result, user_assets)

    def test_bsd_copies_to_user_dir(self):
        """BSDSetup copies assets to ~/.trcc/assets/gui/."""
        from trcc.legacy.adapters.system.bsd_platform import BSDPlatform
        from trcc.legacy.ui.gui.assets import _PKG_ASSETS_DIR
        with TemporaryDirectory() as tmpdir:
            with patch('trcc.legacy.adapters.system.bsd_platform.Path.home',
                       return_value=Path(tmpdir)):
                result = BSDPlatform().resolve_assets_dir(_PKG_ASSETS_DIR)
            if _PKG_ASSETS_DIR.exists():
                user_assets = Path(tmpdir) / '.trcc' / 'assets' / 'gui'
                self.assertEqual(result, user_assets)

    def test_set_assets_dir(self):
        """set_assets_dir updates the module-level _ASSETS_DIR."""
        from trcc.legacy.ui.gui import assets as assets_mod
        from trcc.legacy.ui.gui.assets import set_assets_dir
        original = assets_mod._ASSETS_DIR
        try:
            test_path = Path('/tmp/test_assets')
            set_assets_dir(test_path)
            self.assertEqual(assets_mod._ASSETS_DIR, test_path)
        finally:
            set_assets_dir(original)


# ============================================================================
# UCPreview
# ============================================================================

from trcc.legacy.ui.gui.uc_preview import UCPreview  # noqa: E402


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
        img = make_test_surface(320, 320, (128, 128, 128))
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

from trcc.legacy.ui.gui.uc_device import (  # noqa: E402
    DEVICE_IMAGE_MAP,
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
        # Either CZTV fallback (base name) or None if no assets
        if normal:
            self.assertIsInstance(normal, str)

    def test_get_device_images_hid_default_skipped(self):
        """HID devices with generic A1CZTV return None (await handshake)."""
        normal, active = _get_device_images({
            'protocol': 'hid', 'button_image': 'A1CZTV',
        })
        self.assertIsNone(normal)
        self.assertIsNone(active)


class TestUCDevice(unittest.TestCase):
    """Test UCDevice sidebar widget."""

    def test_init(self):
        """UCDevice initializes without crashing."""
        sidebar = UCDevice()
        self.assertEqual(sidebar.width(), 180)
        self.assertEqual(sidebar.height(), 800)

    def test_no_devices(self):
        sidebar = UCDevice()
        self.assertEqual(sidebar.get_devices(), [])
        self.assertIsNone(sidebar.get_selected_device())

    def test_update_devices(self):
        """update_devices creates device buttons."""
        sidebar = UCDevice()
        devices = [
            {'name': 'LCD1', 'path': '/dev/sg0'},
            {'name': 'LCD2', 'path': '/dev/sg1'},
        ]
        sidebar.update_devices(devices)
        self.assertEqual(len(sidebar.device_buttons), 2)

    def test_about_signal(self):
        sidebar = UCDevice()
        fired = []
        sidebar.about_clicked.connect(lambda: fired.append(True))
        sidebar.about_btn.click()
        self.assertTrue(fired)

    def test_home_signal(self):
        sidebar = UCDevice()
        fired = []
        sidebar.home_clicked.connect(lambda: fired.append(True))
        sidebar.sensor_btn.click()
        self.assertTrue(fired)


# ============================================================================
# UCThemeLocal
# ============================================================================

from trcc.legacy.ui.gui.uc_theme_local import UCThemeLocal  # noqa: E402


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
            user_names = [w.item_info.name for w in panel.item_widgets
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

from trcc.legacy.adapters.system.linux_platform import (  # noqa: E402
    LinuxAutostartManager,
    LinuxPlatform,
)
from trcc.legacy.ui.gui.uc_about import ensure_autostart  # noqa: E402

_AUTOSTART_MOD = 'trcc.legacy.adapters.system.linux_platform'


class TestAutostart(unittest.TestCase):
    """Tests for LinuxAutostartManager and ensure_autostart wrapper.

    `_manager()` — tests the XDG-desktop-entry details directly.
    `_platform()` — tests the ensure_autostart wrapper through the Platform
                    interface (autostart_enable/disable/enabled).
    """

    def _manager(self) -> LinuxAutostartManager:
        return LinuxAutostartManager()

    def _platform(self) -> LinuxPlatform:
        return LinuxPlatform()

    # --- is_enabled ---

    def test_is_enabled_returns_bool(self):
        result = self._manager().is_enabled()
        self.assertIsInstance(result, bool)

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    def test_is_enabled_true(self, mock_file):
        mock_file.exists.return_value = True
        self.assertTrue(self._manager().is_enabled())

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    def test_is_enabled_false(self, mock_file):
        mock_file.exists.return_value = False
        self.assertFalse(self._manager().is_enabled())

    # --- enable / disable ---

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_DIR')
    def test_set_autostart_enable(self, mock_dir, mock_file):
        """enable() creates dir and writes .desktop file."""
        self._manager().enable()
        mock_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        mock_file.write_text.assert_called_once()
        content = mock_file.write_text.call_args[0][0]
        self.assertIn('[Desktop Entry]', content)
        self.assertIn('gui --resume', content)

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    def test_set_autostart_disable_removes_file(self, mock_file):
        mock_file.exists.return_value = True
        self._manager().disable()
        mock_file.unlink.assert_called_once()

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    def test_set_autostart_disable_noop_when_missing(self, mock_file):
        mock_file.exists.return_value = False
        self._manager().disable()
        mock_file.unlink.assert_not_called()

    def test_set_autostart_real_filesystem(self):
        """Integration test with real temp dir."""
        import trcc.legacy.adapters.system.linux_platform as mod
        manager = self._manager()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            orig_dir = mod._AUTOSTART_DIR
            orig_file = mod._AUTOSTART_FILE
            try:
                mod._AUTOSTART_DIR = tmp_path / 'autostart'
                mod._AUTOSTART_FILE = mod._AUTOSTART_DIR / 'trcc-linux.desktop'
                manager.enable()
                self.assertTrue(mod._AUTOSTART_FILE.exists())
                content = mod._AUTOSTART_FILE.read_text()
                self.assertIn('TRCC Linux', content)
                self.assertIn('gui --resume', content)
                manager.disable()
                self.assertFalse(mod._AUTOSTART_FILE.exists())
            finally:
                mod._AUTOSTART_DIR = orig_dir
                mod._AUTOSTART_FILE = orig_file

    # --- get_exec ---

    @patch('shutil.which', return_value='/usr/bin/trcc')
    def test_get_trcc_exec_pip_installed(self, mock_which):
        result = LinuxAutostartManager.get_exec()
        self.assertEqual(result, '/usr/bin/trcc')

    @patch('shutil.which', return_value=None)
    def test_get_trcc_exec_fallback(self, mock_which):
        result = LinuxAutostartManager.get_exec()
        self.assertIn('python', result)
        # Fallback uses `python -m trcc.cli` (the CLI entrypoint module);
        # the older `trcc.ui.cli` name no longer exists.
        self.assertIn('trcc.cli', result)
        self.assertIn('PYTHONPATH=', result)

    # --- _desktop_entry ---

    def test_make_desktop_entry_format(self):
        """Desktop entry has correct XDG fields."""
        manager = self._manager()
        with patch.object(LinuxAutostartManager, 'get_exec', return_value='/usr/bin/trcc'):
            entry = manager._desktop_entry()
        self.assertIn('[Desktop Entry]', entry)
        self.assertIn('Type=Application', entry)
        self.assertIn('Name=TRCC Linux', entry)
        self.assertIn('Exec=/usr/bin/trcc gui --resume', entry)
        self.assertIn('Terminal=false', entry)
        self.assertIn('X-GNOME-Autostart-enabled=true', entry)

    # --- ensure (first launch) ---

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_DIR')
    @patch('trcc.legacy.conf.save_config')
    @patch('trcc.legacy.conf.load_config', return_value={})
    def test_ensure_autostart_first_launch(self, mock_load, mock_save,
                                            mock_dir, mock_file):
        mock_file.exists.return_value = False
        result = ensure_autostart(self._platform())
        self.assertTrue(result)
        mock_dir.mkdir.assert_called_once()
        mock_file.write_text.assert_called_once()
        mock_save.assert_called_once()
        self.assertTrue(mock_save.call_args[0][0]['autostart_configured'])

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_DIR')
    @patch('trcc.legacy.conf.save_config')
    @patch('trcc.legacy.conf.load_config', return_value={'other_key': 'val'})
    def test_ensure_autostart_first_launch_preserves_config(self, mock_load,
                                                             mock_save,
                                                             mock_dir, mock_file):
        mock_file.exists.return_value = False
        ensure_autostart(self._platform())
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved['other_key'], 'val')
        self.assertTrue(saved['autostart_configured'])

    # --- ensure (subsequent launch) ---

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    @patch('trcc.legacy.conf.save_config')
    @patch('trcc.legacy.conf.load_config', return_value={'autostart_configured': True})
    def test_ensure_autostart_subsequent_enabled(self, mock_load, mock_save, mock_file):
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = "old content"
        result = ensure_autostart(self._platform())
        self.assertTrue(result)
        mock_save.assert_not_called()

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    @patch('trcc.legacy.conf.save_config')
    @patch('trcc.legacy.conf.load_config', return_value={'autostart_configured': True})
    def test_ensure_autostart_subsequent_disabled(self, mock_load, mock_save, mock_file):
        mock_file.exists.return_value = False
        result = ensure_autostart(self._platform())
        self.assertFalse(result)

    # --- refresh (path change) ---

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    @patch('trcc.legacy.conf.save_config')
    @patch('trcc.legacy.conf.load_config', return_value={'autostart_configured': True})
    def test_ensure_autostart_refreshes_stale_path(self, mock_load, mock_save, mock_file):
        # Refresh-on-subsequent-launch goes through LinuxAutostartManager.refresh().
        # The GUI wrapper ``uc_about.ensure_autostart`` only calls
        # ``platform.autostart_enable()`` (first launch) or
        # ``platform.autostart_enabled()`` (subsequent) — neither path goes
        # through refresh().  Drive refresh() directly here.
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = '[Desktop Entry]\nExec=/old/path\n'
        with patch.object(LinuxAutostartManager, '_desktop_entry',
                          return_value='[Desktop Entry]\nExec=/new/path\n'):
            self._manager().refresh()
        mock_file.write_text.assert_called_once_with('[Desktop Entry]\nExec=/new/path\n')

    @patch(f'{_AUTOSTART_MOD}._AUTOSTART_FILE')
    @patch('trcc.legacy.conf.save_config')
    @patch('trcc.legacy.conf.load_config', return_value={'autostart_configured': True})
    def test_ensure_autostart_no_refresh_when_same(self, mock_load, mock_save, mock_file):
        mock_file.exists.return_value = True
        mock_file.read_text.return_value = '[Desktop Entry]\nExec=/same/path\n'
        with patch.object(LinuxAutostartManager, '_desktop_entry',
                          return_value='[Desktop Entry]\nExec=/same/path\n'):
            ensure_autostart(self._platform())
        mock_file.write_text.assert_not_called()


# ============================================================================
# detect_language
# ============================================================================

from trcc.legacy.conf import _detect_language  # noqa: E402
from trcc.legacy.core.models import LOCALE_TO_LANG  # noqa: E402


class TestDetectLanguage(unittest.TestCase):
    """Test language detection from locale."""

    def test_returns_string(self):
        lang = _detect_language()
        self.assertIsInstance(lang, str)

    def test_locale_mapping_keys(self):
        """All expected locales are mapped."""
        self.assertIn('en', LOCALE_TO_LANG)
        self.assertIn('zh_CN', LOCALE_TO_LANG)
        self.assertIn('de', LOCALE_TO_LANG)

    @patch('trcc.legacy.conf.locale')
    def test_english_locale(self, mock_locale):
        mock_locale.getlocale.return_value = ('en_US', 'UTF-8')
        self.assertEqual(_detect_language(), 'en')

    @patch('trcc.legacy.conf.locale')
    def test_chinese_locale(self, mock_locale):
        mock_locale.getlocale.return_value = ('zh_CN', 'UTF-8')
        self.assertEqual(_detect_language(), 'zh')

    @patch('trcc.legacy.conf.locale')
    def test_unknown_locale_defaults_to_en(self, mock_locale):
        mock_locale.getlocale.return_value = ('zz_ZZ', 'UTF-8')
        self.assertEqual(_detect_language(), 'en')

    @patch.dict('os.environ', {'LANG': ''})
    @patch('trcc.legacy.conf.locale')
    def test_none_locale_defaults_to_en(self, mock_locale):
        mock_locale.getlocale.return_value = (None, None)
        self.assertEqual(_detect_language(), 'en')


if __name__ == '__main__':
    unittest.main()
