"""
Tests for data_repository.py (directory/extraction helpers) and conf.py (config persistence).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from trcc.legacy.adapters.infra.data_repository import (
    DataManager,
    Resources,
    _find_pkg_data_dir,
)
from trcc.legacy.conf import Settings, load_config, save_config
from trcc.legacy.core.paths import has_themes, resolve_theme_dir


class TestPathHelpers(unittest.TestCase):
    """Test path construction helpers."""

    def test_get_theme_dir(self):
        path = resolve_theme_dir(320, 320)
        self.assertTrue(path.endswith('theme320320'))

    def test_get_theme_dir_other_resolution(self):
        path = resolve_theme_dir(480, 480)
        self.assertTrue(path.endswith('theme480480'))

    def test_get_web_dir(self):
        path = DataManager.get_web_dir(320, 320)
        self.assertTrue(path.endswith(os.path.join('web', '320320')))

    def test_get_web_masks_dir(self):
        path = DataManager.get_web_masks_dir(320, 320)
        self.assertTrue(path.endswith(os.path.join('web', 'zt320320')))


class TestHasActualThemes(unittest.TestCase):
    """Test has_themes() from core/paths.py."""

    def test_nonexistent_dir(self):
        self.assertFalse(has_themes('/nonexistent/path'))

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(has_themes(d))

    def test_dir_with_only_gitkeep(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, '.gitkeep').touch()
            self.assertFalse(has_themes(d))

    def test_dir_with_subdirs_and_pngs(self):
        with tempfile.TemporaryDirectory() as d:
            subdir = os.path.join(d, '000a')
            os.mkdir(subdir)
            Path(subdir, '01.png').touch()
            self.assertTrue(has_themes(d))

    def test_dir_with_subdirs_no_pngs(self):
        """Subdirs without PNGs (e.g. leftover config1.dc) are not valid themes."""
        with tempfile.TemporaryDirectory() as d:
            subdir = os.path.join(d, '000a')
            os.mkdir(subdir)
            Path(subdir, 'config1.dc').touch()
            self.assertFalse(has_themes(d))


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
            patch('trcc.legacy.conf.CONFIG_PATH', self.config_path),
            patch('trcc.legacy.conf.CONFIG_DIR', self.tmp),
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
            patch('trcc.legacy.conf.CONFIG_PATH', self.config_path),
            patch('trcc.legacy.conf.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_resolution(self):
        """No saved resolution returns None — no hardcoded fallback."""
        self.assertIsNone(Settings._get_saved_resolution())

    def test_save_and_load_resolution(self):
        save_config({'devices': {'0': {'w': 480, 'h': 480}}})
        self.assertEqual(Settings._get_saved_resolution(), (480, 480))

    def test_invalid_resolution_returns_default(self):
        """Invalid saved resolution returns None — no hardcoded fallback."""
        save_config({'resolution': 'bad'})
        self.assertIsNone(Settings._get_saved_resolution())


class TestTempUnitConfig(unittest.TestCase):
    """Test temperature unit save/load."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.legacy.conf.CONFIG_PATH', self.config_path),
            patch('trcc.legacy.conf.CONFIG_DIR', self.tmp),
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


class TestConfigMigration(unittest.TestCase):
    """Test _migrate_config version-aware state clearing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.legacy.conf.CONFIG_PATH', self.config_path),
            patch('trcc.legacy.conf.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_install_stamps_version(self):
        """First run: no config → stamps current version."""
        from trcc.legacy.conf import _migrate_config
        _migrate_config()
        cfg = load_config()
        from trcc.__version__ import __version__
        self.assertEqual(cfg['config_version'], __version__)

    def test_same_version_no_change(self):
        """Same version: config untouched."""
        from trcc.__version__ import __version__
        save_config({
            'config_version': __version__,
            'devices': {'0:0416_8001': {'theme': 'dark'}},
            'resolution': [480, 480],
            'temp_unit': 1,
        })
        from trcc.legacy.conf import _migrate_config
        _migrate_config()
        cfg = load_config()
        self.assertIn('devices', cfg)
        self.assertEqual(cfg['resolution'], [480, 480])
        self.assertEqual(cfg['temp_unit'], 1)

    def test_version_mismatch_clears_device_state(self):
        """Upgrade: clears devices, resolution, selected_device; preserves user prefs."""
        save_config({
            'config_version': '0.0.1',
            'devices': {'0:0416_8001': {'theme': 'dark'}},
            'resolution': [480, 480],
            'selected_device': '/dev/sg0',
            'installed_resolutions': ['320x320'],
            'temp_unit': 1,
            'lang': 'en',
            'format_prefs': {'time_format': 1},
            'hdd_enabled': False,
        })
        from trcc.legacy.conf import _migrate_config
        _migrate_config()
        cfg = load_config()
        # Device-derived state cleared
        self.assertNotIn('devices', cfg)
        self.assertNotIn('resolution', cfg)
        self.assertNotIn('selected_device', cfg)
        self.assertNotIn('installed_resolutions', cfg)
        # User prefs preserved
        self.assertEqual(cfg['temp_unit'], 1)
        self.assertEqual(cfg['lang'], 'en')
        self.assertEqual(cfg['format_prefs'], {'time_format': 1})
        self.assertFalse(cfg['hdd_enabled'])
        # Version stamped
        from trcc.__version__ import __version__
        self.assertEqual(cfg['config_version'], __version__)

    def test_version_mismatch_deletes_probe_cache(self):
        """Upgrade: deletes LED probe cache file."""
        probe_cache = os.path.join(self.tmp, 'led_probe_cache.json')
        with open(probe_cache, 'w') as f:
            json.dump({'cache': True}, f)
        save_config({'config_version': '0.0.1'})
        from trcc.legacy.conf import _migrate_config
        _migrate_config()
        self.assertFalse(os.path.exists(probe_cache))


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
            patch('trcc.legacy.conf.CONFIG_PATH', self.config_path),
            patch('trcc.legacy.conf.CONFIG_DIR', self.tmp),
            patch.object(DataManager, '_data_dir', staticmethod(lambda: self.user_data)),
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
    """Test device_config_key formatting — index-only key, vid_pid cached."""

    def test_format(self):
        key = Settings.device_config_key(0, 0x87CD, 0x70DB)
        self.assertEqual(key, '0')

    def test_format_with_index(self):
        key = Settings.device_config_key(2, 0x0402, 0x3922)
        self.assertEqual(key, '2')

    def test_caches_vid_pid(self):
        Settings.device_config_key(0, 0x0001, 0x0002)
        self.assertEqual(Settings._vid_pid_cache['0'], '0001_0002')


class TestPerDeviceConfig(unittest.TestCase):
    """Test per-device config save/load — keys are index-only with vid_pid inside."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.patches = [
            patch('trcc.legacy.conf.CONFIG_PATH', self.config_path),
            patch('trcc.legacy.conf.CONFIG_DIR', self.tmp),
        ]
        for p in self.patches:
            p.start()
        # Populate vid_pid cache (normally done by device_config_key callers)
        Settings.device_config_key(0, 0x87CD, 0x70DB)
        Settings.device_config_key(1, 0x0402, 0x3922)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_missing_device_returns_empty(self):
        self.assertEqual(Settings.get_device_config('0'), {})

    def test_save_and_get(self):
        Settings.save_device_setting('0', 'brightness_level', 3)
        cfg = Settings.get_device_config('0')
        self.assertEqual(cfg['brightness_level'], 3)
        self.assertEqual(cfg['vid_pid'], '87cd_70db')

    def test_multiple_settings_same_device(self):
        Settings.save_device_setting('0', 'brightness_level', 2)
        Settings.save_device_setting('0', 'rotation', 90)
        cfg = Settings.get_device_config('0')
        self.assertEqual(cfg['brightness_level'], 2)
        self.assertEqual(cfg['rotation'], 90)

    def test_multiple_devices_independent(self):
        Settings.save_device_setting('0', 'brightness_level', 1)
        Settings.save_device_setting('1', 'brightness_level', 3)
        self.assertEqual(Settings.get_device_config('0')['brightness_level'], 1)
        self.assertEqual(Settings.get_device_config('1')['brightness_level'], 3)

    def test_save_complex_value(self):
        carousel = {'enabled': True, 'interval': 5, 'themes': ['Theme1', 'Theme3']}
        Settings.save_device_setting('0', 'carousel', carousel)
        cfg = Settings.get_device_config('0')
        self.assertEqual(cfg['carousel']['enabled'], True)
        self.assertEqual(cfg['carousel']['themes'], ['Theme1', 'Theme3'])

    def test_save_overlay_config(self):
        overlay = {
            'enabled': True,
            'config': {'time_0': {'x': 10, 'y': 10, 'metric': 'time'}},
        }
        Settings.save_device_setting('0', 'overlay', overlay)
        cfg = Settings.get_device_config('0')
        self.assertTrue(cfg['overlay']['enabled'])
        self.assertIn('time_0', cfg['overlay']['config'])

    def test_overwrite_setting(self):
        Settings.save_device_setting('0', 'rotation', 0)
        Settings.save_device_setting('0', 'rotation', 180)
        self.assertEqual(Settings.get_device_config('0')['rotation'], 180)

    def test_device_config_preserves_global(self):
        Settings._save_temp_unit(1)
        Settings.save_device_setting('0', 'brightness_level', 2)
        self.assertEqual(Settings._get_saved_temp_unit(), 1)

    def test_config_json_structure(self):
        """Verify the on-disk JSON structure — index keys with vid_pid inside, w/h per-device."""
        Settings.save_device_setting('0', 'w', 480)
        Settings.save_device_setting('0', 'h', 480)
        Settings._save_temp_unit(1)
        Settings.save_device_setting('0', 'theme_path', '/some/path')
        Settings.save_device_setting('0', 'brightness_level', 2)

        with open(self.config_path) as f:
            raw = json.load(f)

        self.assertEqual(raw['devices']['0']['w'], 480)
        self.assertEqual(raw['devices']['0']['h'], 480)
        self.assertEqual(raw['temp_unit'], 1)
        self.assertIn('devices', raw)
        self.assertIn('0', raw['devices'])
        self.assertEqual(raw['devices']['0']['vid_pid'], '87cd_70db')
        self.assertEqual(raw['devices']['0']['theme_path'], '/some/path')

    def test_migrate_old_format(self):
        """Old '0:vid_pid' keys auto-migrate to '0' with vid_pid inside."""
        old_config = {
            'devices': {
                '0:0402_3922': {'brightness_level': 1, 'rotation': 90},
                '1:87cd_70db': {'brightness_level': 3},
            }
        }
        with open(self.config_path, 'w') as f:
            json.dump(old_config, f)

        from trcc.legacy.conf import load_config
        config = load_config()
        devs = config['devices']
        self.assertIn('0', devs)
        self.assertIn('1', devs)
        self.assertNotIn('0:0402_3922', devs)
        self.assertEqual(devs['0']['vid_pid'], '0402_3922')
        self.assertEqual(devs['0']['brightness_level'], 1)
        self.assertEqual(devs['1']['vid_pid'], '87cd_70db')


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
            with patch('trcc.legacy.adapters.infra.data_repository.subprocess.run', return_value=mock_result):
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
            with patch('trcc.legacy.adapters.infra.data_repository.subprocess.run', return_value=mock_result):
                result = DataManager.extract_7z(archive, target)

            self.assertFalse(result)

    def test_7z_not_found(self):
        """7z not installed — FileNotFoundError."""
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, 'test.7z')
            target = os.path.join(d, 'out')
            Path(archive).touch()

            with patch('trcc.legacy.adapters.infra.data_repository.subprocess.run', side_effect=FileNotFoundError):
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
            with patch.object(DataManager, '_data_dir', staticmethod(lambda: d)), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: os.path.join(d, 'user'))):
                self.assertTrue(DataManager.ensure_themes(320, 320))

    def test_no_archive(self):
        """Returns False when no archive and no themes."""
        with tempfile.TemporaryDirectory() as d:
            with patch.object(DataManager, '_data_dir', staticmethod(lambda: d)), \
                 patch('trcc.legacy.adapters.infra.data_repository._PKG_DATA_DIR', d), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: os.path.join(d, 'user'))), \
                 patch.object(DataManager, 'download_archive', return_value=False):
                self.assertFalse(DataManager.ensure_themes(320, 320))

    def test_extracts_from_archive(self):
        """Calls extract_7z when archive exists but themes don't."""
        with tempfile.TemporaryDirectory() as d:
            user_theme_dir = os.path.join(d, 'user', 'theme320320')
            # Place archive in pkg _PKG_DATA_DIR
            archive = os.path.join(d, 'theme320320.7z')
            Path(archive).touch()
            with patch.object(DataManager, '_data_dir', staticmethod(lambda: d)), \
                 patch('trcc.legacy.adapters.infra.data_repository._PKG_DATA_DIR', d), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: os.path.join(d, 'user'))), \
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
            with patch.object(DataManager, '_data_dir', staticmethod(lambda: d)), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: os.path.join(d, 'user'))):
                self.assertTrue(DataManager.ensure_web(320, 320))

    def test_no_archive(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(DataManager, '_data_dir', staticmethod(lambda: d)), \
                 patch('trcc.legacy.adapters.infra.data_repository._PKG_DATA_DIR', d), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: os.path.join(d, 'user'))), \
                 patch.object(DataManager, 'download_archive', return_value=False):
                self.assertFalse(DataManager.ensure_web(320, 320))

    def test_extracts_from_archive(self):
        with tempfile.TemporaryDirectory() as d:
            user_web_dir = os.path.join(d, 'user', 'web', '320320')
            archive_dir = os.path.join(d, 'web')
            os.makedirs(archive_dir)
            archive = os.path.join(archive_dir, '320320.7z')
            Path(archive).touch()
            with patch.object(DataManager, '_data_dir', staticmethod(lambda: d)), \
                 patch('trcc.legacy.adapters.infra.data_repository._PKG_DATA_DIR', d), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: os.path.join(d, 'user'))), \
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
            with patch.object(DataManager, '_data_dir', staticmethod(lambda: d)), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: os.path.join(d, 'user'))):
                self.assertTrue(DataManager.ensure_web_masks(320, 320))

    def test_no_archive(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(DataManager, '_data_dir', staticmethod(lambda: d)), \
                 patch('trcc.legacy.adapters.infra.data_repository._PKG_DATA_DIR', d), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: os.path.join(d, 'user'))), \
                 patch.object(DataManager, 'download_archive', return_value=False):
                self.assertFalse(DataManager.ensure_web_masks(320, 320))


class TestEnsureAll(unittest.TestCase):
    """Test DataManager.ensure_all resolution installation."""

    def _mock_ensure(self, side_effect=None):
        return patch.multiple(
            DataManager,
            is_resolution_installed=staticmethod(lambda w, h: False),
            mark_resolution_installed=staticmethod(lambda w, h: None),
            ensure_themes=staticmethod(lambda w, h: True),
            ensure_web=staticmethod(lambda w, h: True),
            ensure_web_masks=staticmethod(lambda w, h: True),
        )

    def test_square_only_installs_native(self):
        """Square devices (320x320) do not install a rotated variant."""
        calls = []
        with patch.object(DataManager, 'is_resolution_installed', return_value=False), \
             patch.object(DataManager, 'mark_resolution_installed'), \
             patch.object(DataManager, 'ensure_themes'), \
             patch.object(DataManager, 'ensure_web', side_effect=lambda w, h: calls.append((w, h))), \
             patch.object(DataManager, 'ensure_web_masks', side_effect=lambda w, h: calls.append((w, h))):
            DataManager.ensure_all(320, 320)
        self.assertEqual(calls, [(320, 320), (320, 320)])

    def test_non_square_installs_both_orientations(self):
        """Non-square devices (1280x480) also install the portrait (480x1280) web+masks."""
        web_calls = []
        mask_calls = []
        with patch.object(DataManager, 'is_resolution_installed', return_value=False), \
             patch.object(DataManager, 'mark_resolution_installed'), \
             patch.object(DataManager, 'ensure_themes'), \
             patch.object(DataManager, 'ensure_web', side_effect=lambda w, h: web_calls.append((w, h))), \
             patch.object(DataManager, 'ensure_web_masks', side_effect=lambda w, h: mask_calls.append((w, h))):
            DataManager.ensure_all(1280, 480)
        self.assertIn((1280, 480), web_calls)
        self.assertIn((480, 1280), web_calls)
        self.assertIn((1280, 480), mask_calls)
        self.assertIn((480, 1280), mask_calls)

    def test_always_runs_all_ensures(self):
        """ensure_all always calls all ensure_* — each is idempotent internally."""
        with patch.object(DataManager, 'is_resolution_installed', return_value=True), \
             patch.object(DataManager, 'mark_resolution_installed'), \
             patch.object(DataManager, 'ensure_themes') as mock_themes, \
             patch.object(DataManager, 'ensure_web') as mock_web, \
             patch.object(DataManager, 'ensure_web_masks') as mock_masks:
            DataManager.ensure_all(320, 320)
        mock_themes.assert_called_once_with(320, 320)
        mock_web.assert_called_once_with(320, 320)
        mock_masks.assert_called_once_with(320, 320)

    def test_non_square_extracts_correct_archive_paths(self):
        """Fixture test: ensure_all extracts all 5 archives for a 1280x480 device.

        Verifies the real archive path construction — landscape theme + web + masks
        AND portrait web + masks — using a tempdir with fake .7z files.
        """
        with tempfile.TemporaryDirectory() as d:
            pkg = d
            user = os.path.join(d, 'user')

            # Create all expected archives in the package data dir
            web_dir = os.path.join(pkg, 'web')
            os.makedirs(web_dir)
            archives = {
                'theme': os.path.join(pkg, 'theme1280480.7z'),
                'web_ls': os.path.join(web_dir, '1280480.7z'),
                'mask_ls': os.path.join(web_dir, 'zt1280480.7z'),
                'web_pt': os.path.join(web_dir, '4801280.7z'),
                'mask_pt': os.path.join(web_dir, 'zt4801280.7z'),
            }
            for path in archives.values():
                Path(path).touch()

            extracted = []
            with patch('trcc.legacy.adapters.infra.data_repository._PKG_DATA_DIR', pkg), \
                 patch.object(DataManager, '_data_dir', staticmethod(lambda: user)), \
                 patch.object(DataManager, 'mark_resolution_installed'), \
                 patch.object(DataManager, 'extract_7z',
                              side_effect=lambda arc, dst: extracted.append(arc) or True):
                DataManager.ensure_all(1280, 480)

            extracted_names = [os.path.basename(p) for p in extracted]
            self.assertIn('theme1280480.7z', extracted_names)
            self.assertIn('1280480.7z',      extracted_names)
            self.assertIn('zt1280480.7z',    extracted_names)
            self.assertIn('4801280.7z',      extracted_names)
            self.assertIn('zt4801280.7z',    extracted_names)


# -- download_archive SSL ---------------------------------------------------

class TestDownloadArchiveSSL(unittest.TestCase):
    """Test download_archive SSL cert handling for PyInstaller builds."""

    @patch('urllib.request.urlopen')
    def test_clears_ssl_cert_file_env_during_context_creation(self, mock_urlopen):
        """SSL_CERT_FILE is temporarily cleared so Python uses OS cert store."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b''
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, 'test.7z')
            fake_cert = '/nonexistent/cacert.pem'
            os.environ['SSL_CERT_FILE'] = fake_cert
            try:
                DataManager.download_archive('https://example.com/test.7z', dest)
            finally:
                restored = os.environ.get('SSL_CERT_FILE')
                os.environ.pop('SSL_CERT_FILE', None)

            # SSL_CERT_FILE must be restored after context creation
            self.assertEqual(restored, fake_cert)

    @patch('urllib.request.urlopen')
    def test_no_ssl_cert_file_env_unchanged(self, mock_urlopen):
        """When SSL_CERT_FILE is not set, nothing is restored."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b''
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        os.environ.pop('SSL_CERT_FILE', None)
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, 'test.7z')
            DataManager.download_archive('https://example.com/test.7z', dest)
            self.assertNotIn('SSL_CERT_FILE', os.environ)


# -- _find_data_dir ---------------------------------------------------------

class TestFindPkgDataDir(unittest.TestCase):
    """Test _find_pkg_data_dir search logic."""

    def test_returns_pkg_data_as_fallback(self):
        """When no valid dirs exist, falls back to _TRCC_PKG/data."""
        with patch('trcc.legacy.adapters.infra.data_repository._TRCC_PKG', '/fake/src/trcc'), \
             patch('trcc.legacy.adapters.infra.data_repository._PROJECT_ROOT', '/fake'), \
             patch('os.path.isdir', return_value=False):
            result = _find_pkg_data_dir()
            self.assertEqual(result, '/fake/src/trcc/data')


# -- Extract 7z CLI edge cases ----------------------------------------------

class TestExtract7zCLI(unittest.TestCase):
    """Cover 7z CLI edge cases."""

    @patch('trcc.legacy.adapters.infra.data_repository.subprocess.run')
    def test_7z_cli_success(self, mock_run):
        """7z CLI succeeds."""
        mock_run.return_value = MagicMock(returncode=0)
        with tempfile.TemporaryDirectory() as d:
            result = DataManager.extract_7z('/fake/archive.7z', d)
        self.assertTrue(result)

    @patch('trcc.legacy.adapters.infra.data_repository.subprocess.run', side_effect=FileNotFoundError)
    def test_7z_cli_not_found(self, _):
        """7z not installed -> returns False."""
        with tempfile.TemporaryDirectory() as d:
            result = DataManager.extract_7z('/fake/archive.7z', d)
            self.assertFalse(result)

    @patch('trcc.legacy.adapters.infra.data_repository.subprocess.run', side_effect=OSError("fail"))
    def test_7z_cli_exception(self, _):
        """7z CLI raises OSError/SubprocessError -> returns False.

        Production catches (OSError, SubprocessError); bare RuntimeError is
        intentionally not caught and would surface to the caller.
        """
        with tempfile.TemporaryDirectory() as d:
            result = DataManager.extract_7z('/fake/archive.7z', d)
            self.assertFalse(result)


class TestUnwrapNestedDir(unittest.TestCase):
    """Test DataManager._unwrap_nested_dir."""

    def test_flattens_single_nested_dir(self):
        """Single subdirectory is unwrapped — contents moved up."""
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, '1600720')
            os.makedirs(nested)
            Path(nested, 'a001.png').touch()
            Path(nested, 'a002.png').touch()
            DataManager._unwrap_nested_dir(d)
            self.assertTrue(os.path.exists(os.path.join(d, 'a001.png')))
            self.assertTrue(os.path.exists(os.path.join(d, 'a002.png')))
            self.assertFalse(os.path.exists(nested))

    def test_no_op_when_multiple_entries(self):
        """Multiple top-level entries — no unwrapping (flat archive)."""
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'a001.png').touch()
            Path(d, 'a002.png').touch()
            DataManager._unwrap_nested_dir(d)
            self.assertTrue(os.path.exists(os.path.join(d, 'a001.png')))
            self.assertTrue(os.path.exists(os.path.join(d, 'a002.png')))

    def test_no_op_when_single_file(self):
        """Single file (not dir) — no unwrapping."""
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'only.png').touch()
            DataManager._unwrap_nested_dir(d)
            self.assertTrue(os.path.exists(os.path.join(d, 'only.png')))

    def test_no_op_on_missing_dir(self):
        """Non-existent directory — no crash."""
        DataManager._unwrap_nested_dir('/nonexistent/path/abc')

    def test_flattens_nested_subdirs(self):
        """Nested directory with subdirectories (zt mask archives)."""
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, 'zt1600720')
            mask_a = os.path.join(nested, '000a')
            os.makedirs(mask_a)
            Path(mask_a, '01.png').touch()
            Path(mask_a, 'Theme.png').touch()
            DataManager._unwrap_nested_dir(d)
            self.assertTrue(os.path.exists(os.path.join(d, '000a', '01.png')))
            self.assertFalse(os.path.exists(nested))


class TestFindResourceDefault(unittest.TestCase):
    """Cover Resources.find with default search_paths=None."""

    def test_default_paths(self):
        with patch('os.path.exists', return_value=False):
            result = Resources.find('nonexistent.file')
            self.assertIsNone(result)


class TestPortraitDirectorySwitching(unittest.TestCase):
    """Test output_resolution portrait directory switching.

    C# GetWebBackgroundImageDirectory / GetFileListMBDir: non-square displays
    swap width/height when directionB is 90 or 270.
    Orientation logic moved to core/orientation.py — tested here with real
    resolution pairs from the device catalog.
    """

    def test_landscape_rotation_keeps_landscape(self):
        """Rotation 0 or 180 keeps original w×h."""
        from trcc.legacy.core.paths import output_resolution
        self.assertEqual(output_resolution(1280, 480, 0), (1280, 480))
        self.assertEqual(output_resolution(1280, 480, 180), (1280, 480))

    def test_portrait_rotation_swaps_dims(self):
        """Rotation 90 or 270 swaps to h×w."""
        from trcc.legacy.core.paths import output_resolution
        self.assertEqual(output_resolution(1280, 480, 90), (480, 1280))
        self.assertEqual(output_resolution(1280, 480, 270), (480, 1280))

    def test_square_display_no_swap(self):
        """Square displays never swap, regardless of rotation."""
        from trcc.legacy.core.paths import output_resolution
        self.assertEqual(output_resolution(320, 320, 90), (320, 320))

    def test_1600x720_portrait(self):
        """1600x720 swaps to 720x1600 on portrait rotation."""
        from trcc.legacy.core.paths import output_resolution
        self.assertEqual(output_resolution(1600, 720, 90), (720, 1600))

    def test_640x480_portrait(self):
        """640x480 swaps to 480x640 on portrait rotation."""
        from trcc.legacy.core.paths import output_resolution
        self.assertEqual(output_resolution(640, 480, 270), (480, 640))
