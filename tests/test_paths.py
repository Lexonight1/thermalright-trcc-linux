"""
Tests for paths.py — config persistence, per-device config, path helpers.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from trcc.paths import (
    device_config_key,
    get_device_config,
    get_saved_resolution,
    get_saved_temp_unit,
    get_theme_dir,
    get_web_dir,
    get_web_masks_dir,
    load_config,
    save_config,
    save_device_setting,
    save_resolution,
    save_temp_unit,
    find_resource,
    build_search_paths,
    _has_actual_themes,
)


class TestPathHelpers(unittest.TestCase):
    """Test path construction helpers."""

    def test_get_theme_dir(self):
        path = get_theme_dir(320, 320)
        self.assertTrue(path.endswith('Theme320320'))

    def test_get_theme_dir_other_resolution(self):
        path = get_theme_dir(480, 480)
        self.assertTrue(path.endswith('Theme480480'))

    def test_get_web_dir(self):
        path = get_web_dir(320, 320)
        self.assertTrue(path.endswith(os.path.join('Web', '320320')))

    def test_get_web_masks_dir(self):
        path = get_web_masks_dir(320, 320)
        self.assertTrue(path.endswith(os.path.join('Web', 'zt320320')))


class TestHasActualThemes(unittest.TestCase):
    """Test _has_actual_themes helper."""

    def test_nonexistent_dir(self):
        self.assertFalse(_has_actual_themes('/nonexistent/path'))

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(_has_actual_themes(d))

    def test_dir_with_only_gitkeep(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, '.gitkeep').touch()
            self.assertFalse(_has_actual_themes(d))

    def test_dir_with_subdirs(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, '000a'))
            self.assertTrue(_has_actual_themes(d))


class TestFindResource(unittest.TestCase):
    """Test find_resource and build_search_paths."""

    def test_find_existing(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'test.png').touch()
            result = find_resource('test.png', [d])
            self.assertIsNotNone(result)
            self.assertTrue(result.endswith('test.png'))

    def test_find_missing(self):
        with tempfile.TemporaryDirectory() as d:
            result = find_resource('nope.png', [d])
            self.assertIsNone(result)

    def test_build_search_paths_with_custom(self):
        paths = build_search_paths('/custom/dir')
        self.assertEqual(paths[0], '/custom/dir')

    def test_build_search_paths_without_custom(self):
        paths = build_search_paths()
        self.assertGreater(len(paths), 0)


class TestConfigPersistence(unittest.TestCase):
    """Test load_config / save_config with temp config file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.paths.CONFIG_PATH', self.config_path),
            patch('trcc.paths.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_missing_returns_empty(self):
        self.assertEqual(load_config(), {})

    def test_save_and_load(self):
        save_config({'key': 'value'})
        cfg = load_config()
        self.assertEqual(cfg['key'], 'value')

    def test_load_corrupt_returns_empty(self):
        with open(self.config_path, 'w') as f:
            f.write('not json{{{')
        self.assertEqual(load_config(), {})

    def test_save_overwrites(self):
        save_config({'a': 1})
        save_config({'b': 2})
        cfg = load_config()
        self.assertNotIn('a', cfg)
        self.assertEqual(cfg['b'], 2)


class TestResolutionConfig(unittest.TestCase):
    """Test resolution save/load."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.paths.CONFIG_PATH', self.config_path),
            patch('trcc.paths.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_resolution(self):
        self.assertEqual(get_saved_resolution(), (320, 320))

    def test_save_and_load_resolution(self):
        save_resolution(480, 480)
        self.assertEqual(get_saved_resolution(), (480, 480))

    def test_invalid_resolution_returns_default(self):
        save_config({'resolution': 'bad'})
        self.assertEqual(get_saved_resolution(), (320, 320))


class TestTempUnitConfig(unittest.TestCase):
    """Test temperature unit save/load."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.paths.CONFIG_PATH', self.config_path),
            patch('trcc.paths.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_temp_unit(self):
        self.assertEqual(get_saved_temp_unit(), 0)

    def test_save_fahrenheit(self):
        save_temp_unit(1)
        self.assertEqual(get_saved_temp_unit(), 1)


class TestDeviceConfigKey(unittest.TestCase):
    """Test device_config_key formatting."""

    def test_format(self):
        key = device_config_key(0, 0x87CD, 0x70DB)
        self.assertEqual(key, '0:87cd_70db')

    def test_format_with_index(self):
        key = device_config_key(2, 0x0402, 0x3922)
        self.assertEqual(key, '2:0402_3922')

    def test_zero_padded(self):
        key = device_config_key(0, 0x0001, 0x0002)
        self.assertEqual(key, '0:0001_0002')


class TestPerDeviceConfig(unittest.TestCase):
    """Test per-device config save/load."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.paths.CONFIG_PATH', self.config_path),
            patch('trcc.paths.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_missing_device_returns_empty(self):
        self.assertEqual(get_device_config('0:87cd_70db'), {})

    def test_save_and_get(self):
        save_device_setting('0:87cd_70db', 'brightness_level', 3)
        cfg = get_device_config('0:87cd_70db')
        self.assertEqual(cfg['brightness_level'], 3)

    def test_multiple_settings_same_device(self):
        save_device_setting('0:87cd_70db', 'brightness_level', 2)
        save_device_setting('0:87cd_70db', 'rotation', 90)
        cfg = get_device_config('0:87cd_70db')
        self.assertEqual(cfg['brightness_level'], 2)
        self.assertEqual(cfg['rotation'], 90)

    def test_multiple_devices_independent(self):
        save_device_setting('0:87cd_70db', 'brightness_level', 1)
        save_device_setting('1:0402_3922', 'brightness_level', 3)
        self.assertEqual(get_device_config('0:87cd_70db')['brightness_level'], 1)
        self.assertEqual(get_device_config('1:0402_3922')['brightness_level'], 3)

    def test_save_complex_value(self):
        carousel = {'enabled': True, 'interval': 5, 'themes': ['Theme1', 'Theme3']}
        save_device_setting('0:87cd_70db', 'carousel', carousel)
        cfg = get_device_config('0:87cd_70db')
        self.assertEqual(cfg['carousel']['enabled'], True)
        self.assertEqual(cfg['carousel']['themes'], ['Theme1', 'Theme3'])

    def test_save_overlay_config(self):
        overlay = {
            'enabled': True,
            'config': {'time_0': {'x': 10, 'y': 10, 'metric': 'time'}},
        }
        save_device_setting('0:87cd_70db', 'overlay', overlay)
        cfg = get_device_config('0:87cd_70db')
        self.assertTrue(cfg['overlay']['enabled'])
        self.assertIn('time_0', cfg['overlay']['config'])

    def test_overwrite_setting(self):
        save_device_setting('0:87cd_70db', 'rotation', 0)
        save_device_setting('0:87cd_70db', 'rotation', 180)
        self.assertEqual(get_device_config('0:87cd_70db')['rotation'], 180)

    def test_device_config_preserves_global(self):
        save_temp_unit(1)
        save_device_setting('0:87cd_70db', 'brightness_level', 2)
        self.assertEqual(get_saved_temp_unit(), 1)

    def test_config_json_structure(self):
        """Verify the on-disk JSON structure matches documentation."""
        save_resolution(480, 480)
        save_temp_unit(1)
        save_device_setting('0:87cd_70db', 'theme_path', '/some/path')
        save_device_setting('0:87cd_70db', 'brightness_level', 2)

        with open(self.config_path) as f:
            raw = json.load(f)

        self.assertEqual(raw['resolution'], [480, 480])
        self.assertEqual(raw['temp_unit'], 1)
        self.assertIn('devices', raw)
        self.assertIn('0:87cd_70db', raw['devices'])
        self.assertEqual(raw['devices']['0:87cd_70db']['theme_path'], '/some/path')
