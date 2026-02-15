"""
Tests for data_repository.py (directory/extraction helpers) and conf.py (config persistence).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from trcc.conf import Settings, load_config, save_config
from trcc.data_repository import (
    DataManager,
    Resources,
    ThemeDir,
    _find_data_dir,
)


class TestPathHelpers(unittest.TestCase):
    """Test path construction helpers."""

    def test_get_theme_dir(self):
        path = str(ThemeDir.for_resolution(320, 320))
        self.assertTrue(path.endswith('theme320320'))

    def test_get_theme_dir_other_resolution(self):
        path = str(ThemeDir.for_resolution(480, 480))
        self.assertTrue(path.endswith('theme480480'))

    def test_get_web_dir(self):
        path = DataManager.get_web_dir(320, 320)
        self.assertTrue(path.endswith(os.path.join('web', '320320')))

    def test_get_web_masks_dir(self):
        path = DataManager.get_web_masks_dir(320, 320)
        self.assertTrue(path.endswith(os.path.join('web', 'zt320320')))


class TestHasActualThemes(unittest.TestCase):
    """Test ThemeDir.has_themes helper."""

    def test_nonexistent_dir(self):
        self.assertFalse(ThemeDir.has_themes('/nonexistent/path'))

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(ThemeDir.has_themes(d))

    def test_dir_with_only_gitkeep(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, '.gitkeep').touch()
            self.assertFalse(ThemeDir.has_themes(d))

    def test_dir_with_subdirs_and_pngs(self):
        with tempfile.TemporaryDirectory() as d:
            subdir = os.path.join(d, '000a')
            os.mkdir(subdir)
            Path(subdir, '01.png').touch()
            self.assertTrue(ThemeDir.has_themes(d))

    def test_dir_with_subdirs_no_pngs(self):
        """Subdirs without PNGs (e.g. leftover config1.dc) are not valid themes."""
        with tempfile.TemporaryDirectory() as d:
            subdir = os.path.join(d, '000a')
            os.mkdir(subdir)
            Path(subdir, 'config1.dc').touch()
            self.assertFalse(ThemeDir.has_themes(d))


class TestFindResource(unittest.TestCase):
    """Test Resources.find and Resources.build_search_paths."""

    def test_find_existing(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'test.png').touch()
            result = Resources.find('test.png', [d])
            self.assertIsNotNone(result)
            self.assertTrue(result.endswith('test.png'))

    def test_find_missing(self):
        with tempfile.TemporaryDirectory() as d:
            result = Resources.find('nope.png', [d])
            self.assertIsNone(result)

    def test_build_search_paths_with_custom(self):
        paths = Resources.build_search_paths('/custom/dir')
        self.assertEqual(paths[0], '/custom/dir')

    def test_build_search_paths_without_custom(self):
        paths = Resources.build_search_paths()
        self.assertGreater(len(paths), 0)


class TestConfigPersistence(unittest.TestCase):
    """Test load_config / save_config with temp config file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.conf.CONFIG_PATH', self.config_path),
            patch('trcc.conf.CONFIG_DIR', self.tmp),
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
            patch('trcc.conf.CONFIG_PATH', self.config_path),
            patch('trcc.conf.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_resolution(self):
        self.assertEqual(Settings._get_saved_resolution(), (320, 320))

    def test_save_and_load_resolution(self):
        Settings._save_resolution(480, 480)
        self.assertEqual(Settings._get_saved_resolution(), (480, 480))

    def test_invalid_resolution_returns_default(self):
        save_config({'resolution': 'bad'})
        self.assertEqual(Settings._get_saved_resolution(), (320, 320))


class TestTempUnitConfig(unittest.TestCase):
    """Test temperature unit save/load."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.conf.CONFIG_PATH', self.config_path),
            patch('trcc.conf.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_temp_unit(self):
        self.assertEqual(Settings._get_saved_temp_unit(), 0)

    def test_save_fahrenheit(self):
        Settings._save_temp_unit(1)
        self.assertEqual(Settings._get_saved_temp_unit(), 1)


class TestResolutionInstalled(unittest.TestCase):
    """Test one-time resolution install tracking."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.user_data = os.path.join(self.tmp, 'user_data')
        os.makedirs(self.user_data)
        self.pkg_data = os.path.join(self.tmp, 'pkg_data')
        os.makedirs(self.pkg_data)
        self.patches = [
            patch('trcc.conf.CONFIG_PATH', self.config_path),
            patch('trcc.conf.CONFIG_DIR', self.tmp),
            patch('trcc.data_repository.USER_DATA_DIR', self.user_data),
            patch('trcc.data_repository.DATA_DIR', self.pkg_data),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_theme_dir(self, width, height):
        """Create a fake theme directory with a subfolder + PNG so has_themes passes."""
        theme_dir = os.path.join(self.user_data, f'theme{width}{height}', 'DefaultTheme')
        os.makedirs(theme_dir, exist_ok=True)
        Path(theme_dir, '00.png').touch()

    def test_not_installed_by_default(self):
        self.assertFalse(DataManager.is_resolution_installed(320, 320))

    def test_mark_and_check(self):
        self._create_theme_dir(320, 320)
        DataManager.mark_resolution_installed(320, 320)
        self.assertTrue(DataManager.is_resolution_installed(320, 320))
        self.assertFalse(DataManager.is_resolution_installed(480, 480))

    def test_mark_is_idempotent(self):
        DataManager.mark_resolution_installed(320, 320)
        DataManager.mark_resolution_installed(320, 320)
        config = load_config()
        self.assertEqual(config["installed_resolutions"].count("320x320"), 1)

    def test_multiple_resolutions(self):
        self._create_theme_dir(320, 320)
        self._create_theme_dir(480, 480)
        DataManager.mark_resolution_installed(320, 320)
        DataManager.mark_resolution_installed(480, 480)
        self.assertTrue(DataManager.is_resolution_installed(320, 320))
        self.assertTrue(DataManager.is_resolution_installed(480, 480))

    def test_clear_removes_all(self):
        DataManager.mark_resolution_installed(320, 320)
        DataManager.mark_resolution_installed(480, 480)
        Settings.clear_installed_resolutions()
        self.assertFalse(DataManager.is_resolution_installed(320, 320))
        self.assertFalse(DataManager.is_resolution_installed(480, 480))

    def test_clear_on_empty_config(self):
        # Should not raise
        Settings.clear_installed_resolutions()
        self.assertFalse(DataManager.is_resolution_installed(320, 320))

    def test_marker_without_data_returns_false(self):
        """Config says installed but data was wiped — should return False."""
        DataManager.mark_resolution_installed(320, 320)
        # No theme dir created — simulates pip uninstall wiping data
        self.assertFalse(DataManager.is_resolution_installed(320, 320))


class TestDeviceConfigKey(unittest.TestCase):
    """Test device_config_key formatting."""

    def test_format(self):
        key = Settings.device_config_key(0, 0x87CD, 0x70DB)
        self.assertEqual(key, '0:87cd_70db')

    def test_format_with_index(self):
        key = Settings.device_config_key(2, 0x0402, 0x3922)
        self.assertEqual(key, '2:0402_3922')

    def test_zero_padded(self):
        key = Settings.device_config_key(0, 0x0001, 0x0002)
        self.assertEqual(key, '0:0001_0002')


class TestPerDeviceConfig(unittest.TestCase):
    """Test per-device config save/load."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.conf.CONFIG_PATH', self.config_path),
            patch('trcc.conf.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_missing_device_returns_empty(self):
        self.assertEqual(Settings.get_device_config('0:87cd_70db'), {})

    def test_save_and_get(self):
        Settings.save_device_setting('0:87cd_70db', 'brightness_level', 3)
        cfg = Settings.get_device_config('0:87cd_70db')
        self.assertEqual(cfg['brightness_level'], 3)

    def test_multiple_settings_same_device(self):
        Settings.save_device_setting('0:87cd_70db', 'brightness_level', 2)
        Settings.save_device_setting('0:87cd_70db', 'rotation', 90)
        cfg = Settings.get_device_config('0:87cd_70db')
        self.assertEqual(cfg['brightness_level'], 2)
        self.assertEqual(cfg['rotation'], 90)

    def test_multiple_devices_independent(self):
        Settings.save_device_setting('0:87cd_70db', 'brightness_level', 1)
        Settings.save_device_setting('1:0402_3922', 'brightness_level', 3)
        self.assertEqual(Settings.get_device_config('0:87cd_70db')['brightness_level'], 1)
        self.assertEqual(Settings.get_device_config('1:0402_3922')['brightness_level'], 3)

    def test_save_complex_value(self):
        carousel = {'enabled': True, 'interval': 5, 'themes': ['Theme1', 'Theme3']}
        Settings.save_device_setting('0:87cd_70db', 'carousel', carousel)
        cfg = Settings.get_device_config('0:87cd_70db')
        self.assertEqual(cfg['carousel']['enabled'], True)
        self.assertEqual(cfg['carousel']['themes'], ['Theme1', 'Theme3'])

    def test_save_overlay_config(self):
        overlay = {
            'enabled': True,
            'config': {'time_0': {'x': 10, 'y': 10, 'metric': 'time'}},
        }
        Settings.save_device_setting('0:87cd_70db', 'overlay', overlay)
        cfg = Settings.get_device_config('0:87cd_70db')
        self.assertTrue(cfg['overlay']['enabled'])
        self.assertIn('time_0', cfg['overlay']['config'])

    def test_overwrite_setting(self):
        Settings.save_device_setting('0:87cd_70db', 'rotation', 0)
        Settings.save_device_setting('0:87cd_70db', 'rotation', 180)
        self.assertEqual(Settings.get_device_config('0:87cd_70db')['rotation'], 180)

    def test_device_config_preserves_global(self):
        Settings._save_temp_unit(1)
        Settings.save_device_setting('0:87cd_70db', 'brightness_level', 2)
        self.assertEqual(Settings._get_saved_temp_unit(), 1)

    def test_config_json_structure(self):
        """Verify the on-disk JSON structure matches documentation."""
        Settings._save_resolution(480, 480)
        Settings._save_temp_unit(1)
        Settings.save_device_setting('0:87cd_70db', 'theme_path', '/some/path')
        Settings.save_device_setting('0:87cd_70db', 'brightness_level', 2)

        with open(self.config_path) as f:
            raw = json.load(f)

        self.assertEqual(raw['resolution'], [480, 480])
        self.assertEqual(raw['temp_unit'], 1)
        self.assertIn('devices', raw)
        self.assertIn('0:87cd_70db', raw['devices'])
        self.assertEqual(raw['devices']['0:87cd_70db']['theme_path'], '/some/path')


# -- DataManager.extract_7z ------------------------------------------------

class TestExtract7z(unittest.TestCase):
    """Test extract_7z with 7z CLI."""

    def test_7z_cli_success(self):
        """7z CLI extraction succeeds."""
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, 'test.7z')
            target = os.path.join(d, 'out')
            Path(archive).touch()

            mock_result = type('R', (), {'returncode': 0, 'stderr': b'', 'stdout': ''})()
            with patch('trcc.data_repository.subprocess.run', return_value=mock_result):
                result = DataManager.extract_7z(archive, target)

            self.assertTrue(result)
            self.assertTrue(os.path.isdir(target))

    def test_7z_cli_failure(self):
        """7z CLI returns non-zero exit code."""
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, 'test.7z')
            target = os.path.join(d, 'out')
            Path(archive).touch()

            mock_result = type('R', (), {'returncode': 2, 'stderr': b'error', 'stdout': ''})()
            with patch('trcc.data_repository.subprocess.run', return_value=mock_result):
                result = DataManager.extract_7z(archive, target)

            self.assertFalse(result)

    def test_7z_not_found(self):
        """7z not installed — FileNotFoundError."""
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, 'test.7z')
            target = os.path.join(d, 'out')
            Path(archive).touch()

            with patch('trcc.data_repository.subprocess.run', side_effect=FileNotFoundError):
                result = DataManager.extract_7z(archive, target)

            self.assertFalse(result)


# -- DataManager.ensure_* --------------------------------------------------

class TestEnsureThemesExtracted(unittest.TestCase):
    """Test DataManager.ensure_themes."""

    def test_already_present(self):
        """Returns True when themes already exist."""
        with tempfile.TemporaryDirectory() as d:
            theme_dir = os.path.join(d, 'theme320320')
            sub = os.path.join(theme_dir, '000a')
            os.makedirs(sub)
            Path(sub, '00.png').touch()
            with patch('trcc.data_repository.DATA_DIR', d), \
                 patch('trcc.data_repository.USER_DATA_DIR', os.path.join(d, 'user')):
                self.assertTrue(DataManager.ensure_themes(320, 320))

    def test_no_archive(self):
        """Returns False when no archive and no themes."""
        with tempfile.TemporaryDirectory() as d:
            with patch('trcc.data_repository.DATA_DIR', d), \
                 patch('trcc.data_repository.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch.object(DataManager, 'download_archive', return_value=False):
                self.assertFalse(DataManager.ensure_themes(320, 320))

    def test_extracts_from_archive(self):
        """Calls extract_7z when archive exists but themes don't."""
        with tempfile.TemporaryDirectory() as d:
            user_theme_dir = os.path.join(d, 'user', 'theme320320')
            # Place archive in pkg DATA_DIR
            archive = os.path.join(d, 'theme320320.7z')
            Path(archive).touch()
            with patch('trcc.data_repository.DATA_DIR', d), \
                 patch('trcc.data_repository.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch.object(DataManager, 'extract_7z', return_value=True) as mock_ex:
                result = DataManager.ensure_themes(320, 320)
            self.assertTrue(result)
            # Extracts to user_dir (~/.trcc/data/) so data survives pip upgrades
            mock_ex.assert_called_once_with(archive, user_theme_dir)


class TestEnsureWebExtracted(unittest.TestCase):
    """Test DataManager.ensure_web."""

    def test_already_present(self):
        with tempfile.TemporaryDirectory() as d:
            web_dir = os.path.join(d, 'web', '320320')
            os.makedirs(web_dir)
            Path(web_dir, 'preview.png').touch()
            with patch('trcc.data_repository.DATA_DIR', d), \
                 patch('trcc.data_repository.USER_DATA_DIR', os.path.join(d, 'user')):
                self.assertTrue(DataManager.ensure_web(320, 320))

    def test_no_archive(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('trcc.data_repository.DATA_DIR', d), \
                 patch('trcc.data_repository.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch.object(DataManager, 'download_archive', return_value=False):
                self.assertFalse(DataManager.ensure_web(320, 320))

    def test_extracts_from_archive(self):
        with tempfile.TemporaryDirectory() as d:
            user_web_dir = os.path.join(d, 'user', 'web', '320320')
            archive_dir = os.path.join(d, 'web')
            os.makedirs(archive_dir)
            archive = os.path.join(archive_dir, '320320.7z')
            Path(archive).touch()
            with patch('trcc.data_repository.DATA_DIR', d), \
                 patch('trcc.data_repository.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch.object(DataManager, 'extract_7z', return_value=True) as mock_ex:
                result = DataManager.ensure_web(320, 320)
            self.assertTrue(result)
            mock_ex.assert_called_once_with(archive, user_web_dir)


class TestEnsureWebMasksExtracted(unittest.TestCase):
    """Test DataManager.ensure_web_masks."""

    def test_already_present(self):
        with tempfile.TemporaryDirectory() as d:
            masks_dir = os.path.join(d, 'web', 'zt320320')
            sub = os.path.join(masks_dir, '000a')
            os.makedirs(sub)
            Path(sub, '00.png').touch()  # has_themes needs a .png
            with patch('trcc.data_repository.DATA_DIR', d), \
                 patch('trcc.data_repository.USER_DATA_DIR', os.path.join(d, 'user')):
                self.assertTrue(DataManager.ensure_web_masks(320, 320))

    def test_no_archive(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('trcc.data_repository.DATA_DIR', d), \
                 patch('trcc.data_repository.USER_DATA_DIR', os.path.join(d, 'user')), \
                 patch.object(DataManager, 'download_archive', return_value=False):
                self.assertFalse(DataManager.ensure_web_masks(320, 320))


# -- _find_data_dir ---------------------------------------------------------

class TestFindDataDir(unittest.TestCase):
    """Test _find_data_dir search logic."""

    def test_returns_src_data_as_fallback(self):
        """When no valid themes exist, falls back to trcc/data."""
        with patch('trcc.data_repository._THIS_DIR', '/fake/src/trcc'), \
             patch('trcc.data_repository.PROJECT_ROOT', '/fake'), \
             patch('trcc.data_repository.USER_DATA_DIR', '/fake/home/.trcc/data'), \
             patch('os.path.isdir', return_value=False):
            result = _find_data_dir()
            self.assertEqual(result, '/fake/src/trcc/data')


# -- Extract 7z CLI edge cases ----------------------------------------------

class TestExtract7zCLI(unittest.TestCase):
    """Cover 7z CLI edge cases."""

    @patch('trcc.data_repository.subprocess.run')
    def test_7z_cli_success(self, mock_run):
        """7z CLI succeeds."""
        mock_run.return_value = MagicMock(returncode=0)
        with tempfile.TemporaryDirectory() as d:
            result = DataManager.extract_7z('/fake/archive.7z', d)
        self.assertTrue(result)

    @patch('trcc.data_repository.subprocess.run', side_effect=FileNotFoundError)
    def test_7z_cli_not_found(self, _):
        """7z not installed -> returns False."""
        with tempfile.TemporaryDirectory() as d:
            result = DataManager.extract_7z('/fake/archive.7z', d)
            self.assertFalse(result)

    @patch('trcc.data_repository.subprocess.run', side_effect=RuntimeError("fail"))
    def test_7z_cli_exception(self, _):
        """7z CLI raises unexpected exception -> returns False."""
        with tempfile.TemporaryDirectory() as d:
            result = DataManager.extract_7z('/fake/archive.7z', d)
            self.assertFalse(result)


class TestFindResourceDefault(unittest.TestCase):
    """Cover Resources.find with default search_paths=None."""

    def test_default_paths(self):
        with patch('os.path.exists', return_value=False):
            result = Resources.find('nonexistent.file')
            self.assertIsNone(result)
