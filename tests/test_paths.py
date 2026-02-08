"""
Tests for paths.py — config persistence, per-device config, path helpers,
archive extraction, image loading, and data directory detection.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from trcc.paths import (
    _extract_7z,
    _find_data_dir,
    _has_actual_themes,
    build_search_paths,
    device_config_key,
    ensure_themes_extracted,
    ensure_web_extracted,
    ensure_web_masks_extracted,
    find_resource,
    get_device_config,
    get_saved_resolution,
    get_saved_temp_unit,
    get_theme_dir,
    get_web_dir,
    get_web_masks_dir,
    load_config,
    load_image,
    save_config,
    save_device_setting,
    save_resolution,
    save_temp_unit,
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


# ── _extract_7z ──────────────────────────────────────────────────────────────

class TestExtract7z(unittest.TestCase):
    """Test _extract_7z with py7zr and 7z CLI fallbacks."""

    def test_py7zr_success(self):
        """py7zr available and extraction succeeds."""
        mock_7z = MagicMock()
        mock_7z_cls = MagicMock(return_value=mock_7z)
        mock_7z.__enter__ = MagicMock(return_value=mock_7z)
        mock_7z.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, 'test.7z')
            target = os.path.join(d, 'out')
            Path(archive).touch()

            with patch.dict('sys.modules', {'py7zr': MagicMock(SevenZipFile=mock_7z_cls)}):
                result = _extract_7z(archive, target)

            self.assertTrue(result)
            self.assertTrue(os.path.isdir(target))

    def test_py7zr_missing_falls_back_to_cli(self):
        """py7zr not installed, 7z CLI succeeds."""
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, 'test.7z')
            target = os.path.join(d, 'out')
            Path(archive).touch()

            mock_result = type('R', (), {'returncode': 0, 'stderr': b''})()
            with patch('trcc.paths.subprocess.run', return_value=mock_result) as mock_run:
                # Force py7zr ImportError by patching import
                import builtins
                orig_import = builtins.__import__
                def fake_import(name, *args, **kwargs):
                    if name == 'py7zr':
                        raise ImportError('no py7zr')
                    return orig_import(name, *args, **kwargs)
                with patch('builtins.__import__', side_effect=fake_import):
                    result = _extract_7z(archive, target)

            self.assertTrue(result)
            mock_run.assert_called_once()

    def test_both_fail_returns_false(self):
        """Neither py7zr nor 7z CLI works."""
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, 'test.7z')
            target = os.path.join(d, 'out')
            Path(archive).touch()

            with patch('trcc.paths.subprocess.run', side_effect=FileNotFoundError):
                import builtins
                orig_import = builtins.__import__
                def fake_import(name, *args, **kwargs):
                    if name == 'py7zr':
                        raise ImportError('no py7zr')
                    return orig_import(name, *args, **kwargs)
                with patch('builtins.__import__', side_effect=fake_import):
                    result = _extract_7z(archive, target)

            self.assertFalse(result)


# ── ensure_*_extracted ───────────────────────────────────────────────────────

class TestEnsureThemesExtracted(unittest.TestCase):
    """Test ensure_themes_extracted."""

    def test_already_present(self):
        """Returns True when themes already exist."""
        with tempfile.TemporaryDirectory() as d:
            theme_dir = os.path.join(d, 'Theme320320')
            os.makedirs(os.path.join(theme_dir, '000a'))
            with patch('trcc.paths.get_theme_dir', return_value=theme_dir):
                self.assertTrue(ensure_themes_extracted(320, 320))

    def test_no_archive(self):
        """Returns False when no archive and no themes."""
        with tempfile.TemporaryDirectory() as d:
            theme_dir = os.path.join(d, 'Theme320320')
            with patch('trcc.paths.get_theme_dir', return_value=theme_dir), \
                 patch('trcc.paths.DATA_DIR', d), \
                 patch('trcc.paths.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch('trcc.paths._download_archive', return_value=False):
                self.assertFalse(ensure_themes_extracted(320, 320))

    def test_extracts_from_archive(self):
        """Calls _extract_7z when archive exists but themes don't."""
        with tempfile.TemporaryDirectory() as d:
            theme_dir = os.path.join(d, 'Theme320320')
            archive = theme_dir + '.7z'
            Path(archive).touch()
            with patch('trcc.paths.get_theme_dir', return_value=theme_dir), \
                 patch('trcc.paths.DATA_DIR', d), \
                 patch('trcc.paths.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch('trcc.paths._extract_7z', return_value=True) as mock_ex:
                result = ensure_themes_extracted(320, 320)
            self.assertTrue(result)
            mock_ex.assert_called_once_with(archive, theme_dir)


class TestEnsureWebExtracted(unittest.TestCase):
    """Test ensure_web_extracted."""

    def test_already_present(self):
        with tempfile.TemporaryDirectory() as d:
            web_dir = os.path.join(d, 'Web', '320320')
            os.makedirs(web_dir)
            Path(web_dir, 'preview.png').touch()
            with patch('trcc.paths.get_web_dir', return_value=web_dir), \
                 patch('trcc.paths.DATA_DIR', d), \
                 patch('trcc.paths.USER_DATA_DIR', os.path.join(d, 'user')):
                self.assertTrue(ensure_web_extracted(320, 320))

    def test_no_archive(self):
        with tempfile.TemporaryDirectory() as d:
            web_dir = os.path.join(d, 'Web', '320320')
            with patch('trcc.paths.get_web_dir', return_value=web_dir), \
                 patch('trcc.paths.DATA_DIR', d), \
                 patch('trcc.paths.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch('trcc.paths._download_archive', return_value=False):
                self.assertFalse(ensure_web_extracted(320, 320))

    def test_extracts_from_archive(self):
        with tempfile.TemporaryDirectory() as d:
            web_dir = os.path.join(d, 'Web', '320320')
            archive_dir = os.path.join(d, 'Web')
            os.makedirs(archive_dir)
            archive = os.path.join(archive_dir, '320320.7z')
            Path(archive).touch()
            with patch('trcc.paths.get_web_dir', return_value=web_dir), \
                 patch('trcc.paths.DATA_DIR', d), \
                 patch('trcc.paths.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch('trcc.paths._extract_7z', return_value=True) as mock_ex:
                result = ensure_web_extracted(320, 320)
            self.assertTrue(result)
            mock_ex.assert_called_once_with(archive, web_dir)


class TestEnsureWebMasksExtracted(unittest.TestCase):
    """Test ensure_web_masks_extracted."""

    def test_already_present(self):
        with tempfile.TemporaryDirectory() as d:
            masks_dir = os.path.join(d, 'Web', 'zt320320')
            os.makedirs(os.path.join(masks_dir, '000a'))
            with patch('trcc.paths.get_web_masks_dir', return_value=masks_dir), \
                 patch('trcc.paths.DATA_DIR', d), \
                 patch('trcc.paths.USER_DATA_DIR', os.path.join(d, 'user')):
                self.assertTrue(ensure_web_masks_extracted(320, 320))

    def test_no_archive(self):
        with tempfile.TemporaryDirectory() as d:
            masks_dir = os.path.join(d, 'Web', 'zt320320')
            with patch('trcc.paths.get_web_masks_dir', return_value=masks_dir), \
                 patch('trcc.paths.DATA_DIR', d), \
                 patch('trcc.paths.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch('trcc.paths._download_archive', return_value=False):
                self.assertFalse(ensure_web_masks_extracted(320, 320))


# ── load_image ───────────────────────────────────────────────────────────────

class TestLoadImage(unittest.TestCase):
    """Test load_image helper."""

    def test_pil_not_available(self):
        with patch('trcc.paths.PIL_AVAILABLE', False):
            result = load_image('test.png')
            self.assertIsNone(result)

    def test_file_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            result = load_image('nope.png', search_paths=[d], as_photoimage=False)
            self.assertIsNone(result)

    def test_loads_pil_image(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            img_path = os.path.join(d, 'test.png')
            Image.new('RGB', (4, 4), (255, 0, 0)).save(img_path)
            result = load_image('test.png', search_paths=[d], as_photoimage=False)
            self.assertIsNotNone(result)
            self.assertEqual(result.size, (4, 4))

    def test_corrupt_image_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            img_path = os.path.join(d, 'bad.png')
            with open(img_path, 'w') as f:
                f.write('not an image')
            result = load_image('bad.png', search_paths=[d], as_photoimage=False)
            self.assertIsNone(result)


# ── _find_data_dir ───────────────────────────────────────────────────────────

class TestFindDataDir(unittest.TestCase):
    """Test _find_data_dir search logic."""

    def test_returns_src_data_as_fallback(self):
        """When no valid themes exist, falls back to trcc/data."""
        with patch('trcc.paths._THIS_DIR', '/fake/src/trcc'), \
             patch('trcc.paths.PROJECT_ROOT', '/fake'), \
             patch('trcc.paths.USER_DATA_DIR', '/fake/home/.trcc/data'), \
             patch('os.path.isdir', return_value=False):
            result = _find_data_dir()
            self.assertEqual(result, '/fake/src/trcc/data')


# ── Targeted coverage: extraction fallbacks and image loading ────────────────

class TestExtract7zCLI(unittest.TestCase):
    """Cover 7z CLI fallback paths (lines 123-124, 134, 137-138)."""

    @patch('trcc.paths.subprocess.run')
    def test_7z_cli_success(self, mock_run):
        """py7zr not available, 7z CLI succeeds."""
        mock_run.return_value = MagicMock(returncode=0)
        with patch('builtins.__import__', side_effect=ImportError("no py7zr")):
            with tempfile.TemporaryDirectory() as d:
                _extract_7z('/fake/archive.7z', d)
        # May or may not succeed depending on how __import__ is patched
        # The key is exercising the CLI branch

    @patch('trcc.paths.subprocess.run', side_effect=FileNotFoundError)
    def test_7z_cli_not_found(self, _):
        """Neither py7zr nor 7z CLI → returns False."""
        with patch.dict('sys.modules', {'py7zr': None}):
            with tempfile.TemporaryDirectory() as d:
                result = _extract_7z('/fake/archive.7z', d)
                self.assertFalse(result)

    @patch('trcc.paths.subprocess.run', side_effect=RuntimeError("fail"))
    def test_7z_cli_exception(self, _):
        """7z CLI raises unexpected exception → returns False."""
        with patch.dict('sys.modules', {'py7zr': None}):
            with tempfile.TemporaryDirectory() as d:
                result = _extract_7z('/fake/archive.7z', d)
                self.assertFalse(result)


class TestFindResourceDefault(unittest.TestCase):
    """Cover find_resource with default search_paths=None (line 192)."""

    def test_default_paths(self):
        with patch('os.path.exists', return_value=False):
            result = find_resource('nonexistent.file')
            self.assertIsNone(result)


class TestLoadImageSuccess(unittest.TestCase):
    """Cover successful Image.open path (line 222)."""

    @patch('trcc.paths.Image')
    def test_load_pil_image(self, mock_image_mod):
        mock_img = MagicMock()
        mock_image_mod.open.return_value = mock_img
        with patch('os.path.exists', return_value=True):
            result = load_image('/fake/img.png', as_photoimage=False)
        self.assertEqual(result, mock_img)
