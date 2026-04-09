"""
Linux hardware sensor discovery and reading.

Replaces Windows HWiNFO64 shared memory with native Linux sensor sources:
- hwmon: /sys/class/hwmon/* (temperatures, fans, voltages, power, frequency)
- NVIDIA GPU: nvidia-ml-py / pynvml (temperature, utilization, clock, power, VRAM, fan)
- DRM: /sys/class/drm/card* (AMD gpu_busy_percent, Intel gt_cur_freq_mhz)
- psutil: CPU usage/frequency, memory, disk I/O, network I/O
- Intel RAPL: CPU package power via /sys/class/powercap/

Sensor IDs follow the format:
    hwmon:{driver}:{input}    e.g., hwmon:coretemp:temp1
    nvidia:{gpu}:{metric}     e.g., nvidia:0:temp
    drm:{card}:{metric}       e.g., drm:card0:gpu_busy
    psutil:{metric}           e.g., psutil:cpu_percent
    rapl:{domain}             e.g., rapl:package-0
    computed:{metric}         e.g., computed:disk_read
"""

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import psutil

from trcc.adapters.infra.data_repository import SysUtils
from trcc.core.models import SensorInfo
from trcc.core.ports import SensorEnumerator as SensorEnumeratorABC

log = logging.getLogger(__name__)

try:
    import pynvml  # pyright: ignore[reportMissingImports]
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except ImportError:
    pynvml = None  # type: ignore[assignment]
    NVML_AVAILABLE = False
    log.debug("pynvml not installed — NVIDIA GPU sensors unavailable. Install nvidia-ml-py to enable.")
except Exception as e:
    pynvml = None  # type: ignore[assignment]
    NVML_AVAILABLE = False
    log.warning("pynvml init failed — NVIDIA GPU sensors unavailable: %s", e)


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

# GPU vendor IDs (PCI sysfs)
_GPU_VENDOR_NVIDIA = '10de'
_GPU_VENDOR_AMD = '1002'
_GPU_VENDOR_INTEL = '8086'


def _detect_gpu_vendors() -> list[str]:
    """Detect GPU vendors via PCI sysfs, discrete first.

    Scans /sys/bus/pci/devices/*/class for VGA (0x0300) and 3D (0x0302)
    controllers, returns vendor ID strings ordered: NVIDIA > AMD > Intel.
    """
    pci_base = Path('/sys/bus/pci/devices')
    if not pci_base.exists():
        return []

    vendors: list[str] = []
    for dev_dir in pci_base.iterdir():
        class_path = dev_dir / 'class'
        vendor_path = dev_dir / 'vendor'
        if not class_path.exists() or not vendor_path.exists():
            continue
        try:
            pci_class = class_path.read_text().strip()
            if not (pci_class.startswith('0x0300') or pci_class.startswith('0x0302')):
                continue
            vendor = vendor_path.read_text().strip().removeprefix('0x')
            if vendor not in vendors:
                vendors.append(vendor)
        except OSError:
            continue

    # Prefer discrete (NVIDIA/AMD) over integrated (Intel)
    priority = {_GPU_VENDOR_NVIDIA: 0, _GPU_VENDOR_AMD: 1, _GPU_VENDOR_INTEL: 2}
    vendors.sort(key=lambda v: priority.get(v, 99))
    return vendors


def detect_gpus() -> list[dict[str, str]]:
    """Detect all GPUs with PCI slot, vendor, and human-readable name.

    Returns list of dicts: {'pci_slot', 'vendor', 'name', 'driver_key', 'drm_card'}.
    Name is resolved from the kernel's DRM device name or lspci output.
    """
    import subprocess

    pci_base = Path('/sys/bus/pci/devices')
    drm_base = Path('/sys/class/drm')
    hwmon_base = Path('/sys/class/hwmon')
    if not pci_base.exists():
        return []

    # Build PCI slot → lspci name mapping
    pci_names: dict[str, str] = {}
    try:
        result = subprocess.run(
            ['lspci', '-mm'], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            import re
            for line in result.stdout.splitlines():
                # lspci -mm fields (quoted):
                #   0=class, 1=vendor, 2=device, 3=SVendor (may be -), 4=SDevice
                # e.g.: 06:00.0 "VGA..." "AMD..." "Navi 10 [Radeon...]" -rc1 -p00 "Sapphire..." "PULSE RX 7900 XTX"
                match = re.match(r'^(\S+)\s+', line)
                if match:
                    slot = match.group(1)
                    quoted = re.findall(r'"([^"]*)"', line)
                    # Prefer subsystem device name (index 4), fall back to device (index 2)
                    if len(quoted) >= 5 and quoted[4]:
                        pci_names[slot] = quoted[4]
                    elif len(quoted) >= 3:
                        pci_names[slot] = quoted[2]
    except (FileNotFoundError, OSError):
        pass

    # Build PCI device path → DRM card name mapping
    pci_to_drm: dict[str, str] = {}
    if drm_base.exists():
        for card_dir in sorted(drm_base.glob('card[0-9]*')):
            if '-' in card_dir.name:
                continue
            try:
                dev_path = (card_dir / 'device').resolve()
                pci_to_drm[str(dev_path)] = card_dir.name
            except OSError:
                continue

    # Build PCI device path → hwmon driver_key mapping
    pci_to_driver_key: dict[str, str] = {}
    if hwmon_base.exists():
        driver_counts: dict[str, int] = {}
        for hwmon_dir in sorted(hwmon_base.iterdir()):
            driver_name = SysUtils.read_sysfs(str(hwmon_dir / 'name')) or hwmon_dir.name
            driver_counts[driver_name] = driver_counts.get(driver_name, 0) + 1
            if driver_counts[driver_name] > 1:
                driver_key = f"{driver_name}.{driver_counts[driver_name] - 1}"
            else:
                driver_key = driver_name
            try:
                dev_path = str((hwmon_dir / 'device').resolve())
                pci_to_driver_key[dev_path] = driver_key
            except OSError:
                continue

    gpus: list[dict[str, str]] = []
    for dev_dir in sorted(pci_base.iterdir()):
        class_path = dev_dir / 'class'
        vendor_path = dev_dir / 'vendor'
        if not class_path.exists() or not vendor_path.exists():
            continue
        try:
            pci_class = class_path.read_text().strip()
            if not (pci_class.startswith('0x0300') or pci_class.startswith('0x0302')):
                continue
            vendor = vendor_path.read_text().strip().removeprefix('0x')
            pci_slot = dev_dir.name  # e.g. "0000:0f:00.0"
            dev_resolved = str(dev_dir.resolve())

            # Short slot for lspci lookup (strip domain prefix)
            short_slot = pci_slot.split(':', 1)[1] if ':' in pci_slot else pci_slot
            name = pci_names.get(short_slot, f"GPU [{vendor}] at {pci_slot}")

            gpus.append({
                'pci_slot': pci_slot,
                'vendor': vendor,
                'name': name,
                'driver_key': pci_to_driver_key.get(dev_resolved, ''),
                'drm_card': pci_to_drm.get(dev_resolved, ''),
            })
        except OSError:
            continue

    return gpus


class SensorEnumerator(SensorEnumeratorABC):
    """Discovers and reads all available hardware sensors on Linux."""

    def __init__(self):
        self._sensors: list[SensorInfo] = []
        self._hwmon_paths: dict[str, str] = {}   # sensor_id -> sysfs path
        self._nvidia_handles: dict[int, object] = {}  # gpu_index -> handle
        self._drm_paths: dict[str, str] = {}     # sensor_id -> drm sysfs path
        self._rapl_paths: dict[str, str] = {}     # sensor_id -> energy_uj path
        self._rapl_prev: dict[str, tuple[float, float]] = {}  # id -> (energy, time)
        self._net_prev: Optional[tuple] = None     # (counters, time)
        self._disk_prev: Optional[tuple] = None    # (counters, time)
        # Background polling thread — avoids blocking Qt main thread
        self._cached_readings: dict[str, float] = {}
        self._cache_lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._poll_interval: float = 1.0  # seconds, driven by user's refresh setting

    def discover(self) -> list[SensorInfo]:
        """Scan the system for all available sensors. Call once at startup."""
        self._sensors = []
        self._hwmon_paths = {}
        self._nvidia_handles = {}
        self._drm_paths = {}
        self._rapl_paths = {}

        self._discover_hwmon()
        self._discover_nvidia()
        self._discover_drm()
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
        """Return cached sensor readings (non-blocking).

        Background thread refreshes the cache every ~900ms.  If no background
        thread is running (e.g. CLI usage), falls back to a synchronous read.
        """
        with self._cache_lock:
            if self._cached_readings:
                return dict(self._cached_readings)
        # Fallback: no cache yet (first call or no bg thread)
        return self._read_all_sync()

    def set_poll_interval(self, seconds: float) -> None:
        """Set background poll interval (driven by user's data refresh setting)."""
        self._poll_interval = max(0.5, seconds)

    def start_polling(self) -> None:
        """Start background sensor polling thread."""
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="sensor-poll")
        self._poll_thread.start()
        log.debug("Sensor polling thread started (interval=%.1fs)", self._poll_interval)

    def stop_polling(self) -> None:
        """Stop background sensor polling thread."""
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2)
            self._poll_thread = None
            log.debug("Sensor polling thread stopped")

    def _poll_loop(self) -> None:
        """Background loop: read sensors, update cache, sleep."""
        while not self._poll_stop.is_set():
            try:
                readings = self._read_all_sync()
                with self._cache_lock:
                    self._cached_readings = readings
            except Exception:
                log.debug("Sensor poll error", exc_info=True)
            self._poll_stop.wait(self._poll_interval)

    def _read_all_sync(self) -> dict[str, float]:
        """Read current values for ALL discovered sensors (blocking)."""
        readings: dict[str, float] = {}

        # hwmon sensors
        for sid, path in self._hwmon_paths.items():
            val = SysUtils.read_sysfs(path)
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

        # DRM sensors (AMD/Intel GPU)
        self._read_drm(readings)

        # Computed I/O rates
        self._read_computed(readings)

        return readings

    def read_one(self, sensor_id: str) -> Optional[float]:
        """Read a single sensor by ID."""
        if sensor_id in self._hwmon_paths:
            val = SysUtils.read_sysfs(self._hwmon_paths[sensor_id])
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

        if sensor_id in self._drm_paths:
            val = SysUtils.read_sysfs(self._drm_paths[sensor_id])
            if val is not None:
                try:
                    return float(val)
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
            driver_name = SysUtils.read_sysfs(str(hwmon_dir / 'name')) or hwmon_dir.name

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
                label = SysUtils.read_sysfs(str(label_path))
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
        except Exception as e:
            log.warning("nvmlDeviceGetCount failed — NVIDIA GPU sensors unavailable: %s", e)
            return

        for i in range(count):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                self._nvidia_handles[i] = handle
                gpu_name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(gpu_name, bytes):
                    gpu_name = gpu_name.decode()
            except Exception as e:
                log.warning("NVIDIA GPU %d handle/name failed — skipping: %s", i, e)
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

    def _discover_drm(self):
        """Discover GPU sensors from /sys/class/drm/ (AMD utilization, Intel freq)."""
        drm_base = Path('/sys/class/drm')
        if not drm_base.exists():
            return

        for card_dir in sorted(drm_base.glob('card[0-9]*')):
            if '-' in card_dir.name:  # skip card0-DP-1 etc.
                continue

            vendor_path = card_dir / 'device' / 'vendor'
            if not vendor_path.exists():
                continue
            try:
                vendor = vendor_path.read_text().strip().removeprefix('0x')
            except OSError:
                continue

            card = card_dir.name  # e.g. "card0"

            # AMD: gpu_busy_percent (utilization %)
            if vendor == _GPU_VENDOR_AMD:
                busy_path = card_dir / 'device' / 'gpu_busy_percent'
                if busy_path.exists():
                    sid = f"drm:{card}:gpu_busy"
                    self._sensors.append(SensorInfo(
                        id=sid, name=f"GPU / Utilization ({card})",
                        category='usage', unit='%', source='drm',
                    ))
                    self._drm_paths[sid] = str(busy_path)

            # Intel: gt_cur_freq_mhz (graphics clock)
            if vendor == _GPU_VENDOR_INTEL:
                freq_path = card_dir / 'gt_cur_freq_mhz'
                if freq_path.exists():
                    sid = f"drm:{card}:freq"
                    self._sensors.append(SensorInfo(
                        id=sid, name=f"GPU / Frequency ({card})",
                        category='clock', unit='MHz', source='drm',
                    ))
                    self._drm_paths[sid] = str(freq_path)

    def _discover_psutil(self):
        """Discover psutil-based sensors."""

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

            domain_name = SysUtils.read_sysfs(str(name_path)) or rapl_dir.name
            sensor_id = f"rapl:{domain_name}"

            self._sensors.append(SensorInfo(
                id=sensor_id,
                name=f"RAPL / {domain_name.title()} Power",
                category='power', unit='W', source='rapl'
            ))
            self._rapl_paths[sensor_id] = str(energy_path)

    def _discover_computed(self):
        """Register computed I/O rate sensors (disk, network)."""

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
            except Exception as e:
                log.debug("nvidia:%d temp read failed: %s", i, e)
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                readings[f"{prefix}:gpu_util"] = float(util.gpu)
                readings[f"{prefix}:mem_util"] = float(util.memory)
            except Exception as e:
                log.debug("nvidia:%d util read failed: %s", i, e)
            try:
                readings[f"{prefix}:clock"] = float(
                    pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS))
            except Exception as e:
                log.debug("nvidia:%d clock read failed: %s", i, e)
            try:
                readings[f"{prefix}:mem_clock"] = float(
                    pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM))
            except Exception as e:
                log.debug("nvidia:%d mem_clock read failed: %s", i, e)
            try:
                readings[f"{prefix}:power"] = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
            except Exception as e:
                log.debug("nvidia:%d power read failed: %s", i, e)
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                readings[f"{prefix}:vram_used"] = int(mem.used) / (1024 * 1024)
                readings[f"{prefix}:vram_total"] = int(mem.total) / (1024 * 1024)
            except Exception as e:
                log.debug("nvidia:%d vram read failed: %s", i, e)
            try:
                readings[f"{prefix}:fan"] = float(pynvml.nvmlDeviceGetFanSpeed(handle))
            except Exception as e:
                log.debug("nvidia:%d fan read failed: %s", i, e)

    def _read_drm(self, readings: dict[str, float]):
        """Read DRM sysfs sensors (AMD gpu_busy, Intel freq)."""
        for sid, path in self._drm_paths.items():
            val = SysUtils.read_sysfs(path)
            if val is not None:
                try:
                    readings[sid] = float(val)
                except ValueError:
                    pass

    _cpu_freq_cache: float = 0.0
    _cpu_freq_time: float = 0.0
    _CPU_FREQ_TTL: float = 10.0  # cpu_freq barely changes; cache 10s

    def _read_psutil(self, readings: dict[str, float]):
        """Read psutil-based sensors."""

        try:
            readings['psutil:cpu_percent'] = psutil.cpu_percent(interval=None)
        except Exception:
            pass
        try:
            now = time.monotonic()
            if now - self._cpu_freq_time >= self._CPU_FREQ_TTL:
                freq = psutil.cpu_freq()
                if freq:
                    self._cpu_freq_cache = freq.current
                else:
                    self._cpu_freq_cache = 0.0
                self._cpu_freq_time = now
            if self._cpu_freq_cache > 0:
                readings['psutil:cpu_freq'] = self._cpu_freq_cache
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
            val = SysUtils.read_sysfs(path)
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

    # =========================================================================
    # GPU ranking — pick best by VRAM
    # =========================================================================

    def _best_gpu(self) -> dict:
        """Find the GPU with the most VRAM across all vendors.

        Returns {'vendor': str, 'nvidia_idx': int|None, 'drm_card': str,
                 'hwmon_driver': str, 'vram': int}.
        Empty dict if no GPU found.
        """
        best: dict = {}
        best_vram = 0

        # NVIDIA — query VRAM via pynvml handles
        for idx, handle in self._nvidia_handles.items():
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)  # type: ignore[union-attr]
                vram = int(mem.total)
                log.debug("nvidia:%d VRAM=%d MB", idx, vram // (1024 * 1024))
                if vram > best_vram:
                    best_vram = vram
                    best = {'vendor': 'nvidia', 'nvidia_idx': idx,
                            'drm_card': '', 'hwmon_driver': '', 'vram': vram}
            except Exception:
                continue

        # AMD — query VRAM via sysfs
        drm_base = Path('/sys/class/drm')
        for card_dir in sorted(drm_base.glob('card[0-9]*')):
            if '-' in card_dir.name:
                continue
            try:
                vendor = (card_dir / 'device' / 'vendor').read_text().strip().removeprefix('0x')
                if vendor != _GPU_VENDOR_AMD:
                    continue
                vram_path = card_dir / 'device' / 'mem_info_vram_total'
                if not vram_path.exists():
                    continue
                vram = int(vram_path.read_text().strip())
                log.debug("%s VRAM=%d MB", card_dir.name, vram // (1024 * 1024))
                if vram > best_vram:
                    best_vram = vram
                    hwmon = self._drm_card_to_hwmon_driver(card_dir.name)
                    best = {'vendor': 'amd', 'nvidia_idx': None,
                            'drm_card': card_dir.name,
                            'hwmon_driver': hwmon or 'amdgpu', 'vram': vram}
            except (OSError, ValueError):
                continue

        if best:
            log.info("Best GPU: %s (VRAM=%d MB)", best.get('vendor'), best_vram // (1024 * 1024))
        return best

    def _drm_card_to_hwmon_driver(self, card_name: str) -> str:
        """Resolve DRM card name → hwmon driver key for sensor routing."""
        drm_base = Path('/sys/class/drm')
        hwmon_base = Path('/sys/class/hwmon')
        try:
            dev_path = str((drm_base / card_name / 'device').resolve())
        except OSError:
            return ''
        for hwmon_dir in sorted(hwmon_base.iterdir()):
            try:
                if str((hwmon_dir / 'device').resolve()) == dev_path:
                    return SysUtils.read_sysfs(str(hwmon_dir / 'name')) or ''
            except OSError:
                continue
        return ''

    # =========================================================================
    # Default sensor mapping (legacy compat)
    # =========================================================================

    _default_map: Optional[dict[str, str]] = None

    def map_defaults(self) -> dict[str, str]:
        """Build a mapping from legacy metric keys to sensor IDs.

        Returns dict like {'cpu_temp': 'hwmon:coretemp:temp1', ...}.
        Used for backward compatibility with overlay renderer.
        Cached after first call.
        """
        if SensorEnumerator._default_map is not None:
            return SensorEnumerator._default_map

        sensors = self.get_sensors()
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

        # GPU — when gpu_slots are configured (cycle mode), use the active slot.
        # Otherwise fall back to _best_gpu() auto-detection by VRAM.
        from trcc.conf import load_config
        config = load_config()
        gpu_slots = config.get('gpu_slots', {})
        if gpu_slots:
            active_slot = config.get('gpu_active_slot', next(iter(gpu_slots), ''))
            preferred_pci = gpu_slots.get(active_slot)
            gpu: dict = {}
            if preferred_pci:
                for g in detect_gpus():
                    if g['pci_slot'] == preferred_pci:
                        # Normalise detect_gpus() dict to _best_gpu() format:
                        # vendor '1002' → 'amd', driver_key → hwmon_driver
                        v = g.get('vendor', '')
                        if v == _GPU_VENDOR_AMD:
                            gpu = {'vendor': 'amd', 'nvidia_idx': None,
                                   'drm_card': g.get('drm_card', ''),
                                   'hwmon_driver': g.get('driver_key', 'amdgpu')}
                        elif v == _GPU_VENDOR_NVIDIA:
                            gpu = {'vendor': 'nvidia', 'nvidia_idx': 0,
                                   'drm_card': g.get('drm_card', ''),
                                   'hwmon_driver': g.get('driver_key', '')}
                        elif v == _GPU_VENDOR_INTEL:
                            gpu = {'vendor': 'intel', 'nvidia_idx': None,
                                   'drm_card': g.get('drm_card', ''),
                                   'hwmon_driver': g.get('driver_key', 'i915')}
                        break
            if not gpu:
                gpu = self._best_gpu()
        else:
            gpu = self._best_gpu()

        if gpu.get('vendor') == 'nvidia':
            prefix = f"nvidia:{gpu['nvidia_idx']}"
            mapping['gpu_temp'] = f"{prefix}:temp"
            mapping['gpu_usage'] = f"{prefix}:gpu_util"
            mapping['gpu_clock'] = f"{prefix}:clock"
            mapping['gpu_power'] = f"{prefix}:power"
        elif gpu.get('vendor') == 'amd':
            drv = gpu.get('hwmon_driver', 'amdgpu')
            card = gpu.get('drm_card', '')
            mapping['gpu_temp'] = _find_first(source='hwmon', name_contains=drv, category='temperature') or ''
            mapping['gpu_usage'] = _find_first(source='drm', name_contains=card, category='usage') or '' if card else ''
            mapping['gpu_clock'] = _find_first(source='hwmon', name_contains=drv, category='clock') or ''
            mapping['gpu_power'] = _find_first(source='hwmon', name_contains=drv, category='power') or ''
        elif gpu.get('vendor') == 'intel':
            mapping['gpu_temp'] = _find_first(source='hwmon', name_contains='i915', category='temperature') or ''
            mapping['gpu_usage'] = ''
            mapping['gpu_clock'] = _find_first(source='drm', category='clock') or ''
            mapping['gpu_power'] = _find_first(source='hwmon', name_contains='i915', category='power') or ''
        else:
            mapping['gpu_temp'] = ''
            mapping['gpu_usage'] = ''
            mapping['gpu_clock'] = ''
            mapping['gpu_power'] = ''

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

        # Fans — match by hwmon label keywords, fall back to positional
        fan_sensors = [s for s in sensors if s.category == 'fan' and s.source == 'hwmon']
        _fan_slots = [
            ('fan_cpu', ('cpu',)),
            ('fan_gpu', ('gpu',)),
            ('fan_ssd', ('ssd', 'nvme', 'm.2')),
            ('fan_sys2', ('sys', 'chassis', 'case', 'pump')),
        ]
        fan_mapped: dict[str, str] = {}
        unmatched_fans: list[SensorInfo] = []
        for sensor in fan_sensors:
            name_lower = sensor.name.lower()
            matched = False
            for key, keywords in _fan_slots:
                if key not in fan_mapped and any(kw in name_lower for kw in keywords):
                    fan_mapped[key] = sensor.id
                    matched = True
                    break
            if not matched:
                unmatched_fans.append(sensor)
        # Fill remaining empty slots positionally
        empty_keys = [k for k, _ in _fan_slots if k not in fan_mapped]
        for sensor, key in zip(unmatched_fans, empty_keys):
            fan_mapped[key] = sensor.id
        mapping.update(fan_mapped)

        # Remove empty mappings and cache
        SensorEnumerator._default_map = {k: v for k, v in mapping.items() if v}
        return SensorEnumerator._default_map


# Backward-compat alias
def map_defaults(enumerator: SensorEnumerator) -> dict[str, str]:
    """Legacy wrapper — delegates to enumerator.map_defaults()."""
    return enumerator.map_defaults()
