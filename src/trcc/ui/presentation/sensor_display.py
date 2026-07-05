"""Sensor display helpers — toolkit-free, shared by the sensor UIs.

Two pieces both the sensor picker and the system-info dashboard need, lifted out
of the widgets so they're shared + unit-testable without Qt:

* ``format_sensor_value`` — the value→string unit ladder (°C/°F symbol swap, %,
  RPM, W, V, MHz, MB rates).  Identical in both views except the temp symbol, so
  one function with a ``temp_unit`` default unifies them.
* ``group_sensors`` — adapt discover()'s :class:`SensorReading` list into
  :class:`SensorInfo` (source inferred from the id prefix), grouped + ordered by
  hardware source, ready for the picker to render as headers + rows.
"""
from __future__ import annotations

import logging

from ...core.models import SensorInfo, SensorReading

log = logging.getLogger(__name__)

# Hardware-source → display header (the sensor-id prefix IS the source).
_SOURCE_LABELS = {
    "cpu": "CPU", "gpu": "GPU", "fan": "Fans", "memory": "Memory",
    "mem": "Memory", "disk": "Disk", "net": "Network",
}
# Clock "sensors" aren't hardware — never shown in the picker.
_CLOCK_SOURCES = frozenset({"time", "date"})
# Known render order; unknown hardware groups follow, alphabetically.
_SOURCE_ORDER = ("cpu", "gpu", "fan", "memory", "mem", "disk", "net")


def format_sensor_value(value: float, unit: str, temp_unit: int = 0) -> str:
    """Render a sensor ``value`` with its ``unit``.

    ``temp_unit`` (0=°C, 1=°F) only swaps the SYMBOL for ``°C`` bindings — the
    value is already converted upstream (metrics broadcast).  Default 0 matches
    the picker, which always shows °C.
    """
    log.debug("format_sensor_value: %.2f unit=%s temp_unit=%d", value, unit, temp_unit)
    if unit == "°C":
        symbol = "°F" if temp_unit == 1 else "°C"
        return f"{value:.0f}{symbol}"
    if unit in ("%", "RPM", "W"):
        return f"{value:.0f}{unit}"
    if unit == "V":
        return f"{value:.2f}V"
    if unit == "MHz":
        return f"{value:.0f}MHz"
    if unit in ("MB", "MB/s", "KB/s"):
        return f"{value:.1f}{unit}"
    return f"{value:.1f}"


def group_sensors(
    readings: list[SensorReading],
) -> list[tuple[str, list[SensorInfo]]]:
    """Adapt + group discovered sensors into ordered ``(header, sensors)`` groups.

    Each ``SensorReading`` becomes a ``SensorInfo`` whose ``source`` is the id
    prefix (``"hwmon:coretemp:temp1"`` → ``"hwmon"``; bare ids → ``"system"``).
    Groups render in :data:`_SOURCE_ORDER` first, then any other hardware groups
    alphabetically; clock sources are dropped.
    """
    infos: list[SensorInfo] = []
    for r in readings:
        source = r.sensor_id.split(":", 1)[0] if ":" in r.sensor_id else "system"
        infos.append(SensorInfo(
            id=r.sensor_id,
            name=r.label or r.sensor_id,
            category=r.category,
            unit=r.unit,
            source=source,
        ))

    groups: dict[str, list[SensorInfo]] = {}
    for s in infos:
        groups.setdefault(s.source, []).append(s)

    ordered = [s for s in _SOURCE_ORDER if s in groups]
    ordered += [s for s in sorted(groups)
                if s not in _SOURCE_ORDER and s not in _CLOCK_SOURCES]

    result = [(_SOURCE_LABELS.get(src, src.upper()), groups[src]) for src in ordered]
    log.info("group_sensors: %d readings → %d groups: %s", len(readings), len(result),
             ", ".join(f"{h}({len(g)})" for h, g in result))
    return result
