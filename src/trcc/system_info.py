#!/usr/bin/env python3
"""
System Info Provider for TRCC LCD.

Thin facade over SensorEnumerator — delegates all hardware reads, keeps
only formatting logic and subprocess-based fallbacks (dmidecode, smartctl,
lm_sensors) that the enumerator doesn't cover.

Usage:
    # Class API (preferred):
    si = SystemInfo()
    si.cpu_temperature    # Optional[float]
    si.all_metrics        # dict[str, float]
    SystemInfo.format_metric("cpu_temp", 65.0)  # "65°C"

    # Legacy function API (backward-compat, delegates to singleton):
    from trcc.system_info import get_cpu_temperature, get_all_metrics
    get_cpu_temperature()  # same as SystemInfo().cpu_temperature
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Optional

from trcc.constants import DATE_FORMATS, TIME_FORMATS, WEEKDAYS
from trcc.paths import read_sysfs

if TYPE_CHECKING:
    from trcc.sensor_enumerator import SensorEnumerator


class SystemInfo:
    """Reads CPU/GPU/memory/disk/network metrics via SensorEnumerator.

    Each metric is a property returning Optional[float] (None if unavailable).
    Delegates hardware reads to SensorEnumerator (lazy-initialized on first
    access) and falls back to subprocess calls for niche sources the
    enumerator doesn't cover (dmidecode, smartctl, lm_sensors).
    """

    def __init__(self) -> None:
        self._enumerator: Optional[SensorEnumerator] = None
        self._defaults: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------
    # Enumerator delegation
    # ------------------------------------------------------------------

    def _ensure_enumerator(self) -> SensorEnumerator:
        """Lazy-init SensorEnumerator on first use."""
        if self._enumerator is None:
            from trcc.sensor_enumerator import SensorEnumerator
            self._enumerator = SensorEnumerator()
            self._enumerator.discover()
            self._defaults = self._enumerator.map_defaults()
        return self._enumerator

    def _read_metric(self, legacy_key: str) -> Optional[float]:
        """Read a single metric by legacy key via the enumerator."""
        self._ensure_enumerator()
        assert self._defaults is not None
        sensor_id = self._defaults.get(legacy_key)
        if sensor_id:
            return self._enumerator.read_one(sensor_id)  # type: ignore[union-attr]
        return None

    # ------------------------------------------------------------------
    # CPU metrics
    # ------------------------------------------------------------------

    @property
    def cpu_temperature(self) -> Optional[float]:
        """CPU temperature (enumerator hwmon, fallback: lm_sensors)."""
        return self._read_metric('cpu_temp') or self._fallback_cpu_temp()

    @property
    def cpu_usage(self) -> Optional[float]:
        """CPU usage percentage (enumerator psutil, fallback: /proc/loadavg)."""
        return self._read_metric('cpu_percent') or self._fallback_cpu_usage()

    @property
    def cpu_frequency(self) -> Optional[float]:
        """CPU frequency in MHz (enumerator psutil, fallback: /proc/cpuinfo)."""
        return self._read_metric('cpu_freq') or self._fallback_cpu_freq()

    # ------------------------------------------------------------------
    # GPU metrics
    # ------------------------------------------------------------------

    @property
    def gpu_temperature(self) -> Optional[float]:
        """GPU temperature (enumerator: hwmon AMD + pynvml NVIDIA)."""
        return self._read_metric('gpu_temp')

    @property
    def gpu_usage(self) -> Optional[float]:
        """GPU usage percentage."""
        return self._read_metric('gpu_usage')

    @property
    def gpu_clock(self) -> Optional[float]:
        """GPU clock in MHz."""
        return self._read_metric('gpu_clock')

    # ------------------------------------------------------------------
    # Memory metrics
    # ------------------------------------------------------------------

    @property
    def memory_usage(self) -> Optional[float]:
        """Memory usage percentage."""
        return self._read_metric('mem_percent')

    @property
    def memory_available(self) -> Optional[float]:
        """Available memory in MB."""
        return self._read_metric('mem_available')

    @property
    def memory_temperature(self) -> Optional[float]:
        """Memory/DIMM temperature (enumerator hwmon, fallback: lm_sensors)."""
        return self._read_metric('mem_temp') or self._fallback_mem_temp()

    @property
    def memory_clock(self) -> Optional[float]:
        """Memory clock in MHz (dmidecode/lshw/EDAC — no enumerator source)."""
        return self._fallback_mem_clock()

    # ------------------------------------------------------------------
    # Disk metrics
    # ------------------------------------------------------------------

    @property
    def disk_stats(self) -> Dict[str, float]:
        """Disk I/O statistics (read/write MB/s, activity %)."""
        enum = self._ensure_enumerator()
        readings = enum.read_all()
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
        """Disk temperature (enumerator hwmon, fallback: smartctl)."""
        return self._read_metric('disk_temp') or self._fallback_disk_temp()

    # ------------------------------------------------------------------
    # Network metrics
    # ------------------------------------------------------------------

    @property
    def network_stats(self) -> Dict[str, float]:
        """Network I/O statistics (up/down KB/s, totals in MB)."""
        enum = self._ensure_enumerator()
        readings = enum.read_all()
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

    # ------------------------------------------------------------------
    # Fan metrics
    # ------------------------------------------------------------------

    @property
    def fan_speeds(self) -> Dict[str, float]:
        """Fan speeds from hwmon sensors."""
        self._ensure_enumerator()
        assert self._defaults is not None
        enum = self._enumerator
        assert enum is not None
        readings = enum.read_all()
        fans: Dict[str, float] = {}
        for fan_key in ('fan_cpu', 'fan_gpu', 'fan_ssd', 'fan_sys2'):
            sensor_id = self._defaults.get(fan_key)
            if sensor_id and sensor_id in readings:
                fans[fan_key] = readings[sensor_id]
        return fans

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    @property
    def all_metrics(self) -> Dict[str, float]:
        """All system metrics as a flat dict."""
        metrics: Dict[str, float] = {}

        # Date and time (unique to SystemInfo)
        now = datetime.now()
        metrics['date_year'] = now.year
        metrics['date_month'] = now.month
        metrics['date_day'] = now.day
        metrics['time_hour'] = now.hour
        metrics['time_minute'] = now.minute
        metrics['time_second'] = now.second
        metrics['day_of_week'] = now.weekday()
        metrics['date'] = 0
        metrics['time'] = 0
        metrics['weekday'] = 0

        # Batch read ALL sensors once via enumerator
        enum = self._ensure_enumerator()
        assert self._defaults is not None
        readings = enum.read_all()
        for legacy_key, sensor_id in self._defaults.items():
            if sensor_id in readings:
                metrics[legacy_key] = readings[sensor_id]

        # Fallbacks for metrics the enumerator couldn't provide
        _fallbacks = [
            ('cpu_temp', self._fallback_cpu_temp),
            ('cpu_percent', self._fallback_cpu_usage),
            ('cpu_freq', self._fallback_cpu_freq),
            ('mem_temp', self._fallback_mem_temp),
            ('mem_clock', self._fallback_mem_clock),
            ('disk_temp', self._fallback_disk_temp),
        ]
        for key, fallback in _fallbacks:
            if key not in metrics:
                if (v := fallback()) is not None:
                    metrics[key] = v

        return metrics

    # ------------------------------------------------------------------
    # Subprocess fallbacks (not covered by SensorEnumerator)
    # ------------------------------------------------------------------

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
        except Exception:
            pass
        return None

    @staticmethod
    def _fallback_cpu_usage() -> Optional[float]:
        """CPU usage via /proc/loadavg."""
        try:
            loadavg = read_sysfs('/proc/loadavg')
            if loadavg:
                load = float(loadavg.split()[0])
                return min(100.0, load * 10)
        except Exception:
            pass
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
        except Exception:
            pass
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
        except Exception:
            pass
        return None

    @staticmethod
    def _fallback_mem_clock() -> Optional[float]:
        """Memory clock via dmidecode / lshw / EDAC."""
        # Try dmidecode (requires root)
        try:
            result = subprocess.run(
                ['dmidecode', '-t', 'memory'],
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
        except Exception:
            pass

        # Try lshw
        try:
            result = subprocess.run(
                ['lshw', '-class', 'memory', '-short'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                match = re.search(r'(\d+)\s*(?:MT/s|MHz)', result.stdout)
                if match:
                    return float(match.group(1))
        except Exception:
            pass

        # Try EDAC
        mc_path = "/sys/devices/system/edac/mc"
        if os.path.exists(mc_path):
            try:
                for mc in os.listdir(mc_path):
                    content = read_sysfs(f"{mc_path}/{mc}/dimm_info")
                    if content:
                        match = re.search(r'(\d+)\s*MHz', content)
                        if match:
                            return float(match.group(1))
            except Exception:
                pass

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
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def find_hwmon_by_name(name: str) -> Optional[str]:
        """Find hwmon path by sensor name (k10temp, coretemp, amdgpu, etc.)."""
        hwmon_base = "/sys/class/hwmon"
        if not os.path.exists(hwmon_base):
            return None
        for i in range(20):
            hwmon_path = f"{hwmon_base}/hwmon{i}"
            sensor_name = read_sysfs(f"{hwmon_path}/name")
            if sensor_name and name.lower() in sensor_name.lower():
                return hwmon_path
        return None

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_metric(metric: str, value: float, time_format: int = 0,
                      date_format: int = 0, temp_unit: int = 0) -> str:
        """Format a metric value for display (matches Windows TRCC).

        Args:
            metric: The metric name
            value: The numeric value
            time_format: 0=HH:mm, 1=hh:mm AM/PM, 2=HH:mm (same as 0)
            date_format: 0=yyyy/MM/dd, 1=yyyy/MM/dd, 2=dd/MM/yyyy, 3=MM/dd, 4=dd/MM
            temp_unit: 0=Celsius (°C), 1=Fahrenheit (°F) (Windows myModeSub)
        """
        if metric == 'date':
            now = datetime.now()
            fmt = DATE_FORMATS.get(date_format, DATE_FORMATS[0])
            return now.strftime(fmt)
        elif metric == 'time':
            now = datetime.now()
            fmt = TIME_FORMATS.get(time_format, TIME_FORMATS[0])
            return now.strftime(fmt)
        elif metric == 'weekday':
            now = datetime.now()
            return WEEKDAYS[now.weekday()]
        elif metric == 'day_of_week':
            return WEEKDAYS[int(value)]
        elif metric.startswith('time_') or metric.startswith('date_'):
            return f"{int(value):02d}"
        elif 'temp' in metric:
            if temp_unit == 1:  # Fahrenheit
                fahrenheit = value * 9 / 5 + 32
                return f"{fahrenheit:.0f}°F"
            else:
                return f"{value:.0f}°C"
        elif 'percent' in metric or 'usage' in metric or 'activity' in metric:
            return f"{value:.0f}%"
        elif 'freq' in metric or 'clock' in metric:
            if value >= 1000:
                return f"{value/1000:.1f}GHz"
            return f"{value:.0f}MHz"
        elif metric in ('disk_read', 'disk_write'):
            return f"{value:.1f}MB/s"
        elif metric in ('net_up', 'net_down'):
            if value >= 1024:
                return f"{value/1024:.1f}MB/s"
            return f"{value:.0f}KB/s"
        elif metric in ('net_total_up', 'net_total_down'):
            if value >= 1024:
                return f"{value/1024:.1f}GB"
            return f"{value:.0f}MB"
        elif metric.startswith('fan_'):
            return f"{value:.0f}RPM"
        elif metric == 'mem_available':
            if value >= 1024:
                return f"{value/1024:.1f}GB"
            return f"{value:.0f}MB"
        return f"{value:.1f}"


# =========================================================================
# Module-level singleton + backward-compat aliases
# =========================================================================
# Existing code imports `from trcc.system_info import get_cpu_temperature`.
# These aliases preserve that API while delegating to the class.

_instance = SystemInfo()

get_cpu_temperature = lambda: _instance.cpu_temperature  # noqa: E731
get_cpu_usage = lambda: _instance.cpu_usage  # noqa: E731
get_cpu_frequency = lambda: _instance.cpu_frequency  # noqa: E731
get_gpu_temperature = lambda: _instance.gpu_temperature  # noqa: E731
get_gpu_usage = lambda: _instance.gpu_usage  # noqa: E731
get_gpu_clock = lambda: _instance.gpu_clock  # noqa: E731
get_memory_usage = lambda: _instance.memory_usage  # noqa: E731
get_memory_available = lambda: _instance.memory_available  # noqa: E731
get_memory_temperature = lambda: _instance.memory_temperature  # noqa: E731
get_memory_clock = lambda: _instance.memory_clock  # noqa: E731
get_disk_stats = lambda: _instance.disk_stats  # noqa: E731
get_disk_temperature = lambda: _instance.disk_temperature  # noqa: E731
get_network_stats = lambda: _instance.network_stats  # noqa: E731
get_fan_speeds = lambda: _instance.fan_speeds  # noqa: E731
get_all_metrics = lambda: _instance.all_metrics  # noqa: E731
format_metric = SystemInfo.format_metric
find_hwmon_by_name = SystemInfo.find_hwmon_by_name


if __name__ == '__main__':
    print("System Info Test")
    print("=" * 40)

    si = SystemInfo()
    metrics = si.all_metrics
    for key, value in metrics.items():
        print(f"{key}: {si.format_metric(key, value)}")
