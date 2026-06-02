"""Control-center settings Commands: SetTempUnit / SetLanguage /
SetGpuDevice / SetRefreshInterval.

Each Command is a Settings write + an EventBus publish. Validation:
  * SetTempUnit rejects anything other than "C" or "F", and propagates
    the chosen unit to every existing DeviceSettings entry as well as
    the AppSettings global default (cross-cutting setter).
  * SetLanguage rejects empty strings.
  * SetGpuDevice normalizes empty strings to ``None`` (auto-pick).
  * SetRefreshInterval validates to [1.0, 100.0] inclusive (GUI range).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import (
    SetGpuDevice,
    SetLanguage,
    SetRefreshInterval,
    SetTempUnit,
)
from trcc.core.events import (
    GpuDeviceChanged,
    LanguageChanged,
    RefreshIntervalChanged,
    TempUnitChanged,
)

from .conftest import FakePlatform


@pytest.fixture
def app(tmp_home: Path) -> App:
    return App(platform=FakePlatform(tmp_home))


# ── SetTempUnit ───────────────────────────────────────────────────────


@pytest.mark.parametrize("unit", ["C", "F"])
def test_set_temp_unit_accepts_valid(app: App, unit: str) -> None:
    result = app.dispatch(SetTempUnit(unit=unit))
    assert result.ok is True
    assert result.unit == unit
    assert app.settings.app.temp_unit == unit


@pytest.mark.parametrize("bad", ["", "K", "celsius", "c", "f", "C ", "F\n"])
def test_set_temp_unit_rejects_invalid(app: App, bad: str) -> None:
    result = app.dispatch(SetTempUnit(unit=bad))
    assert result.ok is False
    assert "must be 'C' or 'F'" in result.message


def test_set_temp_unit_propagates_to_every_device_settings(app: App) -> None:
    """Cross-cutting setter: all existing per-device temp_unit fields update."""
    # Pre-populate two devices' settings with the default ("C")
    d1 = app.settings.for_device("0402:3922")
    d2 = app.settings.for_device("0416:5302")
    assert d1.temp_unit == "C"
    assert d2.temp_unit == "C"

    app.dispatch(SetTempUnit(unit="F"))

    assert app.settings.app.temp_unit == "F"
    assert app.settings.for_device("0402:3922").temp_unit == "F"
    assert app.settings.for_device("0416:5302").temp_unit == "F"


def test_set_temp_unit_publishes_event(app: App) -> None:
    events: list[TempUnitChanged] = []
    app.events.subscribe(TempUnitChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetTempUnit(unit="F"))

    assert len(events) == 1
    assert events[0].unit == "F"


def test_set_temp_unit_rejection_does_not_publish(app: App) -> None:
    """Invalid units must not fire an event or mutate state."""
    events: list[TempUnitChanged] = []
    app.events.subscribe(TempUnitChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetTempUnit(unit="K"))

    assert events == []
    assert app.settings.app.temp_unit == "C"   # unchanged default


# ── SetLanguage ───────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["en", "zh", "fr", "de", "ja"])
def test_set_language_accepts_valid_codes(app: App, lang: str) -> None:
    result = app.dispatch(SetLanguage(language=lang))
    assert result.ok is True
    assert result.language == lang
    assert app.settings.app.language == lang


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_set_language_rejects_empty(app: App, bad: str) -> None:
    result = app.dispatch(SetLanguage(language=bad))
    assert result.ok is False
    assert "cannot be empty" in result.message


def test_set_language_strips_whitespace(app: App) -> None:
    """Leading/trailing whitespace is stripped before persisting."""
    result = app.dispatch(SetLanguage(language="  fr  "))
    assert result.ok is True
    assert result.language == "fr"
    assert app.settings.app.language == "fr"


def test_set_language_publishes_event(app: App) -> None:
    events: list[LanguageChanged] = []
    app.events.subscribe(LanguageChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetLanguage(language="zh"))

    assert len(events) == 1
    assert events[0].language == "zh"


# ── SetGpuDevice ──────────────────────────────────────────────────────


@pytest.mark.parametrize("gpu_key,expected", [
    ("nvidia:0", "nvidia:0"),
    ("amd:0",    "amd:0"),
    ("intel:igpu", "intel:igpu"),
])
def test_set_gpu_device_stores_key(
    app: App, gpu_key: str, expected: str,
) -> None:
    result = app.dispatch(SetGpuDevice(gpu_key=gpu_key))
    assert result.ok is True
    assert result.gpu_key == expected
    assert app.settings.app.active_gpu == expected


@pytest.mark.parametrize("empty", ["", "   ", "\t\n"])
def test_set_gpu_device_empty_clears_to_auto(app: App, empty: str) -> None:
    """An empty/whitespace key clears the override (auto-pick mode)."""
    # First set a value
    app.dispatch(SetGpuDevice(gpu_key="nvidia:0"))
    assert app.settings.app.active_gpu == "nvidia:0"

    # Then clear it
    result = app.dispatch(SetGpuDevice(gpu_key=empty))
    assert result.ok is True
    assert result.gpu_key is None
    assert app.settings.app.active_gpu is None
    assert "cleared" in result.message


def test_set_gpu_device_publishes_event(app: App) -> None:
    events: list[GpuDeviceChanged] = []
    app.events.subscribe(GpuDeviceChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetGpuDevice(gpu_key="amd:0"))

    assert len(events) == 1
    assert events[0].gpu_key == "amd:0"


# ── SetRefreshInterval ────────────────────────────────────────────────


@pytest.mark.parametrize("seconds", [1.0, 2.5, 30.0, 60.0, 100.0])
def test_set_refresh_interval_accepts_in_range(
    app: App, seconds: float,
) -> None:
    result = app.dispatch(SetRefreshInterval(seconds=seconds))
    assert result.ok is True
    assert result.seconds == seconds
    assert app.settings.app.refresh_interval_s == pytest.approx(seconds)


@pytest.mark.parametrize("seconds", [-1.0, 0.0, 0.5, 0.99, 100.5, 200.0])
def test_set_refresh_interval_rejects_out_of_range(
    app: App, seconds: float,
) -> None:
    """Range is the GUI's data-refresh-rate control: [1, 100] s."""
    result = app.dispatch(SetRefreshInterval(seconds=seconds))
    assert result.ok is False
    assert "in [1.0, 100.0]" in result.message


def test_set_refresh_interval_publishes_event(app: App) -> None:
    events: list[RefreshIntervalChanged] = []
    app.events.subscribe(RefreshIntervalChanged, lambda e: events.append(e))  # type: ignore[arg-type, return-value]

    app.dispatch(SetRefreshInterval(seconds=3.5))

    assert len(events) == 1
    assert events[0].seconds == pytest.approx(3.5)


# ── Persistence: each setter triggers a config.json save ─────────────


def test_settings_persist_across_app_restart(
    app: App, tmp_home: Path,
) -> None:
    """Round-trip: change settings → rebuild App → values survive."""
    app.dispatch(SetTempUnit(unit="F"))
    app.dispatch(SetLanguage(language="zh"))
    app.dispatch(SetGpuDevice(gpu_key="nvidia:0"))
    app.dispatch(SetRefreshInterval(seconds=5.0))

    # Build a fresh App against the same paths — Settings._load reads
    # the file written by the first instance.
    app2 = App(platform=FakePlatform(tmp_home))

    assert app2.settings.app.temp_unit == "F"
    assert app2.settings.app.language == "zh"
    assert app2.settings.app.active_gpu == "nvidia:0"
    assert app2.settings.app.refresh_interval_s == pytest.approx(5.0)
