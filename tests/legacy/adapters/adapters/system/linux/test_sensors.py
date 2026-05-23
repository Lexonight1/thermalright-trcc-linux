"""Tests for Linux sensor enumerator — platform-specific behavior only.

Shared base behavior (psutil base, nvidia base, computed I/O, polling, read_all)
is tested in tests/adapters/system/conftest.py.

Tests follow the app flow: discover() → read_all() → map_defaults().
Mock at I/O boundary: sysfs (Path + SysUtils.read_sysfs), pynvml, psutil.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.adapters.infra.data_repository import SysUtils
from trcc.legacy.adapters.system.linux_sensors import (
    _HWMON_DIVISORS,
    _HWMON_TYPES,
    SensorEnumerator,
)
from trcc.legacy.core.models import SensorInfo

MODULE = 'trcc.legacy.adapters.system.linux_sensors'


# ── Sysfs mock helpers ───────────────────────────────────────────────


def _mock_hwmon_dir(name: str, driver: str, inputs: dict[str, str]) -> MagicMock:
    """Create a mock hwmon directory with given driver and input files.

    inputs: {'temp1': '65000', 'fan1': '1500'} → temp1_input, fan1_input
    """
    hwmon = MagicMock()
    hwmon.name = name

    input_files = []
    for input_name, _val in inputs.items():
        f = MagicMock()
        f.name = f'{input_name}_input'
        input_files.append(f)

    hwmon.glob.return_value = sorted(input_files, key=lambda f: f.name)

    def truediv(_, key):
        m = MagicMock()
        m.name = key
        return m
    hwmon.__truediv__ = truediv

    return hwmon


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_linux(mock_io_no_nvidia):
    """Linux enumerator with mocked sysfs — no hwmon, no DRM, no RAPL."""
    with patch(f'{MODULE}.pynvml', None), \
         patch(f'{MODULE}.Path') as mock_path:
        # Empty sysfs
        for path_str in ('/sys/class/hwmon', '/sys/class/drm',
                         '/sys/class/powercap', '/sys/bus/pci/devices'):
            base = MagicMock()
            base.exists.return_value = False
        mock_path.side_effect = lambda p: _sysfs_mock(p, {})
        mock_io_no_nvidia.path = mock_path
        yield mock_io_no_nvidia


def _sysfs_mock(path_str: str, dirs: dict) -> MagicMock:
    """Return a mock Path that routes by sysfs base path."""
    m = MagicMock()
    m.exists.return_value = str(path_str) in dirs
    if str(path_str) in dirs:
        m.iterdir.return_value = dirs[str(path_str)]
        m.glob.return_value = dirs.get(f'{path_str}:glob', [])
    return m


@pytest.fixture
def enum_minimal(mock_linux):
    """Discovered Linux enumerator — psutil only, no hardware."""
    e = SensorEnumerator()
    e.discover()
    return e


# ── Constants ────────────────────────────────────────────────────────


class TestHwmonConstants:

    def test_types_cover_expected(self):
        for key in ('temp', 'fan', 'in', 'power', 'freq'):
            assert key in _HWMON_TYPES

    def test_divisors_match_types(self):
        for key in _HWMON_TYPES:
            assert key in _HWMON_DIVISORS


# ── Discovery ────────────────────────────────────────────────────────


class TestDiscover:

    def test_psutil_sensors_registered(self, enum_minimal):
        ids = [s.id for s in enum_minimal.get_sensors()]
        assert 'psutil:cpu_percent' in ids
        assert 'psutil:cpu_freq' in ids
        assert 'psutil:mem_available' in ids

    def test_datetime_sensors_registered(self, enum_minimal):
        ids = [s.id for s in enum_minimal.get_sensors()]
        assert 'computed:date_year' in ids
        assert 'computed:day_of_week' in ids

    def test_discover_clears_previous(self, mock_linux):
        e = SensorEnumerator()
        e._sensors = [SensorInfo('old', 'Old', 'x', 'x', 'x')]
        e.discover()
        assert not any(s.id == 'old' for s in e.get_sensors())


class TestDiscoverNvidiaLinux:
    """Linux nvidia discovery — extended metrics (gpu_util, mem_util, vram)."""

    @patch(f'{MODULE}._ensure_nvml', return_value=True)
    @patch(f'{MODULE}.pynvml')
    def test_extended_nvidia_sensors(self, mock_nvml, _mock_ensure, mock_linux):
        mock_nvml.nvmlDeviceGetCount.return_value = 1
        mock_nvml.nvmlDeviceGetHandleByIndex.return_value = 'h'
        mock_nvml.nvmlDeviceGetName.return_value = 'RTX 4090'

        e = SensorEnumerator()
        e.discover()
        ids = [s.id for s in e.get_sensors()]
        # Linux-specific extended metrics
        assert 'nvidia:0:gpu_util' in ids
        assert 'nvidia:0:mem_util' in ids
        assert 'nvidia:0:mem_clock' in ids
        assert 'nvidia:0:vram_used' in ids
        assert 'nvidia:0:vram_total' in ids


# ── Hwmon reading ────────────────────────────────────────────────────


class TestReadHwmon:

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs')
    def test_read_hwmon_temp(self, mock_read):
        """hwmon temp value divided by 1000 (millidegrees → degrees)."""
        mock_read.return_value = '65000'
        e = SensorEnumerator()
        e._hwmon_paths = {'hwmon:coretemp:temp1': '/sys/class/hwmon/hwmon0/temp1_input'}
        e._sensors = [SensorInfo('hwmon:coretemp:temp1', 'CPU', 'temperature', '°C', 'hwmon')]
        readings = e.read_all()
        assert abs(readings['hwmon:coretemp:temp1'] - 65.0) < 0.1

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs', return_value='1500')
    def test_read_hwmon_fan(self, _):
        e = SensorEnumerator()
        e._hwmon_paths = {'hwmon:it8688:fan1': '/sys/class/hwmon/hwmon3/fan1_input'}
        e._sensors = [SensorInfo('hwmon:it8688:fan1', 'Fan', 'fan', 'RPM', 'hwmon')]
        readings = e.read_all()
        assert abs(readings['hwmon:it8688:fan1'] - 1500.0) < 0.1

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs', return_value=None)
    def test_missing_sysfs_value_skipped(self, _):
        e = SensorEnumerator()
        e._hwmon_paths = {'hwmon:x:temp1': '/fake'}
        readings = e.read_all()
        assert 'hwmon:x:temp1' not in readings

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs', return_value='not_a_number')
    def test_invalid_sysfs_value_skipped(self, _):
        e = SensorEnumerator()
        e._hwmon_paths = {'hwmon:x:temp1': '/fake'}
        readings = e.read_all()
        assert 'hwmon:x:temp1' not in readings


class TestReadOne:

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs', return_value='72500')
    def test_read_one_hwmon(self, _):
        e = SensorEnumerator()
        e._hwmon_paths = {'hwmon:k10temp:temp1': '/fake'}
        val = e.read_one('hwmon:k10temp:temp1')
        assert val is not None
        assert abs(val - 72.5) < 0.1

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs', return_value=None)
    def test_read_one_missing(self, _):
        e = SensorEnumerator()
        e._hwmon_paths = {'hwmon:k10temp:temp1': '/fake'}
        assert e.read_one('hwmon:k10temp:temp1') is None


# ── RAPL ─────────────────────────────────────────────────────────────


class TestRapl:

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs')
    @patch(f'{MODULE}.time')
    def test_rapl_power_from_energy_delta(self, mock_time, mock_read):
        """RAPL computes watts from energy_uj delta over time."""
        e = SensorEnumerator()
        e._rapl_paths = {'rapl:package-0': '/fake/energy_uj'}

        # First read: seed baseline
        mock_time.monotonic.return_value = 100.0
        mock_read.return_value = '10000000'  # 10J in µJ
        readings1: dict[str, float] = {}
        e._poll_rapl(readings1)
        assert 'rapl:package-0' not in readings1  # no delta yet

        # Second read: 5J later, 1s elapsed → 5W
        mock_time.monotonic.return_value = 101.0
        mock_read.return_value = '15000000'
        readings2: dict[str, float] = {}
        e._poll_rapl(readings2)
        assert abs(readings2['rapl:package-0'] - 5.0) < 0.1

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs', return_value=None)
    def test_rapl_missing_sysfs(self, _):
        e = SensorEnumerator()
        e._rapl_paths = {'rapl:pkg': '/fake'}
        readings: dict[str, float] = {}
        e._poll_rapl(readings)
        assert readings == {}

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs', return_value='not_a_number')
    def test_rapl_invalid_value(self, _):
        e = SensorEnumerator()
        e._rapl_paths = {'rapl:pkg': '/fake'}
        readings: dict[str, float] = {}
        e._poll_rapl(readings)
        assert readings == {}


# ── Linux nvidia polling ─────────────────────────────────────────────


class TestNvidiaLinuxPoll:

    @patch(f'{MODULE}._ensure_nvml', return_value=True)
    @patch(f'{MODULE}.pynvml')
    def test_extended_nvidia_readings(self, mock_nvml, _mock_ensure):
        """Linux nvidia poll includes gpu_util, mem_util, mem_clock, vram."""
        mock_nvml.NVML_TEMPERATURE_GPU = 0
        mock_nvml.NVML_CLOCK_GRAPHICS = 0
        mock_nvml.NVML_CLOCK_MEM = 1
        mock_nvml.nvmlDeviceGetTemperature.return_value = 65
        mock_nvml.nvmlDeviceGetUtilizationRates.return_value = MagicMock(gpu=80, memory=40)
        mock_nvml.nvmlDeviceGetClockInfo.side_effect = [1800, 7000]  # graphics, mem
        mock_nvml.nvmlDeviceGetPowerUsage.return_value = 250000
        mock_nvml.nvmlDeviceGetMemoryInfo.return_value = MagicMock(
            used=4 * 1024 ** 3, total=16 * 1024 ** 3)
        mock_nvml.nvmlDeviceGetFanSpeed.return_value = 55

        e = SensorEnumerator()
        e._nvidia_handles = {0: 'h'}
        readings: dict[str, float] = {}
        e._poll_nvidia(readings)

        assert readings['nvidia:0:temp'] == 65.0
        assert readings['nvidia:0:gpu_util'] == 80.0
        assert readings['nvidia:0:mem_util'] == 40.0
        assert readings['nvidia:0:clock'] == 1800.0
        assert readings['nvidia:0:mem_clock'] == 7000.0
        assert readings['nvidia:0:power'] == 250.0
        assert readings['nvidia:0:fan'] == 55.0

    @patch(f'{MODULE}._ensure_nvml', return_value=True)
    @patch(f'{MODULE}.pynvml')
    def test_partial_failure_isolated(self, mock_nvml, _mock_ensure):
        """One metric failing doesn't block others."""
        mock_nvml.NVML_TEMPERATURE_GPU = 0
        mock_nvml.NVML_CLOCK_GRAPHICS = 0
        mock_nvml.NVML_CLOCK_MEM = 1
        mock_nvml.nvmlDeviceGetTemperature.side_effect = OSError
        mock_nvml.nvmlDeviceGetUtilizationRates.return_value = MagicMock(gpu=50, memory=20)
        mock_nvml.nvmlDeviceGetClockInfo.side_effect = OSError
        mock_nvml.nvmlDeviceGetPowerUsage.side_effect = OSError
        mock_nvml.nvmlDeviceGetMemoryInfo.side_effect = OSError
        mock_nvml.nvmlDeviceGetFanSpeed.return_value = 45

        e = SensorEnumerator()
        e._nvidia_handles = {0: 'h'}
        readings: dict[str, float] = {}
        e._poll_nvidia(readings)

        assert 'nvidia:0:temp' not in readings
        assert readings['nvidia:0:gpu_util'] == 50.0
        assert readings['nvidia:0:fan'] == 45.0

    @patch(f'{MODULE}._ensure_nvml', return_value=False)
    def test_noop_without_nvml(self, _mock_ensure):
        e = SensorEnumerator()
        readings: dict[str, float] = {}
        e._poll_nvidia(readings)
        assert readings == {}


# ── Linux psutil polling ─────────────────────────────────────────────


class TestPsutilLinux:

    @patch(f'{MODULE}.psutil')
    @patch(f'{MODULE}.time')
    def test_cpu_freq_cached(self, mock_time, mock_psutil):
        """cpu_freq is cached for 10s on Linux."""
        mock_psutil.cpu_percent.return_value = 5.0
        mock_psutil.cpu_freq.return_value = MagicMock(current=3200.0)
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0, available=8 * 1024 ** 3)
        mock_time.monotonic.return_value = 11.0  # past TTL so cache refreshes

        e = SensorEnumerator()
        r: dict[str, float] = {}
        e._poll_psutil(r)
        assert r['psutil:cpu_freq'] == 3200.0

    @patch(f'{MODULE}.psutil')
    @patch(f'{MODULE}.time')
    def test_no_cpu_freq(self, mock_time, mock_psutil):
        mock_psutil.cpu_percent.return_value = 5.0
        mock_psutil.cpu_freq.return_value = None
        mock_psutil.virtual_memory.return_value = MagicMock(percent=50.0, available=8 * 1024 ** 3)
        mock_time.monotonic.return_value = 11.0

        e = SensorEnumerator()
        r: dict[str, float] = {}
        e._poll_psutil(r)
        assert 'psutil:cpu_freq' not in r


# ── map_defaults ─────────────────────────────────────────────────────


class TestMapDefaults:

    def test_fan_mapping_by_keyword(self):
        SensorEnumerator._default_map = None
        e = SensorEnumerator()
        e._sensors = [
            SensorInfo('hwmon:it8688:fan1', 'CPU Fan', 'fan', 'RPM', 'hwmon'),
            SensorInfo('hwmon:it8688:fan2', 'GPU Fan', 'fan', 'RPM', 'hwmon'),
            SensorInfo('hwmon:it8688:fan3', 'SSD Fan', 'fan', 'RPM', 'hwmon'),
        ]
        mapping = e.map_defaults()
        assert mapping.get('fan_cpu') == 'hwmon:it8688:fan1'
        assert mapping.get('fan_gpu') == 'hwmon:it8688:fan2'
        assert mapping.get('fan_ssd') == 'hwmon:it8688:fan3'

    def test_common_mappings(self):
        SensorEnumerator._default_map = None
        e = SensorEnumerator()
        e._discover_psutil()
        e._discover_computed()
        mapping = e.map_defaults()
        assert mapping['cpu_percent'] == 'psutil:cpu_percent'
        assert mapping['mem_available'] == 'psutil:mem_available'
        assert mapping['disk_read'] == 'computed:disk_read'
        assert mapping['net_total_up'] == 'computed:net_total_up'

    @patch(f'{MODULE}._ensure_nvml', return_value=True)
    @patch(f'{MODULE}.pynvml')
    def test_nvidia_gpu_mapping(self, mock_nvml, _mock_ensure):
        mock_nvml.nvmlDeviceGetCount.return_value = 1
        mock_nvml.nvmlDeviceGetHandleByIndex.return_value = 'h'
        mock_nvml.nvmlDeviceGetName.return_value = 'RTX 4090'
        mock_nvml.nvmlDeviceGetMemoryInfo.return_value = MagicMock(
            total=16 * 1024 ** 3)

        SensorEnumerator._default_map = None
        e = SensorEnumerator()
        e._discover_nvidia()
        mapping = e.map_defaults()
        assert mapping['gpu_temp'] == 'nvidia:0:temp'
        assert mapping['gpu_usage'] == 'nvidia:0:gpu_util'


# ── RAPL discovery ───────────────────────────────────────────────────


class TestDiscoverRapl:

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs')
    @patch(f'{MODULE}.Path')
    def test_discovers_top_level_domains(self, mock_path_cls, mock_sysfs):
        rapl_base = MagicMock()
        rapl_base.exists.return_value = True

        domain = MagicMock()
        domain.name = 'intel-rapl:0'
        energy = MagicMock()
        energy.exists.return_value = True
        domain.__truediv__ = lambda self, key: energy if key == 'energy_uj' else MagicMock()
        rapl_base.glob.return_value = [domain]

        mock_path_cls.return_value = rapl_base
        mock_sysfs.return_value = 'package-0'

        e = SensorEnumerator()
        e._discover_rapl()
        assert any(s.id == 'rapl:package-0' for s in e._sensors)

    @patch('trcc.legacy.adapters.infra.data_repository.SysUtils.read_sysfs')
    @patch(f'{MODULE}.Path')
    def test_skips_sub_zones(self, mock_path_cls, mock_sysfs):
        rapl_base = MagicMock()
        rapl_base.exists.return_value = True

        sub_zone = MagicMock()
        sub_zone.name = 'intel-rapl:0:0'  # Sub-zone — has extra colon
        rapl_base.glob.return_value = [sub_zone]

        mock_path_cls.return_value = rapl_base

        e = SensorEnumerator()
        e._discover_rapl()
        assert len(e._sensors) == 0


# ── Module-level checks ──────────────────────────────────────────────


class TestNvmlImport:

    def test_ensure_nvml_is_callable(self):
        from trcc.legacy.adapters.system.linux_platform import _ensure_nvml
        assert callable(_ensure_nvml)


class TestReadSysfs:

    def test_reads_and_strips(self):
        from unittest.mock import mock_open
        m = mock_open(read_data='  42000  \n')
        with patch('builtins.open', m):
            assert SysUtils.read_sysfs('/fake/path') == '42000'

    def test_returns_none_on_error(self):
        assert SysUtils.read_sysfs('/no/such/file') is None
