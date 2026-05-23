"""LED setter Commands + Settings persistence — end-to-end through App."""
from __future__ import annotations

from pathlib import Path

from trcc.app import App
from trcc.core.commands import (
    EnableLedTestMode,
    SetLedBrightness,
    SetLedColor,
    SetLedLoadSource,
    SetLedMode,
    SetLedTempSource,
)
from trcc.core.led_models import LEDMode
from trcc.services.settings import Settings

from .conftest import FakePaths, FakePlatform

_KEY = "0416:8001"


def _app(tmp_path: Path) -> App:
    return App(platform=FakePlatform(tmp_path))


# ── Mode ─────────────────────────────────────────────────────────────


def test_set_mode_updates_persists_resets_phase(tmp_path: Path) -> None:
    app = _app(tmp_path)
    # Seed a non-zero phase so we can prove reset
    app.led_runtime.setdefault(_KEY, _new_runtime_with_timer(42))

    result = app.dispatch(SetLedMode(key=_KEY, mode=LEDMode.RAINBOW))

    assert result.ok is True
    assert "RAINBOW" in result.message
    # Settings persisted
    assert app.settings.for_led(_KEY).mode is LEDMode.RAINBOW
    # Phase reset to 0 so the new mode's animation starts fresh
    assert app.led_runtime[_KEY].rgb_timer == 0


# ── Color ────────────────────────────────────────────────────────────


def test_set_color_persists(tmp_path: Path) -> None:
    app = _app(tmp_path)
    result = app.dispatch(SetLedColor(key=_KEY, color=(10, 20, 30)))
    assert result.ok is True
    assert app.settings.for_led(_KEY).color == (10, 20, 30)


def test_set_color_rejects_out_of_range_channel(tmp_path: Path) -> None:
    app = _app(tmp_path)
    result = app.dispatch(SetLedColor(key=_KEY, color=(300, 0, 0)))
    assert result.ok is False
    assert "out of range" in result.message
    # Color did not change
    assert app.settings.for_led(_KEY).color == (255, 0, 0)


# ── Brightness ───────────────────────────────────────────────────────


def test_set_brightness_persists(tmp_path: Path) -> None:
    app = _app(tmp_path)
    result = app.dispatch(SetLedBrightness(key=_KEY, percent=33))
    assert result.ok is True
    assert app.settings.for_led(_KEY).brightness == 33


def test_set_brightness_rejects_out_of_range(tmp_path: Path) -> None:
    app = _app(tmp_path)
    result = app.dispatch(SetLedBrightness(key=_KEY, percent=150))
    assert result.ok is False
    assert "out of range" in result.message
    # Original default unchanged
    assert app.settings.for_led(_KEY).brightness == 65


# ── Test mode ────────────────────────────────────────────────────────


def test_enable_test_mode_persists_and_resets_counters(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.led_runtime.setdefault(_KEY, _new_runtime_with_test(timer=5, color=2))

    result = app.dispatch(EnableLedTestMode(key=_KEY, enabled=True))

    assert result.ok is True
    assert app.settings.for_led(_KEY).test_mode is True
    assert app.led_runtime[_KEY].test_timer == 0
    assert app.led_runtime[_KEY].test_color == 0


# ── Sensor sources ───────────────────────────────────────────────────


def test_set_temp_source_persists(tmp_path: Path) -> None:
    app = _app(tmp_path)
    result = app.dispatch(SetLedTempSource(key=_KEY, source="gpu"))
    assert result.ok is True
    assert app.settings.for_led(_KEY).temp_source == "gpu"


def test_set_temp_source_rejects_invalid(tmp_path: Path) -> None:
    app = _app(tmp_path)
    result = app.dispatch(SetLedTempSource(key=_KEY, source="ram"))
    assert result.ok is False
    assert "ram" in result.message
    # Unchanged
    assert app.settings.for_led(_KEY).temp_source == "cpu"


def test_set_load_source_persists(tmp_path: Path) -> None:
    app = _app(tmp_path)
    result = app.dispatch(SetLedLoadSource(key=_KEY, source="gpu"))
    assert result.ok is True
    assert app.settings.for_led(_KEY).load_source == "gpu"


# ── Cross-Settings round-trip (save + reload) ───────────────────────


def test_led_settings_persist_across_reload(tmp_path: Path) -> None:
    """Writing through Commands + creating a fresh Settings = same values back."""
    platform = FakePlatform(tmp_path)
    app = App(platform=platform)
    app.dispatch(SetLedMode(key=_KEY, mode=LEDMode.COLORFUL))
    app.dispatch(SetLedColor(key=_KEY, color=(7, 14, 21)))
    app.dispatch(SetLedBrightness(key=_KEY, percent=42))
    app.dispatch(SetLedTempSource(key=_KEY, source="gpu"))

    # Fresh Settings reads from the same config dir
    reloaded = Settings(FakePaths(tmp_path))
    led = reloaded.for_led(_KEY)
    assert led.mode is LEDMode.COLORFUL
    assert led.color == (7, 14, 21)
    assert led.brightness == 42
    assert led.temp_source == "gpu"


# ── Helpers ──────────────────────────────────────────────────────────


def _new_runtime_with_timer(rgb_timer: int):
    from trcc.core.led_models import LedRuntimeState
    return LedRuntimeState(rgb_timer=rgb_timer)


def _new_runtime_with_test(*, timer: int, color: int):
    from trcc.core.led_models import LedRuntimeState
    return LedRuntimeState(test_timer=timer, test_color=color)
