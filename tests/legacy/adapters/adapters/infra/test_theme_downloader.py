"""
Tests for theme_downloader — pack registry, download, list, info, and removal.

Tests cover:
- _get_registry() built from FBL_TO_RESOLUTION
- PackInfo dataclass
- Short alias resolution (themes-320 → themes-320x320)
- list_available() / show_info() display output
- download_pack() delegation to DataManager.ensure_themes()
- remove_pack() uninstall flow

All tests use DI via the conftest tmp_config fixture — no patching of paths.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from trcc.legacy.adapters.infra.theme_downloader import (
    PackInfo,
    ThemeDownloader,
    _get_aliases,
    _get_registry,
    _resolve_pack_name,
    download_pack,
    list_available,
    remove_pack,
    show_info,
)

# ── Registry ─────────────────────────────────────────────────────────────


class TestThemeRegistry(unittest.TestCase):
    """Validate the dynamically built registry."""

    def test_registry_not_empty(self):
        self.assertGreater(len(_get_registry()), 0)

    def test_all_entries_are_pack_info(self):
        for pack_id, info in _get_registry().items():
            with self.subTest(pack=pack_id):
                self.assertIsInstance(info, PackInfo)

    def test_resolution_format(self):
        """Resolution must be WxH format (e.g., '320x320')."""
        for pack_id, info in _get_registry().items():
            with self.subTest(pack=pack_id):
                self.assertRegex(info.resolution, r'^\d+x\d+$')

    def test_pack_id_matches_resolution(self):
        """Pack ID format is themes-{w}x{h}."""
        for pack_id, info in _get_registry().items():
            with self.subTest(pack=pack_id):
                self.assertEqual(pack_id, f"themes-{info.width}x{info.height}")

    def test_archive_name(self):
        """Archive name matches theme{w}{h}.7z."""
        for pack_id, info in _get_registry().items():
            with self.subTest(pack=pack_id):
                self.assertEqual(info.archive, f"theme{info.width}{info.height}.7z")

    def test_url_property(self):
        """URL should point to GitHub raw content."""
        info = list(_get_registry().values())[0]
        self.assertIn("raw.githubusercontent.com", info.url)
        self.assertTrue(info.url.endswith(info.archive))

    def test_known_resolutions_present(self):
        """Key resolutions from FBL_TO_RESOLUTION must be in registry."""
        reg = _get_registry()
        self.assertIn('themes-320x320', reg)
        self.assertIn('themes-240x240', reg)
        self.assertIn('themes-480x480', reg)
        self.assertIn('themes-320x240', reg)
        self.assertIn('themes-1280x480', reg)


# ── Short aliases ────────────────────────────────────────────────────────


class TestShortAliases(unittest.TestCase):
    """Test short alias resolution (themes-320 → themes-320x320)."""

    def test_square_aliases_exist(self):
        """Square resolutions get short aliases."""
        aliases = _get_aliases()
        self.assertIn('themes-320', aliases)
        self.assertIn('themes-240', aliases)
        self.assertIn('themes-480', aliases)

    def test_alias_resolves_correctly(self):
        self.assertEqual(_resolve_pack_name('themes-320'), 'themes-320x320')
        self.assertEqual(_resolve_pack_name('themes-480'), 'themes-480x480')

    def test_canonical_name_passes_through(self):
        self.assertEqual(_resolve_pack_name('themes-320x320'), 'themes-320x320')
        self.assertEqual(_resolve_pack_name('themes-240x320'), 'themes-240x320')


# ── list_available / show_info ───────────────────────────────────────────


class TestListAvailable(unittest.TestCase):

    def test_prints_header(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            list_available()
        output = buf.getvalue()
        self.assertIn("Available theme packs", output)

    def test_lists_pack_ids(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            list_available()
        output = buf.getvalue()
        self.assertIn("themes-320x320", output)


class TestShowInfo(unittest.TestCase):

    def test_shows_pack_details(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            show_info('themes-320x320')
        output = buf.getvalue()
        self.assertIn("320x320", output)
        self.assertIn("Pack ID", output)

    def test_unknown_pack(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            show_info('nonexistent')
        self.assertIn("Unknown", buf.getvalue())


# ── download_pack ────────────────────────────────────────────────────────


class TestDownloadPack(unittest.TestCase):

    def test_unknown_returns_1(self):
        self.assertEqual(download_pack('nonexistent'), 1)

    def test_already_installed(self):
        """When themes exist, returns 0 without downloading."""
        with patch.object(ThemeDownloader, '_is_installed', return_value=True), \
             patch.object(ThemeDownloader, '_theme_count', return_value=5):
            result = download_pack('themes-320x320')
        self.assertEqual(result, 0)

    def test_delegates_to_ensure(self):
        """Calls DataManager.ensure_themes on download."""
        from trcc.legacy.adapters.infra.data_repository import DataManager
        with patch.object(ThemeDownloader, '_is_installed', return_value=False), \
             patch.object(DataManager, 'ensure_themes', return_value=True), \
             patch.object(ThemeDownloader, '_theme_count', return_value=10):
            result = download_pack('themes-320x320')
        self.assertEqual(result, 0)


# ── remove_pack ──────────────────────────────────────────────────────────


class TestRemovePack(unittest.TestCase):

    def test_unknown_pack_returns_1(self):
        self.assertEqual(remove_pack('nonexistent'), 1)

    def test_not_installed_returns_1(self):
        """Returns 1 when theme dir doesn't exist."""
        # conftest tmp_config sets user_data_dir to tmp — no themes there
        self.assertEqual(remove_pack('themes-320x320'), 1)

    def test_removes_installed(self):
        """Creates theme dir in settings.user_data_dir, removes it."""
        import trcc.legacy.conf as _conf
        data_dir = _conf.settings.user_data_dir
        theme_dir = data_dir / 'theme320320'
        theme_dir.mkdir(parents=True)
        (theme_dir / 'Theme1').mkdir()

        result = remove_pack('themes-320x320')
        self.assertEqual(result, 0)
        self.assertFalse(theme_dir.exists())

    def test_remove_with_alias(self):
        import trcc.legacy.conf as _conf
        data_dir = _conf.settings.user_data_dir
        theme_dir = data_dir / 'theme320320'
        theme_dir.mkdir(parents=True)
        (theme_dir / 'Theme1').mkdir()

        result = remove_pack('themes-320')
        self.assertEqual(result, 0)
        self.assertFalse(theme_dir.exists())


# ── _theme_dir / _is_installed / _theme_count ────────────────────────────


class TestHelpers(unittest.TestCase):

    def test_theme_dir_returns_path(self):
        result = ThemeDownloader._theme_dir(320, 320)
        self.assertIsInstance(result, Path)
        self.assertIn('theme320320', str(result))

    def test_is_installed_false_when_empty(self):
        """Empty theme dir = not installed."""
        import trcc.legacy.conf as _conf
        d = _conf.settings.user_data_dir / 'theme320320'
        d.mkdir(parents=True)
        self.assertFalse(ThemeDownloader._is_installed(320, 320))

    def test_is_installed_true_with_content(self):
        import trcc.legacy.conf as _conf
        d = _conf.settings.user_data_dir / 'theme320320'
        d.mkdir(parents=True)
        (d / 'Theme1').mkdir()
        self.assertTrue(ThemeDownloader._is_installed(320, 320))

    def test_theme_count(self):
        import trcc.legacy.conf as _conf
        d = _conf.settings.user_data_dir / 'theme320320'
        d.mkdir(parents=True)
        (d / 'Theme1').mkdir()
        (d / 'Theme2').mkdir()
        (d / 'readme.txt').write_text('hi')  # file, not dir
        self.assertEqual(ThemeDownloader._theme_count(320, 320), 2)

    def test_theme_count_nonexistent(self):
        self.assertEqual(ThemeDownloader._theme_count(999, 999), 0)


if __name__ == '__main__':
    unittest.main()
