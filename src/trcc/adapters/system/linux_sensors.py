"""Linux SensorEnumerator — hwmon, pynvml, DRM sysfs, psutil, Intel RAPL.

Lives in its own module because sensor discovery is a distinct concern
from the LinuxPlatform interface. Imported and re-exported by
`linux_platform` so existing call sites (`from … import SensorEnumerator`)
keep working.
"""
from __future__ import annotations

import logging
import time
from operator import itemgetter
from pathlib import Path

import psutil

from trcc.adapters.infra.data_repository import SysUtils
from trcc.adapters.system._base import (
    NVML_EXC,
    PSUTIL_EXC,
    SensorEnumeratorBase,
    _ensure_nvml,
    pynvml,
)
from trcc.core.models import SensorInfo

log = logging.getLogger(__name__)


# Hwmon input-name prefixes mapped to (category, unit). Anything outside
# this set is ignored during discovery.
_HWMON_TYPES = {
    'temp': ('temperature', '°C'),
    'fan': ('fan', 'RPM'),
    'in': ('voltage', 'V'),
    'power': ('power', 'W'),
    'freq': ('clock', 'MHz'),
}
# Divisors applied to raw sysfs values to produce the unit above.
_HWMON_DIVISORS = {
    'temp': 1000.0,
    'fan': 1.0,
    'in': 1000.0,
    'power': 1000000.0,
    'freq': 1000000.0,
}

_GPU_VENDOR_NVIDIA = '10de'
_GPU_VENDOR_AMD = '1002'
_GPU_VENDOR_INTEL = '8086'


def _detect_gpu_vendors() -> list[str]:
    """Detect GPU vendors via PCI sysfs, discrete first."""
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
            if not (pci_class.startswith(('0x0300', '0x0302'))):
                continue
            vendor = vendor_path.read_text().strip().removeprefix('0x')
            if vendor not in vendors:
                vendors.append(vendor)
        except OSError:
            continue

    priority = {_GPU_VENDOR_NVIDIA: 0, _GPU_VENDOR_AMD: 1, _GPU_VENDOR_INTEL: 2}

    def _vendor_rank(vendor: str) -> int:
        return priority.get(vendor, 99)

    vendors.sort(key=_vendor_rank)
    return vendors


class SensorEnumerator(SensorEnumeratorBase):
    """Linux hardware sensor discovery and reading.

    Sources: hwmon, pynvml, DRM sysfs, psutil, Intel RAPL.
    """

    def __init__(self) -> None:
        super().__init__()
        self._hwmon_paths: dict[str, str] = {}
        self._drm_paths: dict[str, str] = {}
        self._rapl_paths: dict[str, str] = {}
        self._rapl_prev: dict[str, tuple[float, float]] = {}

    def discover(self) -> list[SensorInfo]:
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

    def _discover_psutil(self) -> None:
        self._discover_psutil_base()
        self._sensors.append(
            SensorInfo('psutil:mem_available', 'Memory / Available', 'other', 'MB', 'psutil'),
        )

    def _discover_nvidia(self) -> None:
        if not _ensure_nvml() or pynvml is None:
            return
        try:
            count = pynvml.nvmlDeviceGetCount()
        except NVML_EXC as e:
            log.debug("nvmlDeviceGetCount failed: %s", e)
            return

        for i in range(count):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                gpu_name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(gpu_name, bytes):
                    gpu_name = gpu_name.decode()
                gpu_name = str(gpu_name)
            except NVML_EXC as e:
                log.warning("NVIDIA GPU %d handle/name failed — skipping: %s", i, e)
                continue

            self._nvidia_handles[i] = handle
            prefix = f"nvidia:{i}"
            label = gpu_name if count == 1 else f"GPU {i} ({gpu_name})"

            for metric, name, cat, unit in [
                ('temp', f'{label} / Temperature', 'temperature', '°C'),
                ('gpu_util', f'{label} / GPU Utilization', 'usage', '%'),
                ('mem_util', f'{label} / Memory Utilization', 'usage', '%'),
                ('clock', f'{label} / Graphics Clock', 'clock', 'MHz'),
                ('mem_clock', f'{label} / Memory Clock', 'clock', 'MHz'),
                ('power', f'{label} / Power Draw', 'power', 'W'),
                ('vram_used', f'{label} / VRAM Used', 'other', 'MB'),
                ('vram_total', f'{label} / VRAM Total', 'other', 'MB'),
                ('fan', f'{label} / Fan Speed', 'fan', '%'),
            ]:
                self._sensors.append(SensorInfo(
                    id=f"{prefix}:{metric}", name=name,
                    category=cat, unit=unit, source='nvidia',
                ))

    def _discover_hwmon(self) -> None:
        hwmon_base = Path('/sys/class/hwmon')
        if not hwmon_base.exists():
            return

        driver_counts: dict[str, int] = {}

        for hwmon_dir in sorted(hwmon_base.iterdir()):
            driver_name = SysUtils.read_sysfs(str(hwmon_dir / 'name')) or hwmon_dir.name

            driver_counts[driver_name] = driver_counts.get(driver_name, 0) + 1
            if driver_counts[driver_name] > 1:
                driver_key = f"{driver_name}.{driver_counts[driver_name] - 1}"
            else:
                driver_key = driver_name

            for input_file in sorted(hwmon_dir.glob('*_input')):
                fname = input_file.name
                input_name = fname.replace('_input', '')

                prefix = None
                for pfx in _HWMON_TYPES:
                    if input_name.startswith(pfx):
                        prefix = pfx
                        break
                if prefix is None:
                    continue

                category, unit = _HWMON_TYPES[prefix]
                label_path = hwmon_dir / f'{input_name}_label'
                if (label := SysUtils.read_sysfs(str(label_path))):
                    name = f'{driver_key} / {label}'
                else:
                    name = f'{driver_key} / {input_name}'

                sensor_id = f'hwmon:{driver_key}:{input_name}'
                self._sensors.append(SensorInfo(
                    id=sensor_id, name=name,
                    category=category, unit=unit, source='hwmon',
                ))
                self._hwmon_paths[sensor_id] = str(input_file)

    def _discover_drm(self) -> None:
        drm_base = Path('/sys/class/drm')
        if not drm_base.exists():
            return

        for card_dir in sorted(drm_base.glob('card[0-9]*')):
            if '-' in card_dir.name:
                continue
            vendor_path = card_dir / 'device' / 'vendor'
            if not vendor_path.exists():
                continue
            try:
                vendor = vendor_path.read_text().strip().removeprefix('0x')
            except OSError:
                continue

            card = card_dir.name

            if vendor == _GPU_VENDOR_AMD:
                busy_path = card_dir / 'device' / 'gpu_busy_percent'
                if busy_path.exists():
                    sid = f"drm:{card}:gpu_busy"
                    self._sensors.append(SensorInfo(
                        id=sid, name=f"GPU / Utilization ({card})",
                        category='usage', unit='%', source='drm',
                    ))
                    self._drm_paths[sid] = str(busy_path)

            if vendor == _GPU_VENDOR_INTEL:
                freq_path = card_dir / 'gt_cur_freq_mhz'
                if freq_path.exists():
                    sid = f"drm:{card}:freq"
                    self._sensors.append(SensorInfo(
                        id=sid, name=f"GPU / Frequency ({card})",
                        category='clock', unit='MHz', source='drm',
                    ))
                    self._drm_paths[sid] = str(freq_path)

    def _discover_rapl(self) -> None:
        rapl_base = Path('/sys/class/powercap')
        try:
            if not rapl_base.exists():
                return
            rapl_dirs = sorted(rapl_base.glob('intel-rapl:*'))
        except OSError as e:
            # /sys/class/powercap exists but isn't traversable for this user
            # (pipx / non-root install w/o `trcc setup-rapl` run yet).
            # Skip RAPL silently — without the setup step the sensor
            # values would be unreadable anyway.  Issue #139.
            log.debug("RAPL discovery skipped: %s", e)
            return

        for rapl_dir in rapl_dirs:
            if ':' in rapl_dir.name.split('intel-rapl:')[1]:
                continue
            energy_path = rapl_dir / 'energy_uj'
            name_path = rapl_dir / 'name'
            try:
                if not energy_path.exists():
                    continue
            except OSError as e:
                # Same permission problem at the per-domain energy_uj
                # level — `Path.exists()` calls os.stat which raises
                # PermissionError when the file isn't readable.
                log.debug("RAPL %s skipped: %s", rapl_dir.name, e)
                continue

            domain_name = SysUtils.read_sysfs(str(name_path)) or rapl_dir.name
            sensor_id = f"rapl:{domain_name}"
            self._sensors.append(SensorInfo(
                id=sensor_id,
                name=f"RAPL / {domain_name.title()} Power",
                category='power', unit='W', source='rapl',
            ))
            self._rapl_paths[sensor_id] = str(energy_path)

    # ── Polling ───────────────────────────────────────────────────────

    def _poll_once(self) -> None:
        readings: dict[str, float] = {}

        for sid, path in self._hwmon_paths.items():
            if (val := SysUtils.read_sysfs(path)) is not None:
                try:
                    raw = float(val)
                    prefix = sid.split(':')[-1]
                    for pfx, div in _HWMON_DIVISORS.items():
                        if prefix.startswith(pfx):
                            readings[sid] = raw / div
                            break
                    else:
                        readings[sid] = raw
                except ValueError:
                    pass

        self._poll_nvidia(readings)
        self._poll_psutil(readings)
        self._poll_rapl(readings)

        for sid, path in self._drm_paths.items():
            if (val := SysUtils.read_sysfs(path)) is not None:
                try:
                    readings[sid] = float(val)
                except ValueError:
                    pass

        self._poll_computed_io(readings)
        self._poll_datetime(readings)

        with self._lock:
            self._readings = readings

    _cpu_freq_cache: float = 0.0
    _cpu_freq_time: float = 0.0
    _CPU_FREQ_TTL: float = 10.0

    def _poll_psutil(self, readings: dict[str, float]) -> None:
        try:
            readings['psutil:cpu_percent'] = psutil.cpu_percent(interval=None)
        except PSUTIL_EXC as e:
            log.debug("psutil.cpu_percent failed: %s", e)
        try:
            now = time.monotonic()
            if now - self._cpu_freq_time >= self._CPU_FREQ_TTL:
                if (freq := psutil.cpu_freq()):
                    self._cpu_freq_cache = freq.current
                else:
                    self._cpu_freq_cache = 0.0
                self._cpu_freq_time = now
            if self._cpu_freq_cache > 0:
                readings['psutil:cpu_freq'] = self._cpu_freq_cache
        except PSUTIL_EXC as e:
            log.debug("psutil.cpu_freq failed: %s", e)
        try:
            mem = psutil.virtual_memory()
            readings['psutil:mem_percent'] = mem.percent
            readings['psutil:mem_available'] = mem.available / (1024 * 1024)
        except PSUTIL_EXC as e:
            log.debug("psutil.virtual_memory failed: %s", e)

    def _poll_nvidia(self, readings: dict[str, float]) -> None:
        if not self._ensure_nvidia_ready() or pynvml is None:
            return
        for i, handle in self._nvidia_handles.items():
            prefix = f"nvidia:{i}"
            try:
                readings[f"{prefix}:temp"] = float(
                    pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU))
            except NVML_EXC as e:
                log.debug("%s:temp poll failed: %s", prefix, e)
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                readings[f"{prefix}:gpu_util"] = float(util.gpu)
                readings[f"{prefix}:mem_util"] = float(util.memory)
            except NVML_EXC as e:
                log.debug("%s:util poll failed: %s", prefix, e)
            try:
                readings[f"{prefix}:clock"] = float(
                    pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS))
            except NVML_EXC as e:
                log.debug("%s:clock poll failed: %s", prefix, e)
            try:
                readings[f"{prefix}:mem_clock"] = float(
                    pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM))
            except NVML_EXC as e:
                log.debug("%s:mem_clock poll failed: %s", prefix, e)
            try:
                readings[f"{prefix}:power"] = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
            except NVML_EXC as e:
                log.debug("%s:power poll failed: %s", prefix, e)
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                readings[f"{prefix}:vram_used"] = int(mem.used) / (1024 * 1024)
                readings[f"{prefix}:vram_total"] = int(mem.total) / (1024 * 1024)
            except NVML_EXC as e:
                log.debug("%s:vram poll failed: %s", prefix, e)
            try:
                readings[f"{prefix}:fan"] = float(pynvml.nvmlDeviceGetFanSpeed(handle))
            except NVML_EXC as e:
                log.debug("%s:fan poll failed: %s", prefix, e)

    def _poll_rapl(self, readings: dict[str, float]) -> None:
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
                    power_w = (energy_uj - prev_energy) / (dt * 1_000_000)
                    if power_w >= 0:
                        readings[sid] = power_w
            self._rapl_prev[sid] = (energy_uj, now)

    def read_one(self, sensor_id: str) -> float | None:
        if sensor_id in self._hwmon_paths:
            if (val := SysUtils.read_sysfs(self._hwmon_paths[sensor_id])) is not None:
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
            if (val := SysUtils.read_sysfs(self._drm_paths[sensor_id])) is not None:
                try:
                    return float(val)
                except ValueError:
                    return None

        readings = self.read_all()
        return readings.get(sensor_id)

    # ── Mapping ───────────────────────────────────────────────────────

    def _build_mapping(self) -> dict[str, str]:
        sensors = self._sensors
        _ff = self._find_first
        mapping: dict[str, str] = {}
        self._map_common(mapping)

        mapping['cpu_temp'] = (
            _ff(sensors, source='hwmon', name_contains='Package')
            or _ff(sensors, source='hwmon', name_contains='Tctl')
            or _ff(sensors, source='hwmon', name_contains='coretemp')
            or _ff(sensors, source='hwmon', name_contains='k10temp')
        )
        mapping['cpu_power'] = _ff(sensors, source='rapl')

        gpu = self._best_gpu()
        if gpu.get('vendor') == 'nvidia':
            prefix = f"nvidia:{gpu['nvidia_idx']}"
            mapping['gpu_temp'] = f"{prefix}:temp"
            mapping['gpu_usage'] = f"{prefix}:gpu_util"
            mapping['gpu_clock'] = f"{prefix}:clock"
            mapping['gpu_power'] = f"{prefix}:power"
        elif gpu.get('vendor') == 'amd':
            drv = gpu['hwmon_driver']
            card = gpu['drm_card']
            mapping['gpu_temp'] = _ff(sensors, source='hwmon', name_contains=drv, category='temperature')
            mapping['gpu_usage'] = _ff(sensors, source='drm', name_contains=card, category='usage')
            mapping['gpu_clock'] = _ff(sensors, source='hwmon', name_contains=drv, category='clock')
            mapping['gpu_power'] = _ff(sensors, source='hwmon', name_contains=drv, category='power')
        elif _GPU_VENDOR_INTEL in _detect_gpu_vendors():
            mapping['gpu_temp'] = _ff(sensors, source='hwmon', name_contains='i915', category='temperature')
            mapping['gpu_usage'] = ''
            mapping['gpu_clock'] = _ff(sensors, source='drm', category='clock')
            mapping['gpu_power'] = _ff(sensors, source='hwmon', name_contains='i915', category='power')
        else:
            mapping['gpu_temp'] = ''
            mapping['gpu_usage'] = ''
            mapping['gpu_clock'] = ''
            mapping['gpu_power'] = ''

        mapping['mem_temp'] = _ff(sensors, source='hwmon', name_contains='spd')
        mapping['mem_clock'] = ''

        mapping['disk_temp'] = (
            _ff(sensors, source='hwmon', name_contains='nvme')
            or _ff(sensors, source='hwmon', name_contains='drivetemp')
        )

        self._map_fans(mapping, fan_sources=('hwmon',))
        return mapping

    def get_gpu_list(self) -> list[tuple[str, str]]:
        gpus: list[tuple[str, str, int]] = []

        if _ensure_nvml() and pynvml is not None:
            for idx, handle in self._nvidia_handles.items():
                try:
                    name = pynvml.nvmlDeviceGetName(handle)
                    if isinstance(name, bytes):
                        name = name.decode()
                    name = str(name)
                except NVML_EXC as e:
                    log.debug("nvmlDeviceGetName(idx=%d) failed: %s", idx, e)
                    name = f'GPU {idx}'
                try:
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    vram = int(mem.total)
                except NVML_EXC as e:
                    log.debug("nvmlDeviceGetMemoryInfo(idx=%d) failed: %s", idx, e)
                    vram = 0
                vram_mb = vram // (1024 * 1024)
                gpus.append((f'nvidia:{idx}', f'{name} ({vram_mb} MB)', vram))

        drm_base = Path('/sys/class/drm')
        if drm_base.exists():
            for card_dir in sorted(drm_base.glob('card[0-9]*')):
                if '-' in card_dir.name:
                    continue
                vendor_path = card_dir / 'device' / 'vendor'
                if not vendor_path.exists():
                    continue
                try:
                    vendor = vendor_path.read_text().strip().removeprefix('0x')
                except OSError:
                    continue
                if vendor not in (_GPU_VENDOR_AMD, _GPU_VENDOR_INTEL):
                    continue

                card = card_dir.name
                vendor_label = 'AMD' if vendor == _GPU_VENDOR_AMD else 'Intel'

                hwmon_driver = ''
                hwmon_path = card_dir / 'device' / 'hwmon'
                if hwmon_path.exists():
                    for hdir in hwmon_path.iterdir():
                        if (drv := SysUtils.read_sysfs(str(hdir / 'name'))):
                            hwmon_driver = drv
                            break

                vram = 0
                mem_path = card_dir / 'device' / 'mem_info_vram_total'
                if mem_path.exists():
                    if (val := SysUtils.read_sysfs(str(mem_path))):
                        try:
                            vram = int(val)
                        except ValueError:
                            pass

                vram_mb = vram // (1024 * 1024)
                driver_part = f' {hwmon_driver}' if hwmon_driver else ''
                label = (
                    f'{vendor_label}{driver_part} ({card}, {vram_mb} MB)' if vram_mb
                    else f'{vendor_label}{driver_part} ({card})'
                )
                key = f'{vendor_label.lower()}:{card}'
                gpus.append((key, label, vram))

        gpus.sort(key=itemgetter(2), reverse=True)
        return [(key, name) for key, name, _ in gpus]

    def _best_gpu(self) -> dict:
        best: dict = {}

        if _ensure_nvml() and pynvml is not None:
            for idx, handle in self._nvidia_handles.items():
                try:
                    mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    vram = int(mem.total)
                except NVML_EXC as e:
                    log.debug("_best_gpu(idx=%d) memory probe failed: %s", idx, e)
                    vram = 0
                info = {'vendor': 'nvidia', 'nvidia_idx': idx,
                        'drm_card': '', 'hwmon_driver': '', 'vram': vram}
                if self._preferred_gpu == f'nvidia:{idx}':
                    return info
                if vram > best.get('vram', 0):
                    best = info

        drm_base = Path('/sys/class/drm')
        if drm_base.exists():
            for card_dir in sorted(drm_base.glob('card[0-9]*')):
                if '-' in card_dir.name:
                    continue
                vendor_path = card_dir / 'device' / 'vendor'
                if not vendor_path.exists():
                    continue
                try:
                    vendor = vendor_path.read_text().strip().removeprefix('0x')
                except OSError:
                    continue
                if vendor not in (_GPU_VENDOR_AMD, _GPU_VENDOR_INTEL):
                    continue

                mem_path = card_dir / 'device' / 'mem_info_vram_total'
                vram = 0
                if mem_path.exists():
                    if (val := SysUtils.read_sysfs(str(mem_path))):
                        try:
                            vram = int(val)
                        except ValueError:
                            pass

                hwmon_driver = ''
                hwmon_path = card_dir / 'device' / 'hwmon'
                if hwmon_path.exists():
                    for hdir in hwmon_path.iterdir():
                        if (name := SysUtils.read_sysfs(str(hdir / 'name'))):
                            hwmon_driver = name
                            break
                vendor_name = 'amd' if vendor == _GPU_VENDOR_AMD else 'intel'
                info = {'vendor': vendor_name, 'nvidia_idx': None,
                        'drm_card': card_dir.name, 'hwmon_driver': hwmon_driver,
                        'vram': vram}
                if self._preferred_gpu == f'{vendor_name}:{card_dir.name}':
                    return info
                if vram > best.get('vram', 0):
                    best = info

        return best
