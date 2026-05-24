"""Apply user preferences to raw sensor readings — the single conversion site.

Sensor sources deliver canonical readings (always °C for temperatures,
all available keys present).  User-facing transformations — Celsius →
Fahrenheit conversion when the user picks °F, and disk-key filtering
when the user disables HDD — belong at the metrics broadcast boundary,
not scattered across every renderer.  This module is that boundary.

Two callers in next/:

  * :func:`trcc.services.metrics_loop.MetricsLoop._publish_once` —
    the periodic poll that publishes ``SensorsUpdated`` to the event
    bus.  Subscribers (LCD overlay, system-sensors panel, LED engine,
    GUI activity sidebar) read the personalized dict directly from
    the event payload.
  * :func:`trcc.core.commands.ReadSensors.execute` — the one-shot
    dispatch used by CLI / API / tests and by the GUI's
    view-switch immediate-populate path.  Returns the same shape the
    broadcast carries so periodic + one-shot agree.

Pure function — no I/O, no state, no settings dependency.  The caller
supplies the prefs; this module just applies them.  Matches legacy's
``PollingMetricsLoop._poll_metrics`` lines 116-145 which did exactly
this work at the same architectural location.
"""
from __future__ import annotations

from ..core.models import TempUnit, celsius_to_fahrenheit


def personalize_readings(
    raw: dict[str, float],
    *,
    temp_unit: TempUnit = "C",
    hdd_enabled: bool = True,
) -> dict[str, float]:
    """Return a new dict with user prefs applied to ``raw``.

    Transformations applied (in this order):

      1. **HDD filter.** When ``hdd_enabled is False``, every key
         starting with ``disk:`` is DROPPED from the output (not
         zeroed).  Matches legacy's ``_populated.discard`` semantics
         — consumers that check "is this sensor present?" see it as
         missing and render ``--`` instead of ``0``.

      2. **Temperature conversion.** When ``temp_unit == "F"``, every
         key ending in ``:temp`` has its value converted via
         :func:`celsius_to_fahrenheit`.  Keys are unchanged (the
         sensor id ``cpu:temp`` is unit-agnostic; only the value
         changes meaning).  Non-temperature keys pass through.

    Returns a NEW dict; ``raw`` is not mutated.  Key insertion order
    is preserved for keys that survive filtering.

    >>> personalize_readings({"cpu:temp": 50.0, "cpu:usage": 25.0},
    ...                      temp_unit="F")
    {'cpu:temp': 122.0, 'cpu:usage': 25.0}

    >>> personalize_readings({"cpu:temp": 50.0, "disk:read": 1.5},
    ...                      hdd_enabled=False)
    {'cpu:temp': 50.0}

    >>> personalize_readings({"cpu:temp": 0.0}, temp_unit="F")
    {'cpu:temp': 32.0}
    """
    out: dict[str, float] = {}
    for key, value in raw.items():
        if not hdd_enabled and key.startswith("disk:"):
            continue
        if temp_unit == "F" and key.endswith(":temp"):
            value = celsius_to_fahrenheit(value)
        out[key] = value
    return out
