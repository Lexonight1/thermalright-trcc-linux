"""LED setter Commands + Settings persistence — end-to-end through App."""
from __future__ import annotations

import copy
import dataclasses
from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import (
    EnableLedTestMode,
    SelectZone,
    SetClockFormat,
    SetDiskIndex,
    SetLedBrightness,
    SetLedColor,
    SetLedLoadSource,
    SetLedMode,
    SetLedTempSource,
    SetLedZoneBrightness,
    SetLedZoneColor,
    SetLedZoneMode,
    SetLedZoneSync,
    SetLedZoneSyncInterval,
    SetLedZoneSyncZones,
    SetMemoryRatio,
    SetWeekStart,
    ToggleLed,
    ToggleSegment,
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


# ── The LED gate: settings-only Commands refuse a non-LED device (#252) ─
#
# Aiming an LED Command at an LCD used to answer "LED color set to #ffffff"
# with exit 0 and stash LED settings under that LCD's key.  Only the two
# wire-touching Commands ever checked; these are the settings half.
#
# The gate's three-way answer is what makes it safe: the tests ABOVE drive
# these same Commands against an LED key with nothing attached, so a blunt
# "not attached -> refuse" would break that real pre-attach flow.


_LCD_KEY = "0402:3922"        # Frozen Warframe, SCSI — Kind.LCD in the registry
_UNKNOWN_KEY = "dead:beef"    # matches no registry row


# Every settings-only LED Command + the minimum kwargs to construct it.
# Parametrizing over the whole set is the point: the bug was 19 Commands
# missing one check, so a table that grows with the module is the only
# shape that catches the twentieth.
_GATED: list = [
    (SetLedMode, {"mode": LEDMode.RAINBOW}),
    (SetLedColor, {"color": (1, 2, 3)}),
    (SetLedBrightness, {"percent": 42}),
    (EnableLedTestMode, {"enabled": True}),
    (SetLedTempSource, {"source": "gpu"}),
    (SetLedLoadSource, {"source": "gpu"}),
    (ToggleLed, {"on": False}),
    (SetLedZoneColor, {"zone": 0, "color": (1, 2, 3)}),
    (SetLedZoneMode, {"zone": 0, "mode": LEDMode.RAINBOW}),
    (SetLedZoneBrightness, {"zone": 0, "percent": 42}),
    (SetLedZoneSync, {"enabled": True}),
    (SetLedZoneSyncInterval, {"ticks": 9}),
    (SetLedZoneSyncZones, {"zones": (True, False)}),
    (SelectZone, {"zone": 0}),
    (ToggleSegment, {"index": 0, "on": True}),
    (SetClockFormat, {"is_24h": False}),
    (SetWeekStart, {"sunday_first": True}),
    (SetMemoryRatio, {"ratio": 2}),
    (SetDiskIndex, {"index": 1}),
]

_GATED_IDS = [cmd.__name__ for cmd, _ in _GATED]


def test_gate_covers_every_settings_only_led_command() -> None:
    """The table above must list every ungated settings-only LED Command.

    Guards the table against the module growing past it — a new setter that
    forgets the gate is exactly the #252 bug returning, and it would slip
    through a table someone forgot to extend.
    """
    import inspect

    import trcc.core.commands.led as led_module
    from trcc.core.commands._base import Command

    expected = set()
    for obj in vars(led_module).values():
        if (inspect.isclass(obj) and issubclass(obj, Command)
                and obj is not Command
                and obj.__module__ == led_module.__name__):
            source = inspect.getsource(obj)
            fields = {f.name for f in dataclasses.fields(obj)}
            # Settings-only == has a device key, writes Settings, no wire.
            # SetLedColors / RenderLed keep their own is_led check;
            # LedSnapshot only reads.
            if ("key" in fields and "app.settings.set_" in source
                    and "app.send(" not in source):
                expected.add(obj.__name__)

    assert expected == set(_GATED_IDS), (
        "settings-only LED Commands not covered by the #252 gate table: "
        f"{sorted(expected - set(_GATED_IDS))}"
    )


@pytest.mark.parametrize(("command", "kwargs"), _GATED, ids=_GATED_IDS)
def test_gated_command_refuses_an_attached_lcd(
    tmp_path: Path, command, kwargs,
) -> None:
    """Attached LCD: the device object answers, and nothing is written."""
    app = _app(tmp_path)
    app.attach(0x0402, 0x3922)
    before = copy.deepcopy(app.settings.for_led(_LCD_KEY))

    result = app.dispatch(command(key=_LCD_KEY, **kwargs))

    assert result.ok is False
    assert result.message == f"{_LCD_KEY} is not an LED device"
    assert app.settings.for_led(_LCD_KEY) == before, (
        "refused command still mutated the LCD's LED settings"
    )


@pytest.mark.parametrize(("command", "kwargs"), _GATED, ids=_GATED_IDS)
def test_gated_command_refuses_an_unplugged_lcd(
    tmp_path: Path, command, kwargs,
) -> None:
    """Nothing attached: the VID/PID registry still knows it is an LCD."""
    app = _app(tmp_path)
    result = app.dispatch(command(key=_LCD_KEY, **kwargs))
    assert result.ok is False
    assert result.message == f"{_LCD_KEY} is not an LED device"


@pytest.mark.parametrize(("command", "kwargs"), _GATED, ids=_GATED_IDS)
def test_gated_command_allows_an_unknown_key(
    tmp_path: Path, command, kwargs,
) -> None:
    """Unknown VID/PID is allowed — we cannot prove it is not an LED.

    Refusing here would block configuring a device the registry has not
    learned yet, to guard against a key nobody has.  The gate answers
    "no" only when it can point at evidence.
    """
    app = _app(tmp_path)
    result = app.dispatch(command(key=_UNKNOWN_KEY, **kwargs))
    assert "is not an LED device" not in result.message


# ── Helpers ──────────────────────────────────────────────────────────


def _new_runtime_with_timer(rgb_timer: int):
    from trcc.core.led_models import LedRuntimeState
    return LedRuntimeState(rgb_timer=rgb_timer)


def _new_runtime_with_test(*, timer: int, color: int):
    from trcc.core.led_models import LedRuntimeState
    return LedRuntimeState(test_timer=timer, test_color=color)
