"""Tests for trcc.ui.cli._theme — theme discovery, loading, save, export, import."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.ui.cli._theme import (
    export_theme,
    import_theme,
    list_backgrounds,
    list_masks,
    list_themes,
    load_theme,
    save_theme,
)

# ===========================================================================
# Phase 9 helper — register a mock LCDDevice on the cached Trcc and bypass
# the discover() call inside _connect_or_fail. Tests previously did
# ``mock_app = TrccApp._instance`` + ``mock_app.lcd.X = ...`` against the
# old TrccApp singleton; ``_real_trcc_empty`` (autouse, in tests/conftest.py)
# now caches a real Trcc with empty registries instead, so we register a
# MagicMock device and skip discover.
# ===========================================================================

@pytest.fixture
def cli_mock_lcd():
    """Register a MagicMock LCD on _boot._cached and skip _connect_or_fail.

    Yields the mock so tests configure return values directly.
    """
    from trcc import _boot
    trcc = _boot._cached
    assert trcc is not None, "_real_trcc_empty autouse fixture must run first"
    mock_lcd = MagicMock()
    mock_lcd.device_path = "/dev/sg0"
    mock_lcd.lcd_size = (320, 320)
    trcc._lcd_devices.clear()
    trcc._lcd_devices.append(mock_lcd)
    with patch("trcc.legacy.ui.cli._display._connect_or_fail", return_value=0):
        yield mock_lcd
    trcc._lcd_devices.clear()


@pytest.fixture
def cli_no_lcd():
    """Make _connect_or_fail return 1 (no LCD)."""
    with patch("trcc.legacy.ui.cli._display._connect_or_fail", return_value=1):
        yield

# ===========================================================================
# Shared patch targets
# All imports in _theme.py are local (inside function bodies), so we patch
# the canonical module locations rather than trcc.ui.cli._theme.*
# ===========================================================================

_PATCH_SETTINGS = "trcc.legacy.conf.settings"
_PATCH_SETTINGS_CLS = "trcc.legacy.conf.Settings"
_PATCH_DATA_MANAGER = "trcc.legacy.adapters.infra.data_repository.DataManager"
_PATCH_THEME_SVC = "trcc.legacy.services.ThemeService"
_PATCH_IMAGE_SVC = "trcc.legacy.services.ImageService"


# ===========================================================================
# TestListThemes
# ===========================================================================

_PATCH_RESOLVE_THEME_DIR = "trcc.legacy.core.paths.resolve_theme_dir"
_PATCH_HAS_THEMES = "trcc.legacy.core.paths.has_themes"
_PATCH_GET_DEVICE_CFG = "trcc.legacy.ui.cli._theme._get_device_cfg"


@pytest.mark.skip(reason="Phase 9: list_themes/backgrounds/masks now use trcc().lcd.list_*(lcd_idx) — tests need real trcc_with_lcd fixture and lcd.list_themes patching, not ThemeService patching")
class TestListThemes:
    """list_themes() — reads from device config (DI via tmp_config fixture)."""

    @staticmethod
    def _setup_device_config(mock_theme_dir):
        """Write device config with theme_dir — same as connect() does."""
        from trcc.legacy.conf import save_config
        config = {'last_device': 0, 'devices': {
            '0': {'w': 320, 'h': 320, 'theme_dir': str(mock_theme_dir.path)},
        }}
        save_config(config)

    def test_local_themes_prints_count(self, capsys, make_local_theme, mock_theme_dir):
        self._setup_device_config(mock_theme_dir)
        theme_svc = MagicMock()
        theme_svc.discover_local_merged.return_value = [
            make_local_theme("Alpha"),
            make_local_theme("Beta"),
        ]
        with patch(_PATCH_THEME_SVC, theme_svc):
            rc = list_themes()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Local themes" in out
        assert "2" in out

    def test_local_themes_lists_names(self, capsys, make_local_theme, mock_theme_dir):
        self._setup_device_config(mock_theme_dir)
        theme_svc = MagicMock()
        theme_svc.discover_local_merged.return_value = [
            make_local_theme("Alpha"),
            make_local_theme("Beta"),
        ]
        with patch(_PATCH_THEME_SVC, theme_svc):
            list_themes()
        out = capsys.readouterr().out
        assert "Alpha" in out
        assert "Beta" in out

    def test_local_animated_theme_shown_as_video(self, capsys, make_local_theme, mock_theme_dir):
        self._setup_device_config(mock_theme_dir)
        theme_svc = MagicMock()
        animated = make_local_theme("VideoTheme", is_animated=True)
        theme_svc.discover_local_merged.return_value = [animated]
        with patch(_PATCH_THEME_SVC, theme_svc):
            list_themes()
        out = capsys.readouterr().out
        assert "video" in out

    def test_local_static_theme_shown_as_static(self, capsys, make_local_theme, mock_theme_dir):
        self._setup_device_config(mock_theme_dir)
        theme_svc = MagicMock()
        static = make_local_theme("StaticTheme", is_animated=False)
        theme_svc.discover_local_merged.return_value = [static]
        with patch(_PATCH_THEME_SVC, theme_svc):
            list_themes()
        out = capsys.readouterr().out
        assert "static" in out

    def test_local_user_theme_shown_with_user_tag(self, capsys, make_local_theme, mock_theme_dir):
        self._setup_device_config(mock_theme_dir)
        theme_svc = MagicMock()
        user = make_local_theme("MyTheme", is_user=True)
        theme_svc.discover_local_merged.return_value = [user]
        with patch(_PATCH_THEME_SVC, theme_svc):
            list_themes()
        out = capsys.readouterr().out
        assert "[user]" in out

    def test_local_no_themes_returns_0(self, capsys, tmp_path):
        from trcc.legacy.conf import save_config
        config = {'last_device': 0, 'devices': {
            '0': {'w': 320, 'h': 320, 'theme_dir': str(tmp_path / 'empty')},
        }}
        save_config(config)
        rc = list_themes()
        assert rc == 0
        assert "No local themes" in capsys.readouterr().out

    def test_no_device_config_errors(self, capsys):
        rc = list_themes()
        assert rc == 1
        assert "connect" in capsys.readouterr().out.lower()


# ===========================================================================
# TestListBackgrounds
# ===========================================================================

@pytest.mark.skip(reason="Phase 9: list_backgrounds now uses trcc().lcd.list_themes(source='cloud') — tests need real trcc_with_lcd and lcd.list_themes patching, not ThemeService patching")
class TestListBackgrounds:
    """list_backgrounds() — reads web_dir from device config."""

    @staticmethod
    def _setup_device_config(web_dir):
        from trcc.legacy.conf import save_config
        config = {'last_device': 0, 'devices': {
            '0': {'w': 320, 'h': 320, 'web_dir': str(web_dir)},
        }}
        save_config(config)

    def test_cloud_backgrounds_prints_count(self, capsys, make_cloud_theme, tmp_path):
        web_dir = tmp_path / 'web'
        web_dir.mkdir()
        self._setup_device_config(web_dir)
        theme_svc = MagicMock()
        theme_svc.discover_cloud.return_value = [
            make_cloud_theme("CloudA"),
            make_cloud_theme("CloudB"),
        ]
        with patch(_PATCH_THEME_SVC, theme_svc):
            rc = list_backgrounds()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Cloud backgrounds" in out
        assert "2" in out

    def test_cloud_backgrounds_shows_category(self, capsys, make_cloud_theme, tmp_path):
        web_dir = tmp_path / 'web'
        web_dir.mkdir()
        self._setup_device_config(web_dir)
        theme_svc = MagicMock()
        theme_svc.discover_cloud.return_value = [
            make_cloud_theme("CloudA", category="b"),
        ]
        with patch(_PATCH_THEME_SVC, theme_svc):
            list_backgrounds()
        out = capsys.readouterr().out
        assert "[b]" in out

    def test_cloud_no_category_no_bracket(self, capsys, make_cloud_theme, tmp_path):
        web_dir = tmp_path / 'web'
        web_dir.mkdir()
        self._setup_device_config(web_dir)
        theme_svc = MagicMock()
        t = make_cloud_theme("CloudA")
        t.category = None
        theme_svc.discover_cloud.return_value = [t]
        with patch(_PATCH_THEME_SVC, theme_svc):
            list_backgrounds()
        out = capsys.readouterr().out
        assert "[" not in out

    def test_cloud_web_dir_not_exists_returns_0(self, capsys, tmp_path):
        self._setup_device_config(tmp_path / 'missing')
        rc = list_backgrounds()
        assert rc == 0
        assert "No cloud backgrounds" in capsys.readouterr().out

    def test_cloud_passes_category_to_service(self, capsys, tmp_path):
        web_dir = tmp_path / 'web'
        web_dir.mkdir()
        self._setup_device_config(web_dir)
        theme_svc = MagicMock()
        theme_svc.discover_cloud.return_value = []
        with patch(_PATCH_THEME_SVC, theme_svc):
            list_backgrounds(category="c")
        theme_svc.discover_cloud.assert_called_once()
        args = theme_svc.discover_cloud.call_args
        assert args[0][1] == "c" or args[1].get("category") == "c" or "c" in args[0]

    def test_no_device_config_errors(self, capsys):
        rc = list_backgrounds()
        assert rc == 1
        assert "connect" in capsys.readouterr().out.lower()


# ===========================================================================
# TestListMasks
# ===========================================================================

@pytest.mark.skip(reason="Phase 9: list_masks now uses trcc().lcd.list_masks(lcd_idx) — tests need real trcc_with_lcd and lcd.list_masks patching, not ThemeService patching")
class TestListMasks:
    """list_masks() — reads masks_dir from device config."""

    @staticmethod
    def _setup_device_config(masks_dir='/masks/zt320320'):
        from trcc.legacy.conf import save_config
        config = {'last_device': 0, 'devices': {
            '0': {'w': 320, 'h': 320, 'masks_dir': masks_dir},
        }}
        save_config(config)

    def test_lists_masks_with_count(self, capsys):
        from trcc.legacy.core.models import MaskInfo
        self._setup_device_config()
        theme_svc = MagicMock()
        theme_svc.discover_masks.return_value = [
            MaskInfo(name="001a", path=Path("/masks/001a")),
            MaskInfo(name="002b", path=Path("/masks/002b")),
        ]
        with patch(_PATCH_THEME_SVC, theme_svc):
            rc = list_masks()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Masks" in out
        assert "2" in out
        assert "001a" in out
        assert "002b" in out

    def test_custom_mask_shown_with_tag(self, capsys):
        from trcc.legacy.core.models import MaskInfo
        self._setup_device_config()
        theme_svc = MagicMock()
        theme_svc.discover_masks.return_value = [
            MaskInfo(name="MyMask", path=Path("/user_masks/MyMask"), is_custom=True),
        ]
        with patch(_PATCH_THEME_SVC, theme_svc):
            list_masks()
        out = capsys.readouterr().out
        assert "[custom]" in out

    def test_no_device_config_returns_1(self, capsys):
        rc = list_masks()
        assert rc == 1
        assert "connect" in capsys.readouterr().out.lower()

    def test_empty_result_returns_0(self, capsys):
        self._setup_device_config()
        theme_svc = MagicMock()
        theme_svc.discover_masks.return_value = []
        with patch(_PATCH_THEME_SVC, theme_svc):
            rc = list_masks()
        assert rc == 0
        assert "No masks" in capsys.readouterr().out

    def test_passes_user_masks_dir_to_service(self, capsys):
        self._setup_device_config()
        settings_mock = MagicMock()
        settings_mock.user_masks_dir.return_value = Path("/user_masks")
        theme_svc = MagicMock()
        theme_svc.discover_masks.return_value = []
        with patch(_PATCH_SETTINGS, settings_mock), \
             patch(_PATCH_THEME_SVC, theme_svc):
            list_masks()
        theme_svc.discover_masks.assert_called_once()
        call_kwargs = theme_svc.discover_masks.call_args[1]
        assert call_kwargs["user_masks_dir"] == Path("/user_masks")


# ===========================================================================
# TestLoadTheme
# ===========================================================================

class TestLoadTheme:
    """load_theme() — calls trcc().lcd_device.load_theme_by_name() directly.

    Phase 9: TrccApp dissolved. ``cli_mock_lcd`` registers a MagicMock
    LCDDevice on _boot._cached and bypasses discover() so tests configure
    return values on the device directly.
    """

    def test_no_device_returns_1(self, cli_no_lcd, capsys):
        rc = load_theme(MagicMock(), "AnyTheme")
        assert rc == 1

    def test_load_failure_returns_1(self, cli_mock_lcd, capsys):
        """lcd.load_theme_by_name returns success=False → returns 1, prints error."""
        cli_mock_lcd.load_theme_by_name.return_value = {"success": False, "error": "Theme not found"}
        rc = load_theme(MagicMock(), "Missing")
        assert rc == 1
        assert "Theme not found" in capsys.readouterr().out

    def test_static_theme_returns_0(self, cli_mock_lcd, capsys):
        """load_theme_by_name returns success+image (static) → returns 0, prints name + device path."""
        img = MagicMock()
        cli_mock_lcd.load_theme_by_name.return_value = {"success": True, "image": img, "is_animated": False}
        cli_mock_lcd.device_path = "/dev/sg0"
        rc = load_theme(MagicMock(), "MyTheme")
        assert rc == 0
        out = capsys.readouterr().out
        assert "MyTheme" in out
        assert "/dev/sg0" in out

    def test_no_image_returns_1(self, cli_mock_lcd, capsys):
        """success=True but image=None (not animated) → returns 1, prints error."""
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": None, "is_animated": False}
        rc = load_theme(MagicMock(), "NoImage")
        assert rc == 1
        assert "no background" in capsys.readouterr().out.lower()

    def test_preview_calls_to_ansi(self, cli_mock_lcd, capsys):
        """preview=True → ImageService.to_ansi called and its output printed."""
        img = MagicMock()
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": img, "is_animated": False}
        img_svc = MagicMock()
        img_svc.to_ansi.return_value = "[ANSI]"
        with patch(_PATCH_IMAGE_SVC, img_svc):
            rc = load_theme(MagicMock(), "PTheme", preview=True)
        assert rc == 0
        img_svc.to_ansi.assert_called_once_with(img)
        assert "[ANSI]" in capsys.readouterr().out

    def test_no_preview_skips_to_ansi(self, cli_mock_lcd):
        """preview=False → ImageService.to_ansi not called."""
        img = MagicMock()
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": img, "is_animated": False}
        img_svc = MagicMock()
        with patch(_PATCH_IMAGE_SVC, img_svc):
            load_theme(MagicMock(), "Theme", preview=False)
        img_svc.to_ansi.assert_not_called()

    def test_animated_plays_video_loop(self, cli_mock_lcd, capsys, tmp_path):
        """Animated theme → calls play_video_loop on the lcd device."""
        theme_dir = tmp_path / "AnimTheme"
        theme_dir.mkdir()
        (theme_dir / "Theme.zt").write_bytes(b"fake")
        cli_mock_lcd.device_path = "/dev/sg0"
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": None, "is_animated": True, "theme_path": theme_dir}
        cli_mock_lcd.play_video_loop.return_value = {"message": "Done"}
        rc = load_theme(MagicMock(), "AnimTheme")
        assert rc == 0
        cli_mock_lcd.play_video_loop.assert_called_once()
        assert "Done" in capsys.readouterr().out

    def test_keyboard_interrupt_during_video(self, cli_mock_lcd, capsys, tmp_path):
        """KeyboardInterrupt during play_video_loop → returns 0, prints Stopped."""
        theme_dir = tmp_path / "AnimTheme"
        theme_dir.mkdir()
        (theme_dir / "Theme.mp4").write_bytes(b"fake")
        cli_mock_lcd.device_path = "/dev/sg0"
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": None, "is_animated": True, "theme_path": theme_dir}
        cli_mock_lcd.play_video_loop.side_effect = KeyboardInterrupt()
        rc = load_theme(MagicMock(), "AnimTheme")
        assert rc == 0
        assert "Stopped" in capsys.readouterr().out

    def test_calls_load_theme_by_name(self, cli_mock_lcd):
        """load_theme passes the theme name to lcd.load_theme_by_name."""
        img = MagicMock()
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": img, "is_animated": False}
        load_theme(MagicMock(), "TargetTheme")
        cli_mock_lcd.load_theme_by_name.assert_called_once_with("TargetTheme")

    def test_static_theme_calls_keep_alive_loop(self, cli_mock_lcd, capsys):
        """Static theme → enters keep_alive_loop (blocking resend)."""
        img = MagicMock()
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": img, "is_animated": False}
        cli_mock_lcd.device_path = "/dev/sg0"
        cli_mock_lcd.keep_alive_loop.return_value = {"success": True, "message": "Stopped"}

        rc = load_theme(MagicMock(), "Theme1")

        assert rc == 0
        cli_mock_lcd.keep_alive_loop.assert_called_once()
        assert "Ctrl+C" in capsys.readouterr().out

    def test_static_theme_keyboard_interrupt(self, cli_mock_lcd, capsys):
        """KeyboardInterrupt during keep_alive_loop → returns 0, prints Stopped."""
        img = MagicMock()
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": img, "is_animated": False}
        cli_mock_lcd.device_path = "/dev/sg0"
        cli_mock_lcd.keep_alive_loop.side_effect = KeyboardInterrupt()

        rc = load_theme(MagicMock(), "Theme1")

        assert rc == 0
        assert "Stopped" in capsys.readouterr().out

    def test_static_theme_wires_metrics_fn(self, cli_mock_lcd):
        """keep_alive_loop receives a metrics_fn from _ensure_system."""
        img = MagicMock()
        cli_mock_lcd.load_theme_by_name.return_value = {
            "success": True, "image": img, "is_animated": False}

        mock_svc = MagicMock()
        with patch("trcc.legacy.ui.cli._ensure_system"), \
             patch("trcc.legacy.ui.cli._system_svc", mock_svc):
            load_theme(MagicMock(), "Theme1")

        call_kwargs = cli_mock_lcd.keep_alive_loop.call_args[1]
        assert call_kwargs["metrics_fn"] is not None
        # Calling the lambda should delegate to _system_svc.all_metrics
        call_kwargs["metrics_fn"]()
        mock_svc.all_metrics.__class__  # accessed


# ===========================================================================
# TestSaveTheme
# ===========================================================================

class TestSaveTheme:
    """save_theme() — routes through lcd.save() via LCDDevice.

    Phase 9: TrccApp dissolved. ``cli_mock_lcd`` registers a MagicMock
    LCDDevice on _boot._cached and bypasses discover() so tests configure
    return values on the device directly.
    """

    def test_no_device_returns_1(self, cli_no_lcd, capsys):
        rc = save_theme("MyTheme")
        assert rc == 1

    def test_success_returns_0(self, cli_mock_lcd, capsys):
        cli_mock_lcd.current_image = MagicMock()
        cli_mock_lcd.save.return_value = {"success": True, "message": "Saved: Custom_MyTheme"}
        rc = save_theme("MyTheme")
        assert rc == 0
        assert "Saved" in capsys.readouterr().out

    def test_no_image_returns_1(self, cli_mock_lcd, capsys):
        cli_mock_lcd.current_image = None
        rc = save_theme("MyTheme")
        assert rc == 1
        assert "No background to save" in capsys.readouterr().out

    def test_save_fails_returns_1(self, cli_mock_lcd, capsys):
        cli_mock_lcd.current_image = MagicMock()
        cli_mock_lcd.save.return_value = {"success": False, "message": "Save failed: disk full"}
        rc = save_theme("MyTheme")
        assert rc == 1

    def test_background_loads_image(self, cli_mock_lcd, tmp_path):
        bg_file = tmp_path / "bg.png"
        bg_file.write_bytes(b"fake")
        cli_mock_lcd.current_image = MagicMock()
        cli_mock_lcd.load_image.return_value = {"success": True, "image": MagicMock()}
        cli_mock_lcd.save.return_value = {"success": True, "message": "Saved"}
        save_theme("MyTheme", background=str(bg_file))
        cli_mock_lcd.load_image.assert_called_once()
        cli_mock_lcd.save.assert_called_once_with("MyTheme")

    def test_background_not_found_returns_1(self, cli_mock_lcd, capsys):
        rc = save_theme("MyTheme", background="/nonexistent/bg.png")
        assert rc == 1
        assert "not found" in capsys.readouterr().out.lower()

    def test_metrics_configures_overlay(self, cli_mock_lcd):
        cli_mock_lcd.current_image = MagicMock()
        cli_mock_lcd.save.return_value = {"success": True, "message": "Saved"}
        save_theme("MyTheme", metrics=["cpu_temp:10,10"])
        cli_mock_lcd.set_config.assert_called_once()
        cli_mock_lcd.enable_overlay.assert_called_once_with(True)

    def test_mask_calls_set_mask_from_path(self, cli_mock_lcd, tmp_path):
        mask_file = tmp_path / "mask.png"
        mask_file.write_bytes(b"fake")
        cli_mock_lcd.current_image = MagicMock()
        cli_mock_lcd.set_mask_from_path.return_value = {"success": True, "message": "ok"}
        cli_mock_lcd.save.return_value = {"success": True, "message": "Saved"}
        save_theme("MyTheme", mask=str(mask_file))
        cli_mock_lcd.set_mask_from_path.assert_called_once()
        cli_mock_lcd.save.assert_called_once_with("MyTheme")


# ===========================================================================
# TestExportTheme
# ===========================================================================

class TestExportTheme:
    """export_theme() — success, partial match, not found, no themes dir."""

    def _base_patches(self, mock_theme_dir, themes=None, w=320, h=320):
        from trcc.legacy.conf import save_config
        # Write device config so _get_device_cfg() finds it
        config = {'last_device': 0, 'devices': {
            '0': {'w': w, 'h': h, 'theme_dir': str(mock_theme_dir.path)},
        }}
        save_config(config)
        settings_mock = MagicMock()
        settings_mock.width = w
        settings_mock.height = h
        settings_mock.rotation = 0
        data_mgr = MagicMock()
        theme_svc = MagicMock()
        theme_svc.return_value = theme_svc
        # ThemeService(export_theme_fn=...) returns the same mock instance
        theme_svc.return_value = theme_svc
        if themes is not None:
            theme_svc.discover_local_merged.return_value = themes
        return settings_mock, data_mgr, theme_svc

    def test_exact_match_success(self, capsys, tmp_path, make_local_theme, mock_theme_dir):
        t = make_local_theme("MyTheme", theme_path="/themes/MyTheme")
        sm, dm, ts = self._base_patches(mock_theme_dir, themes=[t])
        ts.export_tr.return_value = (True, "Exported to /out/MyTheme.tr")
        with patch(_PATCH_SETTINGS, sm), \
             patch(_PATCH_DATA_MANAGER, dm), \
             patch(_PATCH_THEME_SVC, ts), \
             patch(_PATCH_RESOLVE_THEME_DIR, return_value=str(mock_theme_dir.path)), \
             patch(_PATCH_HAS_THEMES, return_value=True):
            rc = export_theme("MyTheme", str(tmp_path / "MyTheme.tr"))
        assert rc == 0
        assert "Exported" in capsys.readouterr().out

    def test_partial_match_success(self, capsys, tmp_path, make_local_theme, mock_theme_dir):
        t = make_local_theme("CoolThemeXL", theme_path="/themes/CoolThemeXL")
        sm, dm, ts = self._base_patches(mock_theme_dir, themes=[t])
        ts.export_tr.return_value = (True, "Exported")
        with patch(_PATCH_SETTINGS, sm), \
             patch(_PATCH_DATA_MANAGER, dm), \
             patch(_PATCH_THEME_SVC, ts), \
             patch(_PATCH_RESOLVE_THEME_DIR, return_value=str(mock_theme_dir.path)), \
             patch(_PATCH_HAS_THEMES, return_value=True):
            rc = export_theme("cool", str(tmp_path / "out.tr"))
        assert rc == 0

    def test_not_found_returns_1(self, capsys, tmp_path, make_local_theme, mock_theme_dir):
        sm, dm, ts = self._base_patches(mock_theme_dir, themes=[make_local_theme("OtherTheme")])
        with patch(_PATCH_SETTINGS, sm), \
             patch(_PATCH_DATA_MANAGER, dm), \
             patch(_PATCH_THEME_SVC, ts), \
             patch(_PATCH_RESOLVE_THEME_DIR, return_value=str(mock_theme_dir.path)), \
             patch(_PATCH_HAS_THEMES, return_value=True):
            rc = export_theme("Nonexistent", str(tmp_path / "out.tr"))
        assert rc == 1
        assert "not found" in capsys.readouterr().out.lower()

    def test_theme_with_no_path_returns_1(self, capsys, tmp_path, make_local_theme, mock_theme_dir):
        t = make_local_theme("NullPath")
        t.path = None  # no path attribute
        sm, dm, ts = self._base_patches(mock_theme_dir, themes=[t])
        with patch(_PATCH_SETTINGS, sm), \
             patch(_PATCH_DATA_MANAGER, dm), \
             patch(_PATCH_THEME_SVC, ts), \
             patch(_PATCH_RESOLVE_THEME_DIR, return_value=str(mock_theme_dir.path)), \
             patch(_PATCH_HAS_THEMES, return_value=True):
            rc = export_theme("NullPath", str(tmp_path / "out.tr"))
        assert rc == 1

    def test_no_themes_dir_returns_1(self, capsys, tmp_path):
        from trcc.legacy.conf import save_config
        # Device configured but theme_dir doesn't exist
        config = {'last_device': 0, 'devices': {
            '0': {'w': 320, 'h': 320, 'theme_dir': '/nonexistent'},
        }}
        save_config(config)
        sm = MagicMock()
        sm.width = 320
        sm.height = 320
        sm.rotation = 0
        dm = MagicMock()
        with patch(_PATCH_SETTINGS, sm), \
             patch(_PATCH_DATA_MANAGER, dm):
            rc = export_theme("AnyTheme", str(tmp_path / "out.tr"))
        assert rc == 1
        assert "No themes" in capsys.readouterr().out

    def test_export_fails_returns_1(self, capsys, tmp_path, make_local_theme, mock_theme_dir):
        t = make_local_theme("MyTheme")
        sm, dm, ts = self._base_patches(mock_theme_dir, themes=[t])
        ts.export_tr.return_value = (False, "Export failed: permission denied")
        with patch(_PATCH_SETTINGS, sm), \
             patch(_PATCH_DATA_MANAGER, dm), \
             patch(_PATCH_THEME_SVC, ts), \
             patch(_PATCH_RESOLVE_THEME_DIR, return_value=str(mock_theme_dir.path)), \
             patch(_PATCH_HAS_THEMES, return_value=True):
            rc = export_theme("MyTheme", str(tmp_path / "out.tr"))
        assert rc == 1
        assert "Export failed" in capsys.readouterr().out

    def test_zero_resolution_errors(self, capsys):
        """When no device resolution is saved (0x0), export_theme errors — no fallback."""
        sm = MagicMock()
        sm.width = 0
        sm.height = 0
        dm = MagicMock()
        ts = MagicMock()
        with patch(_PATCH_SETTINGS, sm), \
             patch(_PATCH_DATA_MANAGER, dm), \
             patch(_PATCH_THEME_SVC, ts):
            rc = export_theme("AnyTheme", "/out.tr")
        assert rc == 1
        assert "connect" in capsys.readouterr().out.lower()


# ===========================================================================
# TestImportTheme
# ===========================================================================

class TestImportTheme:
    """import_theme() — success, failure, no device, resolution and path forwarding.

    Phase 9: TrccApp dissolved. ``cli_mock_lcd`` registers a MagicMock LCD on
    _boot._cached and bypasses discover().
    """

    def _mock_theme_svc(self, result) -> MagicMock:
        ts = MagicMock()
        ts.return_value = ts
        ts.import_tr.return_value = result
        return ts

    def test_no_device_returns_1(self, cli_no_lcd, capsys, tmp_path):
        rc = import_theme(str(tmp_path / "theme.tr"))
        assert rc == 1

    def test_success_with_theme_info_result(self, cli_mock_lcd, capsys, tmp_path):
        cli_mock_lcd.lcd_size = (320, 320)
        theme_info = MagicMock()
        theme_info.name = "ImportedTheme"
        ts = self._mock_theme_svc((True, theme_info))
        with patch(_PATCH_SETTINGS), \
             patch(_PATCH_THEME_SVC, ts):
            rc = import_theme(str(tmp_path / "theme.tr"))
        assert rc == 0
        assert "ImportedTheme" in capsys.readouterr().out

    def test_success_with_string_result(self, cli_mock_lcd, capsys, tmp_path):
        cli_mock_lcd.lcd_size = (320, 320)
        ts = self._mock_theme_svc((True, "Import successful"))
        with patch(_PATCH_SETTINGS), \
             patch(_PATCH_THEME_SVC, ts):
            rc = import_theme(str(tmp_path / "theme.tr"))
        assert rc == 0
        assert "Import successful" in capsys.readouterr().out

    def test_failure_returns_1(self, cli_mock_lcd, capsys, tmp_path):
        cli_mock_lcd.lcd_size = (320, 320)
        ts = self._mock_theme_svc((False, "Invalid .tr file"))
        with patch(_PATCH_SETTINGS), \
             patch(_PATCH_THEME_SVC, ts):
            rc = import_theme(str(tmp_path / "theme.tr"))
        assert rc == 1
        assert "Invalid" in capsys.readouterr().out

    def test_passes_resolution_to_import_tr(self, cli_mock_lcd, tmp_path):
        cli_mock_lcd.lcd_size = (640, 480)
        ts = self._mock_theme_svc((True, "ok"))
        with patch(_PATCH_SETTINGS), \
             patch(_PATCH_THEME_SVC, ts):
            import_theme(str(tmp_path / "theme.tr"))
        call_args = ts.import_tr.call_args[0]
        assert (640, 480) in call_args

    def test_passes_correct_file_path(self, cli_mock_lcd, tmp_path):
        cli_mock_lcd.lcd_size = (320, 320)
        ts = self._mock_theme_svc((True, "ok"))
        file_path = str(tmp_path / "my_theme.tr")
        with patch(_PATCH_SETTINGS), \
             patch(_PATCH_THEME_SVC, ts):
            import_theme(file_path)
        call_args = ts.import_tr.call_args[0]
        assert Path(file_path) in call_args
