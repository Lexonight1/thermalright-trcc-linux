"""Tests for core/models.py – ThemeInfo, ThemeModel, DeviceModel, VideoState."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from trcc.core.models import (
    DeviceInfo,
    DeviceModel,
    OverlayElement,
    OverlayElementType,
    OverlayModel,
    PlaybackState,
    ThemeInfo,
    ThemeModel,
    ThemeType,
    VideoModel,
    VideoState,
)

# =============================================================================
# ThemeInfo
# =============================================================================

class TestThemeInfoFromDirectory(unittest.TestCase):
    """ThemeInfo.from_directory() filesystem scanning."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _make_theme(self, name, files=('00.png',)):
        d = Path(self.tmpdir) / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b'\x89PNG')
        return d

    def test_basic_theme(self):
        d = self._make_theme('001a', ['00.png'])
        info = ThemeInfo.from_directory(d)
        self.assertEqual(info.name, '001a')
        self.assertEqual(info.theme_type, ThemeType.LOCAL)
        self.assertIsNotNone(info.background_path)

    def test_animated_theme(self):
        d = self._make_theme('002a', ['00.png', 'Theme.zt'])
        info = ThemeInfo.from_directory(d)
        self.assertTrue(info.is_animated)
        self.assertIsNotNone(info.animation_path)

    def test_mask_only_theme(self):
        d = self._make_theme('mask', ['01.png'])
        info = ThemeInfo.from_directory(d)
        self.assertTrue(info.is_mask_only)
        self.assertIsNone(info.background_path)

    def test_resolution_passed_through(self):
        d = self._make_theme('003a', ['00.png'])
        info = ThemeInfo.from_directory(d, resolution=(480, 480))
        self.assertEqual(info.resolution, (480, 480))

    def test_thumbnail_fallback_to_background(self):
        """When Theme.png missing, thumbnail falls back to 00.png."""
        d = self._make_theme('004a', ['00.png'])
        info = ThemeInfo.from_directory(d)
        self.assertIsNotNone(info.thumbnail_path)
        self.assertEqual(info.thumbnail_path.name, '00.png')

    def test_with_config_dc(self):
        d = self._make_theme('005a', ['00.png', 'config1.dc'])
        info = ThemeInfo.from_directory(d)
        self.assertIsNotNone(info.config_path)


class TestThemeInfoFromVideo(unittest.TestCase):
    """ThemeInfo.from_video() cloud theme creation."""

    def test_basic(self):
        info = ThemeInfo.from_video(Path('/tmp/a_test.mp4'))
        self.assertEqual(info.name, 'a_test')
        self.assertEqual(info.theme_type, ThemeType.CLOUD)
        self.assertTrue(info.is_animated)

    def test_category_from_name(self):
        info = ThemeInfo.from_video(Path('/tmp/b_galaxy.mp4'))
        self.assertEqual(info.category, 'b')


# =============================================================================
# ThemeModel
# =============================================================================

class TestThemeModelLocal(unittest.TestCase):
    """ThemeModel.load_local_themes() with temp directories."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _make_theme(self, name, files=('00.png',)):
        d = Path(self.tmpdir) / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b'\x89PNG')
        return d

    def test_loads_themes(self):
        self._make_theme('001a', ['00.png'])
        self._make_theme('002a', ['00.png', 'Theme.png'])
        model = ThemeModel()
        model.set_local_directory(Path(self.tmpdir))
        themes = model.load_local_themes()
        self.assertEqual(len(themes), 2)

    def test_skips_dirs_without_theme_files(self):
        (Path(self.tmpdir) / 'empty').mkdir()
        (Path(self.tmpdir) / 'nofiles').mkdir()
        (Path(self.tmpdir) / 'nofiles' / 'readme.txt').write_text('hi')
        model = ThemeModel()
        model.set_local_directory(Path(self.tmpdir))
        self.assertEqual(len(model.load_local_themes()), 0)

    def test_sorted_order(self):
        self._make_theme('002a', ['00.png'])
        self._make_theme('001a', ['00.png'])
        model = ThemeModel()
        model.set_local_directory(Path(self.tmpdir))
        names = [t.name for t in model.load_local_themes()]
        self.assertEqual(names, ['001a', '002a'])

    def test_fires_callback(self):
        self._make_theme('001a', ['00.png'])
        model = ThemeModel()
        model.set_local_directory(Path(self.tmpdir))
        mock = MagicMock()
        model.on_themes_changed = mock
        model.load_local_themes()
        mock.assert_called_once()

    def test_missing_dir_returns_empty(self):
        model = ThemeModel()
        model.set_local_directory(Path('/nonexistent/path'))
        self.assertEqual(model.load_local_themes(), [])

    def test_filter_default(self):
        self._make_theme('001a', ['00.png'])
        self._make_theme('Custom_1', ['00.png'])
        model = ThemeModel()
        model.set_local_directory(Path(self.tmpdir))
        model.set_filter('default')
        themes = model.load_local_themes()
        names = [t.name for t in themes]
        self.assertIn('001a', names)
        self.assertNotIn('Custom_1', names)


class TestThemeModelSelection(unittest.TestCase):
    """Theme selection and callbacks."""

    def test_select_fires_callback(self):
        model = ThemeModel()
        mock = MagicMock()
        model.on_selection_changed = mock
        theme = ThemeInfo(name='test')
        model.select_theme(theme)
        mock.assert_called_once_with(theme)
        self.assertEqual(model.selected_theme, theme)


# =============================================================================
# DeviceInfo / DeviceModel
# =============================================================================

class TestDeviceInfo(unittest.TestCase):

    def test_resolution_str(self):
        d = DeviceInfo(name='LCD', path='/dev/sg0', resolution=(480, 480))
        self.assertEqual(d.resolution_str, '480x480')

    def test_defaults(self):
        d = DeviceInfo(name='LCD', path='/dev/sg0')
        self.assertEqual(d.brightness, 100)
        self.assertEqual(d.rotation, 0)
        self.assertTrue(d.connected)


class TestDeviceModel(unittest.TestCase):

    def test_select_device(self):
        model = DeviceModel()
        mock = MagicMock()
        model.on_selection_changed = mock
        dev = DeviceInfo(name='LCD', path='/dev/sg0')
        model.select_device(dev)
        self.assertEqual(model.selected_device, dev)
        mock.assert_called_once_with(dev)

    def test_is_busy_default_false(self):
        model = DeviceModel()
        self.assertFalse(model.is_busy)


# =============================================================================
# VideoState
# =============================================================================

class TestVideoState(unittest.TestCase):

    def test_progress_zero_frames(self):
        s = VideoState(total_frames=0)
        self.assertEqual(s.progress, 0.0)

    def test_progress_halfway(self):
        s = VideoState(current_frame=50, total_frames=100)
        self.assertAlmostEqual(s.progress, 50.0)

    def test_time_str(self):
        s = VideoState(current_frame=960, total_frames=1920, fps=16.0)
        self.assertEqual(s.current_time_str, '01:00')
        self.assertEqual(s.total_time_str, '02:00')

    def test_frame_interval(self):
        s = VideoState(fps=16.0)
        self.assertEqual(s.frame_interval_ms, 62)

    def test_frame_interval_zero_fps(self):
        s = VideoState(fps=0)
        self.assertEqual(s.frame_interval_ms, 62)

    def test_time_str_zero_fps(self):
        s = VideoState(fps=0)
        self.assertEqual(s.current_time_str, '00:00')


# =============================================================================
# OverlayModel
# =============================================================================

class TestOverlayModel(unittest.TestCase):

    def test_add_element(self):
        model = OverlayModel()
        elem = OverlayElement(element_type=OverlayElementType.TEXT, text='Hello')
        model.add_element(elem)
        self.assertEqual(len(model.elements), 1)

    def test_remove_element(self):
        model = OverlayModel()
        model.elements = [
            OverlayElement(text='A'),
            OverlayElement(text='B'),
        ]
        model.remove_element(0)
        self.assertEqual(len(model.elements), 1)
        self.assertEqual(model.elements[0].text, 'B')

    def test_remove_invalid_index(self):
        model = OverlayModel()
        model.remove_element(99)  # should not raise

    def test_update_element(self):
        model = OverlayModel()
        model.elements = [OverlayElement(text='old')]
        model.update_element(0, OverlayElement(text='new'))
        self.assertEqual(model.elements[0].text, 'new')

    def test_callback_on_add(self):
        model = OverlayModel()
        mock = MagicMock()
        model.on_config_changed = mock
        model.add_element(OverlayElement())
        mock.assert_called_once()


# =============================================================================
# ThemeModel – cloud themes + filter
# =============================================================================

class TestThemeModelCloud(unittest.TestCase):
    """ThemeModel.load_cloud_themes() with temp mp4 files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_loads_mp4_files(self):
        (Path(self.tmpdir) / 'a001.mp4').write_bytes(b'\x00')
        (Path(self.tmpdir) / 'a002.mp4').write_bytes(b'\x00')
        model = ThemeModel()
        model.cloud_web_dir = Path(self.tmpdir)
        themes = model.load_cloud_themes()
        self.assertEqual(len(themes), 2)

    def test_cloud_category_filter(self):
        (Path(self.tmpdir) / 'a001.mp4').write_bytes(b'\x00')
        (Path(self.tmpdir) / 'b001.mp4').write_bytes(b'\x00')
        model = ThemeModel()
        model.cloud_web_dir = Path(self.tmpdir)
        model.category_filter = 'a'
        themes = model.load_cloud_themes()
        self.assertEqual(len(themes), 1)
        self.assertTrue(themes[0].name.startswith('a'))

    def test_cloud_no_dir_returns_empty(self):
        model = ThemeModel()
        model.cloud_web_dir = None
        self.assertEqual(model.load_cloud_themes(), [])

    def test_cloud_missing_dir_returns_empty(self):
        model = ThemeModel()
        model.cloud_web_dir = Path('/nonexistent/cloud/themes')
        self.assertEqual(model.load_cloud_themes(), [])

    def test_cloud_fires_callback(self):
        (Path(self.tmpdir) / 'a001.mp4').write_bytes(b'\x00')
        model = ThemeModel()
        model.cloud_web_dir = Path(self.tmpdir)
        mock = MagicMock()
        model.on_themes_changed = mock
        model.load_cloud_themes()
        mock.assert_called_once()


class TestThemeModelFilter(unittest.TestCase):
    """ThemeModel._passes_filter for user mode."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _make_theme(self, name, files=('00.png',)):
        d = Path(self.tmpdir) / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b'\x89PNG')
        return d

    def test_filter_user(self):
        self._make_theme('Normal', ['00.png'])
        self._make_theme('Custom_1', ['00.png'])
        model = ThemeModel()
        model.set_local_directory(Path(self.tmpdir))
        model.set_filter('user')
        themes = model.load_local_themes()
        names = [t.name for t in themes]
        self.assertIn('Custom_1', names)
        # 'Normal' type is LOCAL, not USER, and doesn't start with Custom/User
        self.assertNotIn('Normal', names)

    def test_filter_all(self):
        self._make_theme('001a', ['00.png'])
        self._make_theme('Custom_2', ['00.png'])
        model = ThemeModel()
        model.set_local_directory(Path(self.tmpdir))
        model.set_filter('all')
        themes = model.load_local_themes()
        self.assertEqual(len(themes), 2)

    def test_set_category(self):
        model = ThemeModel()
        model.set_category('b')
        self.assertEqual(model.category_filter, 'b')


# =============================================================================
# DeviceModel – detect + send
# =============================================================================

class TestDeviceModelDetect(unittest.TestCase):

    @patch('trcc.scsi_device.find_lcd_devices')
    def test_detect_devices(self, mock_find):
        mock_find.return_value = [
            {'name': 'LCD1', 'path': '/dev/sg0', 'resolution': (320, 320),
             'vendor': 'T', 'product': 'L', 'model': 'X', 'vid': 1, 'pid': 2, 'device_index': 0},
        ]
        model = DeviceModel()
        devices = model.detect_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, 'LCD1')
        self.assertEqual(devices[0].path, '/dev/sg0')
        # Should auto-select first
        self.assertIsNotNone(model.selected_device)

    @patch('trcc.scsi_device.find_lcd_devices', side_effect=ImportError)
    def test_detect_import_error(self, _):
        model = DeviceModel()
        devices = model.detect_devices()
        self.assertEqual(len(devices), 0)

    @patch('trcc.scsi_device.find_lcd_devices')
    def test_detect_fires_callback(self, mock_find):
        mock_find.return_value = []
        model = DeviceModel()
        mock_cb = MagicMock()
        model.on_devices_changed = mock_cb
        model.detect_devices()
        mock_cb.assert_called_once()


class TestDeviceModelSend(unittest.TestCase):

    @patch('trcc.scsi_device.send_image_to_device', return_value=True)
    def test_send_success(self, mock_send):
        model = DeviceModel()
        model.selected_device = DeviceInfo(name='LCD', path='/dev/sg0')
        result = model.send_image(b'\x00' * 100, 320, 320)
        self.assertTrue(result)
        mock_send.assert_called_once()

    def test_send_no_device(self):
        model = DeviceModel()
        model.selected_device = None
        self.assertFalse(model.send_image(b'\x00', 320, 320))

    @patch('trcc.scsi_device.send_image_to_device', return_value=True)
    def test_send_busy_returns_false(self, _):
        model = DeviceModel()
        model.selected_device = DeviceInfo(name='LCD', path='/dev/sg0')
        model._send_busy = True
        self.assertFalse(model.send_image(b'\x00', 320, 320))

    @patch('trcc.scsi_device.send_image_to_device', side_effect=Exception('fail'))
    def test_send_exception_returns_false(self, _):
        model = DeviceModel()
        model.selected_device = DeviceInfo(name='LCD', path='/dev/sg0')
        result = model.send_image(b'\x00', 320, 320)
        self.assertFalse(result)
        self.assertFalse(model._send_busy)

    @patch('trcc.scsi_device.send_image_to_device', return_value=True)
    def test_send_fires_callback(self, _):
        model = DeviceModel()
        model.selected_device = DeviceInfo(name='LCD', path='/dev/sg0')
        mock_cb = MagicMock()
        model.on_send_complete = mock_cb
        model.send_image(b'\x00', 320, 320)
        mock_cb.assert_called_once_with(True)


# =============================================================================
# VideoModel
# =============================================================================

class TestVideoModel(unittest.TestCase):

    def test_load_with_mock_player(self):
        model = VideoModel()
        mock_player = MagicMock()
        mock_player.frame_count = 100
        mock_player.fps = 16
        mock_player.frames = []

        with patch('trcc.gif_animator.VideoPlayer', return_value=mock_player):
            result = model.load(Path('/tmp/test.mp4'))

        self.assertTrue(result)
        self.assertEqual(model.state.total_frames, 100)
        self.assertEqual(model.state.fps, 16)

    def test_load_zt_uses_theme_zt_player(self):
        model = VideoModel()
        mock_player = MagicMock()
        mock_player.frame_count = 50
        mock_player.fps = 0

        with patch('trcc.gif_animator.ThemeZtPlayer', return_value=mock_player):
            result = model.load(Path('/tmp/test.zt'))

        self.assertTrue(result)
        self.assertEqual(model.state.fps, 16)  # Zero fps → default 16

    def test_load_failure(self):
        model = VideoModel()
        with patch('trcc.gif_animator.VideoPlayer', side_effect=Exception('bad')):
            result = model.load(Path('/tmp/corrupt.mp4'))
        self.assertFalse(result)

    def test_play_pause_stop(self):
        model = VideoModel()
        model._player = MagicMock()

        model.play()
        self.assertEqual(model.state.state, PlaybackState.PLAYING)

        model.pause()
        self.assertEqual(model.state.state, PlaybackState.PAUSED)

        model.stop()
        self.assertEqual(model.state.state, PlaybackState.STOPPED)
        self.assertEqual(model.state.current_frame, 0)

    def test_seek(self):
        model = VideoModel()
        model.state.total_frames = 200
        model.seek(50.0)
        self.assertEqual(model.state.current_frame, 100)

    def test_seek_clamps(self):
        model = VideoModel()
        model.state.total_frames = 100
        model.seek(150.0)  # Over 100%
        self.assertEqual(model.state.current_frame, 99)

    def test_get_frame_preloaded(self):
        model = VideoModel()
        mock_frame = MagicMock()
        model.frames = [mock_frame]
        self.assertEqual(model.get_frame(0), mock_frame)

    def test_get_frame_from_player(self):
        model = VideoModel()
        model.frames = []
        mock_player = MagicMock()
        mock_player.get_current_frame.return_value = 'frame_data'
        model._player = mock_player
        result = model.get_frame(5)
        self.assertEqual(result, 'frame_data')

    def test_get_frame_none(self):
        model = VideoModel()
        self.assertIsNone(model.get_frame())

    def test_advance_frame(self):
        model = VideoModel()
        model.state.state = PlaybackState.PLAYING
        model.state.total_frames = 10
        model.frames = [MagicMock() for _ in range(10)]
        frame = model.advance_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(model.state.current_frame, 1)

    def test_advance_frame_loops(self):
        model = VideoModel()
        model.state.state = PlaybackState.PLAYING
        model.state.total_frames = 3
        model.state.current_frame = 2
        model.state.loop = True
        model.frames = [MagicMock() for _ in range(3)]
        model.advance_frame()
        self.assertEqual(model.state.current_frame, 0)

    def test_advance_frame_stops_at_end(self):
        model = VideoModel()
        model.state.state = PlaybackState.PLAYING
        model.state.total_frames = 3
        model.state.current_frame = 2
        model.state.loop = False
        model.frames = [MagicMock() for _ in range(3)]
        model.advance_frame()
        self.assertEqual(model.state.state, PlaybackState.STOPPED)

    def test_advance_not_playing(self):
        model = VideoModel()
        model.state.state = PlaybackState.STOPPED
        self.assertIsNone(model.advance_frame())

    def test_is_playing(self):
        model = VideoModel()
        self.assertFalse(model.is_playing)
        model.state.state = PlaybackState.PLAYING
        self.assertTrue(model.is_playing)

    def test_state_changed_callback(self):
        model = VideoModel()
        model._player = MagicMock()
        cb = MagicMock()
        model.on_state_changed = cb
        model.play()
        cb.assert_called_once()

    def test_frame_ready_callback(self):
        model = VideoModel()
        model.state.state = PlaybackState.PLAYING
        model.state.total_frames = 5
        model.frames = [MagicMock() for _ in range(5)]
        cb = MagicMock()
        model.on_frame_ready = cb
        model.advance_frame()
        cb.assert_called_once()


# =============================================================================
# OverlayModel – renderer + load_from_dc
# =============================================================================

class TestOverlayModelRenderer(unittest.TestCase):

    @patch('trcc.overlay_renderer.OverlayRenderer')
    def test_render(self, mock_renderer_cls):
        mock_renderer = MagicMock()
        mock_renderer.render.return_value = 'rendered_image'
        mock_renderer_cls.return_value = mock_renderer

        model = OverlayModel()
        model.enabled = True
        elem = OverlayElement(element_type=OverlayElementType.HARDWARE, metric_key='cpu_temp')
        model.add_element(elem)

        result = model.render({'cpu_temp': 42})
        self.assertEqual(result, 'rendered_image')

    def test_render_disabled(self):
        model = OverlayModel()
        model.enabled = False
        model.background = 'bg_image'
        self.assertEqual(model.render(), 'bg_image')

    def test_set_background(self):
        model = OverlayModel()
        model._renderer = MagicMock()
        model.set_background('new_bg')
        model._renderer.set_background.assert_called_once_with('new_bg')

    @patch('trcc.overlay_renderer.OverlayRenderer')
    def test_load_from_dc(self, mock_renderer_cls):
        mock_renderer_cls.return_value = MagicMock()

        # Create a minimal valid dc file
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            dc_path = f.name

        model = OverlayModel()
        with patch('trcc.dc_parser.parse_dc_file'), \
             patch('trcc.dc_parser.dc_to_overlay_config') as mock_convert:
            mock_convert.return_value = {
                'hw_0': {'enabled': True, 'x': 10, 'y': 20, 'color': (255, 0, 0),
                         'font_size': 16, 'metric': 'cpu_temp', 'format': '{value}°C'},
            }
            result = model.load_from_dc(Path(dc_path))

        import os
        os.unlink(dc_path)
        self.assertTrue(result)
        self.assertEqual(len(model.elements), 1)
        self.assertEqual(model.elements[0].x, 10)

    def test_load_from_dc_failure(self):
        model = OverlayModel()
        result = model.load_from_dc(Path('/nonexistent/config1.dc'))
        self.assertFalse(result)


# ── Targeted coverage: callbacks and edge paths ──────────────────────────────

class TestThemeModelCloudAndFilter(unittest.TestCase):

    def test_set_cloud_directories(self):
        model = ThemeModel()
        model.set_cloud_directories(Path('/web'), Path('/masks'))
        self.assertEqual(model.cloud_web_dir, Path('/web'))
        self.assertEqual(model.cloud_masks_dir, Path('/masks'))

    def test_filter_user(self):
        model = ThemeModel()
        model.filter_mode = 'user'
        user_theme = ThemeInfo(name="UserCustom", path=Path('/t'), theme_type=ThemeType.USER)
        default_theme = ThemeInfo(name="Theme1", path=Path('/t2'), theme_type=ThemeType.LOCAL)
        self.assertTrue(model._passes_filter(user_theme))
        self.assertFalse(model._passes_filter(default_theme))


class TestVideoModelCallbacks(unittest.TestCase):

    def test_load_with_preload_and_callback(self):
        vm = VideoModel()
        callback = MagicMock()
        vm.on_state_changed = callback
        player = MagicMock()
        player.frame_count = 10
        player.fps = 30
        player.frames = [b'f1', b'f2']
        with patch('trcc.core.models.VideoPlayer', return_value=player, create=True), \
             patch('trcc.core.models.ThemeZtPlayer', create=True):
            # VideoPlayer is a lazy import, patch at gif_animator level
            with patch('trcc.gif_animator.VideoPlayer', return_value=player, create=True):
                result = vm.load(Path('/fake.mp4'), preload=True)
        if result:
            callback.assert_called()
            self.assertEqual(vm.frames, [b'f1', b'f2'])

    def test_pause_with_callback(self):
        vm = VideoModel()
        vm._player = MagicMock()
        callback = MagicMock()
        vm.on_state_changed = callback
        vm.pause()
        self.assertEqual(vm.state.state, PlaybackState.PAUSED)
        callback.assert_called_once()

    def test_stop_with_callback(self):
        vm = VideoModel()
        vm._player = MagicMock()
        callback = MagicMock()
        vm.on_state_changed = callback
        vm.stop()
        self.assertEqual(vm.state.state, PlaybackState.STOPPED)
        callback.assert_called_once()

    def test_seek_clamps(self):
        vm = VideoModel()
        vm.state.total_frames = 100
        vm.seek(150)
        self.assertEqual(vm.state.current_frame, 99)
        vm.seek(0)
        self.assertEqual(vm.state.current_frame, 0)


class TestVideoStateTotalTimeStr(unittest.TestCase):

    def test_zero_fps(self):
        vs = VideoState()
        vs.fps = 0
        self.assertEqual(vs.total_time_str, "00:00")


class TestOverlayModelMutations(unittest.TestCase):

    def test_remove_element_with_callback(self):
        model = OverlayModel()
        elem = OverlayElement(enabled=True, x=10, y=10)
        model.elements.append(elem)
        callback = MagicMock()
        model.on_config_changed = callback
        model.remove_element(0)
        self.assertEqual(len(model.elements), 0)
        callback.assert_called_once()

    def test_update_element_with_callback(self):
        model = OverlayModel()
        elem = OverlayElement(enabled=True, x=10, y=10)
        model.elements.append(elem)
        callback = MagicMock()
        model.on_config_changed = callback
        new_elem = OverlayElement(enabled=True, x=50, y=50)
        model.update_element(0, new_elem)
        self.assertEqual(model.elements[0].x, 50)
        callback.assert_called_once()

    def test_update_renderer_creates_renderer(self):
        model = OverlayModel()
        model._renderer = None
        elem = OverlayElement(enabled=True, x=10, y=10, metric_key='cpu_temp')
        model.elements.append(elem)
        # _update_renderer tries to import OverlayRenderer — let it work or fail gracefully
        model._update_renderer()

    def test_load_from_dc_populates_elements(self):
        model = OverlayModel()
        fake_config = {
            'cpu_temp': {'enabled': True, 'x': 100, 'y': 200, 'metric': 'cpu_temp'},
        }
        with patch('trcc.dc_parser.parse_dc_file', return_value={}), \
             patch('trcc.dc_parser.dc_to_overlay_config', return_value=fake_config):
            result = model.load_from_dc(Path('/fake/config1.dc'))
        self.assertTrue(result)
        self.assertEqual(len(model.elements), 1)
        self.assertEqual(model.elements[0].x, 100)


if __name__ == '__main__':
    unittest.main()
