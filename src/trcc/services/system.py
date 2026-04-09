"""System monitoring service — sensors, panels, and metrics.

Business logic for hardware monitoring: sensor discovery, metric reading,
formatting, and dashboard panel configuration. Pure Python, no Qt dependencies.

Absorbs system_info.py (SystemInfo) logic. Uses system_sensors.py
(SensorEnumerator) and system_config.py (SysInfoConfig) as infrastructure.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from ..core.models import HardwareMetrics
from ..core.models import format_metric as _format_metric

if TYPE_CHECKING:
    from ..core.models import SensorInfo
    from ..core.ports import SensorEnumerator

log = logging.getLogger(__name__)


def _read_sysfs(path: str) -> Optional[str]:
    """Safely read a sysfs/proc file, return stripped content or None."""
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


# Sentinel: distinguishes "not yet queried" from "queried, returned None".
_SENTINEL = object()


class SystemService:
    """Unified system monitoring: sensor discovery, metrics, panel config."""

    def __init__(self, enumerator: SensorEnumerator | None = None) -> None:
        if enumerator is None:
            raise RuntimeError(
                "SystemService requires an enumerator. "
                "Use ControllerBuilder to wire dependencies.")
        self._enumerator: SensorEnumerator = enumerator
        self._discovered = False
        self._defaults: Optional[Dict[str, str]] = None
        # Cached fallback values — computed once, reused on subsequent calls
        self._fallback_cache: Optional[Dict[str, float]] = None
        self._fallback_lock = threading.Lock()
        self._mem_clock_cache: object | float | None = _SENTINEL

    # ── Sensor discovery ──────────────────────────────────────────────

    def discover(self) -> list[SensorInfo]:
        """Scan hardware for available sensors. Call once at startup."""
        sensors = self._enumerator.discover()
        self._discovered = True
        self._defaults = None  # Reset cached defaults
        self._enumerator.start_polling()
        return sensors

    def set_poll_interval(self, seconds: float) -> None:
        """Set background sensor poll interval (user's data refresh setting)."""
        self._enumerator.set_poll_interval(seconds)

    def start_polling(self) -> None:
        """Start background sensor polling thread."""
        self._ensure_discovered()
        self._enumerator.start_polling()

    def stop_polling(self) -> None:
        """Stop background sensor polling thread."""
        self._enumerator.stop_polling()

    def _ensure_discovered(self) -> None:
        """Lazy-discover sensors on first use."""
        if not self._discovered:
            self.discover()

    @property
    def sensors(self) -> list[SensorInfo]:
        """All discovered sensors."""
        self._ensure_discovered()
        return self._enumerator.get_sensors()

    @property
    def enumerator(self):
        """Direct access to SensorEnumerator (for GUI sensor picker)."""
        self._ensure_discovered()
        return self._enumerator

    # ── Readings ──────────────────────────────────────────────────────

    def read_all(self) -> dict[str, float]:
        """Read current values for all discovered sensors."""
        self._ensure_discovered()
        return self._enumerator.read_all()

    def read_one(self, sensor_id: str) -> Optional[float]:
        """Read a single sensor by ID."""
        self._ensure_discovered()
        return self._enumerator.read_one(sensor_id)

    # ── Legacy key mapping ────────────────────────────────────────────

    def _ensure_defaults(self) -> Dict[str, str]:
        """Get legacy metric key → sensor_id mapping (cached)."""
        self._ensure_discovered()
        if self._defaults is None:
            self._defaults = self._enumerator.map_defaults() or {}
        defaults: Dict[str, str] = self._defaults  # type: ignore[assignment]
        return defaults

    def _read_metric(self, legacy_key: str) -> Optional[float]:
        """Read a single metric by legacy key via the enumerator."""
        defaults = self._ensure_defaults()
        sensor_id = defaults.get(legacy_key)
        if sensor_id:
            return self._enumerator.read_one(sensor_id)
        return None

    # ── GPU slot support (used by LEDService for instant GPU switching) ──

    def gpu_mapping_for_pci(self, pci_slot: str) -> dict[str, str]:
        """Build gpu_temp/usage/clock/power sensor IDs for a PCI slot.

        Delegates to the enumerator. Used by LEDService to pre-build
        per-slot sensor mappings at init.
        """
        self._ensure_discovered()
        return self._enumerator.gpu_mapping_for_pci(pci_slot)

    def update_gpu_defaults(self, mapping: dict[str, str]) -> None:
        """Patch the cached defaults with GPU sensor IDs for a specific slot.

        Called by LEDService on cycle switch to reroute gpu_temp/usage/etc.
        to the newly active GPU's sensors without full re-enumeration.
        """
        defaults = self._ensure_defaults()
        defaults.update(mapping)

    # ── Metric properties ─────────────────────────────────────────────

    @property
    def cpu_temperature(self) -> Optional[float]:
        """CPU temperature (enumerator hwmon, fallback: lm_sensors)."""
        return self._read_metric('cpu_temp') or self._fallback_cpu_temp()

    @property
    def cpu_usage(self) -> Optional[float]:
        """CPU usage percentage."""
        return self._read_metric('cpu_percent') or self._fallback_cpu_usage()

    @property
    def cpu_frequency(self) -> Optional[float]:
        """CPU frequency in MHz."""
        return self._read_metric('cpu_freq') or self._fallback_cpu_freq()

    @property
    def gpu_temperature(self) -> Optional[float]:
        return self._read_metric('gpu_temp')

    @property
    def gpu_usage(self) -> Optional[float]:
        return self._read_metric('gpu_usage')

    @property
    def gpu_clock(self) -> Optional[float]:
        return self._read_metric('gpu_clock')

    @property
    def memory_usage(self) -> Optional[float]:
        return self._read_metric('mem_percent')

    @property
    def memory_available(self) -> Optional[float]:
        return self._read_metric('mem_available')

    @property
    def memory_temperature(self) -> Optional[float]:
        return self._read_metric('mem_temp') or self._fallback_mem_temp()

    @property
    def memory_clock(self) -> Optional[float]:
        return self._fallback_mem_clock()

    @property
    def disk_stats(self) -> Dict[str, float]:
        readings = self.read_all()
        stats: Dict[str, float] = {}
        for legacy, sensor in [
            ('disk_read', 'computed:disk_read'),
            ('disk_write', 'computed:disk_write'),
            ('disk_activity', 'computed:disk_activity'),
        ]:
            if sensor in readings:
                stats[legacy] = readings[sensor]
        return stats

    @property
    def disk_temperature(self) -> Optional[float]:
        return self._read_metric('disk_temp') or self._fallback_disk_temp()

    @property
    def network_stats(self) -> Dict[str, float]:
        readings = self.read_all()
        stats: Dict[str, float] = {}
        for legacy, sensor in [
            ('net_up', 'computed:net_up'),
            ('net_down', 'computed:net_down'),
            ('net_total_up', 'computed:net_total_up'),
            ('net_total_down', 'computed:net_total_down'),
        ]:
            if sensor in readings:
                stats[legacy] = readings[sensor]
        return stats

    @property
    def fan_speeds(self) -> Dict[str, float]:
        defaults = self._ensure_defaults()
        readings = self.read_all()
        fans: Dict[str, float] = {}
        for fan_key in ('fan_cpu', 'fan_gpu', 'fan_ssd', 'fan_sys2'):
            sensor_id = defaults.get(fan_key)
            if sensor_id and sensor_id in readings:
                fans[fan_key] = readings[sensor_id]
        return fans

    # ── Aggregate metrics ─────────────────────────────────────────────

    @property
    def all_metrics(self) -> HardwareMetrics:
        """All system metrics as a typed DTO (non-blocking).

        Sensor readings come from cached background thread.
        Fallback values come from a separate background computation.
        Only date/time is computed inline (instant).
        """
        m = HardwareMetrics()

        # Date and time (instant — no I/O)
        now = datetime.now()
        m.date_year = now.year
        m.date_month = now.month
        m.date_day = now.day
        m.time_hour = now.hour
        m.time_minute = now.minute
        m.time_second = now.second
        m.day_of_week = now.weekday()

        # Batch read ALL sensors from cache (instant)
        defaults = self._ensure_defaults()
        readings = self._enumerator.read_all()
        populated: set[str] = set()
        for attr_name, sensor_id in defaults.items():
            if sensor_id in readings and hasattr(m, attr_name):
                setattr(m, attr_name, readings[sensor_id])
                populated.add(attr_name)

        # Fallback values for metrics the enumerator couldn't provide.
        # Computed once (may call subprocess), then cached for future calls.
        self._ensure_fallbacks(populated)
        with self._fallback_lock:
            if self._fallback_cache:
                for attr_name, value in self._fallback_cache.items():
                    if attr_name not in populated and hasattr(m, attr_name):
                        setattr(m, attr_name, value)

        # Zero out disk metrics when HDD info is disabled (C# isHDD toggle)
        from ..conf import settings
        if not settings.hdd_enabled:
            m.disk_temp = 0.0
            m.disk_activity = 0.0
            m.disk_read = 0.0
            m.disk_write = 0.0

        return m

    def _ensure_fallbacks(self, existing_keys: set[str]) -> None:
        """Compute fallback metrics once, then cache for future calls."""
        with self._fallback_lock:
            if self._fallback_cache is not None:
                return  # Already computed

        cache: Dict[str, float] = {}
        fallbacks = [
            ('cpu_temp', self._fallback_cpu_temp),
            ('cpu_percent', self._fallback_cpu_usage),
            ('cpu_freq', self._fallback_cpu_freq),
            ('mem_temp', self._fallback_mem_temp),
            ('mem_clock', self._fallback_mem_clock),
            ('disk_temp', self._fallback_disk_temp),
        ]
        for key, fallback in fallbacks:
            if key not in existing_keys:
                try:
                    v = fallback()
                    if v is not None:
                        cache[key] = v
                    else:
                        log.debug("Fallback for %s returned no value", key)
                except Exception as e:
                    log.debug("Fallback for %s failed: %s", key, e)
        with self._fallback_lock:
            self._fallback_cache = cache

    # ── Formatting ────────────────────────────────────────────────────

    @staticmethod
    def format_metric(metric: str, value: float, time_format: int = 0,
                      date_format: int = 0, temp_unit: int = 0) -> str:
        """Format a metric value for display. Delegates to core.models."""
        return _format_metric(metric, value, time_format=time_format,
                              date_format=date_format, temp_unit=temp_unit)

    # ── Utilities ─────────────────────────────────────────────────────

    @staticmethod
    def find_hwmon_by_name(name: str) -> Optional[str]:
        """Find hwmon path by sensor name (k10temp, coretemp, amdgpu, etc.)."""
        hwmon_base = "/sys/class/hwmon"
        if not os.path.exists(hwmon_base):
            return None
        for i in range(20):
            hwmon_path = f"{hwmon_base}/hwmon{i}"
            sensor_name = _read_sysfs(f"{hwmon_path}/name")
            if sensor_name and name.lower() in sensor_name.lower():
                return hwmon_path
        return None

    # ── Subprocess fallbacks ──────────────────────────────────────────

    def _fallback_cpu_temp(self) -> Optional[float]:
        """CPU temp via lm_sensors subprocess."""
        try:
            result = subprocess.run(
                ['sensors', '-u'], capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split('\n'):
                if 'temp1_input' in line or 'Tctl' in line.lower():
                    match = re.search(r':\s*([0-9.]+)', line)
                    if match:
                        return float(match.group(1))
        except FileNotFoundError:
            log.debug("lm_sensors not installed — cpu_temp fallback unavailable")
        except Exception as e:
            log.debug("cpu_temp fallback failed: %s", e)
        return None

    @staticmethod
    def _fallback_cpu_usage() -> Optional[float]:
        """CPU usage via /proc/loadavg."""
        try:
            loadavg = _read_sysfs('/proc/loadavg')
            if loadavg:
                load = float(loadavg.split()[0])
                return min(100.0, load * 10)
        except Exception as e:
            log.debug("cpu_percent fallback failed: %s", e)
        return None

    @staticmethod
    def _fallback_cpu_freq() -> Optional[float]:
        """CPU frequency via /proc/cpuinfo."""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if 'cpu MHz' in line:
                        match = re.search(r':\s*([0-9.]+)', line)
                        if match:
                            return float(match.group(1))
        except Exception as e:
            log.debug("cpu_freq fallback failed: %s", e)
        return None

    def _fallback_mem_temp(self) -> Optional[float]:
        """Memory temp via lm_sensors subprocess."""
        try:
            result = subprocess.run(
                ['sensors', '-u'], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                in_memory_section = False
                for line in result.stdout.split('\n'):
                    line_lower = line.lower()
                    if any(x in line_lower for x in ['ddr', 'dimm', 'memory']):
                        in_memory_section = True
                    elif line and not line.startswith(' ') and ':' not in line:
                        in_memory_section = False
                    if in_memory_section and 'temp' in line_lower and '_input' in line_lower:
                        match = re.search(r':\s*([0-9.]+)', line)
                        if match:
                            return float(match.group(1))
        except FileNotFoundError:
            log.debug("lm_sensors not installed — mem_temp fallback unavailable")
        except Exception as e:
            log.debug("mem_temp fallback failed: %s", e)
        return None

    def _fallback_mem_clock(self) -> Optional[float]:
        """Memory clock via dmidecode / lshw / EDAC.  Cached after first call."""
        if self._mem_clock_cache is not _SENTINEL:
            return self._mem_clock_cache  # type: ignore[return-value]
        value = self._probe_mem_clock()
        self._mem_clock_cache = value
        return value

    @staticmethod
    def _probe_mem_clock(privileged_cmd_fn: Any = None) -> Optional[float]:
        """Actually probe memory clock (called once, result cached)."""
        if privileged_cmd_fn is None:
            # Fallback: build command list directly (no sudo wrapper)
            def _default_cmd(cmd: str, args: list[str]) -> list[str]:
                return [cmd, *args]
            privileged_cmd_fn = _default_cmd
        try:
            result = subprocess.run(
                privileged_cmd_fn('dmidecode', ['-t', 'memory']),
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'Configured Memory Speed' in line:
                        match = re.search(r'(\d+)\s*(?:MT/s|MHz)', line)
                        if match:
                            return float(match.group(1))
                for line in result.stdout.split('\n'):
                    if 'Speed:' in line and 'Unknown' not in line:
                        match = re.search(r'(\d+)\s*(?:MT/s|MHz)', line)
                        if match:
                            return float(match.group(1))
        except FileNotFoundError:
            log.debug("dmidecode not installed — mem_clock fallback unavailable")
        except Exception as e:
            log.debug("mem_clock dmidecode probe failed: %s", e)

        try:
            result = subprocess.run(
                ['lshw', '-class', 'memory', '-short'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                match = re.search(r'(\d+)\s*(?:MT/s|MHz)', result.stdout)
                if match:
                    return float(match.group(1))
        except FileNotFoundError:
            log.debug("lshw not installed — mem_clock lshw probe unavailable")
        except Exception as e:
            log.debug("mem_clock lshw probe failed: %s", e)

        mc_path = "/sys/devices/system/edac/mc"
        if os.path.exists(mc_path):
            try:
                for mc in os.listdir(mc_path):
                    content = _read_sysfs(f"{mc_path}/{mc}/dimm_info")
                    if content:
                        match = re.search(r'(\d+)\s*MHz', content)
                        if match:
                            return float(match.group(1))
            except Exception as e:
                log.debug("mem_clock EDAC probe failed: %s", e)

        return None

    @staticmethod
    def _fallback_disk_temp() -> Optional[float]:
        """Disk temperature via smartctl."""
        try:
            result = subprocess.run(
                ['smartctl', '-A', '/dev/sda'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'Temperature' in line or 'Airflow_Temperature' in line:
                        parts = line.split()
                        for part in parts:
                            if part.isdigit() and int(part) < 100:
                                return float(part)
        except FileNotFoundError:
            log.debug("smartctl not installed — disk_temp fallback unavailable")
        except Exception as e:
            log.debug("disk_temp fallback failed: %s", e)
        return None


# ── Module-level convenience API ─────────────────────────────────────────────
# Explicit singleton — composition roots call set_instance() at startup.

_instance: SystemService | None = None


def set_instance(svc: SystemService) -> None:
    """Set the module-level SystemService singleton.

    Called by composition roots (GUI, CLI, API) after building the service
    with injected dependencies.  Replaces the old ``_get_instance()`` which
    violated hexagonal architecture by importing from adapters.
    """
    global _instance  # noqa: PLW0603
    _instance = svc


def get_instance() -> SystemService:
    """Return the module-level SystemService singleton.

    Raises RuntimeError if ``set_instance()`` has not been called yet.
    """
    if _instance is None:
        raise RuntimeError(
            "SystemService not initialized. "
            "Call set_instance() from a composition root.")
    return _instance


def get_all_metrics() -> HardwareMetrics:
    """Get all hardware metrics."""
    return get_instance().all_metrics


def set_poll_interval(seconds: float) -> None:
    """Set background sensor poll interval (user's data refresh setting)."""
    get_instance().set_poll_interval(seconds)


def get_cached_metrics(max_age: float = 0.5) -> HardwareMetrics:
    """Return cached metrics if fresh enough, else poll once.

    Multiple Qt timers (metrics, info panel, activity sidebar, LED handler)
    all need metrics each second.  This ensures only ONE actual sensor poll
    per ``max_age`` window — callers that fire within the same tick share
    the same result.
    """
    global _cached_metrics, _cached_metrics_time  # noqa: PLW0603
    now = time.monotonic()
    if _cached_metrics is None or (now - _cached_metrics_time) > max_age:
        _cached_metrics = get_all_metrics()
        _cached_metrics_time = now
    return _cached_metrics


_cached_metrics: HardwareMetrics | None = None
_cached_metrics_time: float = 0.0


def format_metric(key: str, value: float, **kwargs: Any) -> str:
    """Format a single metric value for display."""
    return _format_metric(key, value, **kwargs)

