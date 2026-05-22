"""macOS hardware sensor discovery and reading.

Platform-specific sources:
- IOKit SMC (gosmc-aligned client): temperatures, fans, and electrical rails
  (power / voltage / current tables derived from iSMC smc/sensors.go, GPL-3.0)
- IOKit HID event client (Apple Silicon): thermal / current / voltage sensor hub
- powermetrics: CPU/GPU/ANE/combined power, GPU residency/clock, CPU core freqs (Apple Silicon),
  parsed from ``powermetrics -f plist`` (text fallback). Optional root helper
  (``native/macos/trcc_powermetrics_helper/``) or subprocess (sudo for the app process).
- psutil: CPU usage/frequency, memory, disk I/O, network I/O
- pynvml: NVIDIA GPU (rare on Mac, eGPU only)

Sensor IDs:
    smc:{key}              e.g., smc:TC0P, smc:PCPT (watts), smc:VD0R (volts)
    hid:{slug}             HID product-derived id (Apple Silicon)
    iokit:{sensor}         e.g., iokit:cpu_power, iokit:combined_power (powermetrics plist)
    psutil:{metric}        e.g., psutil:cpu_percent
    nvidia:{gpu}:{metric}  e.g., nvidia:0:temp
    computed:{metric}      e.g., computed:disk_read
"""
from __future__ import annotations

import logging
import plistlib
import re
import subprocess

import psutil

from trcc.legacy.adapters.system._base import SUBPROCESS_EXC, SensorEnumeratorBase
from trcc.legacy.adapters.system.macos.hardware import _is_apple_silicon
from trcc.legacy.adapters.system.macos.hid_sensors import (
    hid_layer_ready,
    poll_hid_readings,
    read_hid_sensor_pairs,
)
from trcc.legacy.adapters.system.macos.powermetrics_extra import (
    extra_sensor_infos,
    full_powermetrics_sampler_csv,
    readings_from_powermetrics_extras,
)
from trcc.legacy.adapters.system.macos.powermetrics_ipc import fetch_powermetrics_bytes
from trcc.legacy.adapters.system.macos.powermetrics_plist import parse_powermetrics_plist_root
from trcc.legacy.adapters.system.macos.smc_client import (
    SMCClient,
    SMCKeyData_t,
)
from trcc.legacy.adapters.system.macos.smc_client import (
    parse_smc_bytes as _parse_smc_bytes,
)
from trcc.legacy.adapters.system.macos.smc_ismc_tables import ISMCSMC_ELECTRICAL_KEYS
from trcc.legacy.core.models import SensorInfo

log = logging.getLogger(__name__)

__all__ = [
    'MacOSSensorEnumerator',
    'SMCKeyData_t',
    '_parse_smc_bytes',
]

IS_APPLE_SILICON = _is_apple_silicon()

# IOKit C-bridge surface (ctypes / CoreFoundation): no shared Python base.
_IOKIT_EXC: tuple[type[BaseException], ...] = (OSError, AttributeError, ValueError, RuntimeError)

# Reject garbage SMC floats (wrong key / datatype) from discovery and poll.
_SMC_TEMP_C_MIN = -30.0
_SMC_TEMP_C_MAX = 130.0

# SMC key table — Intel + Apple Silicon (discovery probes each key).
_SMC_KEYS: dict[str, tuple[str, str, str]] = {
    'TC0P': ('CPU Proximity', 'temperature', '°C'),
    'TC0D': ('CPU Die', 'temperature', '°C'),
    'TC0E': ('CPU Core 0', 'temperature', '°C'),
    'TC1C': ('CPU Core 1', 'temperature', '°C'),
    'TC2C': ('CPU Core 2', 'temperature', '°C'),
    'TC3C': ('CPU Core 3', 'temperature', '°C'),
    'Tp01': ('CPU P-Core 1', 'temperature', '°C'),
    'Tp02': ('CPU P-Core 2', 'temperature', '°C'),
    'Tp05': ('CPU P-Core 5', 'temperature', '°C'),
    'Tp09': ('CPU P-Core 9', 'temperature', '°C'),
    'Tp0T': ('CPU Package', 'temperature', '°C'),
    'TG0P': ('GPU Proximity', 'temperature', '°C'),
    'TG0D': ('GPU Die', 'temperature', '°C'),
    'Tg04': ('GPU Die 0', 'temperature', '°C'),
    'Tg05': ('GPU Die 1', 'temperature', '°C'),
    'Tg0f': ('GPU Die', 'temperature', '°C'),
    'Tg0j': ('GPU Die', 'temperature', '°C'),
    'Tm0P': ('Memory Proximity', 'temperature', '°C'),
    'Tm00': ('Memory Bank 0', 'temperature', '°C'),
    'Tm01': ('Memory Bank 1', 'temperature', '°C'),
    'F0Ac': ('Fan 0', 'fan', 'RPM'),
    'F1Ac': ('Fan 1', 'fan', 'RPM'),
    'F2Ac': ('Fan 2', 'fan', 'RPM'),
    'F3Ac': ('Fan 3', 'fan', 'RPM'),
    'TN0P': ('Northbridge', 'temperature', '°C'),
    'TB0T': ('Battery', 'temperature', '°C'),
}

# Extra SMC keys to probe on Apple Silicon (iSMC / sysfs-style names).
_SMC_PROBE_KEYS_AS: frozenset[str] = frozenset({
    'Tp0D', 'Tp0H', 'Tp0L', 'Tp0P', 'Tp0X', 'Tp0b',
    'Tg0D', 'Tg0L', 'Tg0T',
    'Tm02', 'Tm06', 'Tm08', 'Tm09',
})

# M3–M5 die / cluster temps from iSMC smc/sensors.go (Platform M3–M5).
_SMC_PROBE_TEMP_AS: frozenset[str] = frozenset({
    'Te04', 'Te05', 'Te06', 'Te09', 'Te0G', 'Te0H', 'Te0I', 'Te0L', 'Te0P',
    'Te0Q', 'Te0R', 'Te0S', 'Te0T', 'Te0U', 'Te0V', 'Tf14', 'Tf18', 'Tf19',
    'Tf1A', 'Tf1D', 'Tf1E', 'Tf24', 'Tf28', 'Tf29', 'Tf2A', 'Tf2D', 'Tf2E',
    'Tg0G', 'Tg0H', 'Tg0K', 'Tg0L', 'Tg0U', 'Tg0X', 'Tg0d', 'Tg0e', 'Tg0g',
    'Tg0j', 'Tg0k', 'Tg1U', 'Tg1Y', 'Tg1c', 'Tg1g', 'Tg1k', 'Tm0p', 'Tm1p',
    'Tm2p', 'Tp00', 'Tp04', 'Tp05', 'Tp06', 'Tp08', 'Tp0C', 'Tp0D', 'Tp0E',
    'Tp0G', 'Tp0K', 'Tp0L', 'Tp0M', 'Tp0O', 'Tp0R', 'Tp0U', 'Tp0W', 'Tp0X',
    'Tp0a', 'Tp0b', 'Tp0c', 'Tp0d', 'Tp0g', 'Tp0h', 'Tp0i', 'Tp0j', 'Tp0m',
    'Tp0n', 'Tp0o', 'Tp0p', 'Tp0u', 'Tp0y', 'Tp12', 'Tp16', 'Tp1E', 'Tp1F',
    'Tp1G', 'Tp1K', 'Tp1Q', 'Tp1R', 'Tp1S', 'Tp1j', 'Tp1n', 'Tp1t', 'Tp1w',
    'Tp1z', 'Tp22', 'Tp25', 'Tp28', 'Tp2B', 'Tp2E', 'Tp2J', 'Tp2M', 'Tp2Q',
    'Tp2T', 'Tp2W', 'Tp3P', 'Tp3X', 'Tpx8', 'Tpx9', 'TpxA', 'TpxB', 'TpxC',
    'TpxD',
})

# Prefer these SMC keys for legacy cpu_power when powermetrics has no CPU line.
_SMC_CPU_POWER_KEY_PRIORITY: tuple[str, ...] = (
    'PCPT', 'PCPR', 'PCTR', 'PCPC', 'PCLT', 'PCPL', 'PCAM', 'PSTR', 'PDTR',
)


def _smc_temp_celsius_plausible(value: float) -> bool:
    if value != value:  # NaN
        return False
    return _SMC_TEMP_C_MIN <= value <= _SMC_TEMP_C_MAX


def _smc_electrical_plausible(category: str, val: float) -> bool:
    if val != val:  # NaN
        return False
    if category == 'power':
        return -1.0 <= val <= 4000.0
    if category == 'voltage':
        return 0.03 <= val <= 64.0
    if category == 'current':
        return -250.0 <= val <= 250.0
    return False


def _meta_for_smc_key(key: str) -> tuple[str, str, str]:
    if key in _SMC_KEYS:
        return _SMC_KEYS[key]
    m = re.match(r'^F(\d+)Ac$', key)
    if m:
        return (f'Fan {m.group(1)}', 'fan', 'RPM')
    if len(key) == 4 and key.startswith('Tp'):
        return (f'CPU {key}', 'temperature', '°C')
    if len(key) == 4 and key.startswith('Tg'):
        return (f'GPU {key}', 'temperature', '°C')
    if re.match(r'^Tm[0-9A-Fa-f]{2}$', key):
        return (f'Memory {key}', 'temperature', '°C')
    return (f'SMC {key}', 'temperature', '°C')


def _parse_metric(line: str) -> float:
    """Extract numeric value from powermetrics output line."""
    match = re.search(r'([\d.]+)', line.split(':')[-1])
    if match:
        return float(match.group(1))
    return 0.0


def _merge_powermetrics_text(readings: dict[str, float], stdout: str) -> None:
    """Legacy line parser for ``powermetrics`` human-readable output."""
    cpu_core_freqs: list[float] = []
    for line in stdout.splitlines():
        line = line.strip()
        lo = line.lower()

        if (
            'iokit:gpu_busy' not in readings
            and 'gpu' in lo
            and any(kw in lo for kw in (
                'active residency', 'usage', 'utilization', 'busy',
            ))
        ):
            m = re.search(r'([\d.]+)\s*%', line)
            if m:
                v = float(m.group(1))
                if 0.0 <= v <= 100.0:
                    readings['iokit:gpu_busy'] = v

        elif (
            'iokit:gpu_power' not in readings
            and 'gpu' in lo
            and 'power' in lo
        ):
            mw = re.search(r'([\d.]+)\s*mW', line, re.IGNORECASE)
            w = re.search(r'([\d.]+)\s*W\b', line, re.IGNORECASE)
            if mw:
                readings['iokit:gpu_power'] = float(mw.group(1)) / 1000.0
            elif w:
                readings['iokit:gpu_power'] = float(w.group(1))

        elif 'iokit:cpu_power' not in readings and (
            'power' in lo or 'watts' in lo or re.search(r'\d\s*mW', line, re.I)
        ):
            if 'ane' in lo or 'neural engine' in lo:
                pass
            elif re.search(
                r'\b(cpu|package|cluster|soc)\b', lo,
            ) and not re.search(r'^\s*gpu\b', lo):
                mw = re.search(r'([\d.]+)\s*mW', line, re.IGNORECASE)
                w = re.search(r'([\d.]+)\s*W\b', line, re.IGNORECASE)
                v_w: float | None = None
                if mw:
                    v_w = float(mw.group(1)) / 1000.0
                elif w:
                    v_w = float(w.group(1))
                if v_w is not None and 0.0 <= v_w <= 500.0:
                    readings['iokit:cpu_power'] = v_w

        elif (
            'iokit:gpu_clock' not in readings
            and 'gpu' in lo
            and 'mhz' in lo
        ):
            m = re.search(r'([\d.]+)\s*MHz', line, re.IGNORECASE)
            if m and 100.0 <= float(m.group(1)) <= 4000.0:
                readings['iokit:gpu_clock'] = float(m.group(1))

        else:
            m = re.match(
                r'cpu\s+\d+\s+frequency:\s*([\d.]+)\s*mhz', lo,
            )
            if m:
                v = float(m.group(1))
                if 1.0 <= v <= 8000.0:
                    cpu_core_freqs.append(v)

    if cpu_core_freqs:
        readings['psutil:cpu_freq'] = max(cpu_core_freqs)


class MacOSSensorEnumerator(SensorEnumeratorBase):
    """Discovers and reads hardware sensors on macOS."""

    def __init__(self) -> None:
        super().__init__()
        self._smc = SMCClient()
        self._hid_ids: frozenset[str] = frozenset()

    def discover(self) -> list[SensorInfo]:
        self._sensors.clear()
        with self._lock:
            self._readings.clear()
        self._hid_ids = frozenset()
        self._discover_psutil_base()
        # HID before SMC on AS: iSMC merges both for temps; the hub usually carries
        # authoritative die readings (see iSMC README). Discover HID first so logs
        # reflect the primary source order.
        if IS_APPLE_SILICON:
            self._discover_hid()
        self._discover_smc()
        if IS_APPLE_SILICON:
            self._discover_apple_silicon_gpu()
        self._discover_nvidia()
        self._discover_computed()
        log.info("macOS sensor discovery: %d sensors", len(self._sensors))
        return self._sensors

    def _discover_smc(self) -> None:
        if not self._smc.open():
            log.debug('SMC unavailable — skipping SMC keys')
            return
        keys: set[str] = set(_SMC_KEYS)
        if IS_APPLE_SILICON:
            keys |= _SMC_PROBE_KEYS_AS | _SMC_PROBE_TEMP_AS
        n_fans = self._smc.read_key_uint32('FNum')
        if n_fans is not None and 0 < n_fans < 16:
            for i in range(n_fans):
                keys.add(f'F{i}Ac')
        registered_smc: set[str] = set()
        for key in sorted(keys):
            name, category, unit = _meta_for_smc_key(key)
            if category == 'fan':
                val = self._smc.read_fan_rpm(key)
                if val is None or val < 0:
                    continue
            else:
                val = self._smc.read_key_float(key)
                if val is None or not _smc_temp_celsius_plausible(val):
                    continue
                # Apple Silicon SMC often exposes Tp/Tg keys that read 0 when idle or
                # unimplemented; registering them steals mapping from HID (iSMC temp path).
                if IS_APPLE_SILICON and val == 0.0:
                    continue
            self._sensors.append(
                SensorInfo(f'smc:{key}', name, category, unit, 'smc'),
            )
            registered_smc.add(key)

        # iSMC ApplePower / AppleVoltage / AppleCurrent (expanded) — watts, volts, amps.
        for key in sorted(ISMCSMC_ELECTRICAL_KEYS.keys()):
            if key in registered_smc:
                continue
            name, category, unit = ISMCSMC_ELECTRICAL_KEYS[key]
            val = self._smc.read_key_float(key)
            if val is None or not _smc_electrical_plausible(category, val):
                continue
            if val == 0.0:
                continue
            self._sensors.append(
                SensorInfo(f'smc:{key}', name, category, unit, 'smc'),
            )
            registered_smc.add(key)

        log.info(
            'SMC discovery: %d sensors found',
            sum(1 for s in self._sensors if s.source == 'smc'),
        )

    def _discover_hid(self) -> None:
        if not hid_layer_ready():
            log.debug('HID layer not available on this system')
            return
        try:
            rows = read_hid_sensor_pairs()
        except _IOKIT_EXC as e:
            log.warning('HID sensor discovery failed: %s', e)
            return
        ids: list[str] = []
        for sid, name, category, unit, val in rows:
            if val <= 0:
                continue
            self._sensors.append(SensorInfo(sid, name, category, unit, 'hid'))
            ids.append(sid)
        self._hid_ids = frozenset(ids)
        log.info('HID discovery: %d sensors', len(ids))

    def _discover_apple_silicon_gpu(self) -> None:
        for sid, name, category, unit in (
            ('iokit:cpu_power', 'CPU Power', 'power', 'W'),
            ('iokit:gpu_busy', 'GPU Usage', 'gpu_busy', '%'),
            ('iokit:gpu_clock', 'GPU Clock', 'clock', 'MHz'),
            ('iokit:gpu_power', 'GPU Power', 'power', 'W'),
            ('iokit:ane_power', 'ANE Power', 'power', 'W'),
            ('iokit:combined_power', 'SoC Combined Power', 'power', 'W'),
        ):
            self._sensors.append(
                SensorInfo(sid, name, category, unit, 'iokit'),
            )
        self._sensors.extend(extra_sensor_infos())

    def _on_stop(self) -> None:
        self._smc.close()

    def _poll_once(self) -> None:
        readings: dict[str, float] = {}
        self._poll_psutil_base(readings)
        readings['computed:disk_percent'] = self._poll_apfs_disk_percent()
        self._poll_computed_io(readings)
        self._poll_smc(readings)
        if IS_APPLE_SILICON and self._hid_ids:
            try:
                readings.update(poll_hid_readings(self._hid_ids))
            except _IOKIT_EXC as e:
                log.debug('HID poll failed: %s', e)
        if IS_APPLE_SILICON:
            self._poll_powermetrics_gpu(readings)
        self._poll_nvidia(readings)
        self._poll_datetime(readings)

        with self._lock:
            self._readings = readings

    def _poll_smc(self, readings: dict[str, float]) -> None:
        if not self._smc.connected:
            return
        for sensor in self._sensors:
            if sensor.source != 'smc':
                continue
            key = sensor.id.split(':', 1)[1]
            if sensor.category == 'fan':
                val = self._smc.read_fan_rpm(key)
            elif sensor.category == 'temperature':
                val = self._smc.read_key_float(key)
                if val is not None and not _smc_temp_celsius_plausible(val):
                    continue
            else:
                val = self._smc.read_key_float(key)
            if val is not None:
                readings[sensor.id] = val

    @staticmethod
    def _subprocess_powermetrics_raw(samplers: str, *, plist: bool) -> bytes:
        cmd = [
            'powermetrics', '--samplers', samplers, '-n', '1', '-i', '100',
        ]
        if plist:
            cmd.extend(['-f', 'plist'])
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=8,
            )
        except SUBPROCESS_EXC as e:
            log.debug('powermetrics subprocess failed: %s', e)
            return b''
        out = result.stdout
        return out if isinstance(out, (bytes, bytearray)) else b''

    def _poll_powermetrics_gpu(self, readings: dict[str, float]) -> None:
        if not IS_APPLE_SILICON:
            return
        try:
            samp = full_powermetrics_sampler_csv()
            raw = fetch_powermetrics_bytes(samp, timeout=8.0)
            if not raw:
                raw = self._subprocess_powermetrics_raw(samp, plist=True)
            if raw:
                try:
                    chunk = raw.split(b'\x00', 1)[0]
                    root = plistlib.loads(chunk)
                    merged = dict(parse_powermetrics_plist_root(root))
                    merged.update(readings_from_powermetrics_extras(root))
                    if merged:
                        readings.update(merged)
                        return
                except (plistlib.InvalidFileException, ValueError, KeyError, TypeError) as e:
                    log.debug('powermetrics plist parse failed: %s', e)

            result = subprocess.run(
                [
                    'powermetrics', '--samplers', samp, '-n', '1', '-i', '100',
                ],
                capture_output=True, text=True, timeout=8,
            )
            if (result.stdout or '').strip():
                _merge_powermetrics_text(readings, result.stdout)

        except SUBPROCESS_EXC as e:
            log.debug(
                'powermetrics failed (install helper or run with sudo): %s', e,
            )

    def _poll_apfs_disk_percent(self) -> float:
        try:
            result = subprocess.run(
                ['diskutil', 'apfs', 'list'],
                capture_output=True, text=True, timeout=5,
            )
            capacity = 0
            in_use = 0
            for line in result.stdout.splitlines():
                if 'Size (Capacity Ceiling)' in line:
                    m = re.search(r'(\d+)\s+B', line)
                    if m:
                        capacity = int(m.group(1))
                elif 'Capacity In Use By Volumes' in line:
                    m = re.search(r'(\d+)\s+B', line)
                    if m:
                        in_use = int(m.group(1))
            if capacity > 0:
                return round(in_use / capacity * 100, 1)
        except SUBPROCESS_EXC as e:
            log.debug('diskutil apfs list failed: %s', e)
        return psutil.disk_usage('/').percent

    def get_gpu_list(self) -> list[tuple[str, str]]:
        gpus: list[tuple[str, str]] = []
        if IS_APPLE_SILICON:
            gpus.append(('iokit:gpu', 'Apple Silicon GPU'))
        else:
            try:
                import json as _json
                result = subprocess.run(
                    ['system_profiler', 'SPDisplaysDataType', '-json'],
                    capture_output=True, text=True, timeout=5,
                )
                data = _json.loads(result.stdout)
                for item in data.get('SPDisplaysDataType', []):
                    name = item.get('sppci_model', 'Unknown GPU')
                    vram = item.get('sppci_vram', '')
                    label = f'{name} ({vram})' if vram else name
                    key = f'smc:{name.lower().replace(" ", "_")[:20]}'
                    gpus.append((key, label))
            except SUBPROCESS_EXC as e:
                log.debug('system_profiler SPDisplaysDataType failed: %s', e)
                gpus.append(('smc:gpu', 'Intel Mac GPU'))
        gpus.extend(super().get_gpu_list())
        return gpus

    @staticmethod
    def _hid_temp_id_matching_name(sensors: list[SensorInfo], pattern: re.Pattern[str]) -> str:
        for s in sensors:
            if s.source == 'hid' and s.category == 'temperature' and pattern.search(s.name):
                return s.id
        return ''

    @staticmethod
    def _first_hid_temperature_id(sensors: list[SensorInfo]) -> str:
        for s in sensors:
            if s.source == 'hid' and s.category == 'temperature':
                return s.id
        return ''

    def _apple_silicon_hid_cpu_temp(self, sensors: list[SensorInfo]) -> str:
        """Prefer iStat-style HID Product labels, then PMU tdie/tdev strings."""
        for pat in (
            re.compile(r'(?i)performance\s+core'),
            re.compile(r'(?i)cpu\s+performance'),
            re.compile(r'(?i)efficiency\s+core'),
            re.compile(r'(?i)cpu\s+efficiency'),
            re.compile(r'(?i)tdie'),
            re.compile(r'(?i)tdev'),
            re.compile(r'(?i)\bTP[0-9]'),
            re.compile(r'(?i)soc|package|pmu_t'),
        ):
            if (sid := self._hid_temp_id_matching_name(sensors, pat)):
                return sid
        return self._first_hid_temperature_id(sensors)

    def _apple_silicon_hid_gpu_temp(
        self, sensors: list[SensorInfo], cpu_sensor_id: str,
    ) -> str:
        for pat in (
            re.compile(r'(?i)graphics'),
            re.compile(r'(?i)gpu'),
            re.compile(r'(?i)gddr'),
            re.compile(r'(?i)grfx'),
        ):
            if (sid := self._hid_temp_id_matching_name(sensors, pat)):
                return sid
        # Unified SoC: pick a second tdie channel if it differs from CPU mapping
        tdies = [
            s for s in sensors
            if s.source == 'hid' and s.category == 'temperature'
            and re.search(r'(?i)tdie', s.name)
        ]
        if len(tdies) >= 2 and cpu_sensor_id:
            for s in tdies:
                if s.id != cpu_sensor_id:
                    return s.id
        return cpu_sensor_id

    @staticmethod
    def _first_smc_temp_key(sensors: list[SensorInfo], key_re: re.Pattern[str]) -> str:
        """First discovered SMC temperature sensor whose four-char key matches."""
        for s in sensors:
            if s.source != 'smc' or s.category != 'temperature':
                continue
            part = s.id.split(':', 1)[-1]
            if len(part) >= 4 and key_re.match(part[:4]):
                return s.id
        return ''

    @staticmethod
    def _first_smc_cpu_power_id(sensors: list[SensorInfo]) -> str:
        """Best-effort SMC key for CPU / package power (watts)."""
        smc_p = [s for s in sensors if s.source == 'smc' and s.category == 'power']
        by_key = {s.id.split(':', 1)[-1]: s.id for s in smc_p}
        for k in _SMC_CPU_POWER_KEY_PRIORITY:
            if k in by_key:
                return by_key[k]
        for s in smc_p:
            nl = s.name.lower()
            if 'cpu' in nl or 'package' in nl:
                return s.id
        return smc_p[0].id if smc_p else ''

    def _build_mapping(self) -> dict[str, str]:
        sensors = self._sensors
        _ff = self._find_first
        mapping: dict[str, str] = {}
        self._map_common(mapping)

        _tp_key = re.compile(r'^Tp')
        _tg_key = re.compile(r'^Tg')
        _tm_hex = re.compile(r'^Tm[0-9A-Fa-f]{2}$')

        # iSMC getTemperature() deep-merges smc + hid; on Apple Silicon the HID hub
        # is the usual source for die temps. Prefer HID here so legacy cpu/gpu/mem_temp
        # map to the same readings iSMC shows, and SMC Tp/Tg/Tm act as fallback.
        if IS_APPLE_SILICON:
            mapping['cpu_temp'] = (
                self._apple_silicon_hid_cpu_temp(sensors)
                or self._first_smc_temp_key(sensors, _tp_key)
            )
        else:
            mapping['cpu_temp'] = ''
        mapping['cpu_temp'] = mapping['cpu_temp'] or (
            _ff(sensors, source='smc', name_contains='CPU', category='temperature')
            or _ff(sensors, source='hid', name_contains='cpu', category='temperature')
            or _ff(sensors, source='hid', name_contains='CPU', category='temperature')
            or _ff(sensors, source='iokit', name_contains='cpu', category='temperature')
        )

        if IS_APPLE_SILICON:
            mapping['gpu_temp'] = (
                self._apple_silicon_hid_gpu_temp(sensors, mapping.get('cpu_temp', ''))
                or self._first_smc_temp_key(sensors, _tg_key)
            )
        else:
            mapping['gpu_temp'] = ''
        mapping['gpu_temp'] = mapping['gpu_temp'] or (
            _ff(sensors, source='smc', name_contains='GPU', category='temperature')
            or _ff(sensors, source='hid', name_contains='graphics', category='temperature')
            or _ff(sensors, source='hid', name_contains='gpu', category='temperature')
            or _ff(sensors, source='hid', name_contains='GPU', category='temperature')
            or _ff(sensors, source='iokit', name_contains='gpu', category='temperature')
            or _ff(sensors, source='nvidia', category='temperature')
        )

        mapping['cpu_power'] = (
            _ff(sensors, source='iokit', name_contains='CPU', category='power')
            or self._first_smc_cpu_power_id(sensors)
        )

        mapping['gpu_usage'] = (
            _ff(sensors, source='nvidia', category='gpu_busy')
            or _ff(sensors, source='iokit', category='gpu_busy')
        )
        mapping['gpu_clock'] = _ff(sensors, source='iokit', category='clock')
        mapping['gpu_power'] = (
            _ff(sensors, source='nvidia', category='power')
            or _ff(sensors, source='iokit', name_contains='GPU', category='power')
        )

        if IS_APPLE_SILICON:
            mem_hid = ''
            for pat in (re.compile(r'(?i)dram'), re.compile(r'(?i)memory'),
                        re.compile(r'(?i)mem[_\s-]?die')):
                if (sid := self._hid_temp_id_matching_name(sensors, pat)):
                    mem_hid = sid
                    break
            mapping['mem_temp'] = mem_hid or self._first_smc_temp_key(sensors, _tm_hex)
        else:
            mapping['mem_temp'] = ''
        mapping['mem_temp'] = mapping['mem_temp'] or _ff(
            sensors, source='smc', name_contains='Memory', category='temperature',
        ) or _ff(
            sensors, source='hid', name_contains='memory', category='temperature',
        )
        if IS_APPLE_SILICON and not mapping['mem_temp']:
            for pat in (re.compile(r'(?i)dram'), re.compile(r'(?i)memory'),
                        re.compile(r'(?i)mem[_\s-]?die')):
                if (sid := self._hid_temp_id_matching_name(sensors, pat)):
                    mapping['mem_temp'] = sid
                    break

        self._map_fans(mapping, fan_sources=('smc', 'hid', 'iokit', 'nvidia'))

        return mapping
