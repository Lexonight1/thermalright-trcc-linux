"""Tests for gui/lcd_handler.py — LCDHandler lifecycle and routing.

Covers:
- Construction, properties, widget dict wiring
- apply_device_config: brightness, rotation, split mode restoration + restore_last_theme
- Theme selection: path-based, cloud, animated, persist flag
- Mask application: apply_mask, persist mask_path (mask restore logic tested in test_lcd_device.py)
- Video: play_pause, stop, seek, tick routing
- Overlay: on_overlay_changed, update_metrics, flash_element
- Display settings: set_brightness, set_rotation, set_split_mode
- Background toggle, screencast frame routing
- Slideshow: update state, tick, timer management
- Lifecycle: stop_timers, cleanup
"""
from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QTimer

# =========================================================================
# Construction
# =========================================================================


class TestConstruction:
    """LCDHandler construction and properties."""

    def test_stores_lcd(self, make_lcd_handler, mock_lcd_device):
        h = make_lcd_handler(lcd=mock_lcd_device)
        assert h.display is mock_lcd_device

    def test_device_key_starts_empty(self, lcd_handler):
        assert lcd_handler.device_key == ''

    def test_brightness_level_default(self, lcd_handler):
        assert lcd_handler.brightness_level == 100  # DEFAULT_BRIGHTNESS_LEVEL = 100%

    def test_split_mode_default(self, lcd_handler):
        assert lcd_handler.split_mode == 0

    def test_ldd_is_split_default_false(self, lcd_handler):
        assert lcd_handler.ldd_is_split is False

    def test_is_background_active_default_false(self, lcd_handler):
        assert lcd_handler.is_background_active is False

    def test_is_background_active_setter(self, lcd_handler):
        lcd_handler.is_background_active = True
        assert lcd_handler.is_background_active is True

    def test_three_timers_created(self, make_lcd_handler):
        calls = []
        def track_timer(cb, single_shot=False):
            calls.append((cb, single_shot))
            return MagicMock(spec=QTimer)
        make_lcd_handler(make_timer=track_timer)
        assert len(calls) == 3
        # Flash timer is single_shot
        assert calls[2][1] is True


# =========================================================================
# apply_device_config
# =========================================================================


class TestApplyDeviceConfig:
    """apply_device_config — restore brightness, rotation, split, theme."""

    def _device(self, resolution=(320, 320)):
        dev = MagicMock()
        dev.device_index = 0
        dev.vid = 0x0402
        dev.pid = 0x3922
        dev.resolution = resolution
        return dev

    @patch('trcc.gui.lcd_handler.Settings')
    def test_sets_device_key(self, mock_settings, lcd_handler):
        mock_settings.device_config_key.return_value = 'test_key'
        mock_settings.get_device_config.return_value = {}
        lcd_handler.apply_device_config(self._device(), 320, 320)
        assert lcd_handler.device_key == 'test_key'

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restores_brightness(self, mock_settings, lcd_handler, mock_lcd_device):
        """Stored percent value restored directly — no level mapping."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {'brightness_level': 50}
        lcd_handler.apply_device_config(self._device(), 320, 320)
        assert lcd_handler.brightness_level == 50
        mock_lcd_device.set_brightness.assert_called_with(50)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restores_rotation(self, mock_settings, make_lcd_handler, mock_lcd_device):
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {'rotation': 90}
        h = make_lcd_handler()
        h.apply_device_config(self._device(), 320, 320)
        mock_lcd_device.set_rotation.assert_called_with(90)
        h._w['rotation_combo'].setCurrentIndex.assert_called_with(1)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_resolution_change_updates_widgets(self, mock_settings, make_lcd_handler, mock_lcd_device):
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        # Device already configured at 480x480 from connect() — orientation matches
        from trcc.core.orientation import Orientation
        mock_lcd_device.orientation = Orientation(480, 480)
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(self._device(), 480, 480)
        h._w['preview'].set_resolution.assert_called_with(480, 480)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_widgets_always_updated(self, mock_settings, lcd_handler):
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        lcd_handler.apply_device_config(self._device(), 320, 320)
        lcd_handler._w['preview'].set_resolution.assert_called_with(320, 320)
        lcd_handler._w['image_cut'].set_resolution.assert_called_with(320, 320)
        lcd_handler._w['video_cut'].set_resolution.assert_called_with(320, 320)
        lcd_handler._w['theme_setting'].set_resolution.assert_called_with(320, 320)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_split_mode_restored_for_split_resolution(self, mock_settings, make_lcd_handler, mock_lcd_device):
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {'split_mode': 1}
        mock_lcd_device.lcd_size = (320, 320)  # different from target to trigger change
        h = make_lcd_handler(lcd=mock_lcd_device)
        # 1600x720 is the split resolution (SPLIT_MODE_RESOLUTIONS)
        h.apply_device_config(self._device((1600, 720)), 1600, 720)
        assert h.ldd_is_split is True


# =========================================================================
# reactivate — refresh shared widgets on device switch
# =========================================================================


class TestReactivate:
    """reactivate() — refresh preview, theme dirs, overlay on device switch."""

    @patch('trcc.gui.lcd_handler.Settings')
    def test_updates_all_widget_resolutions(self, mock_settings,
                                            make_lcd_handler, mock_lcd_device):
        mock_settings.get_device_config.return_value = {}
        from trcc.core.orientation import Orientation
        mock_lcd_device.orientation = Orientation(480, 480)
        h = make_lcd_handler()
        h._device_key = 'k'
        h.reactivate(480, 480)
        h._w['preview'].set_resolution.assert_called_with(480, 480)
        h._w['image_cut'].set_resolution.assert_called_with(480, 480)
        h._w['video_cut'].set_resolution.assert_called_with(480, 480)
        h._w['theme_setting'].set_resolution.assert_called_with(480, 480)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_sets_preview_image(self, mock_settings,
                                make_lcd_handler, mock_lcd_device):
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.current_image = 'fake_image'
        h = make_lcd_handler(lcd=mock_lcd_device)
        h._device_key = 'k'
        h.reactivate(320, 320)
        h._w['preview'].set_image.assert_called_with('fake_image')

    @patch('trcc.gui.lcd_handler.Settings')
    def test_clears_preview_when_no_image(self, mock_settings,
                                          make_lcd_handler, mock_lcd_device):
        """No saved theme and no current image → preview cleared."""
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.current_image = None
        h = make_lcd_handler(lcd=mock_lcd_device)
        h._device_key = 'k'
        h.reactivate(320, 320)
        h._w['preview'].set_image.assert_called_with(None)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_reloads_overlay_from_config(self, mock_settings,
                                         make_lcd_handler, mock_lcd_device):
        overlay_cfg = {'config': {'time': {'x': 10}}, 'enabled': True}
        mock_settings.get_device_config.return_value = {'overlay': overlay_cfg}
        mock_lcd_device.current_image = 'img'
        h = make_lcd_handler(lcd=mock_lcd_device)
        h._device_key = 'k'
        h.reactivate(320, 320)
        h._w['theme_setting'].load_from_overlay_config.assert_called_with(
            {'time': {'x': 10}})
        h._w['theme_setting'].set_overlay_enabled.assert_called_with(True)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_disabled_when_no_device_key(self, mock_settings,
                                                  make_lcd_handler):
        h = make_lcd_handler()
        h._device_key = ''
        h.reactivate(320, 320)
        h._w['theme_setting'].set_overlay_enabled.assert_called_with(False)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_disabled_on_missing_config(self, mock_settings,
                                                 make_lcd_handler):
        mock_settings.get_device_config.return_value = {}
        h = make_lcd_handler()
        h._device_key = 'k'
        h.reactivate(320, 320)
        h._w['theme_setting'].set_overlay_enabled.assert_called_with(False)


# =========================================================================
# _update_theme_directories — first-install auto-load guard
# =========================================================================


class TestUpdateThemeDirectories:
    """_update_theme_directories first-install auto-load and skip-if-saved-theme guard."""

    def _make_data_root(self, tmp_path: Path, w: int = 320, h: int = 320) -> Path:
        """Create a data root with one valid theme subfolder under theme{w}{h}."""
        theme_root = tmp_path / f'theme{w}{h}'
        theme1 = theme_root / 'Theme1'
        theme1.mkdir(parents=True)
        (theme1 / '00.png').touch()
        return tmp_path

    @patch('trcc.gui.lcd_handler.Settings')
    def test_auto_loads_first_theme_on_first_install(self, mock_settings, make_lcd_handler, mock_lcd_device, tmp_path):
        """With no current image and no saved theme_path, auto-loads the first theme folder."""
        data_root = self._make_data_root(tmp_path)
        from trcc.core.orientation import Orientation
        o = Orientation(320, 320)
        o.data_root = data_root
        mock_lcd_device.orientation = o

        mock_settings.get_device_config.return_value = {}  # no saved theme_path

        mock_lcd_device.current_image = None  # no image loaded yet

        with patch('trcc.gui.lcd_handler.ThemeInfo') as mock_ti:
            mock_ti.from_directory.return_value = MagicMock()
            h = make_lcd_handler(lcd=mock_lcd_device)
            h._device_key = 'dev0'
            h._update_theme_directories()

        mock_lcd_device.select.assert_called(), "Should auto-load first theme on first install"

    @patch('trcc.gui.lcd_handler.Settings')
    def test_skips_auto_load_when_saved_theme_path_exists(self, mock_settings, make_lcd_handler, mock_lcd_device, tmp_path):
        """With no current image but a saved theme_path (legacy), skips auto-load."""
        data_root = self._make_data_root(tmp_path)
        mock_lcd_device.orientation.data_root = data_root

        # Legacy config key — must still guard against auto-load
        mock_settings.get_device_config.return_value = {'theme_path': '/themes/MyTheme'}

        mock_lcd_device.current_image = None

        h = make_lcd_handler(lcd=mock_lcd_device)
        h._device_key = 'dev0'
        mock_lcd_device.select.reset_mock()
        h._update_theme_directories()

        mock_lcd_device.select.assert_not_called(), \
            "Must not auto-load theme1 when user already has a saved theme_path"

    @patch('trcc.gui.lcd_handler.Settings')
    def test_skips_auto_load_when_saved_theme_name_exists(self, mock_settings, make_lcd_handler, mock_lcd_device, tmp_path):
        """With no current image but a saved theme_name (current format), skips auto-load."""
        data_root = self._make_data_root(tmp_path)
        mock_lcd_device.orientation.data_root = data_root

        # Current config format — theme_name, not theme_path
        mock_settings.get_device_config.return_value = {
            'theme_name': 'MyTheme', 'theme_type': 'local',
        }

        mock_lcd_device.current_image = None

        h = make_lcd_handler(lcd=mock_lcd_device)
        h._device_key = 'dev0'
        mock_lcd_device.select.reset_mock()
        h._update_theme_directories()

        mock_lcd_device.select.assert_not_called(), \
            "Must not auto-load theme1 when user already has a saved theme_name"

    @patch('trcc.gui.lcd_handler.Settings')
    def test_skips_auto_load_when_image_already_showing(self, mock_settings, make_lcd_handler, mock_lcd_device, tmp_path):
        """With a current image already loaded, skips auto-load regardless of saved config."""
        data_root = self._make_data_root(tmp_path)
        mock_lcd_device.orientation.data_root = data_root
        mock_settings.get_device_config.return_value = {}

        mock_lcd_device.current_image = MagicMock()  # image already showing

        h = make_lcd_handler(lcd=mock_lcd_device)
        h._device_key = 'dev0'
        mock_lcd_device.select.reset_mock()
        h._update_theme_directories()

        mock_lcd_device.select.assert_not_called(), \
            "Must not auto-load when current_image is already set"


# =========================================================================
# Theme selection
# =========================================================================


class TestThemeSelection:
    """select_theme_from_path, select_cloud_theme — routing + state."""

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_select_theme_from_path_calls_select(self, mock_ti, mock_settings, lcd_handler, mock_lcd_device):
        mock_ti.from_directory.return_value = MagicMock()
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)

        lcd_handler.select_theme_from_path(path)
        mock_lcd_device.select.assert_called()

    def test_select_theme_nonexistent_path_noop(self, lcd_handler, mock_lcd_device):
        path = MagicMock(spec=Path)
        path.exists.return_value = False
        mock_lcd_device.select.reset_mock()
        lcd_handler.select_theme_from_path(path)
        mock_lcd_device.select.assert_not_called()

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_select_theme_stops_video(self, mock_ti, mock_settings, lcd_handler, mock_lcd_device):
        """Theme selection calls stop() on the lcd device."""
        mock_ti.from_directory.return_value = MagicMock()
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)

        lcd_handler.select_theme_from_path(path)
        mock_lcd_device.stop.assert_called()

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_select_theme_persists_path(self, mock_ti, mock_settings, lcd_handler):
        mock_ti.from_directory.return_value = MagicMock()
        lcd_handler._device_key = 'dev0'
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)
        path.__str__ = lambda self: '/themes/TestTheme'

        lcd_handler.select_theme_from_path(path, persist=True)
        mock_settings.save_device_settings.assert_any_call(
            'dev0', theme_name=path.name, theme_type='local', mask_id='')

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_select_theme_no_persist(self, mock_ti, mock_settings, lcd_handler):
        mock_ti.from_directory.return_value = MagicMock()
        lcd_handler._device_key = 'dev0'
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)

        lcd_handler.select_theme_from_path(path, persist=False)
        mock_settings.save_device_setting.assert_not_called()

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_no_double_send_when_overlay_follows(self, mock_ti, mock_settings, lcd_handler, mock_lcd_device):
        """select_theme_from_path must not call send() when
        overlay config will follow — avoids double-send blink on theme switch."""
        mock_ti.from_directory.return_value = MagicMock()
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)
        mock_lcd_device.load_overlay_config_from_dir.return_value = None

        lcd_handler.select_theme_from_path(path)

        mock_lcd_device.send.assert_not_called(), (
            "send() must not fire when overlay_config=True — "
            "_load_theme_overlay_config owns the single send to avoid blink"
        )

    @patch('trcc.gui.lcd_handler.Settings')
    @patch('trcc.gui.lcd_handler.ThemeInfo')
    def test_single_render_and_send_on_theme_switch(self, mock_ti, mock_settings, lcd_handler, mock_lcd_device):
        """Exactly one render_and_send on a normal theme switch with overlay config."""
        mock_ti.from_directory.return_value = MagicMock()
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.__truediv__ = lambda self, x: MagicMock(exists=lambda: False)
        mock_lcd_device.load_overlay_config_from_dir.return_value = {'elements': []}

        lcd_handler.select_theme_from_path(path)

        mock_lcd_device.render_and_send.assert_called_once()
        mock_lcd_device.send.assert_not_called()



# =========================================================================
# Mask
# =========================================================================


class TestMask:
    """apply_mask — loads mask, updates preview, persists."""

    @patch('trcc.gui.lcd_handler.Settings')
    def test_apply_mask_with_path(self, mock_settings, lcd_handler, mock_lcd_device):
        lcd_handler._device_key = 'dev0'
        mask_info = MagicMock()
        mask_info.path = '/masks/01'
        mask_info.is_custom = False
        mock_lcd_device.load_mask_standalone.return_value = {
            'success': True, 'image': MagicMock()}
        lcd_handler.apply_mask(mask_info)
        mock_lcd_device.load_mask_standalone.assert_called()
        mock_settings.save_device_settings.assert_any_call(
            'dev0', mask_id='01', mask_custom=False)

    def test_apply_mask_no_path_sets_status(self, lcd_handler):
        mask_info = MagicMock()
        mask_info.path = None
        mask_info.name = "Empty"
        lcd_handler.apply_mask(mask_info)
        lcd_handler._w['preview'].set_status.assert_called_once()

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_dispatches_restore_last_theme(self, mock_settings, make_lcd_handler, mock_lcd_device):
        """apply_device_config calls restore_last_theme — shared path with CLI/API."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.restore_last_theme.return_value = {'success': False, 'error': 'No saved theme'}
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        mock_lcd_device.restore_last_theme.assert_called()

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_skipped_when_auto_loaded(self, mock_settings, make_lcd_handler, mock_lcd_device):
        """apply_device_config skips restore_last_theme when first-install auto-load
        already loaded a theme (_update_theme_directories returned True)."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        h = make_lcd_handler(lcd=mock_lcd_device)
        h._update_theme_directories = MagicMock(return_value=True)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        mock_lcd_device.restore_last_theme.assert_not_called()

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_updates_preview_on_success(self, mock_settings, make_lcd_handler, mock_lcd_device):
        """apply_device_config updates preview widget when restore_last_theme succeeds."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        img = MagicMock()
        mock_lcd_device.restore_last_theme.return_value = {
            'success': True, 'image': img, 'is_animated': False,
            'overlay_config': None, 'overlay_enabled': False,
        }
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        h._w['preview'].set_image.assert_called_with(img, fast=False)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_starts_animation_timer_for_video_theme(self, mock_settings, make_lcd_handler, mock_lcd_device):
        """apply_device_config starts animation timer when restoring a video theme."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.playing = True
        img = MagicMock()
        mock_lcd_device.restore_last_theme.return_value = {
            'success': True, 'image': img, 'is_animated': True,
            'overlay_config': None, 'overlay_enabled': False,
        }
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        h._animation_timer.start.assert_called_with(33)  # lcd.interval = 33
        h._w['preview'].set_playing.assert_called_with(True)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_restore_no_animation_timer_for_static_theme(self, mock_settings, make_lcd_handler, mock_lcd_device):
        """apply_device_config does NOT start animation timer for a static theme."""
        mock_settings.device_config_key.return_value = 'k'
        mock_settings.get_device_config.return_value = {}
        mock_lcd_device.playing = False
        img = MagicMock()
        mock_lcd_device.restore_last_theme.return_value = {
            'success': True, 'image': img, 'is_animated': False,
            'overlay_config': None, 'overlay_enabled': True,
        }
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.apply_device_config(
            MagicMock(device_index=0, vid=0x0402, pid=0x3922), 320, 320)
        h._animation_timer.start.assert_not_called()


# =========================================================================
# Video
# =========================================================================


class TestVideo:
    """play_pause, stop, seek, tick."""

    def test_play_pause_toggles(self, make_lcd_handler, mock_lcd_device):
        """play_pause calls pause(); result state drives timer."""
        mock_lcd_device.pause.return_value = {'state': 'playing', 'success': True}
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.play_pause()
        mock_lcd_device.pause.assert_called()
        h._w['preview'].set_playing.assert_called_with(True)
        h._animation_timer.start.assert_called_with(33)  # lcd.interval = 33

    def test_play_pause_pauses(self, make_lcd_handler, mock_lcd_device):
        """play_pause calls pause(); paused state stops timer."""
        mock_lcd_device.pause.return_value = {'state': 'paused', 'success': True}
        h = make_lcd_handler(lcd=mock_lcd_device)
        h.play_pause()
        h._w['preview'].set_playing.assert_called_with(False)
        h._animation_timer.stop.assert_called()

    def test_stop_video_calls_stop(self, lcd_handler, mock_lcd_device):
        """stop_video calls stop() on the LCD device."""
        lcd_handler.stop_video()
        mock_lcd_device.stop.assert_called()
        lcd_handler._animation_timer.stop.assert_called()
        lcd_handler._w['preview'].set_playing.assert_called_with(False)
        lcd_handler._w['preview'].show_video_controls.assert_called_with(False)

    def test_seek_calls_seek(self, lcd_handler, mock_lcd_device):
        """seek calls seek() with correct percent."""
        lcd_handler.seek(50.0)
        mock_lcd_device.seek.assert_called_with(50.0)


# =========================================================================
# Overlay
# =========================================================================


class TestOverlay:
    """on_overlay_changed, update_metrics, flash_element."""

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_changed_dispatches_enable_and_config(self, mock_settings, lcd_handler, mock_lcd_device):
        lcd_handler._device_key = 'dev0'
        lcd_handler._lcd.enabled = False  # overlay disabled — should trigger enable_overlay
        lcd_handler._w['theme_setting'].overlay_grid.overlay_enabled = True
        data = {'elements': []}
        lcd_handler.on_overlay_changed(data)
        mock_lcd_device.enable_overlay.assert_called()
        mock_lcd_device.set_config.assert_called_with(data)

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_changed_empty_data_noop(self, mock_settings, lcd_handler, mock_lcd_device):
        mock_lcd_device.set_config.reset_mock()
        lcd_handler.on_overlay_changed({})
        mock_lcd_device.set_config.assert_not_called()

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_changed_none_data_noop(self, mock_settings, lcd_handler, mock_lcd_device):
        mock_lcd_device.set_config.reset_mock()
        lcd_handler.on_overlay_changed(None)
        mock_lcd_device.set_config.assert_not_called()

    @patch('trcc.gui.lcd_handler.Settings')
    def test_overlay_changed_persists(self, mock_settings, lcd_handler):
        lcd_handler._device_key = 'dev0'
        lcd_handler._w['theme_setting'].overlay_grid.overlay_enabled = True
        data = {'elements': [{'type': 'text'}]}
        lcd_handler.on_overlay_changed(data)
        mock_settings.save_device_setting.assert_called_once()
        saved = mock_settings.save_device_setting.call_args[0]
        assert saved[0] == 'dev0'
        assert saved[1] == 'overlay'

    def test_overlay_tick_no_overlay_no_send(self, lcd_handler, mock_lcd_device):
        """update_metrics must NOT send for static no-overlay themes."""
        lcd_handler._lcd.playing = False
        lcd_handler._lcd.enabled = False
        lcd_handler._lcd.connected = True
        lcd_handler.update_metrics(MagicMock())
        mock_lcd_device.render_and_send.assert_not_called()
        mock_lcd_device.rebuild_video_cache.assert_not_called()

    def test_overlay_tick_overlay_enabled_no_send(self, lcd_handler, mock_lcd_device):
        """update_metrics must NOT send when overlay is enabled."""
        lcd_handler._lcd.playing = False
        lcd_handler._lcd.enabled = True
        lcd_handler._lcd.connected = True
        lcd_handler.update_metrics(MagicMock())
        mock_lcd_device.render_and_send.assert_not_called(), (
            "update_metrics must not send when overlay is enabled — "
            "tick() owns the send to avoid double-send blink"
        )
        mock_lcd_device.rebuild_video_cache.assert_not_called()

    def test_overlay_tick_noop_when_disconnected(self, lcd_handler, mock_lcd_device):
        """update_metrics does nothing when device is not connected."""
        lcd_handler._lcd.playing = False
        lcd_handler._lcd.connected = False
        lcd_handler.update_metrics(MagicMock())
        mock_lcd_device.render_and_send.assert_not_called()
        mock_lcd_device.rebuild_video_cache.assert_not_called()

    def test_update_preview_sets_preview_image(self, lcd_handler):
        """update_preview mirrors a frame rendered by tick() to the preview widget."""
        image = MagicMock()
        lcd_handler.update_preview(image)
        lcd_handler._w['preview'].set_image.assert_called_once_with(image)

    def test_overlay_tick_during_video_dispatches_update_cache_text(self, lcd_handler, mock_lcd_device):
        """update_metrics while video plays calls rebuild_video_cache directly."""
        lcd_handler._lcd.enabled = True
        lcd_handler._lcd.playing = True
        metrics = MagicMock()
        lcd_handler.update_metrics(metrics)
        mock_lcd_device.update_video_cache_text.assert_called_with(metrics)

    def test_overlay_tick_video_no_render_and_send(self, lcd_handler, mock_lcd_device):
        """update_metrics while video plays must not call render_and_send."""
        lcd_handler._lcd.enabled = True
        lcd_handler._lcd.playing = True
        lcd_handler.update_metrics(MagicMock())
        mock_lcd_device.render_and_send.assert_not_called()

    def test_flash_element_calls_set_flash_index(self, lcd_handler, mock_lcd_device):
        """flash_element calls set_flash_index with the correct index."""
        lcd_handler.flash_element(3)
        mock_lcd_device.set_flash_index.assert_called_with(3)
        lcd_handler._flash_timer.start.assert_called_with(980)

    def test_flash_timeout_clears_flash_index(self, lcd_handler, mock_lcd_device):
        """_on_flash_timeout calls set_flash_index(-1)."""
        lcd_handler._on_flash_timeout()
        mock_lcd_device.set_flash_index.assert_called_with(-1)

    def test_update_mask_position_calls_method(self, lcd_handler, mock_lcd_device):
        """update_mask_position calls set_mask_position."""
        lcd_handler.update_mask_position(10, 20)
        mock_lcd_device.set_mask_position.assert_called_with(10, 20)


# =========================================================================
# Display settings
# =========================================================================


class TestDisplaySettings:
    """set_brightness, set_rotation, set_split_mode."""

    def test_set_brightness_updates_level(self, lcd_handler):
        lcd_handler._lcd.set_brightness.return_value = {'success': True, 'image': MagicMock()}
        lcd_handler.set_brightness(50)
        assert lcd_handler.brightness_level == 50

    def test_set_brightness_updates_preview(self, lcd_handler):
        img = MagicMock()
        lcd_handler._lcd.set_brightness.return_value = {'success': True, 'image': img}
        lcd_handler.set_brightness(3)
        lcd_handler._w['preview'].set_image.assert_called_with(img)

    def test_set_brightness_sends_frame_if_auto_send(self, lcd_handler, mock_lcd_device):
        """set_brightness calls send() when auto_send is on."""
        img = MagicMock()
        lcd_handler._lcd.set_brightness.return_value = {'success': True, 'image': img}
        lcd_handler._lcd.auto_send = True
        lcd_handler.set_brightness(2)
        mock_lcd_device.send.assert_called_with(img)

    def test_set_brightness_no_send_if_no_auto(self, lcd_handler, mock_lcd_device):
        img = MagicMock()
        lcd_handler._lcd.set_brightness.return_value = {'success': True, 'image': img}
        lcd_handler._lcd.auto_send = False
        lcd_handler.set_brightness(2)
        mock_lcd_device.send.assert_not_called()

    def test_set_rotation_delegates_to_device(self, lcd_handler):
        lcd_handler._lcd.set_rotation.return_value = {'success': True, 'image': MagicMock()}
        lcd_handler.set_rotation(90)
        lcd_handler._lcd.set_rotation.assert_called_with(90)

    def test_set_rotation_updates_cloud_widgets(self, lcd_handler):
        lcd_handler._lcd.set_rotation.return_value = {'success': True}
        lcd_handler.set_rotation(270)
        lcd_handler._w['theme_web'].set_resolution.assert_called()
        lcd_handler._w['theme_mask'].set_resolution.assert_called()

    def test_rotation_reloads_cloud_theme_when_cached(self, lcd_handler, mock_lcd_device, tmp_path):
        """Non-square device with cloud theme: rotation loads orientation-matched video."""
        from trcc.core.orientation import Orientation
        mock_lcd_device.set_rotation.return_value = {'success': True}
        # Real Orientation so web_dir derives correctly
        o = Orientation(1280, 480)
        o.data_root = tmp_path
        o.rotation = 90
        mock_lcd_device.orientation = o
        # Simulate active cloud theme
        mock_lcd_device.current_theme_path = Path('/web/1280480/a007.mp4')
        # Portrait video already exists at derived path
        portrait_dir = tmp_path / 'web' / '4801280'
        portrait_dir.mkdir(parents=True)
        (portrait_dir / 'a007.mp4').write_bytes(b'fake')

        mock_lcd_device.select.reset_mock()
        lcd_handler.set_rotation(90)
        mock_lcd_device.select.assert_called_once()

    def test_rotation_skips_cloud_reload_for_square(self, lcd_handler, mock_lcd_device):
        """Square device: rotation never triggers cloud theme reload."""
        mock_lcd_device.set_rotation.return_value = {'success': True}
        mock_lcd_device.orientation.native = (320, 320)
        mock_lcd_device.current_theme_path = Path('/web/320320/a007.mp4')

        mock_lcd_device.select.reset_mock()
        lcd_handler.set_rotation(90)
        mock_lcd_device.select.assert_not_called()

    def test_rotation_skips_cloud_reload_for_local_theme(self, lcd_handler, mock_lcd_device):
        """Local theme (no .mp4): rotation doesn't trigger cloud download."""
        mock_lcd_device.set_rotation.return_value = {'success': True}
        mock_lcd_device.orientation.native = (1280, 480)
        mock_lcd_device.current_theme_path = Path('/themes/Theme1')

        mock_lcd_device.select.reset_mock()
        lcd_handler.set_rotation(90)
        mock_lcd_device.select.assert_not_called()

    def test_set_split_mode_updates_state(self, lcd_handler):
        lcd_handler._lcd.set_split_mode.return_value = {'success': True, 'image': MagicMock()}
        lcd_handler.set_split_mode(2)
        assert lcd_handler.split_mode == 2
        lcd_handler._lcd.set_split_mode.assert_called_with(2)


# =========================================================================
# Background / Screencast
# =========================================================================


class TestBackgroundScreencast:
    """on_background_toggle, on_screencast_frame."""

    def test_background_toggle_on_stops_video(self, lcd_handler, mock_lcd_device):
        """Background toggle calls stop() on the LCD device."""
        lcd_handler.on_background_toggle(True)
        assert lcd_handler.is_background_active is True
        lcd_handler._animation_timer.stop.assert_called()
        mock_lcd_device.stop.assert_called()

    def test_background_toggle_off(self, lcd_handler):
        lcd_handler.on_background_toggle(False)
        assert lcd_handler.is_background_active is False

    def test_screencast_frame_calls_send(self, lcd_handler, mock_lcd_device):
        """on_screencast_frame calls send()."""
        img = MagicMock()
        lcd_handler.on_screencast_frame(img)
        lcd_handler._w['preview'].set_image.assert_called_with(img)
        mock_lcd_device.send.assert_called_with(img)


# =========================================================================
# Save / Export / Import
# =========================================================================


class TestThemeIO:
    """save_theme, export_config, import_config."""

    @patch('trcc.gui.lcd_handler.Settings')
    def test_save_theme_success(self, mock_settings_cls, lcd_handler, mock_lcd_device, tmp_path):
        # Set data_root so orientation.theme_dir returns a valid ThemeDir
        mock_lcd_device.orientation.data_root = tmp_path
        mock_lcd_device.current_theme_path = Path('/themes/Custom_MyTheme')
        lcd_handler._device_key = 'dev0'
        lcd_handler.save_theme("MyTheme")
        mock_lcd_device.save.assert_called()
        lcd_handler._w['preview'].set_status.assert_called_with('Saved')
        mock_settings_cls.save_device_setting.assert_any_call(
            'dev0', 'theme_name', 'Custom_MyTheme')
        mock_settings_cls.save_device_setting.assert_any_call(
            'dev0', 'theme_type', 'local')

    def test_export_config(self, lcd_handler, mock_lcd_device):
        lcd_handler.export_config(Path('/out/theme.tr'))
        mock_lcd_device.export_config.assert_called()

    def test_import_config_success_reloads(self, lcd_handler, mock_lcd_device, tmp_path):
        # Set data_root so orientation.theme_dir returns a valid ThemeDir
        mock_lcd_device.orientation.data_root = tmp_path
        lcd_handler.import_config(Path('/in/theme.tr'))
        mock_lcd_device.import_config.assert_called()
        lcd_handler._w['theme_local'].set_theme_directory.assert_called_once()
        lcd_handler._w['theme_local'].load_themes.assert_called_once()


# =========================================================================
# Render
# =========================================================================


class TestRender:
    """render_and_preview, _render_and_send."""

    def test_render_and_preview_returns_image(self, lcd_handler):
        """render_and_preview calls lcd.render() directly (read-only)."""
        img = MagicMock()
        lcd_handler._lcd.render.return_value = {'image': img}
        result = lcd_handler.render_and_preview()
        assert result is img
        lcd_handler._w['preview'].set_image.assert_called_with(img)

    def test_render_and_preview_no_image(self, lcd_handler):
        lcd_handler._lcd.render.return_value = {}
        result = lcd_handler.render_and_preview()
        assert result is None

    def test_render_and_send_calls_method(self, lcd_handler, mock_lcd_device):
        """_render_and_send calls render_and_send and updates preview."""
        img = MagicMock()
        mock_lcd_device.render_and_send.return_value = {'success': True, 'image': img}
        lcd_handler._ui_active = True
        lcd_handler._w['preview'].isVisible.return_value = True
        lcd_handler._render_and_send()
        mock_lcd_device.render_and_send.assert_called()
        lcd_handler._w['preview'].set_image.assert_called_with(img)


# =========================================================================
# Lifecycle
# =========================================================================


class TestLifecycle:
    """deactivate, cleanup."""

    def test_deactivate(self, lcd_handler):
        lcd_handler._lcd.playing = False
        lcd_handler.deactivate()
        lcd_handler._animation_timer.stop.assert_called()
        lcd_handler._slideshow_timer.stop.assert_called()

    def test_deactivate_keeps_video_timer_running(self, lcd_handler):
        """Switching away should not pause physical video playback on other LCDs."""
        lcd_handler._lcd.playing = True
        lcd_handler.deactivate()
        lcd_handler._animation_timer.stop.assert_not_called()
        lcd_handler._slideshow_timer.stop.assert_called()

    def test_restore_inactive_state_starts_video_timer(self, make_lcd_handler, mock_lcd_device):
        """Inactive LCDs should start their animation timer after restoring an animated theme."""
        mock_lcd_device.connected = True
        mock_lcd_device.playing = True
        mock_lcd_device.interval = 40
        mock_lcd_device.restore_last_theme.return_value = {
            'success': True,
            'is_animated': True,
        }
        h = make_lcd_handler(lcd=mock_lcd_device)
        h._animation_timer.isActive.return_value = False

        h.restore_inactive_state()

        h._animation_timer.start.assert_called_with(40)

    def test_cleanup_calls_stop(self, lcd_handler, mock_lcd_device):
        """cleanup calls stop() — ensures video state is consistent."""
        lcd_handler.cleanup()
        lcd_handler._animation_timer.stop.assert_called()
        lcd_handler._slideshow_timer.stop.assert_called()
        lcd_handler._flash_timer.stop.assert_called()
        mock_lcd_device.stop.assert_called()
        mock_lcd_device.cleanup.assert_called()
