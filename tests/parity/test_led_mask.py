"""LED segment-display mask parity — sensor snapshot → on/off pattern.

Every style with a ``SegmentDisplay`` translates a sensor + phase +
time-format snapshot into a fixed-length boolean mask that drives the
``is_on`` field of the wire payload.  Both trees implement this from
the same C# source (FormLED.cs); the parity test pins them at the
mask-array level so any drift between the two ports surfaces here
before it reaches the wire.

Coverage: every legacy DISPLAYS entry (1..11) × representative sensor
scenarios.  LF13 (style 12) has no segment display, by design.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.parity._shared import style_by_legacy_id

# =========================================================================
# Sensor scenarios — canonical synthetic snapshots
# =========================================================================


# Each scenario is a dict from ``HardwareMetrics`` field name to value.
# Both trees consume *the same* dict — legacy populates HardwareMetrics
# directly; next/ wraps a SensorReading dict whose keys are mapped from
# the same legacy attribute names via LegacyMetricsView.
_SCENARIO_IDLE = {
    "cpu_temp": 35.0, "cpu_percent": 5.0,
    "gpu_temp": 40.0, "gpu_usage": 0.0,
    "mem_percent": 18.0,
    "time_hour": 9.0, "time_minute": 5.0, "time_second": 30.0,
    "date_year": 2026.0, "date_month": 5.0, "date_day": 20.0,
    "day_of_week": 2.0,        # Wednesday
}

_SCENARIO_LOAD = {
    "cpu_temp": 78.0, "cpu_percent": 95.0,
    "gpu_temp": 82.0, "gpu_usage": 100.0,
    "mem_percent": 64.0,
    "time_hour": 14.0, "time_minute": 58.0, "time_second": 12.0,
    "date_year": 2026.0, "date_month": 5.0, "date_day": 20.0,
    "day_of_week": 2.0,
}

_SCENARIO_CLOCK_FOCUS = {
    "cpu_temp": 50.0, "cpu_percent": 30.0,
    "gpu_temp": 55.0, "gpu_usage": 12.0,
    "mem_percent": 25.0,
    "time_hour": 23.0, "time_minute": 47.0, "time_second": 11.0,
    "date_year": 2026.0, "date_month": 12.0, "date_day": 31.0,
    "day_of_week": 4.0,        # Friday
}

_SCENARIOS = {
    "idle": _SCENARIO_IDLE,
    "load": _SCENARIO_LOAD,
    "clock": _SCENARIO_CLOCK_FOCUS,
}


# =========================================================================
# Metric builders — produce legacy + next/ shapes from one dict
# =========================================================================


def _legacy_metrics(scenario: dict[str, float]) -> Any:
    """Build a legacy ``HardwareMetrics`` populated from the scenario dict."""
    from trcc.legacy.core.models.sensor import HardwareMetrics

    metrics = HardwareMetrics()
    for field_name, value in scenario.items():
        setattr(metrics, field_name, value)
    # The mask code reads from typed fields, not the readings dict —
    # but populate _populated so any presence-check code path lights up.
    metrics._populated.update(scenario)
    return metrics


def _next_metrics(scenario: dict[str, float]) -> Any:
    """Build a next/ ``LegacyMetricsView`` over a SensorReading dict
    that resolves the same attribute names to the same values."""
    from trcc.legacy.core.models import SensorReading
    from trcc.legacy.services.led_segment import LegacyMetricsView

    readings: dict[str, SensorReading] = {}
    # LegacyMetricsView translates legacy_attr → next sensor_id via a
    # static map; for every translatable field, mirror the value.
    for legacy_attr, next_sensor_id in LegacyMetricsView._LEGACY_TO_NEXT.items():
        if legacy_attr in scenario:
            readings[next_sensor_id] = SensorReading(
                sensor_id=next_sensor_id, category="parity",
                value=scenario[legacy_attr], unit="", label="",
            )
    return LegacyMetricsView(readings)


# =========================================================================
# Mask pipelines per tree
# =========================================================================


def _legacy_mask(
    *, style_id: int, scenario: dict[str, float], phase: int,
    temp_unit: str, is_24h: bool, week_sunday: bool,
) -> list[bool]:
    from trcc.legacy.core.led_segment import compute_mask as legacy_compute

    return legacy_compute(
        style_id=style_id,
        metrics=_legacy_metrics(scenario),
        phase=phase,
        temp_unit=temp_unit,
        is_24h=is_24h,
        week_sunday=week_sunday,
    )


def _next_mask(
    *, style_id: int, scenario: dict[str, float], phase: int,
    temp_unit: str, is_24h: bool, week_sunday: bool,
) -> list[bool]:
    from trcc.legacy.services.led_segment import compute_mask as next_compute

    style = style_by_legacy_id()[style_id]
    return next_compute(
        style=style,
        metrics=_next_metrics(scenario),
        phase=phase,
        temp_unit=temp_unit,
        is_24h=is_24h,
        week_sunday=week_sunday,
    )


# =========================================================================
# Coverage dimensions
# =========================================================================


def _segment_display_style_ids() -> list[int]:
    """Style ids that have a SegmentDisplay registered in legacy.

    LF13 (12) is a pure-RGB strip with no segment readout.
    """
    from trcc.legacy.core.led_segment import DISPLAYS

    return sorted(DISPLAYS)


_SCENARIO_IDS = sorted(_SCENARIOS)


def _phase_range(style_id: int) -> list[int]:
    """Phases to exercise for *style_id*.

    Some styles cycle through multiple display phases (CPU temp →
    GPU temp → time, etc).  Iterate every documented phase plus phase
    0 so single-phase styles aren't skipped.
    """
    from trcc.legacy.core.led_segment import DISPLAYS

    display = DISPLAYS.get(style_id)
    if display is None:
        return [0]
    count = max(1, int(getattr(display, "phase_count", 0) or 1))
    return list(range(count))


# =========================================================================
# Matrix tests
# =========================================================================


@pytest.mark.parametrize("style_id", _segment_display_style_ids())
@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
@pytest.mark.parametrize("temp_unit", ["C", "F"])
def test_mask_matches_for_scenario(
    style_id: int, scenario_id: str, temp_unit: str,
) -> None:
    """Phase 0, both temp units — the canonical parity case."""
    scenario = _SCENARIOS[scenario_id]
    legacy = _legacy_mask(
        style_id=style_id, scenario=scenario, phase=0,
        temp_unit=temp_unit, is_24h=True, week_sunday=False,
    )
    next_ = _next_mask(
        style_id=style_id, scenario=scenario, phase=0,
        temp_unit=temp_unit, is_24h=True, week_sunday=False,
    )
    assert legacy == next_, (
        f"style {style_id} / scenario {scenario_id!r} / temp_unit {temp_unit}: "
        f"legacy {sum(legacy)}/{len(legacy)} LEDs on; "
        f"next/ {sum(next_)}/{len(next_)} LEDs on"
    )


@pytest.mark.parametrize("style_id", _segment_display_style_ids())
def test_mask_matches_across_every_phase(style_id: int) -> None:
    """Every documented phase under the load scenario.

    Multi-phase styles (AX120, PA120, LC1, LF8, LF12) cycle through
    different metrics on the same physical LEDs — a phase drift
    between trees would surface here as the strongest diff (different
    digits lit).
    """
    scenario = _SCENARIOS["load"]
    for phase in _phase_range(style_id):
        legacy = _legacy_mask(
            style_id=style_id, scenario=scenario, phase=phase,
            temp_unit="C", is_24h=True, week_sunday=False,
        )
        next_ = _next_mask(
            style_id=style_id, scenario=scenario, phase=phase,
            temp_unit="C", is_24h=True, week_sunday=False,
        )
        assert legacy == next_, (
            f"style {style_id} phase {phase}: "
            f"legacy on={sum(legacy)} vs next/ on={sum(next_)}"
        )


@pytest.mark.parametrize("style_id", _segment_display_style_ids())
@pytest.mark.parametrize("is_24h", [True, False])
def test_mask_matches_with_clock_format_variants(
    style_id: int, is_24h: bool,
) -> None:
    """Clock-display styles (LC2, partial LF12) format the time in 12h
    vs 24h.  Both trees should react identically to the format flag."""
    scenario = _SCENARIOS["clock"]
    legacy = _legacy_mask(
        style_id=style_id, scenario=scenario, phase=0,
        temp_unit="C", is_24h=is_24h, week_sunday=False,
    )
    next_ = _next_mask(
        style_id=style_id, scenario=scenario, phase=0,
        temp_unit="C", is_24h=is_24h, week_sunday=False,
    )
    assert legacy == next_, (
        f"style {style_id} is_24h={is_24h}: "
        f"legacy on={sum(legacy)} vs next/ on={sum(next_)}"
    )


# =========================================================================
# Coverage sanity
# =========================================================================


def test_every_legacy_display_has_a_parity_row() -> None:
    """If legacy adds a new SegmentDisplay, the matrix automatically
    extends.  This pin asserts we're not silently dropping coverage."""
    from trcc.legacy.core.led_segment import DISPLAYS

    ids = _segment_display_style_ids()
    assert set(ids) == set(DISPLAYS)
    assert len(ids) == 11      # AX120 … LF15; LF13 has no display
