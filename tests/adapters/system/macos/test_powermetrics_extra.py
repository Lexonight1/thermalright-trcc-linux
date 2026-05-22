"""Tests for optional TRCC_POWERMETRICS_EXTRA_SAMPLERS plist merge."""
from __future__ import annotations

import os
import plistlib
from unittest.mock import MagicMock, patch

import pytest

from tests.adapters.system.macos.test_sensors import (
    DISKUTIL_OUTPUT,
    POWERMETRICS_PLIST_BYTES,
    FakeSMCClient,
    MockSMC,
)
from trcc.legacy.adapters.system.macos.powermetrics_extra import (
    extra_powermetrics_sampler_csv,
    extra_powermetrics_sensor_specs,
    full_powermetrics_sampler_csv,
    readings_from_powermetrics_extras,
)


def test_sampler_csv_dedupes_and_filters() -> None:
    with patch.dict(os.environ, {'TRCC_POWERMETRICS_EXTRA_SAMPLERS': 'network, disk ,network,foo'}):
        assert extra_powermetrics_sampler_csv() == 'network,disk'


def test_full_sampler_csv_appends_extras() -> None:
    with patch.dict(os.environ, {'TRCC_POWERMETRICS_EXTRA_SAMPLERS': 'thermal,network'}):
        assert full_powermetrics_sampler_csv() == 'gpu_power,cpu_power,thermal,network'


def test_sampler_csv_empty() -> None:
    with patch.dict(os.environ, {'TRCC_POWERMETRICS_EXTRA_SAMPLERS': ''}):
        assert extra_powermetrics_sampler_csv() is None
        assert full_powermetrics_sampler_csv() == 'gpu_power,cpu_power'


def test_sensor_specs_order() -> None:
    with patch.dict(os.environ, {'TRCC_POWERMETRICS_EXTRA_SAMPLERS': 'battery,thermal'}):
        specs = extra_powermetrics_sensor_specs()
        ids = [s[0] for s in specs]
        assert ids[0] == 'iokit:battery_percent'
        assert ids[1] == 'iokit:thermal_pressure'


def test_readings_extras_parse() -> None:
    root = plistlib.loads(plistlib.dumps({
        'thermal_pressure': 'Moderate',
        'battery': {'percent_charge': 87},
        'network': {
            'ibyte_rate': 100.0,
            'obyte_rate': 200.0,
            'ipacket_rate': 1.0,
            'opacket_rate': 2.0,
        },
        'disk': {
            'rbytes_per_s': 0.0,
            'wbytes_per_s': 1e6,
            'rops_per_s': 0.0,
            'wops_per_s': 10.0,
        },
    }, fmt=plistlib.FMT_XML))
    r = readings_from_powermetrics_extras(root)
    assert r['iokit:thermal_pressure'] == 2.0
    assert r['iokit:battery_percent'] == 87.0
    assert r['iokit:net_ibyte_rate'] == 100.0
    assert r['iokit:disk_wbytes_per_s'] == 1e6


@pytest.fixture
def mock_smc_as():
    smc = MockSMC()
    smc.add_key('FNum', 'ui8', 2.0)
    smc.add_key('Tp01', 'sp78', 45.0)
    smc.add_key('Tg0f', 'sp78', 52.0)
    smc.add_key('Tm0P', 'sp78', 38.0)
    smc.add_key('F0Ac', 'fpe2', 1200.0)
    smc.add_key('F1Ac', 'fpe2', 1350.0)
    return smc


@pytest.fixture
def enum_as_with_extras(mock_io_no_nvidia, mock_smc_as):
    """Apple Silicon enumerator with SMC + extra powermetrics env."""
    with patch.dict(os.environ, {'TRCC_POWERMETRICS_EXTRA_SAMPLERS': 'thermal'}):
        with patch('trcc.legacy.adapters.system.macos.sensors.IS_APPLE_SILICON', True), \
             patch('trcc.legacy.adapters.system.macos.sensors.SMCClient') as mc_cls, \
             patch('trcc.legacy.adapters.system.macos.sensors.hid_layer_ready', return_value=False), \
             patch('trcc.legacy.adapters.system.macos.sensors.fetch_powermetrics_bytes', return_value=None), \
             patch('trcc.legacy.adapters.system.macos.sensors.subprocess') as sub:
            from trcc.legacy.adapters.system.macos.sensors import MacOSSensorEnumerator

            mc_cls.return_value = FakeSMCClient(mock_smc_as)

            combined = plistlib.dumps({
                **plistlib.loads(POWERMETRICS_PLIST_BYTES),
                'thermal_pressure': 'Nominal',
            }, fmt=plistlib.FMT_XML)

            def _run(cmd, **kwargs):
                if not isinstance(cmd, (list, tuple)):
                    return MagicMock(stdout='')
                if 'diskutil' in cmd:
                    return MagicMock(stdout=DISKUTIL_OUTPUT)
                if 'powermetrics' not in cmd:
                    return MagicMock(stdout='')
                joined = ' '.join(str(x) for x in cmd)
                if '-f' in joined:
                    return MagicMock(stdout=combined)
                return MagicMock(stdout='')

            sub.run.side_effect = _run
            e = MacOSSensorEnumerator()
            e.discover()
            yield e


def test_discover_registers_extra_sensors(enum_as_with_extras) -> None:
    ids = [s.id for s in enum_as_with_extras.get_sensors()]
    assert 'iokit:thermal_pressure' in ids


def test_poll_merges_extra_thermal(enum_as_with_extras) -> None:
    r = enum_as_with_extras.read_all()
    assert r.get('iokit:thermal_pressure') == 0.0
