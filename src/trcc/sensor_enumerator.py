"""
Linux hardware sensor discovery and reading.

Replaces Windows HWiNFO64 shared memory with native Linux sensor sources:
- hwmon: /sys/class/hwmon/* (temperatures, fans, voltages, power, frequency)
- NVIDIA GPU: nvidia-ml-py / pynvml (temperature, utilization, clock, power, VRAM, fan)
- psutil: CPU usage/frequency, memory, disk I/O, network I/O
- Intel RAPL: CPU package power via /sys/class/powercap/

Sensor IDs follow the format:
    hwmon:{driver}:{input}    e.g., hwmon:coretemp:temp1
    nvidia:{gpu}:{metric}     e.g., nvidia:0:temp
    psutil:{metric}           e.g., psutil:cpu_percent
    rapl:{domain}             e.g., rapl:package-0
    computed:{metric}         e.g., computed:disk_read
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from trcc.paths import read_sysfs

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except Exception:
    pynvml = None  # type: ignore[assignment]
    NVML_AVAILABLE = False


@dataclass
class SensorInfo:
    """Describes a single hardware sensor."""
    id: str             # Unique ID: "hwmon:coretemp:temp1"
    name: str           # Human-readable: "CPU Package"
    category: str       # "temperature", "fan", "clock", "usage", "power", "voltage", "other"
    unit: str           # "°C", "RPM", "MHz", "%", "W", "V", "MB/s", "KB/s", "MB"
    source: str         # "hwmon", "nvidia", "psutil", "rapl", "computed"


# Maps hwmon input prefix to (category, unit)
_HWMON_TYPES = {
    'temp': ('temperature', '°C'),
    'fan': ('fan', 'RPM'),
    'in': ('voltage', 'V'),
    'power': ('power', 'W'),
    'freq': ('clock', 'MHz'),
}

# Maps hwmon input prefix to value divisor (sysfs uses millidegrees, microvolts, etc.)
_HWMON_DIVISORS = {
    'temp': 1000.0,    # millidegrees → degrees
    'fan': 1.0,        # already RPM
    'in': 1000.0,      # millivolts → volts
    'power': 1000000.0,  # microwatts → watts
    'freq': 1000000.0,  # Hz → MHz
}



class SensorEnumerator:
    """Discovers and reads all available hardware sensors on the system."""

    def __init__(self):
        self._sensors: list[SensorInfo] = []
        self._hwmon_paths: dict[str, str] = {}   # sensor_id -> sysfs path
        self._nvidia_handles: dict[int, object] = {}  # gpu_index -> handle
        self._rapl_paths: dict[str, str] = {}     # sensor_id -> energy_uj path
        self._rapl_prev: dict[str, tuple[float, float]] = {}  # id -> (energy, time)
        self._net_prev: Optional[tuple] = None     # (counters, time)
        self._disk_prev: Optional[tuple] = None    # (counters, time)

    def discover(self) -> list[SensorInfo]:
        """Scan the system for all available sensors. Call once at startup."""
        self._sensors = []
        self._hwmon_paths = {}
        self._nvidia_handles = {}
        self._rapl_paths = {}

        self._discover_hwmon()
        self._discover_nvidia()
        self._discover_psutil()
        self._discover_rapl()
        self._discover_computed()

        return self._sensors

    def get_sensors(self) -> list[SensorInfo]:
        """Return previously discovered sensors."""
        return self._sensors

    def get_by_category(self, category: str) -> list[SensorInfo]:
        """Filter sensors by category."""
        return [s for s in self._sensors if s.category == category]

    def read_all(self) -> dict[str, float]:
        """Read current values for ALL discovered sensors."""
        readings: dict[str, float] = {}

        # hwmon sensors
        for sid, path in self._hwmon_paths.items():
            val = read_sysfs(path)
            if val is not None:
                try:
                    raw = float(val)
                    # Determine divisor from sensor type prefix
                    prefix = sid.split(':')[-1]  # e.g., "temp1"
                    for pfx, div in _HWMON_DIVISORS.items():
                        if prefix.startswith(pfx):
                            readings[sid] = raw / div
                            break
                    else:
                        readings[sid] = raw
                except ValueError:
                    pass

        # NVIDIA sensors
        self._read_nvidia(readings)

        # psutil sensors
        self._read_psutil(readings)

        # RAPL power
        self._read_rapl(readings)

        # Computed I/O rates
        self._read_computed(readings)

        return readings

    def read_one(self, sensor_id: str) -> Optional[float]:
        """Read a single sensor by ID."""
        if sensor_id in self._hwmon_paths:
            val = read_sysfs(self._hwmon_paths[sensor_id])
            if val is not None:
                try:
                    raw = float(val)
                    prefix = sensor_id.split(':')[-1]
                    for pfx, div in _HWMON_DIVISORS.items():
                        if prefix.startswith(pfx):
                            return raw / div
                    return raw
                except ValueError:
                    return None

        # For other sources, read_all is more efficient
        readings = self.read_all()
        return readings.get(sensor_id)

    # =========================================================================
    # Discovery methods
    # =========================================================================

    def _discover_hwmon(self):
        """Discover sensors from /sys/class/hwmon/."""
        hwmon_base = Path('/sys/class/hwmon')
        if not hwmon_base.exists():
            return

        # Track driver name occurrences to disambiguate duplicates (e.g., two spd5118 DIMMs)
        driver_counts: dict[str, int] = {}

        for hwmon_dir in sorted(hwmon_base.iterdir()):
            driver_name = read_sysfs(str(hwmon_dir / 'name')) or hwmon_dir.name

            # Disambiguate duplicate driver names with index suffix
            driver_counts[driver_name] = driver_counts.get(driver_name, 0) + 1
            if driver_counts[driver_name] > 1:
                driver_key = f"{driver_name}.{driver_counts[driver_name] - 1}"
            else:
                driver_key = driver_name

            for input_file in sorted(hwmon_dir.glob('*_input')):
                fname = input_file.name  # e.g., "temp1_input"
                input_name = fname.replace('_input', '')  # e.g., "temp1"

                # Determine type
                prefix = None
                for pfx in _HWMON_TYPES:
                    if input_name.startswith(pfx):
                        prefix = pfx
                        break
                if prefix is None:
                    continue

                category, unit = _HWMON_TYPES[prefix]
                sensor_id = f"hwmon:{driver_key}:{input_name}"

                # Try to get human-readable label
                label_path = hwmon_dir / f"{input_name}_label"
                label = read_sysfs(str(label_path))
                if label:
                    name = f"{driver_key} / {label}"
                else:
                    name = f"{driver_key} / {input_name}"

                self._sensors.append(SensorInfo(
                    id=sensor_id, name=name,
                    category=category, unit=unit, source='hwmon'
                ))
                self._hwmon_paths[sensor_id] = str(input_file)

    def _discover_nvidia(self):
        """Discover NVIDIA GPU sensors via pynvml."""
        if not NVML_AVAILABLE or pynvml is None:
            return

        try:
            count = pynvml.nvmlDeviceGetCount()
        except Exception:
            return

        for i in range(count):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                self._nvidia_handles[i] = handle
                gpu_name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(gpu_name, bytes):
                    gpu_name = gpu_name.decode()
            except Exception:
                continue

            prefix = f"nvidia:{i}"
            label = gpu_name if count == 1 else f"GPU {i} ({gpu_name})"

            sensors = [
                ('temp', f'{label} / Temperature', 'temperature', '°C'),
                ('gpu_util', f'{label} / GPU Utilization', 'usage', '%'),
                ('mem_util', f'{label} / Memory Utilization', 'usage', '%'),
                ('clock', f'{label} / Graphics Clock', 'clock', 'MHz'),
                ('mem_clock', f'{label} / Memory Clock', 'clock', 'MHz'),
                ('power', f'{label} / Power Draw', 'power', 'W'),
                ('vram_used', f'{label} / VRAM Used', 'other', 'MB'),
                ('vram_total', f'{label} / VRAM Total', 'other', 'MB'),
                ('fan', f'{label} / Fan Speed', 'fan', '%'),
            ]
            for metric, name, cat, unit in sensors:
                self._sensors.append(SensorInfo(
                    id=f"{prefix}:{metric}", name=name,
                    category=cat, unit=unit, source='nvidia'
                ))

    def _discover_psutil(self):
        """Discover psutil-based sensors."""
        if not PSUTIL_AVAILABLE:
            return

        psutil_sensors = [
            ('psutil:cpu_percent', 'CPU / Total Usage', 'usage', '%'),
            ('psutil:cpu_freq', 'CPU / Frequency', 'clock', 'MHz'),
            ('psutil:mem_percent', 'Memory / Usage', 'usage', '%'),
            ('psutil:mem_available', 'Memory / Available', 'other', 'MB'),
        ]
        for sid, name, cat, unit in psutil_sensors:
            self._sensors.append(SensorInfo(
                id=sid, name=name, category=cat, unit=unit, source='psutil'
            ))

    def _discover_rapl(self):
        """Discover Intel RAPL power sensors."""
        rapl_base = Path('/sys/class/powercap')
        if not rapl_base.exists():
            return

        for rapl_dir in sorted(rapl_base.glob('intel-rapl:*')):
            # Only top-level domains (not sub-zones like intel-rapl:0:0)
            if ':' in rapl_dir.name.split('intel-rapl:')[1]:
                continue

            energy_path = rapl_dir / 'energy_uj'
            name_path = rapl_dir / 'name'
            if not energy_path.exists():
                continue

            domain_name = read_sysfs(str(name_path)) or rapl_dir.name
            sensor_id = f"rapl:{domain_name}"

            self._sensors.append(SensorInfo(
                id=sensor_id,
                name=f"RAPL / {domain_name.title()} Power",
                category='power', unit='W', source='rapl'
            ))
            self._rapl_paths[sensor_id] = str(energy_path)

    def _discover_computed(self):
        """Register computed I/O rate sensors (disk, network)."""
        if not PSUTIL_AVAILABLE:
            return

        computed = [
            ('computed:disk_read', 'Disk / Read Rate', 'other', 'MB/s'),
            ('computed:disk_write', 'Disk / Write Rate', 'other', 'MB/s'),
            ('computed:disk_activity', 'Disk / Activity', 'usage', '%'),
            ('computed:net_up', 'Network / Upload Rate', 'other', 'KB/s'),
            ('computed:net_down', 'Network / Download Rate', 'other', 'KB/s'),
            ('computed:net_total_up', 'Network / Total Uploaded', 'other', 'MB'),
            ('computed:net_total_down', 'Network / Total Downloaded', 'other', 'MB'),
        ]
        for sid, name, cat, unit in computed:
            self._sensors.append(SensorInfo(
                id=sid, name=name, category=cat, unit=unit, source='computed'
            ))

    # =========================================================================
    # Reading methods
    # =========================================================================

    def _read_nvidia(self, readings: dict[str, float]):
        """Read all NVIDIA GPU sensors."""
        if not NVML_AVAILABLE or pynvml is None:
            return

        for i, handle in self._nvidia_handles.items():
            prefix = f"nvidia:{i}"
            try:
                readings[f"{prefix}:temp"] = float(
                    pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
            except Exception:
                pass
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                readings[f"{prefix}:gpu_util"] = float(util.gpu)
                readings[f"{prefix}:mem_util"] = float(util.memory)
            except Exception:
                pass
            try:
                readings[f"{prefix}:clock"] = float(
                    pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS))
            except Exception:
                pass
            try:
                readings[f"{prefix}:mem_clock"] = float(
                    pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM))
            except Exception:
                pass
            try:
                readings[f"{prefix}:power"] = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
            except Exception:
                pass
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                readings[f"{prefix}:vram_used"] = int(mem.used) / (1024 * 1024)
                readings[f"{prefix}:vram_total"] = int(mem.total) / (1024 * 1024)
            except Exception:
                pass
            try:
                readings[f"{prefix}:fan"] = float(pynvml.nvmlDeviceGetFanSpeed(handle))
            except Exception:
                pass

    def _read_psutil(self, readings: dict[str, float]):
        """Read psutil-based sensors."""
        if not PSUTIL_AVAILABLE:
            return

        try:
            readings['psutil:cpu_percent'] = psutil.cpu_percent(interval=None)
        except Exception:
            pass
        try:
            freq = psutil.cpu_freq()
            if freq:
                readings['psutil:cpu_freq'] = freq.current
        except Exception:
            pass
        try:
            mem = psutil.virtual_memory()
            readings['psutil:mem_percent'] = mem.percent
            readings['psutil:mem_available'] = mem.available / (1024 * 1024)
        except Exception:
            pass

    def _read_rapl(self, readings: dict[str, float]):
        """Read Intel RAPL power (energy delta → watts)."""
        now = time.monotonic()

        for sid, path in self._rapl_paths.items():
            val = read_sysfs(path)
            if val is None:
                continue
            try:
                energy_uj = float(val)
            except ValueError:
                continue

            if sid in self._rapl_prev:
                prev_energy, prev_time = self._rapl_prev[sid]
                dt = now - prev_time
                if dt > 0:
                    # energy_uj is in microjoules; convert delta to watts
                    power_w = (energy_uj - prev_energy) / (dt * 1_000_000)
                    if power_w >= 0:  # Handle counter wrap
                        readings[sid] = power_w

            self._rapl_prev[sid] = (energy_uj, now)

    def _read_computed(self, readings: dict[str, float]):
        """Read computed I/O rate sensors (disk, network)."""
        if not PSUTIL_AVAILABLE:
            return

        now = time.monotonic()

        # Disk I/O
        try:
            disk = psutil.disk_io_counters()
            if disk and self._disk_prev:
                prev_disk, prev_time = self._disk_prev
                dt = now - prev_time
                if dt > 0:
                    read_bytes = disk.read_bytes - prev_disk.read_bytes
                    write_bytes = disk.write_bytes - prev_disk.write_bytes
                    readings['computed:disk_read'] = read_bytes / (dt * 1024 * 1024)
                    readings['computed:disk_write'] = write_bytes / (dt * 1024 * 1024)
                    # Activity: approximate from busy_time if available
                    if hasattr(disk, 'busy_time') and hasattr(prev_disk, 'busy_time'):
                        busy_ms = disk.busy_time - prev_disk.busy_time
                        readings['computed:disk_activity'] = min(100.0, busy_ms / (dt * 10))
            if disk:
                self._disk_prev = (disk, now)
        except Exception:
            pass

        # Network I/O
        try:
            net = psutil.net_io_counters()
            if net:
                readings['computed:net_total_up'] = net.bytes_sent / (1024 * 1024)
                readings['computed:net_total_down'] = net.bytes_recv / (1024 * 1024)
                if self._net_prev:
                    prev_net, prev_time = self._net_prev
                    dt = now - prev_time
                    if dt > 0:
                        readings['computed:net_up'] = (
                            (net.bytes_sent - prev_net.bytes_sent) / (dt * 1024))
                        readings['computed:net_down'] = (
                            (net.bytes_recv - prev_net.bytes_recv) / (dt * 1024))
                self._net_prev = (net, now)
        except Exception:
            pass


# =============================================================================
# Default sensor mapping: maps old get_all_metrics() keys to sensor IDs
# =============================================================================

# Built lazily on first call to map_defaults()
_DEFAULT_MAP: Optional[dict[str, str]] = None


def map_defaults(enumerator: SensorEnumerator) -> dict[str, str]:
    """Build a mapping from legacy metric keys to sensor IDs.

    Returns dict like {'cpu_temp': 'hwmon:coretemp:temp1', ...}.
    Used for backward compatibility with overlay renderer.
    """
    global _DEFAULT_MAP
    if _DEFAULT_MAP is not None:
        return _DEFAULT_MAP

    sensors = enumerator.get_sensors()
    mapping: dict[str, str] = {}

    def _find_first(source: str = '', name_contains: str = '',
                    category: str = '') -> Optional[str]:
        for s in sensors:
            if source and s.source != source:
                continue
            if category and s.category != category:
                continue
            if name_contains and name_contains.lower() not in s.name.lower():
                continue
            return s.id
        return None

    # CPU
    mapping['cpu_temp'] = (
        _find_first(source='hwmon', name_contains='Package') or
        _find_first(source='hwmon', name_contains='Tctl') or
        _find_first(source='hwmon', name_contains='coretemp') or
        _find_first(source='hwmon', name_contains='k10temp') or
        ''
    )
    mapping['cpu_percent'] = 'psutil:cpu_percent'
    mapping['cpu_freq'] = 'psutil:cpu_freq'
    mapping['cpu_power'] = _find_first(source='rapl') or ''

    # GPU
    mapping['gpu_temp'] = _find_first(source='nvidia', name_contains='Temperature') or ''
    mapping['gpu_usage'] = _find_first(source='nvidia', name_contains='GPU Utilization') or ''
    mapping['gpu_clock'] = _find_first(source='nvidia', name_contains='Graphics Clock') or ''
    mapping['gpu_power'] = _find_first(source='nvidia', name_contains='Power Draw') or ''

    # Memory
    mapping['mem_temp'] = _find_first(source='hwmon', name_contains='spd') or ''
    mapping['mem_percent'] = 'psutil:mem_percent'
    mapping['mem_available'] = 'psutil:mem_available'
    mapping['mem_clock'] = ''  # No reliable Linux source

    # Disk
    mapping['disk_temp'] = (
        _find_first(source='hwmon', name_contains='nvme') or
        _find_first(source='hwmon', name_contains='drivetemp') or
        ''
    )
    mapping['disk_read'] = 'computed:disk_read'
    mapping['disk_write'] = 'computed:disk_write'
    mapping['disk_activity'] = 'computed:disk_activity'

    # Network
    mapping['net_up'] = 'computed:net_up'
    mapping['net_down'] = 'computed:net_down'
    mapping['net_total_up'] = 'computed:net_total_up'
    mapping['net_total_down'] = 'computed:net_total_down'

    # Fans — assign first N fans found
    fan_sensors = [s for s in sensors if s.category == 'fan' and s.source == 'hwmon']
    fan_keys = ['fan_cpu', 'fan_gpu', 'fan_ssd', 'fan_sys2']
    for i, key in enumerate(fan_keys):
        mapping[key] = fan_sensors[i].id if i < len(fan_sensors) else ''

    # Remove empty mappings
    _DEFAULT_MAP = {k: v for k, v in mapping.items() if v}
    return _DEFAULT_MAP
