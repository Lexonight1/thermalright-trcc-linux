"""Tests for macOS sensor enumerator — platform-specific behavior only.

Shared base behavior (psutil, nvidia, computed I/O, polling, read_all)
is tested in tests/adapters/system/conftest.py.

Tests follow the app flow: discover() → read_all() → map_defaults().
Mock at I/O boundary: subprocess for powermetrics/diskutil, SMCClient, HID.
"""
from __future__ import annotations

import ctypes
import plistlib
import struct
from unittest.mock import MagicMock, patch

import pytest

MODULE = 'trcc.legacy.adapters.system.macos.sensors'


def _powermetrics_plist_fixture() -> bytes:
    """``powermetrics -f plist``-shaped sample matching prior text parser expectations."""
    proc = {
        'cpu_power': 1000.0,
        'gpu_power': 4500.0,
        'ane_power': 0.0,
        'combined_power': 5500.0,
        'cpu_energy': 1,
        'gpu_energy': 1,
        'ane_energy': 0,
        'clusters': [
            {'cpus': [
                {'cpu': 0, 'freq_hz': 1690e6},
                {'cpu': 4, 'freq_hz': 2937e6},
            ]},
        ],
    }
    gpu = {
        'freq_hz': 1398.0,
        'idle_ratio': 0.69,
        'dvfm_states': [
            {'freq': 389, 'used_ns': 1, 'used_ratio': 0.13},
            {'freq': 648, 'used_ns': 1, 'used_ratio': 0.18},
        ],
        'sw_requested_state': [],
        'gpu_energy': 1,
    }
    return plistlib.dumps({'processor': proc, 'gpu': gpu}, fmt=plistlib.FMT_XML)


# powermetrics -f plist (Apple Silicon); text fallback tested separately
POWERMETRICS_PLIST_BYTES = _powermetrics_plist_fixture()

# Legacy human-readable output (text fallback path)
POWERMETRICS_GPU_OUTPUT = (
    "GPU active residency: 31%\n"
    "GPU Power: 4.5 W\n"
    "GPU HW active frequency: 1398 MHz\n"
    "CPU 0 frequency: 1690 MHz\n"
    "CPU 4 frequency: 2937 MHz\n"
)

DISKUTIL_OUTPUT = (
    "APFS Container Reference:     disk1\n"
    "Size (Capacity Ceiling):      500107862016 B (500.1 GB)\n"
    "Minimum Size:                 N/A\n"
    "Capacity In Use By Volumes:   400086323200 B (400.1 GB)\n"
    "Capacity Not Allocated:       100021538816 B (100.0 GB)\n"
)


def _make_smc_response(data_type: str, value: float) -> tuple[int, bytes]:
    dt_int = struct.unpack('>I', data_type.ljust(4).encode('ascii'))[0]

    match data_type.rstrip():
        case 'sp78':
            raw = struct.pack('>h', int(value * 256))
        case 'fpe2':
            raw = struct.pack('>H', int(value * 4))
        case 'flt':
            raw = struct.pack('<f', value)
        case 'ui8':
            raw = struct.pack('B', int(value))
        case _:
            raw = struct.pack('>H', int(value))

    return dt_int, raw


class MockSMC:
    """Simulates SMC key payloads for FakeSMCClient."""

    def __init__(self) -> None:
        self.keys: dict[str, tuple[int, bytes]] = {}

    def add_key(self, key: str, data_type: str, value: float) -> None:
        dt_int, raw = _make_smc_response(data_type, value)
        self.keys[key] = (dt_int, raw)


class FakeSMCClient:
    """Maps MockSMC through smc_client.parse_smc_bytes (no real IOKit)."""

    def __init__(self, mock: MockSMC) -> None:
        self._mock = mock

    def open(self) -> bool:
        return True

    @property
    def connected(self) -> bool:
        return True

    def read_key_float(self, key: str) -> float | None:
        if len(key) < 4:
            return None
        key4 = key[:4]
        if key4 not in self._mock.keys:
            return None
        from trcc.legacy.adapters.system.macos.smc_client import parse_smc_bytes

        dt_int, raw = self._mock.keys[key4]
        buf = (ctypes.c_uint8 * 32)()
        for i, b in enumerate(raw):
            buf[i] = b
        return parse_smc_bytes(dt_int, buf, len(raw))

    def read_key_uint32(self, key: str) -> int | None:
        v = self.read_key_float(key)
        return int(v) if v is not None else None

    def read_fan_rpm(self, key: str) -> float | None:
        if len(key) < 4:
            return None
        key4 = key[:4]
        if key4 not in self._mock.keys:
            return None
        from trcc.legacy.adapters.system.macos.smc_client import decode_fan_rpm_raw

        dt_int, raw = self._mock.keys[key4]
        buf = (ctypes.c_uint8 * 32)()
        for i, b in enumerate(raw):
            buf[i] = b
        return decode_fan_rpm_raw(dt_int, len(raw), buf)

    def close(self) -> None:
        pass


@pytest.fixture
def mock_smc():
    """Pre-configured MockSMC with typical Apple Silicon readings + FNum."""
    smc = MockSMC()
    smc.add_key('FNum', 'ui8', 2.0)
    smc.add_key('Tp01', 'sp78', 45.0)
    smc.add_key('Tg0f', 'sp78', 52.0)
    smc.add_key('Tm0P', 'sp78', 38.0)
    smc.add_key('F0Ac', 'fpe2', 1200.0)
    smc.add_key('F1Ac', 'fpe2', 1350.0)
    return smc


@pytest.fixture
def mock_macos(mock_io_no_nvidia, mock_smc):
    """macOS Apple Silicon enumerator with fake SMC + subprocess mocks."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
         patch(f'{MODULE}.SMCClient') as mc_cls, \
         patch(f'{MODULE}.hid_layer_ready', return_value=False), \
         patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
         patch(f'{MODULE}.subprocess') as sub:
        mc_cls.return_value = FakeSMCClient(mock_smc)

        def _run_side_effect(cmd, **kwargs):
            if 'powermetrics' in cmd:
                return MagicMock(stdout=POWERMETRICS_PLIST_BYTES)
            if 'diskutil' in cmd:
                return MagicMock(stdout=DISKUTIL_OUTPUT)
            return MagicMock(stdout='')
        sub.run.side_effect = _run_side_effect
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def mock_macos_no_smc(mock_io_no_nvidia):
    """macOS Apple Silicon without SMC access."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
         patch(f'{MODULE}.SMCClient') as mc_cls, \
         patch(f'{MODULE}.hid_layer_ready', return_value=False), \
         patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
         patch(f'{MODULE}.subprocess') as sub:
        inst = MagicMock()
        inst.open.return_value = False
        inst.connected = False
        mc_cls.return_value = inst

        def _run_side_effect(cmd, **kwargs):
            if 'powermetrics' in cmd:
                return MagicMock(stdout=POWERMETRICS_PLIST_BYTES)
            if 'diskutil' in cmd:
                return MagicMock(stdout=DISKUTIL_OUTPUT)
            return MagicMock(stdout='')
        sub.run.side_effect = _run_side_effect
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def mock_macos_intel(mock_io_no_nvidia):
    """macOS Intel enumerator without SMC."""
    with patch(f'{MODULE}.IS_APPLE_SILICON', False), \
         patch(f'{MODULE}.SMCClient') as mc_cls, \
         patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
         patch(f'{MODULE}.subprocess') as sub:
        inst = MagicMock()
        inst.open.return_value = False
        inst.connected = False
        mc_cls.return_value = inst
        sub.run.return_value = MagicMock(stdout='')
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def enum_no_smc(mock_macos_no_smc):
    """Discovered macOS enumerator without SMC access."""
    from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
    e = MacOSSensorEnumerator()
    e.discover()
    return e


@pytest.fixture
def enum_intel(mock_macos_intel):
    """Discovered macOS Intel enumerator (no SMC)."""
    from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
    e = MacOSSensorEnumerator()
    e.discover()
    return e


@pytest.fixture
def enum_with_smc(mock_macos, mock_smc):
    """Enumerator with working fake SMC (for SMC-specific assertions)."""
    from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
    e = MacOSSensorEnumerator()
    e.discover()
    return e


class TestDiscover:
    """macOS discover() registers sensors from SMC + powermetrics."""

    def test_apple_silicon_gpu_sensors_registered(self, enum_no_smc):
        ids = [s.id for s in enum_no_smc.get_sensors()]
        assert 'iokit:cpu_power' in ids
        assert 'iokit:gpu_busy' in ids
        assert 'iokit:gpu_clock' in ids
        assert 'iokit:gpu_power' in ids
        assert 'iokit:ane_power' in ids
        assert 'iokit:combined_power' in ids

    def test_base_sensors_registered(self, enum_no_smc):
        ids = [s.id for s in enum_no_smc.get_sensors()]
        assert 'psutil:cpu_percent' in ids
        assert 'computed:disk_read' in ids
        assert 'computed:date_year' in ids

    def test_no_smc_without_iokit(self, enum_no_smc):
        """No SMC sensors when SMCClient.open fails."""
        assert not any(s.source == 'smc' for s in enum_no_smc.get_sensors())

    def test_intel_no_smc_without_iokit(self, enum_intel):
        """Intel Mac without SMC — no SMC sensors, psutil still works."""
        sensors = enum_intel.get_sensors()
        assert not any(s.source == 'smc' for s in sensors)
        assert any(s.source == 'psutil' for s in sensors)

    def test_smc_sensors_when_client_works(self, enum_with_smc):
        ids = [s.id for s in enum_with_smc.get_sensors()]
        assert 'smc:Tp01' in ids
        assert 'smc:F0Ac' in ids
        assert 'smc:F1Ac' in ids


class TestReadAll:
    """macOS read_all() returns sensor readings."""

    def test_gpu_metrics_from_powermetrics(self, enum_no_smc):
        readings = enum_no_smc.read_all()
        assert readings['iokit:gpu_busy'] == 31.0
        assert readings['iokit:gpu_power'] == 4.5
        assert readings['iokit:gpu_clock'] == 1398.0
        assert readings['iokit:combined_power'] == pytest.approx(5.5)
        assert readings['iokit:ane_power'] == 0.0

    def test_cpu_freq_from_powermetrics(self, enum_no_smc):
        readings = enum_no_smc.read_all()
        assert readings['psutil:cpu_freq'] == 2937.0

    def test_powermetrics_text_fallback_when_plist_empty(self, mock_io_no_nvidia):
        """If plist samples are empty, fall back to human-readable powermetrics."""
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}.SMCClient') as mc, \
             patch(f'{MODULE}.hid_layer_ready', return_value=False), \
             patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
             patch(f'{MODULE}.subprocess') as sub:
            inst = MagicMock()
            inst.open.return_value = False
            inst.connected = False
            mc.return_value = inst

            def _run(cmd, **kwargs):
                if 'diskutil' in cmd:
                    return MagicMock(stdout=DISKUTIL_OUTPUT)
                if 'powermetrics' in cmd and isinstance(cmd, list) and '-f' in cmd:
                    return MagicMock(stdout=b'')
                if 'powermetrics' in cmd:
                    return MagicMock(stdout=POWERMETRICS_GPU_OUTPUT)
                return MagicMock(stdout='')

            sub.run.side_effect = _run
            from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert readings['iokit:gpu_busy'] == 31.0
            assert readings['iokit:gpu_power'] == 4.5

    def test_apfs_disk_percent(self, enum_no_smc):
        readings = enum_no_smc.read_all()
        expected = round(400086323200 / 500107862016 * 100, 1)
        assert readings['computed:disk_percent'] == expected

    def test_smc_readings_with_fake_client(self, enum_with_smc):
        r = enum_with_smc.read_all()
        assert abs(r['smc:Tp01'] - 45.0) < 0.1
        assert r['smc:F0Ac'] == 1200.0

    def test_gpu_power_milliwatts(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}.SMCClient') as mc, \
             patch(f'{MODULE}.hid_layer_ready', return_value=False), \
             patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
             patch(f'{MODULE}.subprocess') as sub:
            inst = MagicMock()
            inst.open.return_value = False
            inst.connected = False
            mc.return_value = inst

            def _run(cmd, **kwargs):
                if 'powermetrics' in cmd:
                    mini = plistlib.dumps({
                        'processor': {
                            'gpu_power': 150.0,
                            'cpu_power': 1.0,
                            'ane_power': 0.0,
                            'combined_power': 151.0,
                            'cpu_energy': 1,
                            'gpu_energy': 1,
                            'ane_energy': 0,
                        },
                        'gpu': {
                            'freq_hz': 500.0,
                            'idle_ratio': 1.0,
                            'dvfm_states': [],
                            'sw_requested_state': [],
                            'gpu_energy': 1,
                        },
                    }, fmt=plistlib.FMT_XML)
                    return MagicMock(stdout=mini)
                return MagicMock(stdout='')
            sub.run.side_effect = _run
            from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert readings['iokit:gpu_power'] == 0.15

    def test_powermetrics_failure_degrades_gracefully(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}.SMCClient') as mc, \
             patch(f'{MODULE}.hid_layer_ready', return_value=False), \
             patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
             patch(f'{MODULE}.subprocess') as sub:
            inst = MagicMock()
            inst.open.return_value = False
            inst.connected = False
            mc.return_value = inst

            def _run(cmd, **kwargs):
                if 'powermetrics' in cmd:
                    # Production catches SUBPROCESS_EXC. PermissionError
                    # (an OSError) is the realistic "not running as root" mode.
                    raise PermissionError("no root")
                return MagicMock(stdout='')
            sub.run.side_effect = _run
            from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert 'psutil:cpu_percent' in readings
            assert 'iokit:gpu_busy' not in readings

    def test_diskutil_failure_falls_back_to_psutil(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}.SMCClient') as mc, \
             patch(f'{MODULE}.hid_layer_ready', return_value=False), \
             patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
             patch(f'{MODULE}.subprocess') as sub, \
             patch(f'{MODULE}.psutil') as mac_psutil:
            inst = MagicMock()
            inst.open.return_value = False
            inst.connected = False
            mc.return_value = inst

            def _run(cmd, **kwargs):
                if 'diskutil' in cmd:
                    raise FileNotFoundError("no diskutil")
                return MagicMock(stdout=POWERMETRICS_PLIST_BYTES)
            sub.run.side_effect = _run
            mac_psutil.disk_usage.return_value = MagicMock(percent=55.0)
            from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert readings['computed:disk_percent'] == 55.0


class TestMapDefaults:
    """macOS map_defaults() routes to smc/iokit sources."""

    def test_gpu_mapping(self, enum_no_smc):
        mapping = enum_no_smc.map_defaults()
        assert mapping['cpu_power'] == 'iokit:cpu_power'
        assert mapping['gpu_usage'] == 'iokit:gpu_busy'
        assert mapping['gpu_clock'] == 'iokit:gpu_clock'
        assert mapping['gpu_power'] == 'iokit:gpu_power'

    def test_mem_available_correct(self, enum_no_smc):
        mapping = enum_no_smc.map_defaults()
        assert mapping['mem_available'] == 'psutil:mem_available'

    def test_common_mappings_present(self, enum_no_smc):
        mapping = enum_no_smc.map_defaults()
        assert mapping['disk_activity'] == 'computed:disk_activity'
        assert mapping['net_total_up'] == 'computed:net_total_up'
        assert mapping['net_total_down'] == 'computed:net_total_down'

    def test_cpu_temp_maps_smc(self, enum_with_smc):
        m = enum_with_smc.map_defaults()
        assert m['cpu_temp'] == 'smc:Tp01'

    def test_apple_silicon_prefers_hid_over_smc_for_temps(self, mock_io_no_nvidia):
        """Match iSMC: AS die temps come from HID hub; SMC Tp/Tg are fallback."""
        smc = MockSMC()
        smc.add_key('FNum', 'ui8', 0.0)
        smc.add_key('Tp01', 'sp78', 45.0)
        smc.add_key('Tg0f', 'sp78', 52.0)
        hid_rows = [
            ('hid:CPU_Perf', 'CPU Performance Core 1', 'temperature', '°C', 55.0),
            ('hid:GPU_die', 'GPU die temp', 'temperature', '°C', 50.0),
        ]
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}.SMCClient') as mc, \
             patch(f'{MODULE}.hid_layer_ready', return_value=True), \
             patch(f'{MODULE}.read_hid_sensor_pairs', return_value=hid_rows), \
             patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
             patch(f'{MODULE}.subprocess') as sub:
            mc.return_value = FakeSMCClient(smc)

            def _run(cmd, **kwargs):
                if 'powermetrics' in cmd:
                    return MagicMock(stdout=POWERMETRICS_PLIST_BYTES)
                if 'diskutil' in cmd:
                    return MagicMock(stdout=DISKUTIL_OUTPUT)
                return MagicMock(stdout='')

            sub.run.side_effect = _run
            mock_io_no_nvidia.subprocess = sub
            from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            m = e.map_defaults()
            assert m['cpu_temp'] == 'hid:CPU_Perf'
            assert m['gpu_temp'] == 'hid:GPU_die'

    def test_apple_silicon_hid_pmu_tdie_maps_cpu_gpu(self, mock_io_no_nvidia):
        """PMU tdie HID names lack 'CPU'/'GPU' — mapping must still bind metrics."""
        hid_rows = [
            ('hid:PMU_tdie1', 'PMU_tdie1', 'temperature', '°C', 44.0),
            ('hid:PMU_tdie9', 'PMU_tdie9', 'temperature', '°C', 43.0),
        ]
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}.SMCClient') as mc, \
             patch(f'{MODULE}.hid_layer_ready', return_value=True), \
             patch(f'{MODULE}.read_hid_sensor_pairs', return_value=hid_rows), \
             patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=None), \
             patch(f'{MODULE}.subprocess') as sub:
            inst = MagicMock()
            inst.open.return_value = False
            inst.connected = False
            mc.return_value = inst

            def _run(cmd, **kwargs):
                if 'powermetrics' in cmd:
                    return MagicMock(stdout=POWERMETRICS_PLIST_BYTES)
                if 'diskutil' in cmd:
                    return MagicMock(stdout=DISKUTIL_OUTPUT)
                return MagicMock(stdout='')

            sub.run.side_effect = _run
            mock_io_no_nvidia.subprocess = sub
            from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            m = e.map_defaults()
            assert m['cpu_temp'] == 'hid:PMU_tdie1'
            assert m['gpu_temp'] == 'hid:PMU_tdie9'

    def test_powermetrics_helper_skips_subprocess(self, mock_io_no_nvidia):
        """When fetch_powermetrics_bytes returns data, subprocess is not used."""
        with patch(f'{MODULE}.IS_APPLE_SILICON', True), \
             patch(f'{MODULE}.SMCClient') as mc, \
             patch(f'{MODULE}.hid_layer_ready', return_value=False), \
             patch(f'{MODULE}.fetch_powermetrics_bytes', return_value=POWERMETRICS_PLIST_BYTES), \
             patch(f'{MODULE}.subprocess') as sub:
            inst = MagicMock()
            inst.open.return_value = False
            inst.connected = False
            mc.return_value = inst

            def _run(cmd, **kwargs):
                if 'diskutil' in cmd:
                    return MagicMock(stdout=DISKUTIL_OUTPUT)
                pytest.fail(f'unexpected subprocess: {cmd!r}')

            sub.run.side_effect = _run

            from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator
            e = MacOSSensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert readings['iokit:gpu_busy'] == 31.0
            for c in sub.run.call_args_list:
                argv = c.args[0] if c.args else ()
                if argv and argv[0] == 'powermetrics':
                    pytest.fail('powermetrics subprocess should not run when helper returns data')


class TestSMCParsing:
    """parse_smc_bytes handles SMC data types."""

    def test_sp78_temperature(self):
        from trcc.legacy.adapters.system.macos.smc_client import parse_smc_bytes
        dt = struct.unpack('>I', b'sp78')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('>h', int(45.5 * 256))
        raw[0], raw[1] = val[0], val[1]
        result = parse_smc_bytes(dt, raw, 2)
        assert abs(result - 45.5) < 0.01

    def test_fpe2_fan_speed(self):
        from trcc.legacy.adapters.system.macos.smc_client import parse_smc_bytes
        dt = struct.unpack('>I', b'fpe2')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('>H', (1200 * 4))
        raw[0], raw[1] = val[0], val[1]
        result = parse_smc_bytes(dt, raw, 2)
        assert result == 1200.0

    def test_flt_float(self):
        from trcc.legacy.adapters.system.macos.smc_client import parse_smc_bytes
        dt = struct.unpack('>I', b'flt ')[0]
        raw = (ctypes.c_uint8 * 32)()
        val = struct.pack('<f', 3.14)
        for i, b in enumerate(val):
            raw[i] = b
        result = parse_smc_bytes(dt, raw, 4)
        assert abs(result - 3.14) < 0.01

    def test_read_fan_rpm_fpe2_when_datatype_misparsed(self):
        """Wrong SMC datatype can make parse_smc_bytes tiny; raw uint16/4 is RPM."""
        from trcc.legacy.adapters.system.macos.smc_client import SMCClient

        c = SMCClient()
        buf = (ctypes.c_uint8 * 32)()
        struct.pack_into('>H', buf, 0, (1200 * 4))
        dt_fp1f = struct.unpack('>I', b'fp1f')[0]

        def fake_raw(_k: str):
            return dt_fp1f, 2, buf

        c._read_key_raw = fake_raw  # type: ignore[method-assign]
        assert abs(c.read_fan_rpm('F0Ac') - 1200.0) < 0.01

    def test_read_fan_rpm_prefers_fpe2_when_parsed_absurdly_high(self):
        """Misparsed value in-band (~12k) vs fpe2 ~1.3k (matches iStat-style SMC)."""
        from trcc.legacy.adapters.system.macos.smc_client import SMCClient

        c = SMCClient()
        buf = (ctypes.c_uint8 * 32)()
        struct.pack_into('>H', buf, 0, (1336 * 4))

        def fake_raw(_k: str):
            return struct.unpack('>I', b'ui16')[0], 2, buf

        c._read_key_raw = fake_raw  # type: ignore[method-assign]
        with patch(
            'trcc.legacy.adapters.system.macos.smc_client.parse_smc_bytes',
            return_value=12352.0,
        ):
            assert abs(c.read_fan_rpm('F0Ac') - 1336.0) < 0.5

    def test_read_fan_rpm_literal_ui16_when_matches_raw(self):
        from trcc.legacy.adapters.system.macos.smc_client import SMCClient

        c = SMCClient()
        buf = (ctypes.c_uint8 * 32)()
        struct.pack_into('>H', buf, 0, 1336)
        dt_ui16 = struct.unpack('>I', b'ui16')[0]

        def fake_raw(_k: str):
            return dt_ui16, 2, buf

        c._read_key_raw = fake_raw  # type: ignore[method-assign]
        assert abs(c.read_fan_rpm('F0Ac') - 1336.0) < 0.1

    def test_read_fan_rpm_flt_apple_silicon_matches_ismc(self):
        """F0Ac as flt (~1.3k RPM) — same encoding as ``ismc -o json``."""
        from trcc.legacy.adapters.system.macos.smc_client import SMCClient

        c = SMCClient()
        buf = (ctypes.c_uint8 * 32)()
        struct.pack_into('<f', buf, 0, 1339.0)
        dt_flt = struct.unpack('>I', b'flt ')[0]

        def fake_raw(_k: str):
            return dt_flt, 4, buf

        c._read_key_raw = fake_raw  # type: ignore[method-assign]
        assert abs(c.read_fan_rpm('F0Ac') - 1339.0) < 0.5


class TestHidDedupe:
    def test_dedupe_hid_pairs_keeps_first_per_name(self):
        from trcc.legacy.adapters.system.macos.hid_sensors import _dedupe_hid_pairs_by_name

        pairs = [('PMU_tdie1', 40.0), ('PMU_tdie1', 41.0), ('PMU_tdie2', 39.0)]
        assert _dedupe_hid_pairs_by_name(pairs) == [
            ('PMU_tdie1', 40.0), ('PMU_tdie2', 39.0),
        ]


class TestHidThermalNormalize:
    """Sanity bounds and sp78-style decoding for HID thermal floats."""

    def test_direct_celsius(self):
        from trcc.legacy.adapters.system.macos.hid_sensors import _normalize_hid_thermal_celsius

        assert _normalize_hid_thermal_celsius('PMU_tdie1', 34.5) == 34.5

    def test_tdev_raw_sp78(self):
        from trcc.legacy.adapters.system.macos.hid_sensors import _normalize_hid_thermal_celsius

        assert abs(_normalize_hid_thermal_celsius('PMU tdev2', 6400.0) - 25.0) < 0.01

    def test_generic_sp78_range(self):
        from trcc.legacy.adapters.system.macos.hid_sensors import _normalize_hid_thermal_celsius

        assert abs(_normalize_hid_thermal_celsius('PMU_tdie9', 8704.0) - 34.0) < 0.01

    def test_rejects_garbage_magnitude(self):
        from trcc.legacy.adapters.system.macos.hid_sensors import _normalize_hid_thermal_celsius

        assert _normalize_hid_thermal_celsius('PMU_tdie1', 1e200) is None
        assert _normalize_hid_thermal_celsius('PMU_tdie1', float('nan')) is None


class TestParseMetric:
    """_parse_metric helper — powermetrics line parser."""

    def test_temperature(self):
        from trcc.legacy.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('CPU die temperature: 45.23 C') == 45.23

    def test_fan(self):
        from trcc.legacy.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('Fan: 1200 rpm') == 1200.0

    def test_no_number(self):
        from trcc.legacy.adapters.system.macos.sensors import _parse_metric
        assert _parse_metric('no numbers here') == 0.0
