"""Tests for core/paths.py — path constants and directory resolution."""

import os
import unittest

from trcc.core.paths import (
    ASSETS_DIR,
    DATA_DIR,
    RESOURCES_DIR,
    USER_CONFIG_DIR,
    USER_CONTENT_DIR,
    USER_DATA_DIR,
    USER_MASKS_WEB_DIR,
    _has_any_content,
    get_user_masks_dir,
    get_web_dir,
    get_web_masks_dir,
    has_themes,
)

# =============================================================================
# Path constants
# =============================================================================


class TestPathConstants(unittest.TestCase):
    """Verify path constant relationships and structure."""

    def test_user_config_dir_is_home_trcc(self):
        self.assertEqual(USER_CONFIG_DIR, os.path.expanduser('~/.trcc'))

    def test_user_data_dir_under_config(self):
        self.assertEqual(USER_DATA_DIR, os.path.join(USER_CONFIG_DIR, 'data'))

    def test_data_dir_equals_user_data_dir(self):
        """DATA_DIR = USER_DATA_DIR (all runtime data in ~/.trcc/data/)."""
        self.assertEqual(DATA_DIR, USER_DATA_DIR)

    def test_assets_dir_exists(self):
        """Assets directory exists in package."""
        self.assertTrue(os.path.isdir(ASSETS_DIR), f"Missing: {ASSETS_DIR}")

    def test_resources_dir_in_gui_package(self):
        """RESOURCES_DIR lives in the ui/gui/ package (ui/gui/assets/)."""
        self.assertTrue(
            RESOURCES_DIR.endswith(os.path.join('ui', 'gui', 'assets')),
            f"Expected suffix 'ui/gui/assets', got: {RESOURCES_DIR}")

    def test_resources_dir_exists(self):
        self.assertTrue(os.path.isdir(RESOURCES_DIR), f"Missing: {RESOURCES_DIR}")

    def test_resources_dir_has_split_overlay(self):
        """Canonical split-overlay asset resolves under RESOURCES_DIR.

        Guards against future moves: if assets relocate again, this test fails
        in test_paths.py rather than silently disabling the Dynamic Island
        overlay at runtime (regression: GitHub #149)."""
        asset = os.path.join(RESOURCES_DIR, 'split_overlay_b.png')
        self.assertTrue(os.path.isfile(asset), f"Missing: {asset}")


# =============================================================================
# _has_any_content
# =============================================================================


class TestHasAnyContent(unittest.TestCase):
    """_has_any_content() — checks if directory exists and is non-empty."""

    def test_nonexistent_dir(self):
        self.assertFalse(_has_any_content('/nonexistent/path/xyzzy'))

    def test_empty_dir(self, tmp_path=None):
        """Empty directory → False."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(_has_any_content(d))

    def test_dir_with_file(self):
        """Directory with a file → True."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'test.txt'), 'w').close()
            self.assertTrue(_has_any_content(d))

    def test_dir_with_subdir(self):
        """Directory with a subdirectory → True."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, 'sub'))
            self.assertTrue(_has_any_content(d))


# =============================================================================
# has_themes
# =============================================================================


class TestHasThemes(unittest.TestCase):
    """has_themes() — checks for valid theme subdirectories with PNGs."""

    def test_nonexistent_dir(self):
        self.assertFalse(has_themes('/nonexistent/path/xyzzy'))

    def test_empty_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(has_themes(d))

    def test_dir_with_theme_subdir_and_png(self):
        """Subdirectory with a .png file → True."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            theme_dir = os.path.join(d, 'MyTheme')
            os.mkdir(theme_dir)
            open(os.path.join(theme_dir, '00.png'), 'w').close()
            self.assertTrue(has_themes(d))

    def test_dir_with_subdir_but_no_png(self):
        """Subdirectory with no .png files → False."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            theme_dir = os.path.join(d, 'EmptyTheme')
            os.mkdir(theme_dir)
            open(os.path.join(theme_dir, 'config.dc'), 'w').close()
            self.assertFalse(has_themes(d))

    def test_dotdir_excluded(self):
        """Hidden directories (starting with .) are excluded."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            hidden = os.path.join(d, '.hidden')
            os.mkdir(hidden)
            open(os.path.join(hidden, '00.png'), 'w').close()
            self.assertFalse(has_themes(d))

    def test_custom_prefix_excluded(self):
        """Custom_ prefix directories are excluded."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            custom = os.path.join(d, 'Custom_MyTheme')
            os.mkdir(custom)
            open(os.path.join(custom, '00.png'), 'w').close()
            self.assertFalse(has_themes(d))

    def test_files_at_top_level_ignored(self):
        """Files (not dirs) at top level don't count as themes."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'readme.png'), 'w').close()
            self.assertFalse(has_themes(d))

    def test_multiple_themes_one_valid(self):
        """Multiple subdirs, only one needs a .png."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, 'NoFiles')
            os.mkdir(empty)
            valid = os.path.join(d, 'HasPng')
            os.mkdir(valid)
            open(os.path.join(valid, 'bg.png'), 'w').close()
            self.assertTrue(has_themes(d))


# =============================================================================
# get_web_dir / get_web_masks_dir
# =============================================================================


class TestGetWebDir(unittest.TestCase):
    """get_web_dir() — resolves cloud theme web directory."""

    def test_returns_string(self):
        result = get_web_dir(320, 320)
        self.assertIsInstance(result, str)

    def test_contains_resolution_key(self):
        """Path contains resolution as '{w}{h}' key."""
        result = get_web_dir(480, 480)
        self.assertIn('480480', result)

    def test_under_web_subdir(self):
        """Path is under web/ subdirectory."""
        result = get_web_dir(320, 320)
        self.assertIn(os.path.join('web', '320320'), result)

    def test_different_resolutions_different_paths(self):
        self.assertNotEqual(get_web_dir(320, 320), get_web_dir(480, 480))


class TestGetWebMasksDir(unittest.TestCase):
    """get_web_masks_dir() — resolves cloud mask theme directory."""

    def test_returns_string(self):
        result = get_web_masks_dir(320, 320)
        self.assertIsInstance(result, str)

    def test_contains_zt_prefix(self):
        """Mask dir uses 'zt' prefix: zt{w}{h}."""
        result = get_web_masks_dir(320, 320)
        self.assertIn('zt320320', result)

    def test_under_web_subdir(self):
        result = get_web_masks_dir(480, 480)
        self.assertIn(os.path.join('web', 'zt480480'), result)

    def test_different_resolutions_different_paths(self):
        self.assertNotEqual(
            get_web_masks_dir(320, 320), get_web_masks_dir(480, 480))


class TestUserContentPaths(unittest.TestCase):
    """User content paths (~/.trcc-user/) — survives uninstall."""

    def test_user_content_dir(self):
        self.assertEqual(USER_CONTENT_DIR, os.path.expanduser('~/.trcc-user'))

    def test_user_masks_web_dir(self):
        self.assertEqual(
            USER_MASKS_WEB_DIR,
            os.path.join(os.path.expanduser('~/.trcc-user'), 'data', 'web'))


class TestGetUserMasksDir(unittest.TestCase):
    """get_user_masks_dir() — user custom masks directory."""

    def test_returns_string(self):
        result = get_user_masks_dir(320, 320)
        self.assertIsInstance(result, str)

    def test_contains_zt_prefix(self):
        result = get_user_masks_dir(320, 320)
        self.assertIn('zt320320', result)

    def test_under_trcc_user(self):
        result = get_user_masks_dir(320, 320)
        self.assertIn('.trcc-user', result)

    def test_separate_from_cloud_masks(self):
        """User masks dir is NOT under ~/.trcc/ (survives data re-download)."""
        cloud = get_web_masks_dir(320, 320)
        user = get_user_masks_dir(320, 320)
        self.assertNotEqual(cloud, user)

    def test_different_resolutions(self):
        self.assertNotEqual(
            get_user_masks_dir(320, 320), get_user_masks_dir(480, 480))


if __name__ == '__main__':
    unittest.main()
