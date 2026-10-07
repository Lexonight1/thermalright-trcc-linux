"""Sensor display helpers — toolkit-free, shared by the sensor UIs.

Two pieces both the sensor picker and the system-info dashboard need, lifted out
of the widgets so they're shared + unit-testable without Qt:

* ``format_sensor_value`` — the value→string unit ladder (°C/°F symbol swap, %,
  RPM, W, V, MHz, MB rates).  Identical in both views except the temp symbol, so
  one function with a ``temp_unit`` default unifies them.
* ``group_sensors`` — adapt discover()'s :class:`SensorReading` list into
  :class:`SensorInfo` (source inferred from the id prefix), grouped + ordered by
  hardware source, ready for the picker to render as headers + rows.
* ``apply_live_values`` — merge a ``SensorsUpdated`` broadcast onto a sensor
  CATALOG, so a view can ride the bus instead of polling ``ReadSensors`` on a
  private timer.

The third exists because the two halves of a sensor row have different
lifetimes.  Identity — id, label, category, unit — comes from ``discover()``
and changes when hardware does; VALUES change every tick.  The broadcast
carries values only (``SensorsUpdated.readings`` is ``{sensor_id: value}``), so
a view that renders a unit or a category needs both, and the merge is the same
in every view that does.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace

from ...core.models import SensorInfo, SensorReading, TempUnit
from ...services.metrics_personalize import personalize_unit

log = logging.getLogger(__name__)

# Hardware-source → display header (the sensor-id prefix IS the source).
_SOURCE_LABELS = {
    "cpu": "CPU", "gpu": "GPU", "fan": "Fans", "memory": "Memory",
    "mem": "Memory", "disk": "Disk", "net": "Network",
    "board": "Board", "volt": "Voltages",
}
# Clock "sensors" aren't hardware — never shown in the picker.
_CLOCK_SOURCES = frozenset({"time", "date"})
# Known render order; unknown hardware groups follow, alphabetically.
_SOURCE_ORDER = ("cpu", "gpu", "fan", "memory", "mem", "disk", "net")


def format_sensor_value(value: float, unit: str, temp_unit: int = 0) -> str:
    """Render a sensor ``value`` with its ``unit``.

    The value is ALWAYS already converted upstream (``ReadSensors`` and the
    metrics broadcast both personalise before publishing) — only the symbol is
    decided here, and two callers spell the same fact differently:

    * The dashboard binds a row once and stores ``"°C"`` in the saved layout
      forever, so it passes ``temp_unit`` (0=°C, 1=°F) alongside.
    * The picker renders whatever the reading declares, and a personalised
      reading declares ``"°F"`` outright.

    Both are honoured, so a °F reading keeps its degree sign instead of
    falling through to the unit-less default and rendering "122.0".
    """
    log.debug("format_sensor_value: %.2f unit=%s temp_unit=%d", value, unit, temp_unit)
    if unit in ("°C", "°F"):
        symbol = "°F" if (unit == "°F" or temp_unit == 1) else "°C"
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


def apply_live_values(
    catalog: Sequence[SensorReading],
    values: Mapping[str, float],
    *,
    temp_unit: TempUnit = "C",
) -> list[SensorReading]:
    """Return *catalog* carrying the broadcast's ``values``.

    The bus half of what ``ReadSensors`` does in one dispatch: a view holds the
    catalog it last read and refreshes only the numbers, on the cadence the
    user configured (``refresh_interval_s``) rather than on a timer of its own.

    **A sensor missing from ``values`` is DROPPED, not zeroed** — the same
    semantics ``ReadSensors`` applies to the same dict, and the reason the user
    disabling HDD makes ``disk:*`` rows disappear rather than read 0.  A row
    that cannot be read is absent; ``0`` is a reading.

    The unit comes from :func:`personalize_unit`, so a catalog cached under one
    temperature preference renders correctly under the other without being
    re-fetched.
    """
    out = [
        replace(
            reading,
            value=values[reading.sensor_id],
            unit=personalize_unit(reading.sensor_id, reading.unit,
                                  temp_unit=temp_unit),
        )
        for reading in catalog if reading.sensor_id in values
    ]
    log.debug("apply_live_values: %d catalog × %d values → %d reading(s), "
              "temp_unit=%s", len(catalog), len(values), len(out), temp_unit)
    return out
