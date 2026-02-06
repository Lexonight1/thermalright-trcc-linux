"""Tests for core/models.py – ThemeInfo, ThemeModel, DeviceModel, VideoState."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

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


if __name__ == '__main__':
    unittest.main()
