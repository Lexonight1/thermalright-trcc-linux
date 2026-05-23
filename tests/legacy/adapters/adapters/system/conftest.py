"""Shared fixtures for platform sensor enumerator tests.

Tests follow the app's DI flow:
    Builder creates enumerator → discover() → start_polling() → read_all() → map_defaults()

Fixtures mock at I/O boundaries only (psutil, pynvml, time, datetime).
Tests call public API — never internal methods.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime as real_datetime
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.core.models import SensorInfo

# Patch targets for the shared base class
BASE = 'trcc.legacy.adapters.system._base'


@dataclass
class MockIO:
    """Holds all mocked I/O boundaries. Platform conftest extends this."""
    psutil: MagicMock = field(default_factory=MagicMock)
    pynvml: MagicMock = field(default_factory=MagicMock)
    time: MagicMock = field(default_factory=MagicMock)
    datetime: MagicMock = field(default_factory=MagicMock)

    def setup_psutil(
        self, *,
        cpu_percent: float = 5.0,
        cpu_freq: float = 3200.0,
        mem_used_mb: float = 8000.0,
        mem_available_mb: float = 8000.0,
        mem_total_mb: float = 16000.0,
        mem_percent: float = 50.0,
        disk_percent: float = 45.0,
    ) -> None:
        """Configure psutil to return sane defaults."""
        self.psutil.cpu_percent.return_value = cpu_percent
        self.psutil.cpu_freq.return_value = MagicMock(current=cpu_freq)
        self.psutil.virtual_memory.return_value = MagicMock(
            used=int(mem_used_mb * 1024 * 1024),
            available=int(mem_available_mb * 1024 * 1024),
            total=int(mem_total_mb * 1024 * 1024),
            percent=mem_percent,
        )
        self.psutil.disk_io_counters.return_value = None
        self.psutil.net_io_counters.return_value = None
        self.psutil.disk_usage.return_value = MagicMock(percent=disk_percent)

    def setup_nvidia(
        self, *,
        name: str = 'RTX 4090',
        temp: int = 65,
        usage: int = 30,
        clock: int = 1800,
        power_mw: int = 250000,
        fan: int = 45,
        vram_used_mb: int = 4096,
        vram_total_mb: int = 16384,
    ) -> None:
        """Configure pynvml to return one GPU with given metrics."""
        self.pynvml.nvmlDeviceGetCount.return_value = 1
        self.pynvml.nvmlDeviceGetHandleByIndex.return_value = 'h0'
        name_val = name
        self.pynvml.nvmlDeviceGetName.return_value = name_val
        self.pynvml.NVML_TEMPERATURE_GPU = 0
        self.pynvml.NVML_CLOCK_GRAPHICS = 0
        self.pynvml.NVML_CLOCK_MEM = 1
        self.pynvml.nvmlDeviceGetTemperature.return_value = temp
        self.pynvml.nvmlDeviceGetUtilizationRates.return_value = MagicMock(
            gpu=usage, memory=15)
        self.pynvml.nvmlDeviceGetClockInfo.return_value = clock
        self.pynvml.nvmlDeviceGetPowerUsage.return_value = power_mw
        self.pynvml.nvmlDeviceGetFanSpeed.return_value = fan
        self.pynvml.nvmlDeviceGetMemoryInfo.return_value = MagicMock(
            used=vram_used_mb * 1024 * 1024,
            total=vram_total_mb * 1024 * 1024,
        )

    def setup_no_nvidia(self) -> None:
        """Configure pynvml as unavailable."""
        self.pynvml.nvmlDeviceGetCount.side_effect = RuntimeError("no driver")

    def setup_time(self, start: float = 100.0) -> None:
        """Configure monotonic clock."""
        self.time.monotonic.return_value = start

    def setup_datetime(
        self, year: int = 2026, month: int = 4, day: int = 9,
        hour: int = 14, minute: int = 30, second: int = 0,
    ) -> None:
        """Configure datetime.now()."""
        self.datetime.datetime.now.return_value = real_datetime(
            year, month, day, hour, minute, second)

    def setup_disk_io_deltas(
        self, *,
        read_bytes_1: int = 0, write_bytes_1: int = 0,
        read_bytes_2: int = 2 * 1024 * 1024, write_bytes_2: int = 1024 * 1024,
        time_1: float = 100.0, time_2: float = 102.0,
    ) -> None:
        """Configure two full poll cycles for disk rate computation.

        _poll_once calls time.monotonic() once (in _poll_computed_io).
        Two read_all() calls = two _poll_once() = two monotonic() calls.
        """
        self.time.monotonic.side_effect = [time_1, time_2]
        self.psutil.disk_io_counters.side_effect = [
            MagicMock(read_bytes=read_bytes_1, write_bytes=write_bytes_1),
            MagicMock(read_bytes=read_bytes_2, write_bytes=write_bytes_2),
        ]
        self.psutil.net_io_counters.return_value = None

    def setup_net_io_deltas(
        self, *,
        sent_1: int = 0, recv_1: int = 0,
        sent_2: int = 2048, recv_2: int = 4096,
        time_1: float = 100.0, time_2: float = 102.0,
    ) -> None:
        """Configure two full poll cycles for network rate computation."""
        self.time.monotonic.side_effect = [time_1, time_2]
        self.psutil.disk_io_counters.return_value = None
        self.psutil.net_io_counters.side_effect = [
            MagicMock(bytes_sent=sent_1, bytes_recv=recv_1),
            MagicMock(bytes_sent=sent_2, bytes_recv=recv_2),
        ]

    def setup_defaults(self) -> None:
        """Set up all mocks with sane defaults for a full discover+read cycle."""
        self.setup_psutil()
        self.setup_time()
        self.setup_datetime()


@pytest.fixture
def mock_io():
    """Mock all base I/O boundaries. Returns MockIO with patches active.

    ``_pn`` is the module-level alias the production code reads — bound to
    ``pynvml`` at import time. Patching ``pynvml`` alone misses ``_pn``, so
    both names get the same mock here.
    """
    io = MockIO()
    with patch(f'{BASE}.psutil', io.psutil), \
         patch(f'{BASE}.pynvml', io.pynvml), \
         patch(f'{BASE}._pn', io.pynvml), \
         patch(f'{BASE}.NVML_AVAILABLE', True), \
         patch(f'{BASE}.time', io.time), \
         patch(f'{BASE}.datetime', io.datetime):
        io.setup_defaults()
        yield io


@pytest.fixture
def mock_io_no_nvidia():
    """Mock base I/O with NVIDIA unavailable."""
    io = MockIO()
    with patch(f'{BASE}.psutil', io.psutil), \
         patch(f'{BASE}.pynvml', None), \
         patch(f'{BASE}._pn', None), \
         patch(f'{BASE}.NVML_AVAILABLE', False), \
         patch(f'{BASE}.time', io.time), \
         patch(f'{BASE}.datetime', io.datetime):
        io.setup_defaults()
        yield io


# ══════════════════════════════════════════════════════════════════════
# Base behavior tests — tested once, covers all platforms
# ══════════════════════════════════════════════════════════════════════


def _make_base_enum():
    """Create lightest subclass (BSD) for base behavior tests."""
    from trcc.legacy.adapters.system.bsd_platform import SensorEnumerator
    return SensorEnumerator()


class TestDiscover:
    """discover() registers sensors from all sources."""

    def test_psutil_sensors_registered(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        sensors = enum.discover()
        ids = [s.id for s in sensors]
        assert 'psutil:cpu_percent' in ids
        assert 'psutil:cpu_freq' in ids
        assert 'psutil:mem_available' in ids
        assert 'psutil:mem_percent' in ids
        assert 'computed:disk_read' in ids
        assert 'computed:disk_activity' in ids
        assert 'computed:net_up' in ids
        assert 'computed:net_total_up' in ids

    def test_nvidia_sensors_registered(self, mock_io):
        mock_io.setup_nvidia()
        enum = _make_base_enum()
        sensors = enum.discover()
        ids = [s.id for s in sensors]
        assert 'nvidia:0:temp' in ids
        assert 'nvidia:0:gpu_busy' in ids
        assert 'nvidia:0:clock' in ids
        assert 'nvidia:0:power' in ids
        assert 'nvidia:0:fan' in ids
        assert 'nvidia:0:mem_used' in ids
        assert 'nvidia:0:mem_total' in ids

    def test_datetime_sensors_registered(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        sensors = enum.discover()
        ids = [s.id for s in sensors]
        assert 'computed:date_year' in ids
        assert 'computed:day_of_week' in ids

    def test_no_nvidia_when_unavailable(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        sensors = enum.discover()
        assert not any(s.source == 'nvidia' for s in sensors)

    def test_discover_clears_previous(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        enum._sensors = [SensorInfo('old', 'Old', 'x', 'x', 'x')]
        enum.discover()
        assert not any(s.id == 'old' for s in enum.get_sensors())


class TestReadAll:
    """read_all() returns sensor data through the full pipeline."""

    def test_returns_psutil_readings(self, mock_io_no_nvidia):
        mock_io_no_nvidia.setup_psutil(cpu_percent=42.0, cpu_freq=3600.0, mem_percent=75.0)
        enum = _make_base_enum()
        enum.discover()
        readings = enum.read_all()
        assert readings['psutil:cpu_percent'] == 42.0
        assert readings['psutil:cpu_freq'] == 3600.0
        assert readings['psutil:mem_percent'] == 75.0

    def test_returns_nvidia_readings(self, mock_io):
        mock_io.setup_nvidia(temp=72, usage=95, power_mw=320000)
        enum = _make_base_enum()
        enum.discover()
        readings = enum.read_all()
        assert readings['nvidia:0:temp'] == 72.0
        assert readings['nvidia:0:gpu_busy'] == 95.0
        assert readings['nvidia:0:power'] == 320.0

    def test_returns_datetime_readings(self, mock_io_no_nvidia):
        mock_io_no_nvidia.setup_datetime(year=2026, month=4, day=9, hour=14, minute=30)
        enum = _make_base_enum()
        enum.discover()
        readings = enum.read_all()
        assert readings['computed:date_year'] == 2026.0
        assert readings['computed:date_month'] == 4.0
        assert readings['computed:time_hour'] == 14.0

    def test_bootstraps_on_first_call(self, mock_io_no_nvidia):
        """read_all() triggers poll if no data cached yet."""
        enum = _make_base_enum()
        enum.discover()
        assert enum._readings == {}
        readings = enum.read_all()
        assert 'psutil:cpu_percent' in readings

    def test_returns_copy(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        enum.discover()
        r = enum.read_all()
        r['psutil:cpu_percent'] = 999.0
        assert enum.read_all()['psutil:cpu_percent'] != 999.0

    def test_cpu_percent_bootstraps_with_short_interval(self, mock_io_no_nvidia):
        """First poll uses interval=0.08 to avoid 0.0 cold start."""
        enum = _make_base_enum()
        enum.discover()
        enum.read_all()
        mock_io_no_nvidia.psutil.cpu_percent.assert_called_once_with(interval=0.08)


class TestComputedIO:
    """Disk/network rate computation from counter deltas."""

    def test_disk_rates_after_two_ticks(self, mock_io_no_nvidia):
        """Rates appear after the polling thread's second tick."""
        mock_io_no_nvidia.setup_disk_io_deltas()
        enum = _make_base_enum()
        enum.discover()
        # Simulate two poll ticks (what the background thread does)
        enum._poll_once()  # tick 1 — seeds baseline
        enum._poll_once()  # tick 2 — computes rates
        readings = enum.read_all()
        assert readings['computed:disk_read'] == 1.0   # 2MB / 2s
        assert readings['computed:disk_write'] == 0.5   # 1MB / 2s

    def test_net_rates_after_two_ticks(self, mock_io_no_nvidia):
        mock_io_no_nvidia.setup_net_io_deltas()
        enum = _make_base_enum()
        enum.discover()
        enum._poll_once()
        enum._poll_once()
        readings = enum.read_all()
        assert readings['computed:net_up'] == 1.0     # 2048B / 2s / 1024 = 1 KB/s
        assert readings['computed:net_down'] == 2.0   # 4096B / 2s / 1024 = 2 KB/s

    def test_net_totals_on_first_tick(self, mock_io_no_nvidia):
        mock_io_no_nvidia.psutil.disk_io_counters.return_value = None
        mock_io_no_nvidia.psutil.net_io_counters.return_value = MagicMock(
            bytes_sent=10 * 1024 * 1024, bytes_recv=20 * 1024 * 1024)
        enum = _make_base_enum()
        enum.discover()
        readings = enum.read_all()  # bootstraps first tick
        assert readings['computed:net_total_up'] == 10.0
        assert readings['computed:net_total_down'] == 20.0
        assert 'computed:net_up' not in readings  # no rate without previous sample


class TestMapDefaults:
    """map_defaults() returns metric→sensor_id mapping."""

    def test_common_mappings_present(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        enum.discover()
        mapping = enum.map_defaults()
        assert mapping['cpu_percent'] == 'psutil:cpu_percent'
        assert mapping['cpu_freq'] == 'psutil:cpu_freq'
        assert mapping['mem_percent'] == 'psutil:mem_percent'
        assert mapping['mem_available'] == 'psutil:mem_available'
        assert mapping['disk_read'] == 'computed:disk_read'
        assert mapping['net_up'] == 'computed:net_up'
        assert mapping['net_total_up'] == 'computed:net_total_up'

    def test_caches_result(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        enum.discover()
        m1 = enum.map_defaults()
        m2 = enum.map_defaults()
        assert m1 is m2


class TestPolling:
    """start_polling / stop_polling lifecycle."""

    def test_start_stop(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        enum.discover()
        enum.start_polling(interval=0.01)
        assert enum._poll_thread is not None
        assert enum._poll_thread.is_alive()
        enum.stop_polling()
        assert enum._poll_thread is None

    def test_double_start_is_noop(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        enum.discover()
        enum.start_polling(interval=0.01)
        first = enum._poll_thread
        enum.start_polling(interval=0.01)
        assert enum._poll_thread is first
        enum.stop_polling()


class TestGetters:
    """get_sensors, get_by_category, read_one."""

    def test_get_by_category(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        enum.discover()
        psutil_sensors = enum.get_by_category('cpu_percent')
        assert len(psutil_sensors) == 1
        assert psutil_sensors[0].id == 'psutil:cpu_percent'

    def test_read_one(self, mock_io_no_nvidia):
        enum = _make_base_enum()
        enum.discover()
        enum.read_all()  # populate cache
        val = enum.read_one('psutil:cpu_percent')
        assert val is not None
        assert val == 5.0


class TestFindFirst:
    """_find_first static helper."""

    def test_finds_by_source(self):
        from trcc.legacy.adapters.system._base import SensorEnumeratorBase
        sensors = [
            SensorInfo('a', 'A', 'temperature', '°C', 'hwmon'),
            SensorInfo('b', 'B', 'temperature', '°C', 'nvidia'),
        ]
        assert SensorEnumeratorBase._find_first(sensors, source='nvidia') == 'b'

    def test_returns_empty_on_no_match(self):
        from trcc.legacy.adapters.system._base import SensorEnumeratorBase
        assert SensorEnumeratorBase._find_first([], source='nvidia') == ''

    def test_finds_by_name_and_category(self):
        from trcc.legacy.adapters.system._base import SensorEnumeratorBase
        sensors = [
            SensorInfo('a', 'CPU Die', 'temperature', '°C', 'iokit'),
            SensorInfo('b', 'GPU Die', 'temperature', '°C', 'iokit'),
        ]
        assert SensorEnumeratorBase._find_first(
            sensors, name_contains='GPU', category='temperature') == 'b'
