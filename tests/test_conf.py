"""Tests for trcc.conf — Settings singleton, config persistence, language detection.

Covers every function and branch in conf.py for 100% coverage.
Uses the tmp_config fixture from conftest.py for filesystem isolation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.conf import (
    Settings,
    _detect_language,
    _migrate_config,
    load_config,
    load_last_handshake,
    save_config,
    save_last_handshake,
)


def _mock_resolver():
    """Create a mock path resolver for Settings DI in tests."""
    r = MagicMock()
    r.data_dir.return_value = '/tmp/trcc/data'
    r.web_dir.side_effect = lambda w, h: f'/tmp/trcc/data/web/{w}{h}'
    r.web_masks_dir.side_effect = lambda w, h: f'/tmp/trcc/data/web/zt{w}{h}'
    r.user_masks_dir.side_effect = lambda w, h: f'/tmp/trcc-user/data/web/zt{w}{h}'
    return r

# =========================================================================
# load_config / save_config
# =========================================================================


class TestLoadConfig:
    """load_config: reads JSON from CONFIG_PATH, returns {} on errors."""

    def test_returns_empty_dict_when_file_missing(self, tmp_config):
        from trcc.legacy.conf import CONFIG_PATH
        if os.path.exists(CONFIG_PATH):
            os.remove(CONFIG_PATH)
        assert load_config() == {}

    def test_reads_valid_json(self, tmp_config):
        save_config({"hello": "world"})
        assert load_config() == {"hello": "world"}

    def test_returns_empty_dict_on_corrupt_json(self, tmp_config):
        config_path = os.path.join(str(tmp_config / "trcc"), "config.json")
        with open(config_path, "w") as f:
            f.write("{broken json!!")
        assert load_config() == {}

    def test_returns_empty_dict_on_os_error(self, tmp_config, monkeypatch):
        """Simulate an OSError (e.g. permission denied)."""
        import builtins

        real_open = builtins.open

        def _bad_open(path, *a, **kw):
            from trcc.legacy.conf import CONFIG_PATH
            if str(path) == CONFIG_PATH:
                raise OSError("permission denied")
            return real_open(path, *a, **kw)

        monkeypatch.setattr(builtins, "open", _bad_open)
        assert load_config() == {}


class TestSaveConfig:
    """save_config: writes JSON to CONFIG_PATH, creates dirs."""

    def test_creates_dir_and_writes_json(self, tmp_config):
        save_config({"key": "value"})
        assert load_config() == {"key": "value"}

    def test_overwrites_existing_config(self, tmp_config):
        save_config({"v": 1})
        save_config({"v": 2})
        assert load_config()["v"] == 2


# =========================================================================
# Handshake cache
# =========================================================================


class TestHandshakeCache:
    """save_last_handshake / load_last_handshake: handshake JSON cache."""

    def test_round_trip(self, tmp_config):
        data = {"pm": 50, "fbl": 72, "raw": [0xDA, 0xDB]}
        save_last_handshake(data)
        assert load_last_handshake() == data

    def test_load_returns_empty_when_missing(self, tmp_config):
        assert load_last_handshake() == {}

    def test_load_returns_empty_on_corrupt_json(self, tmp_config):
        from trcc.legacy.conf import _HANDSHAKE_CACHE_PATH
        with open(_HANDSHAKE_CACHE_PATH, "w") as f:
            f.write("not json{{{")
        assert load_last_handshake() == {}

    def test_load_returns_empty_on_os_error(self, tmp_config, monkeypatch):
        import builtins

        real_open = builtins.open

        def _bad_open(path, *a, **kw):
            from trcc.legacy.conf import _HANDSHAKE_CACHE_PATH
            if str(path) == _HANDSHAKE_CACHE_PATH:
                raise OSError("disk error")
            return real_open(path, *a, **kw)

        monkeypatch.setattr(builtins, "open", _bad_open)
        assert load_last_handshake() == {}


# =========================================================================
# _migrate_config
# =========================================================================


class TestMigrateConfig:
    """_migrate_config: clears device state on version change."""

    def test_no_op_when_version_matches(self, tmp_config):
        from trcc.__version__ import __version__
        save_config({"config_version": __version__, "devices": {"0": {"vid_pid": "1234_5678"}}})
        _migrate_config()
        cfg = load_config()
        assert cfg["devices"] == {"0": {"vid_pid": "1234_5678"}}

    def test_first_run_sets_version_no_clear(self, tmp_config):
        """No saved_version (fresh install) — just sets config_version."""
        from trcc.__version__ import __version__
        _migrate_config()
        cfg = load_config()
        assert cfg["config_version"] == __version__

    def test_clears_device_state_on_version_mismatch(self, tmp_config):
        from trcc.__version__ import __version__
        save_config({
            "config_version": "0.0.1",
            "devices": {"0": {"vid_pid": "1234_5678", "theme": "dark"}},
            "resolution": [480, 480],
            "selected_device": "/dev/sg0",
            "installed_resolutions": {"320320": True},
            "temp_unit": 1,
            "lang": "de",
        })
        _migrate_config()
        cfg = load_config()
        # Device-derived keys cleared
        assert "devices" not in cfg
        assert "resolution" not in cfg
        assert "selected_device" not in cfg
        assert "installed_resolutions" not in cfg
        # User prefs preserved
        assert cfg["temp_unit"] == 1
        assert cfg["lang"] == "de"
        assert cfg["config_version"] == __version__

    def test_deletes_led_probe_cache_on_version_mismatch(self, tmp_config):
        from trcc.legacy.conf import CONFIG_DIR
        probe_cache = os.path.join(CONFIG_DIR, "led_probe_cache.json")
        with open(probe_cache, "w") as f:
            json.dump({"stale": True}, f)
        save_config({"config_version": "0.0.1"})
        _migrate_config()
        assert not os.path.exists(probe_cache)

    def test_handles_led_probe_cache_delete_failure(self, tmp_config, monkeypatch):
        """OSError on probe cache delete is logged, not raised."""
        from trcc.legacy.conf import CONFIG_DIR
        probe_cache = os.path.join(CONFIG_DIR, "led_probe_cache.json")
        with open(probe_cache, "w") as f:
            json.dump({"stale": True}, f)
        save_config({"config_version": "0.0.1"})

        real_remove = os.remove
        def _fail_remove(path):
            if "led_probe_cache" in str(path):
                raise OSError("permission denied")
            return real_remove(path)

        monkeypatch.setattr(os, "remove", _fail_remove)
        # Should not raise
        _migrate_config()
        # Cache file still exists since remove failed
        assert os.path.exists(probe_cache)


# =========================================================================
# _detect_language
# =========================================================================


class TestDetectLanguage:
    """_detect_language: maps system locale to ISO 639-1 code."""

    def test_exact_locale_match(self, monkeypatch):
        monkeypatch.setattr("locale.getlocale", lambda: ("zh_CN", "UTF-8"))
        assert _detect_language() == "zh"

    def test_exact_locale_en(self, monkeypatch):
        monkeypatch.setattr("locale.getlocale", lambda: ("en", "UTF-8"))
        assert _detect_language() == "en"

    def test_prefix_match_de_DE(self, monkeypatch):
        """de_DE is not in LOCALE_TO_LANG, but prefix 'de' is."""
        monkeypatch.setattr("locale.getlocale", lambda: ("de_DE", "UTF-8"))
        assert _detect_language() == "de"

    def test_prefix_match_fr(self, monkeypatch):
        monkeypatch.setattr("locale.getlocale", lambda: ("fr_FR", "UTF-8"))
        assert _detect_language() == "fr"

    def test_prefix_match_ru(self, monkeypatch):
        monkeypatch.setattr("locale.getlocale", lambda: ("ru_RU", "UTF-8"))
        assert _detect_language() == "ru"

    def test_prefix_match_ja(self, monkeypatch):
        monkeypatch.setattr("locale.getlocale", lambda: ("ja_JP", "UTF-8"))
        assert _detect_language() == "ja"

    def test_prefix_match_es(self, monkeypatch):
        monkeypatch.setattr("locale.getlocale", lambda: ("es_ES", "UTF-8"))
        assert _detect_language() == "es"

    def test_prefix_match_pt(self, monkeypatch):
        monkeypatch.setattr("locale.getlocale", lambda: ("pt_BR", "UTF-8"))
        assert _detect_language() == "pt"

    def test_zh_TW_traditional_chinese(self, monkeypatch):
        monkeypatch.setattr("locale.getlocale", lambda: ("zh_TW", "UTF-8"))
        assert _detect_language() == "zh_TW"

    def test_falls_back_to_env_LANG(self, monkeypatch):
        """locale.getlocale returns (None, None) — fall back to $LANG."""
        monkeypatch.setattr("locale.getlocale", lambda: (None, None))
        monkeypatch.setenv("LANG", "de.UTF-8")
        assert _detect_language() == "de"

    def test_falls_back_to_en_when_no_locale(self, monkeypatch):
        """No locale, no $LANG — defaults to 'en'."""
        monkeypatch.setattr("locale.getlocale", lambda: (None, None))
        monkeypatch.delenv("LANG", raising=False)
        assert _detect_language() == "en"

    def test_korean_locale_returns_ko(self, monkeypatch):
        """Korean locale returns 'ko' code."""
        monkeypatch.setattr("locale.getlocale", lambda: ("ko_KR", "UTF-8"))
        assert _detect_language() == "ko"

    def test_unknown_locale_returns_en(self, monkeypatch):
        """Unrecognized locale with no prefix match returns 'en'."""
        monkeypatch.setattr("locale.getlocale", lambda: ("sw_KE", "UTF-8"))
        assert _detect_language() == "en"

    def test_exception_in_getlocale_returns_en(self, monkeypatch):
        """locale.getlocale() raises — catch and default to 'en'."""
        monkeypatch.setattr("locale.getlocale", lambda: (_ for _ in ()).throw(ValueError("bad")))
        assert _detect_language() == "en"


# =========================================================================
# Settings — persistence helpers (static methods)
# =========================================================================


class TestSettingsPersistence:
    """Static persistence methods on Settings class."""

    def test_get_saved_resolution_default(self, tmp_config):
        assert Settings._get_saved_resolution() is None

    def test_save_and_get_resolution(self, tmp_config):
        save_config({'devices': {'0': {'w': 480, 'h': 480}}})
        assert Settings._get_saved_resolution() == (480, 480)

    def test_get_saved_resolution_invalid_type(self, tmp_config):
        """Non-list resolution returns None — no hardcoded fallback."""
        save_config({"resolution": "bad"})
        assert Settings._get_saved_resolution() is None

    def test_get_saved_resolution_wrong_length(self, tmp_config):
        """List with != 2 elements returns None — no hardcoded fallback."""
        save_config({"resolution": [320]})
        assert Settings._get_saved_resolution() is None

    def test_get_saved_temp_unit_default(self, tmp_config):
        assert Settings._get_saved_temp_unit() == 0

    def test_save_and_get_temp_unit(self, tmp_config):
        Settings._save_temp_unit(1)
        assert Settings._get_saved_temp_unit() == 1

    def test_get_saved_hdd_enabled_default(self, tmp_config):
        assert Settings._get_saved_hdd_enabled() is True

    def test_save_and_get_hdd_enabled(self, tmp_config):
        Settings._save_hdd_enabled(False)
        assert Settings._get_saved_hdd_enabled() is False


# =========================================================================
# Settings — device config, format prefs, selected device
# =========================================================================


class TestSettingsDeviceConfig:
    """Device config, selected device, and format prefs."""

    def test_device_config_key(self):
        assert Settings.device_config_key(0, 0x87CD, 0x70DB) == "0"
        assert Settings.device_config_key(1, 0x0416, 0x5302) == "1"

    def test_device_config_key_caches_vid_pid(self):
        Settings.device_config_key(0, 0x87CD, 0x70DB)
        assert Settings._vid_pid_cache["0"] == "87cd_70db"

    def test_get_device_config_empty(self, tmp_config):
        assert Settings.get_device_config("0") == {}

    def test_save_and_get_device_setting(self, tmp_config):
        key = Settings.device_config_key(0, 0x87CD, 0x70DB)
        Settings.save_device_setting(key, "theme", "dark")
        Settings.save_device_setting(key, "rotation", 90)
        cfg = Settings.get_device_config(key)
        assert cfg["theme"] == "dark"
        assert cfg["rotation"] == 90
        assert cfg["vid_pid"] == "87cd_70db"

    def test_save_device_settings_batch(self, tmp_config):
        key = Settings.device_config_key(0, 0x0416, 0x5408)
        Settings.save_device_settings(
            key, theme_name='Theme1', theme_type='local', mask_id='')
        cfg = Settings.get_device_config(key)
        assert cfg['theme_name'] == 'Theme1'
        assert cfg['theme_type'] == 'local'
        assert cfg['mask_id'] == ''
        assert cfg['vid_pid'] == '0416_5408'

    def test_get_selected_device_none(self, tmp_config):
        assert Settings.get_selected_device() is None

    def test_save_and_get_selected_device(self, tmp_config):
        Settings.save_selected_device("/dev/sg0")
        assert Settings.get_selected_device() == "/dev/sg0"

    def test_get_last_device_default(self, tmp_config):
        assert Settings.get_last_device() == 0

    def test_save_and_get_last_device(self, tmp_config):
        Settings.save_last_device(2)
        assert Settings.get_last_device() == 2

    def test_save_last_device_persists_to_config(self, tmp_config):
        Settings.save_last_device(1)
        config = load_config()
        assert config['last_device'] == 1

    def test_get_format_prefs_empty(self, tmp_config):
        assert Settings.get_format_prefs() == {}

    def test_save_and_get_format_pref(self, tmp_config):
        Settings.save_format_pref("time_format", 1)
        Settings.save_format_pref("date_format", 2)
        prefs = Settings.get_format_prefs()
        assert prefs == {"time_format": 1, "date_format": 2}

    def test_clear_installed_resolutions(self, tmp_config):
        save_config({"installed_resolutions": {"320320": True}})
        Settings.clear_installed_resolutions()
        assert "installed_resolutions" not in load_config()

    def test_clear_installed_resolutions_no_key(self, tmp_config):
        """No-op when key doesn't exist."""
        save_config({"other": 1})
        Settings.clear_installed_resolutions()
        assert load_config()["other"] == 1


# =========================================================================
# Settings.apply_format_prefs
# =========================================================================


class TestApplyFormatPrefs:
    """apply_format_prefs: applies saved format prefs to overlay config."""

    def test_no_prefs_returns_unchanged(self, tmp_config):
        overlay = {"e0": {"metric": "time", "time_format": 0}}
        result = Settings.apply_format_prefs(overlay)
        assert result["e0"]["time_format"] == 0

    def test_applies_time_format(self, tmp_config):
        Settings.save_format_pref("time_format", 1)
        overlay = {"e0": {"metric": "time", "time_format": 0}}
        result = Settings.apply_format_prefs(overlay)
        assert result["e0"]["time_format"] == 1

    def test_applies_date_format(self, tmp_config):
        Settings.save_format_pref("date_format", 2)
        overlay = {"e0": {"metric": "date", "date_format": 0}}
        result = Settings.apply_format_prefs(overlay)
        assert result["e0"]["date_format"] == 2

    def test_applies_temp_unit_to_all_entries_with_metric(self, tmp_config):
        Settings.save_format_pref("temp_unit", 1)
        overlay = {
            "e0": {"metric": "cpu_temp"},
            "e1": {"metric": "gpu_temp"},
        }
        result = Settings.apply_format_prefs(overlay)
        assert result["e0"]["temp_unit"] == 1
        assert result["e1"]["temp_unit"] == 1

    def test_skips_non_dict_entries(self, tmp_config):
        Settings.save_format_pref("time_format", 1)
        overlay = {"meta": "some_string", "e0": {"metric": "time", "time_format": 0}}
        result = Settings.apply_format_prefs(overlay)
        assert result["meta"] == "some_string"
        assert result["e0"]["time_format"] == 1

    def test_no_metric_key_no_temp_unit_applied(self, tmp_config):
        """Entries without 'metric' key don't get temp_unit."""
        Settings.save_format_pref("temp_unit", 1)
        overlay = {"e0": {"value": 42}}
        result = Settings.apply_format_prefs(overlay)
        assert "temp_unit" not in result["e0"]


# =========================================================================
# Settings.__init__ — instance construction
# =========================================================================


class TestSettingsInit:
    """Settings.__init__: loads config, resolves paths."""

    def test_init_loads_defaults(self, tmp_config):
        with patch("trcc.legacy.conf._migrate_config"):
            s = Settings(_mock_resolver())
        # No saved resolution → defaults to (0, 0)
        assert s.resolution == (0, 0)
        assert s.temp_unit == 0
        assert s.hdd_enabled is True

    def test_init_loads_saved_state(self, tmp_config):
        save_config({
            "resolution": [480, 480],
            "temp_unit": 1,
            "hdd_enabled": False,
            "lang": "de",
        })
        with patch("trcc.legacy.conf._migrate_config"):
            s = Settings(_mock_resolver())
        assert s.resolution == (480, 480)
        assert s.width == 480
        assert s.height == 480
        assert s.temp_unit == 1
        assert s.hdd_enabled is False
        assert s.lang == "de"

    def test_init_zero_resolution(self, tmp_config):
        """Zero resolution is stored without error."""
        save_config({"resolution": [0, 0]})
        with patch("trcc.legacy.conf._migrate_config"):
            s = Settings(_mock_resolver())
        assert s.width == 0
        assert s.height == 0


# =========================================================================
# Settings — instance methods
# =========================================================================


class TestSettingsInstance:
    """Instance methods: set_resolution, set_temp_unit, set_hdd_enabled, lang."""

    @pytest.fixture
    def settings(self, tmp_config):
        """Create a Settings instance with mock path resolver (DI)."""
        with patch("trcc.legacy.conf._migrate_config"):
            s = Settings(_mock_resolver())
        return s

    def test_set_resolution_updates_in_memory(self, settings, tmp_config):
        """set_resolution updates in-memory state; persistence is via save_device_setting."""
        settings.set_resolution(480, 480)
        assert settings.resolution == (480, 480)
        # Not persisted automatically — lcd_handler saves via save_device_setting
        assert Settings._get_saved_resolution() is None

    def test_set_resolution_no_op_same_value(self, settings, tmp_config):
        """No change when setting the same resolution."""
        settings.set_resolution(320, 320)
        old = settings.resolution
        settings.set_resolution(320, 320)
        assert settings.resolution == old

    def test_set_resolution_no_persist(self, settings, tmp_config):
        """set_resolution is always in-memory only; config unchanged."""
        settings.set_resolution(640, 480, persist=False)
        assert settings.resolution == (640, 480)
        # Config unchanged — resolution not persisted (saved via save_device_setting)
        assert Settings._get_saved_resolution() is None

    def test_set_temp_unit_updates_and_persists(self, settings, tmp_config):
        settings.set_temp_unit(1)
        assert settings.temp_unit == 1
        assert Settings._get_saved_temp_unit() == 1

    def test_set_hdd_enabled_updates_and_persists(self, settings, tmp_config):
        settings.set_hdd_enabled(False)
        assert settings.hdd_enabled is False
        assert Settings._get_saved_hdd_enabled() is False

    def test_lang_setter_persists(self, settings, tmp_config):
        settings.lang = "ja"
        assert settings.lang == "ja"
        assert load_config()["lang"] == "ja"

    def test_get_saved_lang_falls_back_to_detect(self, tmp_config, monkeypatch):
        """No saved lang in config -> falls back to _detect_language."""
        monkeypatch.setattr("locale.getlocale", lambda: ("ja_JP", "UTF-8"))
        with patch("trcc.legacy.conf._migrate_config"):
            s = Settings(_mock_resolver())
        assert s.lang == "ja"

    def test_get_saved_lang_uses_saved(self, tmp_config):
        """Saved lang in config is used directly."""
        save_config({"lang": "es"})
        with patch("trcc.legacy.conf._migrate_config"):
            s = Settings(_mock_resolver())
        assert s.lang == "es"

    def test_get_saved_lang_migrates_legacy_code(self, tmp_config):
        """Legacy C# suffix in config.json is auto-migrated to ISO 639-1."""
        save_config({"lang": "d"})
        with patch("trcc.legacy.conf._migrate_config"):
            s = Settings(_mock_resolver())
        assert s.lang == "de"
        assert load_config()["lang"] == "de"  # Persisted


# =========================================================================
# Settings.set_rotation (persistence only — domain logic in core/orientation)
# =========================================================================


class TestSetRotation:
    """set_rotation: updates rotation state (no longer resolves dirs)."""

    @pytest.fixture
    def settings(self, tmp_config):
        save_config({"resolution": [1280, 480]})
        with patch("trcc.legacy.conf._migrate_config"):
            return Settings(_mock_resolver())



# =========================================================================
# Settings properties (width, height, resolution, user_data_dir)
# =========================================================================


class TestSettingsProperties:
    """Read-only properties return correct values."""

    def test_user_data_dir(self, tmp_config):
        r = _mock_resolver()
        with patch("trcc.legacy.conf._migrate_config"):
            s = Settings(r)
        assert s.user_data_dir == Path(r.data_dir())
