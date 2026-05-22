"""Tests for BSD sensor enumerator — platform-specific behavior only.

Shared base behavior (psutil, nvidia, computed I/O, polling, read_all)
is tested in tests/adapters/system/conftest.py.

Tests follow the app flow: discover() → read_all() → map_defaults().
Mock at I/O boundary only: subprocess for sysctl.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

MODULE = 'trcc.legacy.adapters.system.bsd_platform'

SYSCTL_OUTPUT = (
    "dev.cpu.0.temperature: 45.0C\n"
    "dev.cpu.1.temperature: 47.0C\n"
    "hw.acpi.thermal.tz0.temperature: 40.0C\n"
)


@pytest.fixture
def mock_bsd(mock_io_no_nvidia):
    """BSD enumerator with mocked sysctl."""
    with patch(f'{MODULE}.subprocess') as sub:
        sub.run.return_value = MagicMock(returncode=0, stdout=SYSCTL_OUTPUT)
        mock_io_no_nvidia.subprocess = sub
        yield mock_io_no_nvidia


@pytest.fixture
def enum(mock_bsd):
    """Discovered BSD enumerator — ready for read_all/map_defaults."""
    from trcc.legacy.adapters.system.bsd_platform import SensorEnumerator
    e = SensorEnumerator()
    e.discover()
    return e


class TestDiscover:
    """BSD discover() registers sysctl sensors alongside base sensors."""

    def test_sysctl_cpu_temps_registered(self, enum):
        ids = [s.id for s in enum.get_sensors()]
        assert 'sysctl:cpu0_temp' in ids
        assert 'sysctl:cpu1_temp' in ids

    def test_sysctl_thermal_zones_registered(self, enum):
        ids = [s.id for s in enum.get_sensors()]
        assert 'sysctl:tz0_temp' in ids

    def test_base_sensors_also_registered(self, enum):
        ids = [s.id for s in enum.get_sensors()]
        assert 'psutil:cpu_percent' in ids
        assert 'computed:disk_read' in ids
        assert 'computed:date_year' in ids

    def test_sysctl_failure_degrades_gracefully(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.subprocess') as sub:
            # Production catches SUBPROCESS_EXC — realistic sysctl absence is
            # FileNotFoundError, which is an OSError.
            sub.run.side_effect = FileNotFoundError("no sysctl")
            from trcc.legacy.adapters.system.bsd_platform import SensorEnumerator
            e = SensorEnumerator()
            sensors = e.discover()
            assert not any(s.source == 'sysctl' for s in sensors)
            assert any(s.source == 'psutil' for s in sensors)


class TestReadAll:
    """BSD read_all() returns sysctl temps alongside base readings."""

    def test_sysctl_temps_in_readings(self, enum):
        readings = enum.read_all()
        assert readings['sysctl:cpu0_temp'] == 45.0
        assert readings['sysctl:cpu1_temp'] == 47.0
        assert readings['sysctl:tz0_temp'] == 40.0

    def test_psutil_readings_also_present(self, enum):
        readings = enum.read_all()
        assert 'psutil:cpu_percent' in readings
        assert 'psutil:mem_percent' in readings

    def test_datetime_readings_present(self, enum):
        readings = enum.read_all()
        assert readings['computed:date_year'] == 2026.0

    def test_sysctl_failure_returns_base_readings_only(self, mock_io_no_nvidia):
        with patch(f'{MODULE}.subprocess') as sub:
            # Production catches SUBPROCESS_EXC. OSError covers permission /
            # not-found / timeout-style failures; bare RuntimeError is not caught.
            sub.run.side_effect = OSError("sysctl died")
            from trcc.legacy.adapters.system.bsd_platform import SensorEnumerator
            e = SensorEnumerator()
            e.discover()
            readings = e.read_all()
            assert 'psutil:cpu_percent' in readings
            assert 'sysctl:cpu0_temp' not in readings


class TestMapDefaults:
    """BSD map_defaults() routes CPU temp to sysctl source."""

    def test_cpu_temp_from_sysctl(self, enum):
        mapping = enum.map_defaults()
        assert mapping['cpu_temp'].startswith('sysctl:')

    def test_common_mappings_present(self, enum):
        mapping = enum.map_defaults()
        assert mapping['cpu_percent'] == 'psutil:cpu_percent'
        assert mapping['disk_read'] == 'computed:disk_read'
        assert mapping['net_total_up'] == 'computed:net_total_up'

    def test_no_gpu_without_nvidia(self, enum):
        mapping = enum.map_defaults()
        assert 'gpu_temp' not in mapping
