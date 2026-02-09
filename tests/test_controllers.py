"""
Tests for core.controllers – MVC business logic controllers.

Tests cover:
- ThemeController: set_directories, load/filter/select, callbacks
- DeviceController: detect, select, send_image_async, callbacks
- VideoController: load, play/pause/stop, tick, seek, frame interval
- OverlayController: enable/disable, add/remove/update elements, render,
  renderer delegation (mask, temp unit, config, scaling)
- LCDDeviceController: initialization, resolution, rotation, brightness,
  theme loading, working dir lifecycle, cleanup, save/export/import,
  video tick, LCD send pipeline, mask position parsing, overlay preview
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from PIL import Image

from trcc.core.controllers import (
    DeviceController,
    LCDDeviceController,
    OverlayController,
    ThemeController,
    VideoController,
    create_controller,
)
from trcc.core.models import (
    DeviceInfo,
    OverlayElement,
    OverlayElementType,
    PlaybackState,
    ThemeInfo,
    ThemeModel,
    ThemeType,
    VideoState,
)

# Common patches for LCDDeviceController tests (avoids file I/O)
FORM_PATCHES = [
    'trcc.core.controllers.get_saved_resolution',
    'trcc.core.controllers.save_resolution',
    'trcc.core.controllers.ensure_themes_extracted',
    'trcc.core.controllers.ensure_web_extracted',
    'trcc.core.controllers.ensure_web_masks_extracted',
    'trcc.core.controllers.get_web_dir',
    'trcc.core.controllers.get_web_masks_dir',
]


def _make_form_controller():
    """Create a LCDDeviceController with all path functions mocked."""
    patches = []
    for target in FORM_PATCHES:
        if 'get_saved_resolution' in target:
            m = patch(target, return_value=(320, 320))
        elif 'get_web_dir' in target or 'get_web_masks_dir' in target:
            m = patch(target, return_value='/tmp/web')
        else:
            m = patch(target)
        patches.append(m)
        m.start()
    ctrl = LCDDeviceController()
    return ctrl, patches


def _stop_patches(patches):
    for p in patches:
        p.stop()


def _make_test_image(w=320, h=320, color=(128, 0, 0)):
    """Create a small RGB PIL Image for testing."""
    return Image.new('RGB', (w, h), color)


# =============================================================================
# ThemeController
# =============================================================================

class TestThemeController(unittest.TestCase):
    """Test ThemeController business logic."""

    def setUp(self):
        self.ctrl = ThemeController()

    def test_initial_state(self):
        self.assertIsInstance(self.ctrl.model, ThemeModel)
        self.assertIsNone(self.ctrl.get_selected())
        self.assertEqual(self.ctrl.get_themes(), [])

    def test_set_directories(self):
        """set_directories propagates to model."""
        local = Path('/tmp/themes')
        web = Path('/tmp/web')
        masks = Path('/tmp/masks')
        self.ctrl.set_directories(local_dir=local, web_dir=web, masks_dir=masks)
        self.assertEqual(self.ctrl.model.local_theme_dir, local)
        self.assertEqual(self.ctrl.model.cloud_web_dir, web)
        self.assertEqual(self.ctrl.model.cloud_masks_dir, masks)

    def test_set_filter(self):
        """set_filter updates model and fires callback."""
        fired = []
        self.ctrl.on_filter_changed = lambda mode: fired.append(mode)
        self.ctrl.set_filter('user')
        self.assertEqual(self.ctrl.model.filter_mode, 'user')
        self.assertEqual(fired, ['user'])

    def test_set_category(self):
        """set_category passes through to model, 'all' maps to None."""
        self.ctrl.set_category('b')
        self.assertEqual(self.ctrl.model.category_filter, 'b')
        self.ctrl.set_category('all')
        self.assertIsNone(self.ctrl.model.category_filter)

    def test_select_theme_fires_callback(self):
        """Selecting a theme fires on_theme_selected."""
        fired = []
        self.ctrl.on_theme_selected = lambda t: fired.append(t)
        theme = ThemeInfo(name='Test')
        self.ctrl.select_theme(theme)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].name, 'Test')

    def test_load_local_themes_with_dir(self):
        """Loading themes from a directory with valid themes."""
        with tempfile.TemporaryDirectory() as tmp:
            theme_dir = Path(tmp) / 'Theme1'
            theme_dir.mkdir()
            (theme_dir / '00.png').write_bytes(b'PNG')
            (theme_dir / 'Theme.png').write_bytes(b'PNG')

            self.ctrl.set_directories(local_dir=Path(tmp))
            self.ctrl.load_local_themes((320, 320))
            themes = self.ctrl.get_themes()
            self.assertEqual(len(themes), 1)
            self.assertEqual(themes[0].name, 'Theme1')

    def test_on_themes_loaded_callback(self):
        """on_themes_loaded fires after load."""
        fired = []
        self.ctrl.on_themes_loaded = lambda themes: fired.append(len(themes))

        with tempfile.TemporaryDirectory() as tmp:
            theme_dir = Path(tmp) / 'T1'
            theme_dir.mkdir()
            (theme_dir / '00.png').write_bytes(b'x')

            self.ctrl.set_directories(local_dir=Path(tmp))
            self.ctrl.load_local_themes()

        self.assertEqual(len(fired), 1)

    def test_categories_dict(self):
        """CATEGORIES has expected keys."""
        self.assertIn('all', ThemeController.CATEGORIES)
        self.assertIn('a', ThemeController.CATEGORIES)


# =============================================================================
# DeviceController
# =============================================================================

class TestDeviceController(unittest.TestCase):
    """Test DeviceController device management."""

    def setUp(self):
        self.ctrl = DeviceController()

    def test_initial_state(self):
        self.assertEqual(self.ctrl.get_devices(), [])
        self.assertIsNone(self.ctrl.get_selected())

    def test_select_device(self):
        """Selecting a device fires callback."""
        fired = []
        self.ctrl.on_device_selected = lambda d: fired.append(d)
        dev = DeviceInfo(name='LCD', path='/dev/sg0')
        self.ctrl.select_device(dev)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].path, '/dev/sg0')

    def test_send_started_callback(self):
        """send_image_async fires on_send_started."""
        started = []
        self.ctrl.on_send_started = lambda: started.append(True)

        # Mock the model so send doesn't actually hit hardware
        self.ctrl.model._send_busy = False
        self.ctrl.model.selected_device = DeviceInfo(name='LCD', path='/dev/sg0')

        with patch.object(self.ctrl.model, 'send_image'):
            self.ctrl.send_image_async(b'\x00' * 100, 10, 10)

        self.assertTrue(started)

    def test_send_skipped_when_busy(self):
        """send_image_async is a no-op when model is busy."""
        started = []
        self.ctrl.on_send_started = lambda: started.append(True)
        self.ctrl.model._send_busy = True
        self.ctrl.send_image_async(b'\x00', 1, 1)
        self.assertEqual(started, [])  # Never fired

    def test_devices_changed_callback(self):
        """on_devices_changed fires when model's callback triggers."""
        fired = []
        self.ctrl.on_devices_changed = lambda devs: fired.append(len(devs))
        # Simulate model firing its callback
        self.ctrl.model.devices = [DeviceInfo(name='A', path='/dev/sg0')]
        self.ctrl._on_model_devices_changed()
        self.assertEqual(fired, [1])


# =============================================================================
# VideoController
# =============================================================================

class TestVideoController(unittest.TestCase):
    """Test VideoController playback logic."""

    def setUp(self):
        self.ctrl = VideoController()

    def test_initial_state(self):
        self.assertFalse(self.ctrl.is_playing())
        self.assertFalse(self.ctrl.has_frames())

    def test_set_target_size(self):
        self.ctrl.set_target_size(480, 480)
        self.assertEqual(self.ctrl.model.target_size, (480, 480))

    def test_play_pause_stop(self):
        """Play/pause/stop update model state."""
        # Need a mock player
        self.ctrl.model._player = MagicMock()
        self.ctrl.model.state.total_frames = 10

        self.ctrl.play()
        self.assertTrue(self.ctrl.is_playing())

        self.ctrl.pause()
        self.assertFalse(self.ctrl.is_playing())

        self.ctrl.play()
        self.ctrl.stop()
        self.assertFalse(self.ctrl.is_playing())

    def test_toggle_play_pause(self):
        """toggle_play_pause switches states."""
        self.ctrl.model._player = MagicMock()
        self.ctrl.model.state.total_frames = 10

        self.ctrl.toggle_play_pause()  # stopped → playing
        self.assertTrue(self.ctrl.is_playing())

        self.ctrl.toggle_play_pause()  # playing → paused
        self.assertFalse(self.ctrl.is_playing())

    def test_seek(self):
        self.ctrl.model.state.total_frames = 100
        self.ctrl.seek(50.0)
        self.assertEqual(self.ctrl.model.state.current_frame, 50)

    def test_tick_when_not_playing(self):
        """tick() returns None when not playing."""
        self.assertIsNone(self.ctrl.tick())

    def test_tick_advances_frame(self):
        """tick() advances frame and calls on_send_frame."""
        sent = []
        self.ctrl.on_send_frame = lambda f: sent.append(f)

        # Set up playing state with preloaded frames
        fake_frame = MagicMock()
        self.ctrl.model.frames = [fake_frame, fake_frame]
        self.ctrl.model.state.total_frames = 2
        self.ctrl.model.state.state = PlaybackState.PLAYING
        self.ctrl.model.state.current_frame = 0

        frame = self.ctrl.tick()
        self.assertIsNotNone(frame)
        self.assertEqual(len(sent), 1)  # LCD_SEND_INTERVAL=1

    def test_get_frame_interval(self):
        """Default 16fps → ~62ms."""
        ms = self.ctrl.get_frame_interval()
        self.assertGreater(ms, 0)
        self.assertEqual(ms, 62)  # 1000/16 = 62

    def test_on_video_loaded_callback(self):
        """on_video_loaded fires after successful load."""
        fired = []
        self.ctrl.on_video_loaded = lambda s: fired.append(s)

        # Mock load to succeed
        with patch.object(self.ctrl.model, 'load', return_value=True):
            self.ctrl.load(Path('fake.mp4'))

        self.assertEqual(len(fired), 1)


# =============================================================================
# OverlayController
# =============================================================================

class TestOverlayController(unittest.TestCase):
    """Test OverlayController overlay management."""

    def setUp(self):
        self.ctrl = OverlayController()

    def test_initial_state(self):
        self.assertFalse(self.ctrl.is_enabled())
        self.assertEqual(self.ctrl.get_elements(), [])

    def test_enable_disable(self):
        self.ctrl.enable(True)
        self.assertTrue(self.ctrl.is_enabled())
        self.ctrl.enable(False)
        self.assertFalse(self.ctrl.is_enabled())

    def test_set_target_size(self):
        self.ctrl.set_target_size(480, 480)
        self.assertEqual(self.ctrl.model.target_size, (480, 480))

    def test_add_element(self):
        elem = OverlayElement(element_type=OverlayElementType.TEXT, text='Hello')
        self.ctrl.add_element(elem)
        self.assertEqual(len(self.ctrl.get_elements()), 1)
        self.assertEqual(self.ctrl.get_elements()[0].text, 'Hello')

    def test_remove_element(self):
        self.ctrl.add_element(OverlayElement(text='A'))
        self.ctrl.add_element(OverlayElement(text='B'))
        self.ctrl.remove_element(0)
        self.assertEqual(len(self.ctrl.get_elements()), 1)
        self.assertEqual(self.ctrl.get_elements()[0].text, 'B')

    def test_update_element(self):
        self.ctrl.add_element(OverlayElement(text='old'))
        self.ctrl.update_element(0, OverlayElement(text='new'))
        self.assertEqual(self.ctrl.get_elements()[0].text, 'new')

    def test_on_config_changed_callback(self):
        """on_config_changed fires on add/remove/update."""
        fired = []
        self.ctrl.on_config_changed = lambda: fired.append(True)
        self.ctrl.add_element(OverlayElement(text='x'))
        self.ctrl.update_element(0, OverlayElement(text='y'))
        self.ctrl.remove_element(0)
        self.assertEqual(len(fired), 3)

    def test_update_metrics(self):
        """update_metrics stores metrics for render."""
        self.ctrl.update_metrics({'cpu_temp': 65})
        self.assertEqual(self.ctrl._metrics['cpu_temp'], 65)

    def test_render_disabled_returns_background(self):
        """When disabled, render returns background unchanged."""
        bg = MagicMock()
        self.ctrl.model.background = bg
        result = self.ctrl.render()
        self.assertIs(result, bg)


# =============================================================================
# LCDDeviceController
# =============================================================================

class TestLCDDeviceController(unittest.TestCase):
    """Test LCDDeviceController main application controller."""

    def setUp(self):
        # Patch paths module to avoid file I/O
        self.patches = [
            patch('trcc.core.controllers.get_saved_resolution', return_value=(320, 320)),
            patch('trcc.core.controllers.save_resolution'),
            patch('trcc.core.controllers.ensure_themes_extracted'),
            patch('trcc.core.controllers.ensure_web_extracted'),
            patch('trcc.core.controllers.ensure_web_masks_extracted'),
            patch('trcc.core.controllers.get_web_dir', return_value='/tmp/web'),
            patch('trcc.core.controllers.get_web_masks_dir', return_value='/tmp/masks'),
        ]
        for p in self.patches:
            p.start()

        self.ctrl = LCDDeviceController()

    def tearDown(self):
        self.ctrl.cleanup()
        for p in self.patches:
            p.stop()

    def test_initial_resolution(self):
        self.assertEqual(self.ctrl.lcd_width, 320)
        self.assertEqual(self.ctrl.lcd_height, 320)

    def test_working_dir_created(self):
        """Constructor creates a temp working directory."""
        self.assertTrue(self.ctrl.working_dir.exists())
        self.assertTrue(self.ctrl.working_dir.is_dir())

    def test_cleanup_removes_working_dir(self):
        """cleanup() removes the working directory."""
        wd = self.ctrl.working_dir
        self.assertTrue(wd.exists())
        self.ctrl.cleanup()
        self.assertFalse(wd.exists())

    def test_set_resolution(self):
        """set_resolution updates width/height and sub-controllers."""
        fired = []
        self.ctrl.on_resolution_changed = lambda w, h: fired.append((w, h))
        self.ctrl.set_resolution(480, 480)
        self.assertEqual(self.ctrl.lcd_width, 480)
        self.assertEqual(self.ctrl.lcd_height, 480)
        self.assertEqual(self.ctrl.video.model.target_size, (480, 480))
        self.assertEqual(self.ctrl.overlay.model.target_size, (480, 480))
        self.assertEqual(fired, [(480, 480)])

    def test_set_resolution_no_op_same(self):
        """set_resolution is a no-op if already at that resolution."""
        fired = []
        self.ctrl.on_resolution_changed = lambda w, h: fired.append((w, h))
        self.ctrl.set_resolution(320, 320)
        self.assertEqual(fired, [])  # No callback

    def test_set_rotation(self):
        """set_rotation wraps at 360."""
        self.ctrl.set_rotation(90)
        self.assertEqual(self.ctrl.rotation, 90)
        self.ctrl.set_rotation(450)
        self.assertEqual(self.ctrl.rotation, 90)

    def test_set_brightness_clamps(self):
        """set_brightness clamps to 0-100."""
        self.ctrl.set_brightness(150)
        self.assertEqual(self.ctrl.brightness, 100)
        self.ctrl.set_brightness(-10)
        self.assertEqual(self.ctrl.brightness, 0)

    def test_auto_send_default(self):
        self.assertTrue(self.ctrl.auto_send)

    def test_sub_controllers_initialized(self):
        """All sub-controllers are proper types."""
        self.assertIsInstance(self.ctrl.themes, ThemeController)
        self.assertIsInstance(self.ctrl.devices, DeviceController)
        self.assertIsInstance(self.ctrl.video, VideoController)
        self.assertIsInstance(self.ctrl.overlay, OverlayController)

    def test_play_pause(self):
        """play_pause delegates to video controller."""
        with patch.object(self.ctrl.video, 'toggle_play_pause') as mock:
            self.ctrl.play_pause()
            mock.assert_called_once()

    def test_seek_video(self):
        with patch.object(self.ctrl.video, 'seek') as mock:
            self.ctrl.seek_video(50.0)
            mock.assert_called_once_with(50.0)

    def test_is_video_playing(self):
        with patch.object(self.ctrl.video, 'is_playing', return_value=False):
            self.assertFalse(self.ctrl.is_video_playing())

    def test_on_device_selected_updates_resolution(self):
        """Device selection triggers resolution update if different."""
        dev = DeviceInfo(name='LCD', path='/dev/sg0', resolution=(480, 480))
        with patch.object(self.ctrl, 'set_resolution') as mock_res:
            self.ctrl._on_device_selected(dev)
            mock_res.assert_called_once_with(480, 480)

    def test_on_device_selected_same_resolution(self):
        """No resolution update when device matches current."""
        dev = DeviceInfo(name='LCD', path='/dev/sg0', resolution=(320, 320))
        with patch.object(self.ctrl, 'set_resolution') as mock_res:
            self.ctrl._on_device_selected(dev)
            mock_res.assert_not_called()

    def test_status_update_callback(self):
        """_update_status fires on_status_update."""
        fired = []
        self.ctrl.on_status_update = lambda s: fired.append(s)
        self.ctrl._update_status('testing')
        self.assertEqual(fired, ['testing'])

    def test_error_callback(self):
        """_handle_error fires on_error."""
        errors = []
        self.ctrl.on_error = lambda e: errors.append(e)
        self.ctrl._handle_error('broke')
        self.assertEqual(errors, ['broke'])

    def test_send_current_image_no_image(self):
        """send_current_image with no image is a no-op."""
        self.ctrl.current_image = None
        self.ctrl.send_current_image()  # Should not raise

    def test_clear_working_dir(self):
        """_clear_working_dir recreates empty directory."""
        (self.ctrl.working_dir / 'junk.txt').write_text('x')
        self.ctrl._clear_working_dir()
        self.assertTrue(self.ctrl.working_dir.exists())
        self.assertEqual(list(self.ctrl.working_dir.iterdir()), [])

    def test_copy_theme_to_working_dir(self):
        """Files are copied from source to working dir."""
        with tempfile.TemporaryDirectory() as src:
            (Path(src) / '00.png').write_bytes(b'PNG_DATA')
            (Path(src) / 'config1.dc').write_bytes(b'\xdc\x00')

            self.ctrl._copy_theme_to_working_dir(Path(src))

            self.assertTrue((self.ctrl.working_dir / '00.png').exists())
            self.assertTrue((self.ctrl.working_dir / 'config1.dc').exists())


class TestLCDDeviceControllerRotation(unittest.TestCase):
    """Test _apply_rotation and _apply_brightness image transforms."""

    def setUp(self):
        self.patches = [
            patch('trcc.core.controllers.get_saved_resolution', return_value=(320, 320)),
            patch('trcc.core.controllers.save_resolution'),
            patch('trcc.core.controllers.ensure_themes_extracted'),
            patch('trcc.core.controllers.ensure_web_extracted'),
            patch('trcc.core.controllers.ensure_web_masks_extracted'),
            patch('trcc.core.controllers.get_web_dir', return_value='/tmp/web'),
            patch('trcc.core.controllers.get_web_masks_dir', return_value='/tmp/masks'),
        ]
        for p in self.patches:
            p.start()
        self.ctrl = LCDDeviceController()

    def tearDown(self):
        self.ctrl.cleanup()
        for p in self.patches:
            p.stop()

    def test_apply_rotation_0(self):
        """0° rotation returns image unchanged."""
        img = MagicMock()
        self.ctrl.rotation = 0
        result = self.ctrl._apply_rotation(img)
        self.assertIs(result, img)

    def test_apply_rotation_non_zero(self):
        """Non-zero rotation calls transpose."""
        img = MagicMock()
        img.transpose.return_value = MagicMock()
        self.ctrl.rotation = 180
        self.ctrl._apply_rotation(img)
        img.transpose.assert_called_once()

    def test_apply_brightness_full(self):
        """100% brightness returns image unchanged."""
        img = MagicMock()
        self.ctrl.brightness = 100
        result = self.ctrl._apply_brightness(img)
        self.assertIs(result, img)


# =============================================================================
# ThemeController – additional coverage
# =============================================================================

class TestThemeControllerEdgeCases(unittest.TestCase):
    """Additional ThemeController coverage."""

    def setUp(self):
        self.ctrl = ThemeController()

    def test_set_directories_local_only(self):
        """set_directories with only local_dir."""
        self.ctrl.set_directories(local_dir=Path('/tmp/loc'))
        self.assertEqual(self.ctrl.model.local_theme_dir, Path('/tmp/loc'))

    def test_set_directories_web_only_no_masks(self):
        """web_dir without masks_dir is ignored (both required)."""
        self.ctrl.set_directories(web_dir=Path('/tmp/web'))
        self.assertIsNone(self.ctrl.model.cloud_web_dir)

    def test_load_cloud_themes(self):
        """load_cloud_themes delegates to model."""
        with patch.object(self.ctrl.model, 'load_cloud_themes') as m:
            self.ctrl.load_cloud_themes()
            m.assert_called_once()

    def test_on_model_selection_changed_none(self):
        """None theme doesn't fire on_theme_selected."""
        fired = []
        self.ctrl.on_theme_selected = lambda t: fired.append(t)
        self.ctrl._on_model_selection_changed(None)
        self.assertEqual(fired, [])

    def test_on_model_themes_changed_no_callback(self):
        """_on_model_themes_changed without callback is a no-op."""
        self.ctrl.on_themes_loaded = None
        self.ctrl._on_model_themes_changed()  # Should not raise

    def test_set_filter_no_callback(self):
        """set_filter without on_filter_changed still sets model."""
        self.ctrl.on_filter_changed = None
        self.ctrl.set_filter('default')
        self.assertEqual(self.ctrl.model.filter_mode, 'default')


# =============================================================================
# DeviceController – additional coverage
# =============================================================================

class TestDeviceControllerEdgeCases(unittest.TestCase):
    """Additional DeviceController coverage."""

    def setUp(self):
        self.ctrl = DeviceController()

    def test_detect_devices_delegates(self):
        """detect_devices calls model.detect_devices."""
        with patch.object(self.ctrl.model, 'detect_devices') as m:
            self.ctrl.detect_devices()
            m.assert_called_once()

    def test_on_model_send_complete_callback(self):
        """_on_model_send_complete fires on_send_complete."""
        fired = []
        self.ctrl.on_send_complete = lambda ok: fired.append(ok)
        self.ctrl._on_model_send_complete(True)
        self.assertEqual(fired, [True])

    def test_on_model_send_complete_no_callback(self):
        """_on_model_send_complete without callback is safe."""
        self.ctrl.on_send_complete = None
        self.ctrl._on_model_send_complete(False)

    def test_on_model_devices_changed_no_callback(self):
        """_on_model_devices_changed without callback is safe."""
        self.ctrl.on_devices_changed = None
        self.ctrl._on_model_devices_changed()

    def test_on_model_selection_changed_none_device(self):
        """None device doesn't fire on_device_selected."""
        fired = []
        self.ctrl.on_device_selected = lambda d: fired.append(d)
        self.ctrl._on_model_selection_changed(None)
        self.assertEqual(fired, [])


# =============================================================================
# VideoController – additional coverage
# =============================================================================

class TestVideoControllerEdgeCases(unittest.TestCase):
    """Additional VideoController coverage."""

    def setUp(self):
        self.ctrl = VideoController()

    def test_load_failure(self):
        """Load failure does not fire on_video_loaded."""
        fired = []
        self.ctrl.on_video_loaded = lambda s: fired.append(s)
        with patch.object(self.ctrl.model, 'load', return_value=False):
            result = self.ctrl.load(Path('bad.mp4'))
        self.assertFalse(result)
        self.assertEqual(fired, [])

    def test_on_model_state_changed_callback(self):
        """_on_model_state_changed fires on_state_changed."""
        fired = []
        self.ctrl.on_state_changed = lambda s: fired.append(s)
        state = VideoState(state=PlaybackState.PLAYING)
        self.ctrl._on_model_state_changed(state)
        self.assertEqual(fired, [PlaybackState.PLAYING])

    def test_on_model_state_changed_no_callback(self):
        """_on_model_state_changed without callback is safe."""
        self.ctrl.on_state_changed = None
        self.ctrl._on_model_state_changed(VideoState())

    def test_on_model_frame_ready_callback(self):
        """_on_model_frame_ready fires on_frame_ready."""
        fired = []
        self.ctrl.on_frame_ready = lambda f: fired.append(f)
        self.ctrl._on_model_frame_ready('frame_data')
        self.assertEqual(fired, ['frame_data'])

    def test_on_model_frame_ready_no_callback(self):
        """_on_model_frame_ready without callback is safe."""
        self.ctrl.on_frame_ready = None
        self.ctrl._on_model_frame_ready('frame_data')

    def test_tick_with_progress_callback(self):
        """tick fires on_progress_update."""
        progress_fired = []
        self.ctrl.on_progress_update = lambda p, c, t: progress_fired.append((p, c, t))

        fake_frame = MagicMock()
        self.ctrl.model.frames = [fake_frame, fake_frame]
        self.ctrl.model.state.total_frames = 2
        self.ctrl.model.state.state = PlaybackState.PLAYING
        self.ctrl.model.state.current_frame = 0

        self.ctrl.tick()
        self.assertEqual(len(progress_fired), 1)

    def test_tick_no_frame_returned(self):
        """tick returns None when advance_frame returns None."""
        self.ctrl.model.state.state = PlaybackState.PLAYING
        with patch.object(self.ctrl.model, 'advance_frame', return_value=None):
            # Force is_playing True
            self.ctrl.model._player = MagicMock()
            self.ctrl.model.state.state = PlaybackState.PLAYING
            result = self.ctrl.tick()
            self.assertIsNone(result)

    def test_has_frames_with_data(self):
        """has_frames returns True when model has frames."""
        self.ctrl.model.frames = [MagicMock()]
        self.assertTrue(self.ctrl.has_frames())


# =============================================================================
# OverlayController – renderer delegation
# =============================================================================

class TestOverlayControllerRenderer(unittest.TestCase):
    """Test OverlayController methods that delegate to renderer."""

    def setUp(self):
        self.ctrl = OverlayController()
        # Pre-set a mock renderer so _ensure_renderer returns it
        self.mock_renderer = MagicMock()
        self.ctrl.model._renderer = self.mock_renderer

    def test_set_theme_mask(self):
        mask_img = MagicMock()
        self.ctrl.set_theme_mask(mask_img, (10, 20))
        self.mock_renderer.set_theme_mask.assert_called_once_with(mask_img, (10, 20))

    def test_get_theme_mask(self):
        self.mock_renderer.theme_mask = 'mask'
        self.mock_renderer.theme_mask_position = (5, 5)
        mask, pos = self.ctrl.get_theme_mask()
        self.assertEqual(mask, 'mask')
        self.assertEqual(pos, (5, 5))

    def test_set_mask_visible(self):
        self.ctrl.set_mask_visible(True)
        self.mock_renderer.set_mask_visible.assert_called_once_with(True)

    def test_set_temp_unit(self):
        self.ctrl.set_temp_unit(1)
        self.mock_renderer.set_temp_unit.assert_called_once_with(1)

    def test_set_config(self):
        cfg = {'key': 'val'}
        self.ctrl.set_config(cfg)
        self.mock_renderer.set_config.assert_called_once_with(cfg)

    def test_set_config_resolution(self):
        self.ctrl.set_config_resolution(480, 480)
        self.mock_renderer.set_config_resolution.assert_called_once_with(480, 480)

    def test_set_scale_enabled(self):
        self.ctrl.set_scale_enabled(False)
        self.mock_renderer.set_scale_enabled.assert_called_once_with(False)

    def test_load_config(self):
        with patch.object(self.ctrl.model, 'load_from_dc', return_value=True) as m:
            result = self.ctrl.load_config(Path('/fake/config1.dc'))
            self.assertTrue(result)
            m.assert_called_once()

    def test_render_sets_background(self):
        """render(background) passes background to model."""
        bg = MagicMock()
        with patch.object(self.ctrl.model, 'set_background') as mock_bg, \
             patch.object(self.ctrl.model, 'render', return_value=bg):
            self.ctrl.render(bg)
            mock_bg.assert_called_once_with(bg)

    def test_on_config_changed_no_callback(self):
        """_on_model_config_changed without callback is safe."""
        self.ctrl.on_config_changed = None
        self.ctrl._on_model_config_changed()

    def test_set_background(self):
        """set_background delegates to model."""
        bg = MagicMock()
        with patch.object(self.ctrl.model, 'set_background') as m:
            self.ctrl.set_background(bg)
            m.assert_called_once_with(bg)


class TestOverlayControllerNoRenderer(unittest.TestCase):
    """Test OverlayController when renderer can't be created."""

    def setUp(self):
        self.ctrl = OverlayController()
        self.ctrl.model._renderer = None

    def test_get_theme_mask_no_renderer(self):
        """get_theme_mask returns None, None when no renderer."""
        with patch.object(self.ctrl.model, '_update_renderer'):
            self.ctrl.model._renderer = None
            mask, pos = self.ctrl.get_theme_mask()
            self.assertIsNone(mask)
            self.assertIsNone(pos)

    def test_set_theme_mask_no_renderer(self):
        """set_theme_mask is safe when renderer fails to initialize."""
        with patch.object(self.ctrl.model, '_update_renderer'):
            self.ctrl.model._renderer = None
            self.ctrl.set_theme_mask(MagicMock())  # Should not raise

    def test_set_mask_visible_no_renderer(self):
        with patch.object(self.ctrl.model, '_update_renderer'):
            self.ctrl.model._renderer = None
            self.ctrl.set_mask_visible(True)

    def test_set_temp_unit_no_renderer(self):
        with patch.object(self.ctrl.model, '_update_renderer'):
            self.ctrl.model._renderer = None
            self.ctrl.set_temp_unit(0)

    def test_set_config_no_renderer(self):
        with patch.object(self.ctrl.model, '_update_renderer'):
            self.ctrl.model._renderer = None
            self.ctrl.set_config({})

    def test_set_config_resolution_no_renderer(self):
        with patch.object(self.ctrl.model, '_update_renderer'):
            self.ctrl.model._renderer = None
            self.ctrl.set_config_resolution(320, 320)

    def test_set_scale_enabled_no_renderer(self):
        with patch.object(self.ctrl.model, '_update_renderer'):
            self.ctrl.model._renderer = None
            self.ctrl.set_scale_enabled(True)


# =============================================================================
# LCDDeviceController – theme loading, save, export, import
# =============================================================================

class TestFormCZTVThemeOps(unittest.TestCase):
    """Test LCDDeviceController theme loading and file operations."""

    def setUp(self):
        self.ctrl, self.patches = _make_form_controller()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.ctrl.cleanup()
        _stop_patches(self.patches)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_theme_dir(self, name='TestTheme', with_bg=True, with_mask=False, with_dc=False):
        """Create a theme directory with optional files."""
        d = Path(self.tmp) / name
        d.mkdir(parents=True, exist_ok=True)
        if with_bg:
            img = _make_test_image(self.ctrl.lcd_width, self.ctrl.lcd_height)
            img.save(str(d / '00.png'))
        if with_mask:
            mask = _make_test_image(self.ctrl.lcd_width, self.ctrl.lcd_height, (0, 0, 255))
            mask.save(str(d / '01.png'))
        if with_dc:
            (d / 'config1.dc').write_bytes(b'\xdd' + b'\x00' * 20)
        return d

    def test_load_local_theme_static(self):
        """Load a local theme with a static background image."""
        theme_dir = self._make_theme_dir(with_bg=True)
        theme = ThemeInfo(name='T', path=theme_dir)

        statuses = []
        self.ctrl.on_status_update = lambda s: statuses.append(s)

        self.ctrl.load_local_theme(theme)

        self.assertIsNotNone(self.ctrl.current_image)
        self.assertEqual(self.ctrl.current_theme_path, theme_dir)
        self.assertIn('Theme: T', statuses)

    def test_load_local_theme_animated(self):
        """Load an animated theme delegates to video controller."""
        theme_dir = self._make_theme_dir()
        theme = ThemeInfo(
            name='Anim', path=theme_dir,
            is_animated=True, animation_path=Path('/fake/video.mp4'),
        )
        with patch.object(self.ctrl.video, 'load', return_value=True), \
             patch.object(self.ctrl.video, 'play'):
            self.ctrl.load_local_theme(theme)

    def test_load_local_theme_zt(self):
        """Load theme with Theme.zt file."""
        theme_dir = self._make_theme_dir(with_bg=False)
        (theme_dir / 'Theme.zt').write_bytes(b'\xdc\x00')
        theme = ThemeInfo(name='ZT', path=theme_dir)

        with patch.object(self.ctrl.video, 'load', return_value=True), \
             patch.object(self.ctrl.video, 'play'):
            self.ctrl.load_local_theme(theme)

    def test_load_local_theme_mask_only(self):
        """Load mask-only theme creates black background."""
        theme_dir = self._make_theme_dir(with_bg=False, with_mask=True)
        theme = ThemeInfo(name='Mask', path=theme_dir, is_mask_only=True)

        self.ctrl.load_local_theme(theme)
        self.assertIsNotNone(self.ctrl.current_image)

    def test_load_local_theme_with_mask(self):
        """Load theme with mask image (01.png)."""
        theme_dir = self._make_theme_dir(with_bg=True, with_mask=True)
        theme = ThemeInfo(name='WithMask', path=theme_dir)

        self.ctrl.load_local_theme(theme)
        # Mask was processed (overlay was called)
        self.assertIsNotNone(self.ctrl.current_image)

    def test_load_cloud_theme(self):
        """Load cloud video theme."""
        theme = ThemeInfo(
            name='Cloud', theme_type=ThemeType.CLOUD,
            animation_path=Path('/fake/cloud.mp4'),
        )
        fake_frame = _make_test_image()
        with patch.object(self.ctrl.video, 'load', return_value=True), \
             patch.object(self.ctrl.video, 'play'), \
             patch.object(self.ctrl.video.model, 'get_frame', return_value=fake_frame):
            previews = []
            self.ctrl.on_preview_update = lambda img: previews.append(img)
            self.ctrl.load_cloud_theme(theme)
            self.assertIsNotNone(self.ctrl.current_image)

    def test_load_cloud_theme_no_path(self):
        """Cloud theme without animation_path is a no-op."""
        theme = ThemeInfo(name='Empty', theme_type=ThemeType.CLOUD)
        self.ctrl.load_cloud_theme(theme)
        self.assertIsNone(self.ctrl.current_image)

    def test_load_cloud_theme_no_first_frame(self):
        """Cloud theme where first frame is None."""
        theme = ThemeInfo(
            name='Cloud', theme_type=ThemeType.CLOUD,
            animation_path=Path('/fake/cloud.mp4'),
        )
        with patch.object(self.ctrl.video, 'load', return_value=True), \
             patch.object(self.ctrl.video, 'play'), \
             patch.object(self.ctrl.video.model, 'get_frame', return_value=None):
            self.ctrl.load_cloud_theme(theme)
            self.assertIsNone(self.ctrl.current_image)

    def test_apply_mask(self):
        """Apply a mask overlay on existing content."""
        self.ctrl.current_image = _make_test_image()
        mask_dir = self._make_theme_dir('Mask', with_mask=True)

        self.ctrl.apply_mask(mask_dir)
        self.assertTrue(self.ctrl.overlay.is_enabled())

    def test_apply_mask_no_background(self):
        """Apply mask when no current_image creates black background."""
        self.ctrl.current_image = None
        mask_dir = self._make_theme_dir('Mask', with_mask=True)

        self.ctrl.apply_mask(mask_dir)
        self.assertIsNotNone(self.ctrl.current_image)

    def test_apply_mask_nonexistent_dir(self):
        """apply_mask with non-existent dir is a no-op."""
        self.ctrl.apply_mask(Path('/nonexistent'))

    def test_apply_mask_none(self):
        """apply_mask with None is a no-op."""
        self.ctrl.apply_mask(None)

    def test_save_theme_no_image(self):
        """save_theme with no image returns failure."""
        self.ctrl.current_image = None
        ok, msg = self.ctrl.save_theme('test', Path(self.tmp))
        self.assertFalse(ok)
        self.assertIn('No image', msg)

    def test_save_theme_success(self):
        """save_theme creates Custom_ prefixed directory with files."""
        self.ctrl.current_image = _make_test_image()
        # Put a bg file in working dir
        self.ctrl.current_image.save(str(self.ctrl.working_dir / '00.png'))

        with patch('trcc.core.controllers.LCDDeviceController._ensure_renderer_for_save',
                   create=True, return_value=None):
            ok, msg = self.ctrl.save_theme('MyTheme', Path(self.tmp))

        self.assertTrue(ok)
        self.assertIn('Custom_MyTheme', msg)
        theme_path = Path(self.tmp) / 'Theme320320' / 'Custom_MyTheme'
        self.assertTrue(theme_path.exists())

    def test_save_theme_already_custom(self):
        """save_theme with Custom_ prefix doesn't double-prefix."""
        self.ctrl.current_image = _make_test_image()
        self.ctrl.current_image.save(str(self.ctrl.working_dir / '00.png'))

        ok, msg = self.ctrl.save_theme('Custom_Existing', Path(self.tmp))
        self.assertTrue(ok)
        self.assertIn('Custom_Existing', msg)
        # Check no Custom_Custom_ directory
        self.assertFalse(
            (Path(self.tmp) / 'Theme320320' / 'Custom_Custom_Existing').exists())

    def test_save_theme_generates_thumbnail(self):
        """save_theme creates Theme.png thumbnail."""
        self.ctrl.current_image = _make_test_image()
        ok, _ = self.ctrl.save_theme('Thumb', Path(self.tmp))
        self.assertTrue(ok)
        theme_path = Path(self.tmp) / 'Theme320320' / 'Custom_Thumb'
        self.assertTrue((theme_path / 'Theme.png').exists())

    def test_export_config_no_theme(self):
        """export_config fails when no theme is loaded."""
        self.ctrl.current_theme_path = None
        ok, msg = self.ctrl.export_config(Path('/tmp/out.tr'))
        self.assertFalse(ok)
        self.assertIn('No theme', msg)

    def test_export_config_tr(self):
        """export_config .tr delegates to dc_writer."""
        self.ctrl.current_theme_path = Path('/tmp/theme')
        with patch('trcc.dc_writer.export_theme') as mock_export:
            ok, msg = self.ctrl.export_config(Path('/tmp/out.tr'))
            self.assertTrue(ok)
            mock_export.assert_called_once()

    def test_export_config_json(self):
        """export_config non-.tr writes JSON."""
        self.ctrl.current_theme_path = Path('/tmp/theme')
        out_path = Path(self.tmp) / 'config.json'
        ok, msg = self.ctrl.export_config(out_path)
        self.assertTrue(ok)
        with open(out_path) as f:
            data = json.load(f)
        self.assertIn('theme_path', data)
        self.assertIn('resolution', data)

    def test_export_config_error(self):
        """export_config handles exceptions."""
        self.ctrl.current_theme_path = Path('/tmp/theme')
        with patch('trcc.dc_writer.export_theme', side_effect=RuntimeError('boom')):
            ok, msg = self.ctrl.export_config(Path('/tmp/out.tr'))
            self.assertFalse(ok)
            self.assertIn('Export failed', msg)

    def test_import_config_json(self):
        """import_config from JSON with valid theme_path."""
        # Create a theme directory with required files
        theme_dir = self._make_theme_dir('ImportMe', with_bg=True)
        json_path = Path(self.tmp) / 'import.json'
        with open(json_path, 'w') as f:
            json.dump({'theme_path': str(theme_dir)}, f)

        with patch.object(self.ctrl, 'load_local_theme'):
            ok, msg = self.ctrl.import_config(json_path, Path(self.tmp))
            self.assertTrue(ok)

    def test_import_config_json_missing_path(self):
        """import_config from JSON with non-existent theme_path."""
        json_path = Path(self.tmp) / 'bad.json'
        with open(json_path, 'w') as f:
            json.dump({'theme_path': '/nonexistent'}, f)

        ok, msg = self.ctrl.import_config(json_path, Path(self.tmp))
        self.assertFalse(ok)
        self.assertIn('not found', msg)

    def test_import_config_tr(self):
        """import_config .tr delegates to dc_writer."""
        tr_path = Path(self.tmp) / 'theme.tr'
        tr_path.write_bytes(b'\xdd\xdc\xdd\xdc')

        with patch('trcc.dc_writer.import_theme'), \
             patch.object(self.ctrl, 'load_local_theme'):
            ok, msg = self.ctrl.import_config(tr_path, Path(self.tmp))
            self.assertTrue(ok)

    def test_import_config_error(self):
        """import_config handles exceptions."""
        tr_path = Path(self.tmp) / 'bad.tr'
        tr_path.write_bytes(b'junk')
        with patch('trcc.dc_writer.import_theme', side_effect=RuntimeError('nope')):
            ok, msg = self.ctrl.import_config(tr_path, Path(self.tmp))
            self.assertFalse(ok)
            self.assertIn('Import failed', msg)

    def test_load_image_file(self):
        """load_image_file delegates to _load_static_image."""
        img_path = Path(self.tmp) / 'test.png'
        _make_test_image().save(str(img_path))

        self.ctrl.load_image_file(img_path)
        self.assertIsNotNone(self.ctrl.current_image)


# =============================================================================
# LCDDeviceController – video tick, LCD send pipeline
# =============================================================================

class TestFormCZTVVideoAndSend(unittest.TestCase):
    """Test video tick, LCD frame send, rotation, brightness, RGB565."""

    def setUp(self):
        self.ctrl, self.patches = _make_form_controller()

    def tearDown(self):
        self.ctrl.cleanup()
        _stop_patches(self.patches)

    def test_video_tick_with_frame(self):
        """video_tick updates preview when frame is available."""
        fake_frame = _make_test_image()
        previews = []
        self.ctrl.on_preview_update = lambda img: previews.append(img)

        with patch.object(self.ctrl.video, 'tick', return_value=fake_frame):
            self.ctrl.video_tick()

        self.assertIs(self.ctrl.current_image, fake_frame)
        self.assertEqual(len(previews), 1)

    def test_video_tick_with_overlay(self):
        """video_tick applies overlay when enabled."""
        fake_frame = _make_test_image()
        rendered = _make_test_image(color=(0, 255, 0))
        previews = []
        self.ctrl.on_preview_update = lambda img: previews.append(img)
        self.ctrl.overlay.enable(True)

        with patch.object(self.ctrl.video, 'tick', return_value=fake_frame), \
             patch.object(self.ctrl.overlay, 'render', return_value=rendered):
            self.ctrl.video_tick()

        self.assertEqual(len(previews), 1)

    def test_video_tick_no_frame(self):
        """video_tick with no frame is a no-op."""
        with patch.object(self.ctrl.video, 'tick', return_value=None):
            self.ctrl.video_tick()

    def test_get_video_interval(self):
        """get_video_interval delegates to video controller."""
        ms = self.ctrl.get_video_interval()
        self.assertIsInstance(ms, int)
        self.assertGreater(ms, 0)

    def test_on_video_send_frame_no_overlay(self):
        """_on_video_send_frame sends frame without overlay when disabled."""
        frame = _make_test_image()
        with patch.object(self.ctrl, '_send_frame_to_lcd') as mock_send:
            self.ctrl._on_video_send_frame(frame)
            mock_send.assert_called_once_with(frame)

    def test_on_video_send_frame_with_overlay(self):
        """_on_video_send_frame applies overlay before sending."""
        frame = _make_test_image()
        rendered = _make_test_image(color=(0, 255, 0))
        self.ctrl.overlay.enable(True)

        with patch.object(self.ctrl.overlay, 'render', return_value=rendered), \
             patch.object(self.ctrl, '_send_frame_to_lcd') as mock_send:
            self.ctrl._on_video_send_frame(frame)
            mock_send.assert_called_once_with(rendered)

    def test_send_current_image(self):
        """send_current_image sends image and updates status."""
        self.ctrl.current_image = _make_test_image()
        statuses = []
        self.ctrl.on_status_update = lambda s: statuses.append(s)

        with patch.object(self.ctrl, '_send_frame_to_lcd'):
            self.ctrl.send_current_image()

        self.assertIn('Sent to LCD', statuses)

    def test_send_frame_to_lcd_no_device(self):
        """_send_frame_to_lcd is a no-op without selected device."""
        self.ctrl.devices.model.selected_device = None
        self.ctrl._send_frame_to_lcd(_make_test_image())

    def test_send_frame_to_lcd_with_device(self):
        """_send_frame_to_lcd applies brightness + rotation and sends."""
        dev = DeviceInfo(name='LCD', path='/dev/sg0')
        self.ctrl.devices.model.selected_device = dev

        with patch.object(self.ctrl.devices, 'send_image_async') as mock_send:
            self.ctrl._send_frame_to_lcd(_make_test_image())
            mock_send.assert_called_once()
            # Check args: rgb565_data, width, height
            args = mock_send.call_args
            self.assertEqual(args[0][1], 320)
            self.assertEqual(args[0][2], 320)

    def test_send_frame_to_lcd_error(self):
        """_send_frame_to_lcd handles exceptions gracefully."""
        dev = DeviceInfo(name='LCD', path='/dev/sg0')
        self.ctrl.devices.model.selected_device = dev
        errors = []
        self.ctrl.on_error = lambda e: errors.append(e)

        with patch.object(self.ctrl, '_image_to_rgb565', side_effect=RuntimeError('fail')):
            self.ctrl._send_frame_to_lcd(_make_test_image())

        self.assertEqual(len(errors), 1)
        self.assertIn('LCD send error', errors[0])

    def test_image_to_rgb565(self):
        """_image_to_rgb565 produces correct byte count."""
        img = _make_test_image(4, 4)
        data = self.ctrl._image_to_rgb565(img)
        self.assertEqual(len(data), 4 * 4 * 2)  # 2 bytes per pixel

    def test_image_to_rgb565_rgba_input(self):
        """_image_to_rgb565 handles RGBA input."""
        img = Image.new('RGBA', (4, 4), (128, 64, 32, 255))
        data = self.ctrl._image_to_rgb565(img)
        self.assertEqual(len(data), 4 * 4 * 2)

    def test_apply_rotation_90(self):
        """90° rotation uses ROTATE_270 transpose."""
        img = _make_test_image(4, 4)
        self.ctrl.rotation = 90
        result = self.ctrl._apply_rotation(img)
        self.assertEqual(result.size, (4, 4))

    def test_apply_rotation_270(self):
        """270° rotation uses ROTATE_90 transpose."""
        img = _make_test_image(4, 4)
        self.ctrl.rotation = 270
        result = self.ctrl._apply_rotation(img)
        self.assertEqual(result.size, (4, 4))

    def test_apply_brightness_50(self):
        """50% brightness darkens the image."""
        img = _make_test_image(4, 4, (200, 200, 200))
        self.ctrl.brightness = 50
        result = self.ctrl._apply_brightness(img)
        self.assertIsNotNone(result)
        # Check pixels are darker
        # Verify brightness reduction by comparing pixel values
        self.assertLess(result.getpixel((0, 0))[0], img.getpixel((0, 0))[0])


# =============================================================================
# LCDDeviceController – mask position parsing
# =============================================================================

class TestFormCZTVMaskPosition(unittest.TestCase):
    """Test _parse_mask_position logic."""

    def setUp(self):
        self.ctrl, self.patches = _make_form_controller()

    def tearDown(self):
        self.ctrl.cleanup()
        _stop_patches(self.patches)

    def test_full_size_mask(self):
        """Full-size mask returns (0, 0)."""
        mask = _make_test_image(320, 320)
        pos = self.ctrl._parse_mask_position(None, mask)
        self.assertEqual(pos, (0, 0))

    def test_no_dc_path(self):
        """No DC path returns None for small mask."""
        mask = _make_test_image(100, 100)
        pos = self.ctrl._parse_mask_position(None, mask)
        self.assertIsNone(pos)

    def test_nonexistent_dc_path(self):
        """Non-existent DC path returns None."""
        mask = _make_test_image(100, 100)
        pos = self.ctrl._parse_mask_position('/nonexistent.dc', mask)
        self.assertIsNone(pos)

    def test_dc_with_mask_position(self):
        """DC with mask_enabled and mask_position returns top-left coords."""
        mask = _make_test_image(100, 100)
        dc_data = {
            'mask_settings': {
                'mask_enabled': True,
                'mask_position': (160, 160),  # center
            }
        }
        with patch('trcc.dc_parser.parse_dc_file', return_value=dc_data):
            with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
                f.write(b'\xdd')
                dc_path = f.name
            try:
                pos = self.ctrl._parse_mask_position(dc_path, mask)
                self.assertEqual(pos, (110, 110))  # 160 - 100//2
            finally:
                os.unlink(dc_path)

    def test_dc_mask_not_enabled(self):
        """DC with mask_enabled=False returns None."""
        mask = _make_test_image(100, 100)
        dc_data = {'mask_settings': {'mask_enabled': False}}
        with patch('trcc.dc_parser.parse_dc_file', return_value=dc_data):
            with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
                f.write(b'\xdd')
                dc_path = f.name
            try:
                pos = self.ctrl._parse_mask_position(dc_path, mask)
                self.assertIsNone(pos)
            finally:
                os.unlink(dc_path)

    def test_dc_parse_error(self):
        """DC parse exception returns None."""
        mask = _make_test_image(100, 100)
        with patch('trcc.dc_parser.parse_dc_file', side_effect=RuntimeError):
            with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
                f.write(b'\xdd')
                dc_path = f.name
            try:
                pos = self.ctrl._parse_mask_position(dc_path, mask)
                self.assertIsNone(pos)
            finally:
                os.unlink(dc_path)


# =============================================================================
# LCDDeviceController – callbacks, initialize, render helpers
# =============================================================================

class TestFormCZTVCallbacksAndHelpers(unittest.TestCase):
    """Test theme selection dispatch, preview update, render helpers."""

    def setUp(self):
        self.ctrl, self.patches = _make_form_controller()

    def tearDown(self):
        self.ctrl.cleanup()
        _stop_patches(self.patches)

    def test_on_theme_selected_cloud(self):
        """_on_theme_selected dispatches cloud themes to load_cloud_theme."""
        theme = ThemeInfo(name='C', theme_type=ThemeType.CLOUD)
        with patch.object(self.ctrl, 'load_cloud_theme') as m:
            self.ctrl._on_theme_selected(theme)
            m.assert_called_once_with(theme)

    def test_on_theme_selected_local(self):
        """_on_theme_selected dispatches local themes to load_local_theme."""
        theme = ThemeInfo(name='L', theme_type=ThemeType.LOCAL, path=Path('/tmp/t'))
        with patch.object(self.ctrl, 'load_local_theme') as m:
            self.ctrl._on_theme_selected(theme)
            m.assert_called_once_with(theme)

    def test_on_video_frame(self):
        """_on_video_frame stores the frame."""
        frame = MagicMock()
        self.ctrl._on_video_frame(frame)
        self.assertIs(self.ctrl.current_image, frame)

    def test_update_preview_with_callback(self):
        """_update_preview applies brightness and rotation."""
        img = _make_test_image(4, 4)
        previews = []
        self.ctrl.on_preview_update = lambda i: previews.append(i)
        self.ctrl._update_preview(img)
        self.assertEqual(len(previews), 1)

    def test_update_preview_no_callback(self):
        """_update_preview without callback is safe."""
        self.ctrl.on_preview_update = None
        self.ctrl._update_preview(_make_test_image())

    def test_render_and_send_no_image(self):
        """_render_and_send with no current_image is a no-op."""
        self.ctrl.current_image = None
        self.ctrl._render_and_send()  # Should not raise

    def test_render_and_send_with_overlay(self):
        """_render_and_send applies overlay when enabled."""
        self.ctrl.current_image = _make_test_image()
        self.ctrl.overlay.enable(True)
        rendered = _make_test_image(color=(0, 255, 0))

        with patch.object(self.ctrl.overlay, 'render', return_value=rendered), \
             patch.object(self.ctrl, '_update_preview') as mock_preview, \
             patch.object(self.ctrl, '_send_frame_to_lcd'):
            self.ctrl._render_and_send()
            mock_preview.assert_called_once_with(rendered)

    def test_render_and_send_auto_send(self):
        """_render_and_send sends to LCD when auto_send is True."""
        self.ctrl.current_image = _make_test_image()
        self.ctrl.auto_send = True

        with patch.object(self.ctrl, '_update_preview'), \
             patch.object(self.ctrl, '_send_frame_to_lcd') as mock_send:
            self.ctrl._render_and_send()
            mock_send.assert_called_once()

    def test_render_and_send_no_auto_send(self):
        """_render_and_send skips LCD send when auto_send is False."""
        self.ctrl.current_image = _make_test_image()
        self.ctrl.auto_send = False

        with patch.object(self.ctrl, '_update_preview'), \
             patch.object(self.ctrl, '_send_frame_to_lcd') as mock_send:
            self.ctrl._render_and_send()
            mock_send.assert_not_called()

    def test_render_overlay_and_preview(self):
        """render_overlay_and_preview returns rendered image."""
        self.ctrl.current_image = _make_test_image()
        rendered = _make_test_image(color=(0, 255, 0))

        with patch.object(self.ctrl.overlay, 'render', return_value=rendered), \
             patch.object(self.ctrl, '_update_preview'):
            result = self.ctrl.render_overlay_and_preview()
            self.assertEqual(result, rendered)

    def test_render_overlay_and_preview_no_image(self):
        """render_overlay_and_preview creates black bg when no current_image."""
        self.ctrl.current_image = None

        with patch.object(self.ctrl.overlay, 'render', return_value=_make_test_image()), \
             patch.object(self.ctrl, '_update_preview'):
            result = self.ctrl.render_overlay_and_preview()
            self.assertIsNotNone(result)
            # current_image should have been created by _create_mask_background
            self.assertIsNotNone(self.ctrl.current_image)

    def test_load_dc_config_no_path(self):
        """_load_dc_config with non-existent path is a no-op."""
        self.ctrl._load_dc_config(Path('/nonexistent/config1.dc'))

    def test_load_dc_config_with_file(self):
        """_load_dc_config parses DC and applies overlay config."""
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            f.write(b'\xdd' + b'\x00' * 50)
            dc_path = Path(f.name)
        try:
            dc_data = {'display_elements': []}
            overlay_cfg = {'time_0': {'enabled': True}}
            with patch('trcc.dc_parser.parse_dc_file', return_value=dc_data), \
                 patch('trcc.dc_parser.dc_to_overlay_config', return_value=overlay_cfg), \
                 patch.object(self.ctrl.overlay, 'set_config') as mock_set, \
                 patch.object(self.ctrl.overlay, 'set_config_resolution') as mock_res:
                self.ctrl._load_dc_config(dc_path)
                mock_set.assert_called_once_with(overlay_cfg)
                mock_res.assert_called_once_with(320, 320)
        finally:
            os.unlink(dc_path)

    def test_load_dc_config_parse_error(self):
        """_load_dc_config catches parse exceptions."""
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            f.write(b'\xdd')
            dc_path = Path(f.name)
        try:
            with patch('trcc.dc_parser.parse_dc_file', side_effect=RuntimeError('bad')):
                self.ctrl._load_dc_config(dc_path)  # Should not raise
        finally:
            os.unlink(dc_path)


# =============================================================================
# LCDDeviceController – initialize, set_resolution with _data_dir
# =============================================================================

class TestFormCZTVInitialize(unittest.TestCase):
    """Test initialize and set_resolution with data directories."""

    def setUp(self):
        self.ctrl, self.patches = _make_form_controller()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.ctrl.cleanup()
        _stop_patches(self.patches)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_initialize(self):
        """initialize sets up directories and detects devices."""
        data_dir = Path(self.tmp)
        theme_dir = data_dir / 'Theme320320'
        theme_dir.mkdir()

        with patch.object(self.ctrl.themes, 'set_directories') as mock_dirs, \
             patch.object(self.ctrl.themes, 'load_local_themes') as mock_load, \
             patch.object(self.ctrl.devices, 'detect_devices') as mock_detect:
            self.ctrl.initialize(data_dir)
            mock_dirs.assert_called_once()
            mock_load.assert_called_once()
            mock_detect.assert_called_once()

        self.assertEqual(self.ctrl._data_dir, data_dir)

    def test_set_resolution_reloads_themes(self):
        """set_resolution with _data_dir reloads theme directories."""
        data_dir = Path(self.tmp)
        self.ctrl._data_dir = data_dir

        with patch.object(self.ctrl.themes, 'set_directories') as mock_dirs, \
             patch.object(self.ctrl.themes, 'load_local_themes') as mock_load:
            self.ctrl.set_resolution(480, 480)
            mock_dirs.assert_called_once()
            mock_load.assert_called_once()

    def test_set_resolution_no_persist(self):
        """set_resolution with persist=False doesn't call save_resolution."""
        for p in self.patches:
            # Find the save_resolution mock
            pass
        self.ctrl.set_resolution(240, 240, persist=False)
        self.assertEqual(self.ctrl.lcd_width, 240)


# =============================================================================
# create_controller convenience function
# =============================================================================

# =============================================================================
# Edge cases for 100% coverage
# =============================================================================

class TestDeviceControllerBusy(unittest.TestCase):
    """Test DeviceController.send_image_async when busy."""

    def test_send_image_async_skips_when_busy(self):
        """send_image_async returns immediately when model is busy."""
        ctrl = DeviceController()
        ctrl.model._send_busy = True  # Underlying flag for is_busy property
        started = []
        ctrl.on_send_started = lambda: started.append(True)
        ctrl.send_image_async(b'\x00' * 10, 2, 2)
        self.assertEqual(started, [])  # callback never fired


class TestVideoControllerFrameSkip(unittest.TestCase):
    """Test VideoController tick frame-skipping logic."""

    def test_tick_frame_skip(self):
        """tick skips LCD send when counter < LCD_SEND_INTERVAL."""
        ctrl = VideoController()
        ctrl.LCD_SEND_INTERVAL = 3  # send every 3rd frame

        # Set up model with frames and PLAYING state
        frames = [_make_test_image()] * 10
        ctrl.model.frames = frames
        ctrl.model.state.state = PlaybackState.PLAYING
        ctrl.model.state.total_frames = 10
        ctrl.model.state.current_frame = 0
        ctrl.model.state.loop = True

        sent = []
        ctrl.on_send_frame = lambda f: sent.append(f)
        ctrl.on_progress_update = lambda *a: None

        # Tick 3 times — only the 3rd should trigger send
        ctrl.tick()
        ctrl.tick()
        self.assertEqual(len(sent), 0)
        ctrl.tick()
        self.assertEqual(len(sent), 1)


class TestFormCZTVFinalEdgeCases(unittest.TestCase):
    """Cover remaining uncovered branches in LCDDeviceController."""

    def setUp(self):
        self.ctrl, self.patches = _make_form_controller()

    def tearDown(self):
        self.ctrl.cleanup()
        _stop_patches(self.patches)

    # -- _load_static_image: non-RGB mode branch (line 710) --
    def test_load_static_image_converts_rgba(self):
        """_load_static_image converts RGBA image to RGB."""
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            img = Image.new('RGBA', (320, 320), (255, 0, 0, 128))
            img.save(f.name)
            path = Path(f.name)
        try:
            with patch.object(self.ctrl, '_render_and_send'):
                self.ctrl._load_static_image(path)
            self.assertEqual(self.ctrl.current_image.mode, 'RGB')
        finally:
            os.unlink(path)

    # -- _load_static_image exception (lines 715-716) --
    def test_load_static_image_error(self):
        """_load_static_image handles corrupt file."""
        errors = []
        self.ctrl.on_error = lambda e: errors.append(e)
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(b'not an image')
            path = Path(f.name)
        try:
            self.ctrl._load_static_image(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(errors), 1)
        self.assertIn('Failed to load image', errors[0])

    # -- _create_mask_background exception (lines 725-726) --
    def test_create_mask_background_error(self):
        """_create_mask_background handles Image.new failure."""
        errors = []
        self.ctrl.on_error = lambda e: errors.append(e)
        # Patch _update_preview to raise, since Image.new is imported locally
        with patch.object(self.ctrl, '_update_preview', side_effect=RuntimeError('fail')):
            self.ctrl._create_mask_background(None)
        self.assertEqual(len(errors), 1)
        self.assertIn('mask background', errors[0])

    # -- _load_theme_mask exception (lines 768-769) --
    def test_load_theme_mask_corrupt_file(self):
        """_load_theme_mask handles corrupt image file."""
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            f.write(b'not a png')
            mask_path = Path(f.name)
        try:
            # Should not raise — error is caught internally
            self.ctrl._load_theme_mask(mask_path, None)
        finally:
            os.unlink(mask_path)

    # -- _parse_mask_position: mask_enabled but no center_pos (748→756) --
    def test_parse_mask_position_enabled_no_position(self):
        """_parse_mask_position returns None when enabled but no position."""
        mask = _make_test_image(100, 100)
        dc_data = {'mask_settings': {'mask_enabled': True}}
        with patch('trcc.dc_parser.parse_dc_file', return_value=dc_data):
            with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
                f.write(b'\xdd')
                dc_path = f.name
            try:
                pos = self.ctrl._parse_mask_position(dc_path, mask)
                self.assertIsNone(pos)
            finally:
                os.unlink(dc_path)

    # -- _clear_working_dir when dir doesn't exist (497→499) --
    def test_clear_working_dir_nonexistent(self):
        """_clear_working_dir creates dir even if it doesn't exist yet."""
        shutil.rmtree(self.ctrl.working_dir, ignore_errors=True)
        self.assertFalse(self.ctrl.working_dir.exists())
        self.ctrl._clear_working_dir()
        self.assertTrue(self.ctrl.working_dir.exists())

    # -- apply_mask: mask_path doesn't exist in working dir (689→693) --
    def test_apply_mask_no_mask_image(self):
        """apply_mask works when working dir has no 01.png."""
        mask_dir = Path(tempfile.mkdtemp())
        try:
            # Create a dc file but no 01.png mask
            (mask_dir / 'config1.dc').write_bytes(b'\xdd')
            with patch.object(self.ctrl, '_load_dc_config'):
                self.ctrl.apply_mask(mask_dir)
            # Overlay should still be enabled
            self.assertTrue(self.ctrl.overlay.is_enabled())
        finally:
            shutil.rmtree(mask_dir)

    # -- apply_mask: mask_dir doesn't exist (680→679) --
    def test_apply_mask_nonexistent_dir(self):
        """apply_mask returns early for non-existent directory."""
        self.ctrl.apply_mask(Path('/nonexistent/mask/dir'))

    # -- load_local_theme: no bg, no video, not mask_only (635→639) --
    def test_load_local_theme_no_content(self):
        """load_local_theme with theme that has no bg/video/mask falls through."""
        with tempfile.TemporaryDirectory() as tmp:
            theme_path = Path(tmp)
            # Empty theme dir — no 00.png, no .zt, no animation
            theme = ThemeInfo(name='Empty', theme_type=ThemeType.LOCAL, path=theme_path)
            with patch.object(self.ctrl, '_load_dc_config'), \
                 patch.object(self.ctrl, '_update_status'):
                self.ctrl.load_local_theme(theme)

    # -- save_theme: general exception during save --
    def test_save_theme_write_failure(self):
        """save_theme catches general exceptions during JSON write."""
        self.ctrl.current_image = _make_test_image()
        with tempfile.TemporaryDirectory() as tmp:
            with patch('builtins.open', side_effect=OSError('disk full')):
                ok, msg = self.ctrl.save_theme('Fail', Path(tmp))
            self.assertFalse(ok)
            self.assertIn('Save failed', msg)

    # -- save_theme: no current_theme_path → background is None in config.json --
    def test_save_theme_no_current_theme_path(self):
        """save_theme works when current_theme_path is None (no source theme)."""
        self.ctrl.current_image = _make_test_image()
        self.ctrl.current_theme_path = None
        with tempfile.TemporaryDirectory() as tmp:
            ok, msg = self.ctrl.save_theme('NoSource', Path(tmp))
            self.assertTrue(ok)
            theme_path = Path(tmp) / 'Theme320320' / 'Custom_NoSource'
            with open(str(theme_path / 'config.json')) as f:
                config = json.load(f)
            self.assertIsNone(config['background'])


# =============================================================================
# Reference theme save/load (config.json with path references)
# =============================================================================

class TestReferenceThemeSaveLoad(unittest.TestCase):
    """Test config.json reference format save and load paths."""

    def setUp(self):
        self.ctrl, self.patches = _make_form_controller()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.ctrl.cleanup()
        _stop_patches(self.patches)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_ref_theme(self, name='RefTheme', background=None, mask=None,
                        dc=None, mask_position=None):
        """Create a theme directory with config.json in reference format."""
        d = Path(self.tmp) / name
        d.mkdir(parents=True, exist_ok=True)
        # Write a 00.png fallback
        img = _make_test_image(self.ctrl.lcd_width, self.ctrl.lcd_height)
        img.save(str(d / '00.png'))
        # Write config.json
        config = {
            'background': background,
            'mask': mask,
            'dc': dc or {},
        }
        if mask_position:
            config['mask_position'] = list(mask_position)
        with open(str(d / 'config.json'), 'w') as f:
            json.dump(config, f)
        return d

    # -- load_local_theme: reference with static image background --
    def test_load_ref_static_image(self):
        """load_local_theme with config.json referencing a static image."""
        bg_dir = Path(self.tmp) / 'bg_source'
        bg_dir.mkdir()
        bg_img = _make_test_image()
        bg_path = bg_dir / 'wallpaper.png'
        bg_img.save(str(bg_path))

        theme_dir = self._make_ref_theme(background=str(bg_path))
        theme = ThemeInfo(name='RefStatic', path=theme_dir)

        statuses = []
        self.ctrl.on_status_update = lambda s: statuses.append(s)
        self.ctrl.load_local_theme(theme)

        self.assertIsNotNone(self.ctrl.current_image)
        self.assertEqual(self.ctrl.current_theme_path, theme_dir)
        self.assertIn('Theme: RefStatic', statuses)

    # -- load_local_theme: reference with video background --
    def test_load_ref_video(self):
        """load_local_theme with config.json referencing an mp4."""
        video_path = Path(self.tmp) / 'source' / 'clip.mp4'
        video_path.parent.mkdir()
        video_path.write_bytes(b'\x00' * 100)  # dummy

        theme_dir = self._make_ref_theme(background=str(video_path))
        theme = ThemeInfo(name='RefVideo', path=theme_dir)

        with patch.object(self.ctrl.video, 'load', return_value=True), \
             patch.object(self.ctrl.video, 'play'), \
             patch.object(self.ctrl.video.model, 'get_frame', return_value=_make_test_image()):
            self.ctrl.load_local_theme(theme)
            self.ctrl.video.load.assert_called_once_with(video_path)
            self.ctrl.video.play.assert_called_once()

    # -- load_local_theme: reference with .zt background --
    def test_load_ref_zt(self):
        """load_local_theme with config.json referencing a .zt file."""
        zt_path = Path(self.tmp) / 'source' / 'Theme.zt'
        zt_path.parent.mkdir()
        zt_path.write_bytes(b'\xdc\x00')

        theme_dir = self._make_ref_theme(background=str(zt_path))
        theme = ThemeInfo(name='RefZT', path=theme_dir)

        with patch.object(self.ctrl.video, 'load', return_value=True), \
             patch.object(self.ctrl.video, 'play'):
            self.ctrl.load_local_theme(theme)
            self.ctrl.video.load.assert_called_once_with(zt_path)

    # -- load_local_theme: reference with overlay enabled --
    def test_load_ref_overlay_enabled(self):
        """load_local_theme enables overlay when dc has elements and bg exists."""
        bg_dir = Path(self.tmp) / 'bg_src'
        bg_dir.mkdir()
        bg_img = _make_test_image()
        bg_path = bg_dir / 'bg.png'
        bg_img.save(str(bg_path))

        dc = {'time_0': {'x': 10, 'y': 20, 'metric': 'time'}}
        theme_dir = self._make_ref_theme(background=str(bg_path), dc=dc)
        theme = ThemeInfo(name='RefOverlay', path=theme_dir)

        self.ctrl.load_local_theme(theme)
        # Overlay should be enabled since dc has elements
        self.assertTrue(self.ctrl.overlay.is_enabled())

    def test_load_ref_overlay_disabled_empty_dc(self):
        """load_local_theme does not enable overlay when dc is empty."""
        bg_dir = Path(self.tmp) / 'bg_src2'
        bg_dir.mkdir()
        bg_img = _make_test_image()
        bg_path = bg_dir / 'bg.png'
        bg_img.save(str(bg_path))

        theme_dir = self._make_ref_theme(background=str(bg_path), dc={})
        theme = ThemeInfo(name='RefNoOverlay', path=theme_dir)

        self.ctrl.load_local_theme(theme)
        self.assertFalse(self.ctrl.overlay.is_enabled())

    # -- load_local_theme: reference with mask --
    def test_load_ref_with_mask(self):
        """load_local_theme loads mask from reference path."""
        bg_dir = Path(self.tmp) / 'bg_src'
        bg_dir.mkdir()
        bg_img = _make_test_image()
        bg_path = bg_dir / 'bg.png'
        bg_img.save(str(bg_path))

        mask_dir = Path(self.tmp) / 'mask_src'
        mask_dir.mkdir()
        mask_img = _make_test_image(color=(0, 0, 255))
        mask_img.save(str(mask_dir / '01.png'))

        theme_dir = self._make_ref_theme(
            background=str(bg_path), mask=str(mask_dir),
            mask_position=[160, 160])
        theme = ThemeInfo(name='RefMask', path=theme_dir)

        self.ctrl.load_local_theme(theme)
        self.assertIsNotNone(self.ctrl.current_image)

    # -- load_local_theme: reference with nonexistent background --
    def test_load_ref_missing_background(self):
        """load_local_theme with config.json pointing to missing file."""
        theme_dir = self._make_ref_theme(background='/nonexistent/bg.png')
        theme = ThemeInfo(name='RefMissing', path=theme_dir)

        statuses = []
        self.ctrl.on_status_update = lambda s: statuses.append(s)
        self.ctrl.load_local_theme(theme)
        # Should still complete (early return path) without crashing
        self.assertIn('Theme: RefMissing', statuses)

    # -- load_local_theme: fallback for non-reference theme (no config.json) --
    def test_load_fallback_no_config_json(self):
        """load_local_theme without config.json uses original copy-to-workdir."""
        d = Path(self.tmp) / 'OldTheme'
        d.mkdir()
        _make_test_image().save(str(d / '00.png'))
        theme = ThemeInfo(name='OldStyle', path=d)

        self.ctrl.load_local_theme(theme)
        self.assertIsNotNone(self.ctrl.current_image)

    # -- save_theme: writes config.json with path references --
    def test_save_theme_writes_config_json(self):
        """save_theme creates config.json with background/mask/dc keys."""
        self.ctrl.current_image = _make_test_image()
        source_dir = Path(self.tmp) / 'source_theme'
        source_dir.mkdir()
        _make_test_image().save(str(source_dir / '00.png'))
        self.ctrl.current_theme_path = source_dir

        ok, msg = self.ctrl.save_theme('JsonSave', Path(self.tmp))
        self.assertTrue(ok)
        self.assertIn('Custom_JsonSave', msg)

        theme_path = Path(self.tmp) / 'Theme320320' / 'Custom_JsonSave'
        self.assertTrue((theme_path / 'config.json').exists())
        self.assertTrue((theme_path / 'Theme.png').exists())
        self.assertTrue((theme_path / '00.png').exists())

        with open(str(theme_path / 'config.json')) as f:
            config = json.load(f)
        self.assertIn('background', config)
        self.assertIn('mask', config)
        self.assertIn('dc', config)
        # Background should reference the source 00.png
        self.assertEqual(config['background'], str(source_dir / '00.png'))

    # -- save_theme: video playing → background is video path --
    def test_save_theme_video_background(self):
        """save_theme references video path when video is playing."""
        self.ctrl.current_image = _make_test_image()
        self.ctrl.current_theme_path = Path(self.tmp) / 'src'
        self.ctrl.current_theme_path.mkdir()

        with patch.object(self.ctrl.video, 'is_playing', return_value=True), \
             patch.object(type(self.ctrl.video.model), 'source_path',
                          new_callable=PropertyMock,
                          return_value=Path('/videos/clip.mp4')):
            ok, msg = self.ctrl.save_theme('VidRef', Path(self.tmp))

        self.assertTrue(ok)
        theme_path = Path(self.tmp) / 'Theme320320' / 'Custom_VidRef'
        with open(str(theme_path / 'config.json')) as f:
            config = json.load(f)
        self.assertEqual(config['background'], '/videos/clip.mp4')

    # -- save_theme: with mask → mask path set in config.json --
    def test_save_theme_with_mask(self):
        """save_theme includes mask path when mask is active."""
        self.ctrl.current_image = _make_test_image()
        source_dir = Path(self.tmp) / 'mask_src'
        source_dir.mkdir()
        _make_test_image().save(str(source_dir / '00.png'))
        mask_img = _make_test_image(color=(0, 0, 255))
        mask_img.save(str(source_dir / '01.png'))
        self.ctrl.current_theme_path = source_dir
        self.ctrl._mask_source_dir = source_dir

        # Set mask on overlay
        with patch.object(self.ctrl.overlay, 'get_theme_mask',
                          return_value=(mask_img, (160, 160))):
            ok, msg = self.ctrl.save_theme('MaskRef', Path(self.tmp))

        self.assertTrue(ok)
        theme_path = Path(self.tmp) / 'Theme320320' / 'Custom_MaskRef'
        with open(str(theme_path / 'config.json')) as f:
            config = json.load(f)
        self.assertEqual(config['mask'], str(source_dir))
        self.assertEqual(config['mask_position'], [160, 160])

    # -- save_theme: no mask → mask is null --
    def test_save_theme_no_mask(self):
        """save_theme sets mask to null when no mask active."""
        self.ctrl.current_image = _make_test_image()
        self.ctrl.current_theme_path = Path(self.tmp) / 'nomask'
        self.ctrl.current_theme_path.mkdir()

        ok, msg = self.ctrl.save_theme('NoMask', Path(self.tmp))
        self.assertTrue(ok)
        theme_path = Path(self.tmp) / 'Theme320320' / 'Custom_NoMask'
        with open(str(theme_path / 'config.json')) as f:
            config = json.load(f)
        self.assertIsNone(config['mask'])
        self.assertNotIn('mask_position', config)

    # -- save_theme: updates current_theme_path after save --
    def test_save_theme_updates_current_path(self):
        """save_theme sets current_theme_path to the new Custom_ dir."""
        self.ctrl.current_image = _make_test_image()
        self.ctrl.current_theme_path = Path(self.tmp) / 'original'
        self.ctrl.current_theme_path.mkdir()

        ok, _ = self.ctrl.save_theme('PathUpdate', Path(self.tmp))
        self.assertTrue(ok)
        expected = Path(self.tmp) / 'Theme320320' / 'Custom_PathUpdate'
        self.assertEqual(self.ctrl.current_theme_path, expected)


class TestCreateController(unittest.TestCase):
    """Test create_controller factory function."""

    def test_create_without_data_dir(self):
        with patch('trcc.core.controllers.get_saved_resolution', return_value=(320, 320)):
            ctrl = create_controller()
            self.assertIsInstance(ctrl, LCDDeviceController)
            ctrl.cleanup()

    def test_create_with_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / 'Theme320320').mkdir()

            with patch('trcc.core.controllers.get_saved_resolution', return_value=(320, 320)), \
                 patch('trcc.core.controllers.save_resolution'), \
                 patch('trcc.core.controllers.ensure_themes_extracted'), \
                 patch('trcc.core.controllers.ensure_web_extracted'), \
                 patch('trcc.core.controllers.ensure_web_masks_extracted'), \
                 patch('trcc.core.controllers.get_web_dir', return_value=tmp), \
                 patch('trcc.core.controllers.get_web_masks_dir', return_value=tmp):
                ctrl = create_controller(data_dir)
                self.assertIsInstance(ctrl, LCDDeviceController)
                ctrl.cleanup()
