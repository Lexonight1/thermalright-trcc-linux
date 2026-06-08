"""sensor_display — pure-Python tests (NO Qt, NO QApplication).

Locks the value-format ladder (shared by the picker + the system-info panel)
and the discover()→grouped-SensorInfo adaptation.
"""
from __future__ import annotations

from trcc.core.models import SensorReading
from trcc.ui.presentation.sensor_display import format_sensor_value, group_sensors


def _reading(sensor_id: str, *, unit: str = "°C", label: str = "",
             category: str = "x", value: float = 0.0) -> SensorReading:
    return SensorReading(sensor_id=sensor_id, category=category,
                         value=value, unit=unit, label=label)


# ── format_sensor_value ──────────────────────────────────────────────────


def test_format_celsius_swaps_symbol_on_temp_unit() -> None:
    assert format_sensor_value(60.4, "°C", temp_unit=0) == "60°C"
    assert format_sensor_value(60.4, "°C", temp_unit=1) == "60°F"
    assert format_sensor_value(60.4, "°C") == "60°C"          # default = celsius


def test_format_integer_unit_ladder() -> None:
    assert format_sensor_value(37.6, "%") == "38%"
    assert format_sensor_value(1200.0, "RPM") == "1200RPM"
    assert format_sensor_value(65.2, "W") == "65W"
    assert format_sensor_value(4200.0, "MHz") == "4200MHz"


def test_format_volts_two_decimals() -> None:
    assert format_sensor_value(1.2, "V") == "1.20V"


def test_format_rate_units_one_decimal() -> None:
    assert format_sensor_value(12.34, "MB") == "12.3MB"
    assert format_sensor_value(5.0, "MB/s") == "5.0MB/s"
    assert format_sensor_value(3.21, "KB/s") == "3.2KB/s"


def test_format_unknown_unit_falls_to_one_decimal() -> None:
    assert format_sensor_value(7.25, "??") == "7.2"


# ── group_sensors ────────────────────────────────────────────────────────


def test_group_adapts_reading_to_sensorinfo() -> None:
    [(header, sensors)] = group_sensors([
        _reading("cpu:temp", label="CPU Temp", unit="°C", category="temp"),
    ])
    assert header == "CPU"
    s = sensors[0]
    assert (s.id, s.name, s.source, s.unit, s.category) == (
        "cpu:temp", "CPU Temp", "cpu", "°C", "temp")


def test_group_source_inference_and_name_fallback() -> None:
    [(header, sensors)] = group_sensors([_reading("hwmon:coretemp:t1")])
    assert header == "HWMON"                 # unknown source → upper
    assert sensors[0].source == "hwmon"
    assert sensors[0].name == "hwmon:coretemp:t1"   # empty label → id

    [(header2, _)] = group_sensors([_reading("bare")])
    assert header2 == "SYSTEM"               # no prefix → "system"


def test_group_orders_known_sources_first() -> None:
    groups = group_sensors([
        _reading("fan:1", label="F"),
        _reading("gpu:temp", label="G"),
        _reading("cpu:temp", label="C"),
    ])
    assert [h for h, _ in groups] == ["CPU", "GPU", "Fans"]


def test_group_drops_clock_sources() -> None:
    groups = group_sensors([
        _reading("time:now", label="Time"),
        _reading("date:today", label="Date"),
        _reading("cpu:temp", label="C"),
    ])
    assert [h for h, _ in groups] == ["CPU"]
