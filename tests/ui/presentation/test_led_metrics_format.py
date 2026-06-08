"""LED metric formatter — pure-Python tests (NO Qt, NO QApplication).

These lock the byte-exact display strings the LED panels used to compute inline
in ``UCLedControl.update_*`` (derive-used-GB-from-percent, MT/s, °C/°F glyph
swap, 12h / week-start clock).  Extracting them into
:mod:`trcc.ui.presentation.led_metrics_format` makes that maths testable as
plain data — this file imports no Qt.
"""
from __future__ import annotations

import datetime as dt

from trcc.core.models import HardwareMetrics
from trcc.ui.presentation.led_metrics_format import (
    clock_fields,
    format_disk_labels,
    format_memory_labels,
    format_sensor_gauges,
)

_CELSIUS = "°C"
_FAHRENHEIT = "°F"
_GLYPH_C = "℃"
_GLYPH_F = "℉"


# ── Sensor gauges ────────────────────────────────────────────────────────


def test_sensor_gauges_value_text_unit() -> None:
    m = HardwareMetrics(
        cpu_temp=60.0, cpu_freq=4200.0, cpu_percent=37.0,
        gpu_temp=55.0, gpu_clock=1800.0, gpu_usage=12.0,
    )
    g = format_sensor_gauges(m, _CELSIUS)
    assert g["cpu_temp"] == (60.0, "60", _CELSIUS)
    assert g["cpu_clock"] == (4200.0, "4200", "MHz")
    assert g["cpu_usage"] == (37.0, "37", "%")
    assert g["gpu_temp"] == (55.0, "55", _CELSIUS)
    assert g["gpu_clock"] == (1800.0, "1800", "MHz")
    assert g["gpu_usage"] == (12.0, "12", "%")


def test_sensor_gauges_temp_unit_passthrough() -> None:
    m = HardwareMetrics(cpu_temp=70.0, gpu_temp=65.0)
    g = format_sensor_gauges(m, _FAHRENHEIT)
    assert g["cpu_temp"][2] == _FAHRENHEIT
    assert g["gpu_temp"][2] == _FAHRENHEIT


# ── Memory labels (LC1) ──────────────────────────────────────────────────


def test_memory_temp_nc_when_zero_else_glyph() -> None:
    assert format_memory_labels(HardwareMetrics(mem_temp=0), _CELSIUS, 2)["mem_temp"] == "NC"
    assert format_memory_labels(HardwareMetrics(mem_temp=45), _CELSIUS, 2)["mem_temp"] == f"45{_GLYPH_C}"
    assert format_memory_labels(HardwareMetrics(mem_temp=45), _FAHRENHEIT, 2)["mem_temp"] == f"45{_GLYPH_F}"


def test_memory_clock_and_mts_use_ratio() -> None:
    out = format_memory_labels(HardwareMetrics(mem_clock=3200.0), _CELSIUS, 2)
    assert out["mem_clock"] == "3200MHz"
    assert out["mem_mts"] == "6400MT/S"          # 3200 × 2
    assert out["mem_ratio"] == "2X"


def test_memory_clock_nc_when_zero() -> None:
    out = format_memory_labels(HardwareMetrics(mem_clock=0), _CELSIUS, 4)
    assert out["mem_clock"] == "NC"
    assert out["mem_mts"] == "NC"
    assert out["mem_ratio"] == "4X"


def test_memory_used_gb_derived_from_percent_and_available() -> None:
    # percent=50, available=8000 → total=16000, used=8000 → 8.0 GB
    out = format_memory_labels(
        HardwareMetrics(mem_percent=50.0, mem_available=8000.0), _CELSIUS, 2)
    assert out["mem_used"] == "8.0GB"


def test_memory_used_nc_when_no_percent_or_available() -> None:
    assert format_memory_labels(HardwareMetrics(), _CELSIUS, 2)["mem_used"] == "NC"


# ── Disk labels (LF11) ───────────────────────────────────────────────────


def test_disk_labels_format() -> None:
    m = HardwareMetrics(disk_temp=42.0, disk_activity=18.0,
                        disk_read=540.0, disk_write=130.0)
    out = format_disk_labels(m, _CELSIUS)
    assert out["lf11_disk_temp"] == f"42{_GLYPH_C}"
    assert out["lf11_disk_usage"] == "18%"
    assert out["lf11_disk_read"] == "540MB/S"
    assert out["lf11_disk_write"] == "130MB/S"


def test_disk_temp_nc_when_zero() -> None:
    assert format_disk_labels(HardwareMetrics(disk_temp=0), _CELSIUS)["lf11_disk_temp"] == "NC"


# ── Clock (LC2) ──────────────────────────────────────────────────────────


def test_clock_24h_passthrough() -> None:
    now = dt.datetime(2026, 6, 8, 15, 30)        # Monday → weekday()==0
    assert clock_fields(now, is_24h=True, is_sunday=False) == (6, 8, 15, 30, 0)


def test_clock_12h_folds_afternoon_hour() -> None:
    now = dt.datetime(2026, 6, 8, 15, 30)
    assert clock_fields(now, is_24h=False, is_sunday=False) == (6, 8, 3, 30, 0)


def test_clock_sunday_start_rotates_weekday() -> None:
    now = dt.datetime(2026, 6, 8, 9, 0)          # Monday weekday()==0
    assert clock_fields(now, is_24h=True, is_sunday=True) == (6, 8, 9, 0, 1)
