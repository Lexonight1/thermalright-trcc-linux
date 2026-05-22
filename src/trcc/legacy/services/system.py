"""System monitoring service — sensors, panels, and formatting.

Business logic for sensor discovery, panel breakdowns (disk / network / fan),
and metric formatting.  Pure Python, no Qt dependencies.

Aggregate metrics composition lives on :class:`Platform` — every caller
reads ``trcc.os.metrics`` directly.  The HDD-disable toggle is applied at
the broadcast chokepoint (``PollingMetricsLoop._poll_metrics``).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..core.models import format_metric as _format_metric

if TYPE_CHECKING:
    from ..core.models import SensorInfo
    from ..core.ports import Platform, SensorEnumerator

log = logging.getLogger(__name__)


class SystemService:
    """Sensor enumeration + panel/format helpers (no aggregate metrics).

    Owns the running :class:`SensorEnumerator` (background polling thread)
    and provides panel breakdowns (``disk_stats``, ``network_stats``,
    ``fan_speeds``) plus metric formatting.  Aggregate metrics composition
    lives on :class:`Platform` — read ``trcc.os.metrics`` for that.
    """

    def __init__(self, platform: Platform,
                 settings: Any = None) -> None:
        self._platform: Platform = platform
        self._enumerator: SensorEnumerator = platform.sensors
        self._defaults: dict[str, str] | None = None
        if settings is None:
            from ..conf import settings as _global
            settings = _global
        self._settings = settings
        self._enumerator.discover()

    # ── Polling lifecycle ─────────────────────────────────────────────

    def set_poll_interval(self, seconds: float) -> None:
        """Set background sensor poll interval (user's data refresh setting)."""
        self._enumerator.set_poll_interval(seconds)

    def start_polling(self) -> None:
        """Start background sensor polling thread."""
        self._enumerator.start_polling()

    def stop_polling(self) -> None:
        """Stop background sensor polling thread."""
        self._enumerator.stop_polling()

    @property
    def sensors(self) -> list[SensorInfo]:
        """All discovered sensors."""
        return self._enumerator.get_sensors()

    @property
    def enumerator(self):
        """Direct access to SensorEnumerator (for GUI sensor picker)."""
        return self._enumerator

    # ── Readings ──────────────────────────────────────────────────────

    def read_all(self) -> dict[str, float]:
        """Read current values for all discovered sensors."""
        return self._enumerator.read_all()

    def read_one(self, sensor_id: str) -> float | None:
        """Read a single sensor by ID."""
        return self._enumerator.read_one(sensor_id)

    # ── Legacy key mapping ────────────────────────────────────────────

    def _ensure_defaults(self) -> dict[str, str]:
        """Get legacy metric key → sensor_id mapping (cached)."""
        if self._defaults is None:
            self._defaults = self._enumerator.map_defaults() or {}
        defaults: dict[str, str] = self._defaults  # type: ignore[assignment]
        return defaults

    def _read_metric(self, legacy_key: str) -> float | None:
        """Read a single metric by legacy key via the enumerator."""
        defaults = self._ensure_defaults()
        if (sensor_id := defaults.get(legacy_key)):
            return self._enumerator.read_one(sensor_id)
        return None

    # ── Aggregate views (panel / dashboard) ───────────────────────────

    @property
    def disk_stats(self) -> dict[str, float]:
        readings = self.read_all()
        return {
            legacy: readings[sensor]
            for legacy, sensor in (
                ('disk_read', 'computed:disk_read'),
                ('disk_write', 'computed:disk_write'),
                ('disk_activity', 'computed:disk_activity'),
            ) if sensor in readings
        }

    @property
    def network_stats(self) -> dict[str, float]:
        readings = self.read_all()
        return {
            legacy: readings[sensor]
            for legacy, sensor in (
                ('net_up', 'computed:net_up'),
                ('net_down', 'computed:net_down'),
                ('net_total_up', 'computed:net_total_up'),
                ('net_total_down', 'computed:net_total_down'),
            ) if sensor in readings
        }

    @property
    def fan_speeds(self) -> dict[str, float]:
        defaults = self._ensure_defaults()
        readings = self.read_all()
        return {
            fan_key: readings[defaults[fan_key]]
            for fan_key in ('fan_cpu', 'fan_gpu', 'fan_ssd', 'fan_sys2')
            if (sid := defaults.get(fan_key)) and sid in readings
        }

    # ── Formatting ────────────────────────────────────────────────────

    @staticmethod
    def format_metric(metric: str, value: float, time_format: int = 0,
                      date_format: int = 0, temp_unit: int = 0,
                      lang: str | None = None) -> str:
        """Format a metric value for display. Delegates to core.models.

        ``lang`` propagates through for weekday localization (issue #141).
        """
        return _format_metric(metric, value, time_format=time_format,
                              date_format=date_format, temp_unit=temp_unit,
                              lang=lang)

# ── Module-level convenience API ─────────────────────────────────────────────
# Explicit singleton — composition roots call set_instance() at startup.

_instance: SystemService | None = None


def set_instance(svc: SystemService) -> None:
    """Set the module-level SystemService singleton.

    Called by composition roots (GUI, CLI, API) after building the service
    with injected dependencies.  Replaces the old ``_get_instance()`` which
    violated hexagonal architecture by importing from adapters.
    """
    global _instance
    _instance = svc
    svc.start_polling()


def get_instance() -> SystemService:
    """Return the module-level SystemService singleton.

    Raises RuntimeError if ``set_instance()`` has not been called yet.
    """
    if _instance is None:
        raise RuntimeError(
            "SystemService not initialized. "
            "Call set_instance() from a composition root.")
    return _instance


def set_poll_interval(seconds: float) -> None:
    """Set background sensor poll interval (user's data refresh setting)."""
    get_instance().set_poll_interval(seconds)


def format_metric(key: str, value: float, **kwargs: Any) -> str:
    """Format a single metric value for display."""
    return _format_metric(key, value, **kwargs)

