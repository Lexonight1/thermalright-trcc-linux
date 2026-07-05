"""LED metric → display formatting — toolkit-free, shared by every presentation.

Turns a :class:`trcc.core.models.HardwareMetrics` snapshot into plain display
data (numbers + strings) for the LED control panels.  No Qt: a View calls these
and pokes its own widgets/labels with the result, so the trickiest domain maths
— derive-used-GB-from-percent, MT/s, °C/°F symbol swap, 12h / week-start clock
— is computed once, here, and is unit-testable without a QApplication.

Lifted verbatim from ``UCLedControl.update_*`` so the formatting is byte-identical.
"""
from __future__ import annotations

import datetime as _dt
import logging

from ...core.models import HardwareMetrics

log = logging.getLogger(__name__)

# Mid-string temperature glyphs (NOT the "°C"/"°F" unit *symbols* the gauges
# use): the LC1/LF11 labels render ℃ / ℉ inline.
_DEGREE_C = "℃"
_DEGREE_F = "℉"
_UNIT_CELSIUS = "°C"   # the temp_unit symbol the panel stores/passes


def _temp_label(value: float, temp_unit: str) -> str:
    """``"NC"`` when unread (0), else ``"<v>℃"`` / ``"<v>℉"`` per unit."""
    if value == 0:
        return "NC"
    glyph = _DEGREE_C if temp_unit == _UNIT_CELSIUS else _DEGREE_F
    return f"{value:.0f}{glyph}"


def format_sensor_gauges(
    metrics: HardwareMetrics, temp_unit: str,
) -> dict[str, tuple[float, str, str]]:
    """CPU/GPU temp·clock·usage → ``{key: (value, text, unit)}`` for the 6 gauges.

    ``temp_unit`` is the symbol the panel holds ("°C"/"°F") — passed straight
    through as the gauge's unit suffix for temp readings.
    """
    gauges = {
        "cpu_temp": (metrics.cpu_temp, f"{metrics.cpu_temp:.0f}", temp_unit),
        "cpu_clock": (metrics.cpu_freq, f"{metrics.cpu_freq:.0f}", "MHz"),
        "cpu_usage": (metrics.cpu_percent, f"{metrics.cpu_percent:.0f}", "%"),
        "gpu_temp": (metrics.gpu_temp, f"{metrics.gpu_temp:.0f}", temp_unit),
        "gpu_clock": (metrics.gpu_clock, f"{metrics.gpu_clock:.0f}", "MHz"),
        "gpu_usage": (metrics.gpu_usage, f"{metrics.gpu_usage:.0f}", "%"),
    }
    log.debug("format_sensor_gauges: temp_unit=%s → %s", temp_unit,
              {k: v[1] for k, v in gauges.items()})
    return gauges


def format_memory_labels(
    metrics: HardwareMetrics, temp_unit: str, memory_ratio: int,
) -> dict[str, str]:
    """LC1 (style 4) memory info labels → ``{key: text}``.

    ``mem_used`` derives GB from the percent + available pair (C# showed
    ``MemUsed/1000`` GB); ``mem_mts`` is clock × DDR multiplier.
    """
    out: dict[str, str] = {"mem_temp": _temp_label(metrics.mem_temp, temp_unit)}

    mhz = metrics.mem_clock
    out["mem_clock"] = f"{mhz:.0f}MHz" if mhz else "NC"
    effective = mhz * memory_ratio
    out["mem_mts"] = f"{effective:.0f}MT/S" if mhz else "NC"

    if metrics.mem_percent > 0 and metrics.mem_available > 0:
        total = metrics.mem_available / (1.0 - metrics.mem_percent / 100.0)
        used_gb = (total - metrics.mem_available) / 1000.0
        out["mem_used"] = f"{used_gb:.1f}GB"
    else:
        out["mem_used"] = "NC"

    out["mem_ratio"] = f"{memory_ratio}X"
    log.debug("format_memory_labels: temp_unit=%s ratio=%d → %s",
              temp_unit, memory_ratio, out)
    return out


def format_disk_labels(
    metrics: HardwareMetrics, temp_unit: str,
) -> dict[str, str]:
    """LF11 (style 10) disk info labels → ``{key: text}``."""
    labels = {
        "lf11_disk_temp": _temp_label(metrics.disk_temp, temp_unit),
        "lf11_disk_usage": f"{metrics.disk_activity:.0f}%",
        "lf11_disk_read": f"{metrics.disk_read:.0f}MB/S",
        "lf11_disk_write": f"{metrics.disk_write:.0f}MB/S",
    }
    log.debug("format_disk_labels: temp_unit=%s → %s", temp_unit, labels)
    return labels


def clock_fields(
    now: _dt.datetime, is_24h: bool, is_sunday: bool,
) -> tuple[int, int, int, int, int]:
    """LC2 (style 9) clock → ``(month, day, hour, minute, day_of_week)``.

    ``now`` is passed in (not read from the wall clock) so this is
    deterministic and testable.  12h folds hour>12; Sunday-start rotates the
    weekday index by one.
    """
    hour = now.hour
    if not is_24h and hour > 12:
        hour -= 12
    dow = now.weekday()
    if is_sunday:
        dow = (dow + 1) % 7
    fields = (now.month, now.day, hour, now.minute, dow)
    log.debug("clock_fields: %s 24h=%s sunday=%s → %s",
              now.isoformat(timespec="minutes"), is_24h, is_sunday, fields)
    return fields
