"""Sensor models — hardware metrics DTO, sensor info, dashboard config."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .constants import (
    DATE_FORMATS,
    TIME_FORMATS,
    WEEKDAYS,
    WEEKDAYS_BY_LANG,
    celsius_to_fahrenheit,
)

# =============================================================================
# HardwareMetrics DTO — typed container for all system sensor readings.
# Replaces Dict[str, float] with magic string keys. Consumers use attribute
# access (metrics.cpu_temp) instead of dict lookups (metrics['cpu_temp']).
# Pyright catches typos at lint time. Fields default to 0.0.
# =============================================================================

@dataclass(slots=True)
class HardwareMetrics:
    """Typed DTO for all system sensor readings. Updated once/second by polling."""
    # CPU
    cpu_temp: float = 0.0
    cpu_percent: float = 0.0
    cpu_freq: float = 0.0
    cpu_power: float = 0.0
    # GPU
    gpu_temp: float = 0.0
    gpu_usage: float = 0.0
    gpu_clock: float = 0.0
    gpu_power: float = 0.0
    # Memory
    mem_temp: float = 0.0
    mem_percent: float = 0.0
    mem_clock: float = 0.0
    mem_available: float = 0.0
    # Disk
    disk_temp: float = 0.0
    disk_activity: float = 0.0
    disk_read: float = 0.0
    disk_write: float = 0.0
    # Network
    net_up: float = 0.0
    net_down: float = 0.0
    net_total_up: float = 0.0
    net_total_down: float = 0.0
    # Fan
    fan_cpu: float = 0.0
    fan_gpu: float = 0.0
    fan_ssd: float = 0.0
    fan_sys2: float = 0.0
    # Date/Time
    date_year: float = 0.0
    date_month: float = 0.0
    date_day: float = 0.0
    time_hour: float = 0.0
    time_minute: float = 0.0
    time_second: float = 0.0
    day_of_week: float = 0.0
    date: float = 0.0
    time: float = 0.0
    weekday: float = 0.0

    # Observability surface — every Topic.METRICS subscriber reads from the
    # same record.  Typed fields above answer "what's the CPU temp?" by name;
    # ``readings`` answers "what raw sensors did the OS expose?" for consumers
    # (GUI sensor panel) that iterate the cache; ``_populated`` distinguishes
    # "no source on this OS" from "actually zero" so renderers skip ghost
    # values instead of drawing literal "0°C".
    readings: dict[str, float] = field(default_factory=dict, repr=False, compare=False)
    _populated: set[str] = field(default_factory=set, repr=False, compare=False)

    _TEMP_FIELDS = ('cpu_temp', 'gpu_temp', 'mem_temp', 'disk_temp')

    @staticmethod
    def with_temp_unit(metrics: HardwareMetrics, temp_unit: int) -> HardwareMetrics:
        """Apply temperature unit conversion in-place (0=Celsius, 1=Fahrenheit).

        Called once by the metrics broadcast before dispatch — every
        Topic.METRICS subscriber receives pre-converted temps.
        """
        if temp_unit != 1:
            return metrics
        for attr in HardwareMetrics._TEMP_FIELDS:
            setattr(metrics, attr, celsius_to_fahrenheit(getattr(metrics, attr)))
        return metrics


# Fields that keep float precision (need decimals for unit conversion in
# format_metric: MB→GB, KB/s→MB/s, etc.). All other metrics are truncated to
# int at the read boundary, matching the C# app's behavior.  Lives next to
# the DTO so every metrics-builder (Platform.metrics, LinuxMetrics,
# WindowsMetrics, …) reads the same set instead of redefining locally.
FLOAT_FIELDS: frozenset[str] = frozenset({
    'mem_available', 'disk_read', 'disk_write',
    'net_up', 'net_down', 'net_total_up', 'net_total_down',
})


# Hardware sensor ↔ metric name mapping (single source of truth).
# Maps DC file (main_count, sub_count) → HardwareMetrics attribute name.
# Used by dc_parser, dc_writer, dc_config, uc_sensor_picker.
HARDWARE_METRICS: dict[tuple[int, int], str] = {
    # CPU (main_count=0)
    (0, 1): 'cpu_temp',
    (0, 2): 'cpu_percent',
    (0, 3): 'cpu_freq',
    (0, 4): 'cpu_power',
    # GPU (main_count=1)
    (1, 1): 'gpu_temp',
    (1, 2): 'gpu_usage',
    (1, 3): 'gpu_clock',
    (1, 4): 'gpu_power',
    # MEM (main_count=2)
    (2, 1): 'mem_percent',
    (2, 2): 'mem_clock',
    (2, 3): 'mem_available',
    (2, 4): 'mem_temp',
    # HDD (main_count=3)
    (3, 1): 'disk_read',
    (3, 2): 'disk_write',
    (3, 3): 'disk_activity',
    (3, 4): 'disk_temp',
    # NET (main_count=4)
    (4, 1): 'net_down',
    (4, 2): 'net_up',
    (4, 3): 'net_total_down',
    (4, 4): 'net_total_up',
    # FAN (main_count=5)
    (5, 1): 'fan_cpu',
    (5, 2): 'fan_gpu',
    (5, 3): 'fan_ssd',
    (5, 4): 'fan_sys2',
}

METRIC_TO_IDS: dict[str, tuple[int, int]] = {v: k for k, v in HARDWARE_METRICS.items()}


# =============================================================================
# Sensor DTOs
# =============================================================================

@dataclass(frozen=True, slots=True)
class SensorInfo:
    """Describes a single hardware sensor."""
    id: str             # Unique ID: "hwmon:coretemp:temp1"
    name: str           # Human-readable: "CPU Package"
    category: str       # "temperature", "fan", "clock", "usage", "power", "voltage", "other"
    unit: str           # "°C", "RPM", "MHz", "%", "W", "V", "MB/s", "KB/s", "MB"
    source: str         # "hwmon", "nvidia", "psutil", "rapl", "computed"


# =============================================================================
# Sensor Dashboard Panel Configuration — pure domain dataclasses
# =============================================================================

@dataclass(slots=True)
class SensorBinding:
    """Maps a single dashboard panel row to a sensor."""
    label: str        # Row label displayed on panel ("TEMP", "Usage", etc.)
    sensor_id: str    # SensorEnumerator ID ("hwmon:coretemp:temp1")
    unit: str         # Display unit suffix ("°C", "%", "MHz", etc.)


@dataclass(slots=True)
class PanelConfig:
    """Configuration for a single sensor dashboard panel."""
    category_id: int                          # 0=Custom,1=CPU,2=GPU,3=Memory,4=HDD,5=Network,6=Fan
    name: str                                  # Panel display name
    sensors: list[SensorBinding] = field(default_factory=list)


# =============================================================================
# Sensor Dashboard Category Display Mappings
# =============================================================================

# Category ID → background image name
CATEGORY_IMAGES: dict[int, str] = {
    0: 'sysinfo_custom.png',
    1: 'sysinfo_cpu.png',
    2: 'sysinfo_gpu.png',
    3: 'sysinfo_dram.png',
    4: 'sysinfo_hdd.png',
    5: 'sysinfo_net.png',
    6: 'sysinfo_fan.png',
}

# Category ID → value text color
CATEGORY_COLORS: dict[int, str] = {
    0: '#9375FF',     # Custom: Purple
    1: '#32C5FF',     # CPU: Cyan
    2: '#44D7B6',     # GPU: Teal
    3: '#6DD401',     # Memory: Lime
    4: '#F7B501',     # HDD: Orange
    5: '#FA6401',     # Network: Red-orange
    6: '#E02020',     # Fan: Red
}

# Overlay element hardware category ID → display name (0=CPU, 1=GPU, …)
CATEGORY_NAMES: dict[int, str] = {
    0: 'CPU',
    1: 'GPU',
    2: 'MEM',
    3: 'HDD',
    4: 'NET',
    5: 'FAN',
}

# Overlay element hardware sub-metric labels per category
# {category_id: {sub_count: label}}
SUB_METRICS: dict[int, dict[int, str]] = {
    0: {1: 'Temp', 2: 'Usage', 3: 'Freq',     4: 'Power'},
    1: {1: 'Temp', 2: 'Usage', 3: 'Clock',    4: 'Power'},
    2: {1: 'Used%', 2: 'Clock', 3: 'Used',    4: 'Free'},
    3: {1: 'Read', 2: 'Write', 3: 'Activity', 4: 'Temp'},
    4: {1: 'Down', 2: 'Up',    3: 'Total',    4: 'Ping'},
    5: {1: 'RPM',  2: 'PWM%',  3: 'Temp',     4: 'Speed'},
}

# Activity sidebar sensor definitions: category → [(key_suffix, label, unit, metric_key)]
SENSORS: dict[str, list[tuple[str, str, str, str]]] = {
    'cpu':     [('temp',       'TEMP',      '°C',   'cpu_temp'),
                ('usage',      'Usage',     '%',    'cpu_percent'),
                ('clock',      'Clock',     'MHz',  'cpu_freq'),
                ('power',      'Power',     'W',    'cpu_power')],
    'gpu':     [('temp',       'TEMP',      '°C',   'gpu_temp'),
                ('usage',      'Usage',     '%',    'gpu_usage'),
                ('clock',      'Clock',     'MHz',  'gpu_clock'),
                ('power',      'Power',     'W',    'gpu_power')],
    'memory':  [('temp',       'TEMP',      '°C',   'mem_temp'),
                ('usage',      'Usage',     '%',    'mem_percent'),
                ('clock',      'Clock',     'MHz',  'mem_clock'),
                ('available',  'Available', 'MB',   'mem_available')],
    'hdd':     [('temp',       'TEMP',      '°C',   'disk_temp'),
                ('activity',   'Activity',  '%',    'disk_activity'),
                ('read',       'Read',      'MB/s', 'disk_read'),
                ('write',      'Write',     'MB/s', 'disk_write')],
    'network': [('upload',     'UP rate',   'KB/s', 'net_up'),
                ('download',   'DL rate',   'KB/s', 'net_down'),
                ('total_up',   'Total UP',  'MB',   'net_total_up'),
                ('total_dl',   'Total DL',  'MB',   'net_total_down')],
    'fan':     [('cpu_fan',    'CPUFAN',    'RPM',  'fan_cpu'),
                ('gpu_fan',    'GPUFAN',    'RPM',  'fan_gpu'),
                ('ssd_fan',    'SSDFAN',    'RPM',  'fan_ssd'),
                ('fan2',       'FAN2',      'RPM',  'fan_sys2')],
}

# Maps 'category_keysuffix' → overlay (main_count, sub_count)
SENSOR_TO_OVERLAY: dict[str, tuple[int, int]] = {
    'cpu_temp': (0, 1),     'cpu_usage': (0, 2),     'cpu_clock': (0, 3),     'cpu_power': (0, 4),
    'gpu_temp': (1, 1),     'gpu_usage': (1, 2),     'gpu_clock': (1, 3),     'gpu_power': (1, 4),
    'memory_temp': (2, 1),  'memory_usage': (2, 2),  'memory_clock': (2, 3),  'memory_available': (2, 4),
    'hdd_temp': (3, 1),     'hdd_activity': (3, 2),  'hdd_read': (3, 3),      'hdd_write': (3, 4),
    'network_upload': (4, 1), 'network_download': (4, 2), 'network_total_up': (4, 3), 'network_total_dl': (4, 4),
    'fan_cpu_fan': (5, 1),  'fan_gpu_fan': (5, 2),   'fan_ssd_fan': (5, 3),   'fan_fan2': (5, 4),
}


# =============================================================================
# Metric formatting — single source of truth (matches Windows TRCC)
# =============================================================================


def _weekday_names(lang: str | None) -> list[str]:
    """Return the per-language weekday list, falling back to English."""
    if lang is None:
        return WEEKDAYS
    # Allow region-tagged codes (zh_TW, en_US, …) to fall back to base.
    return (WEEKDAYS_BY_LANG.get(lang)
            or WEEKDAYS_BY_LANG.get(lang.split('_', 1)[0])
            or WEEKDAYS)


def format_metric(metric: str, value: float, time_format: int = 0,
                  date_format: int = 0, temp_unit: int = 0,
                  lang: str | None = None) -> str:
    """Format a metric value for display (matches Windows TRCC).

    ``lang`` is an optional ISO 639-1 code (e.g. 'de', 'fr', 'zh') used for
    weekday abbreviations.  When None, callers fall through to English —
    backwards-compatible with every pre-issue-#141 caller.
    """
    if metric == 'date':
        now = datetime.now()
        fmt = DATE_FORMATS.get(date_format, DATE_FORMATS[0])
        return now.strftime(fmt)
    elif metric == 'time':
        now = datetime.now()
        fmt = TIME_FORMATS.get(time_format, TIME_FORMATS[0])
        result = now.strftime(fmt)
        # Strip leading zero for 12-hour format (cross-platform — avoids
        # Unix %-I vs Windows %#I platform-specific strftime flags)
        if time_format == 1:
            result = result.lstrip('0')
        return result
    elif metric == 'weekday':
        now = datetime.now()
        return _weekday_names(lang)[now.weekday()]
    elif metric == 'day_of_week':
        return _weekday_names(lang)[int(value)]
    elif metric.startswith(('time_', 'date_')):
        return f"{int(value):02d}"
    elif 'temp' in metric:
        suffix = "°F" if temp_unit == 1 else "°C"
        return f"{value:.0f}{suffix}"
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


__all__ = [
    'CATEGORY_COLORS',
    'CATEGORY_IMAGES',
    'CATEGORY_NAMES',
    'HARDWARE_METRICS',
    'METRIC_TO_IDS',
    'SENSORS',
    'SENSOR_TO_OVERLAY',
    'SUB_METRICS',
    'HardwareMetrics',
    'PanelConfig',
    'SensorBinding',
    'SensorInfo',
    'format_metric',
]
