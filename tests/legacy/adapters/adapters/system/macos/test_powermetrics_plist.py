"""Unit tests for ``powermetrics -f plist`` parsing."""
from __future__ import annotations

import plistlib

import pytest

from trcc.legacy.adapters.system.macos.powermetrics_plist import parse_powermetrics_plist


def test_parse_rejects_non_plist() -> None:
    assert parse_powermetrics_plist(b'GPU Power: 1 W\n') is None
    assert parse_powermetrics_plist('not xml') is None


def test_parse_combined_sample() -> None:
    proc = {
        'cpu_power': 2000.0,
        'gpu_power': 3000.0,
        'ane_power': 100.0,
        'combined_power': 5100.0,
        'cpu_energy': 1,
        'gpu_energy': 1,
        'ane_energy': 1,
        'clusters': [
            {'cpus': [{'cpu': 0, 'freq_hz': 2.4e9}]},
        ],
    }
    gpu = {
        'freq_hz': 1200.0,
        'idle_ratio': 0.5,
        'dvfm_states': [
            {'freq': 500, 'used_ns': 1, 'used_ratio': 0.2},
            {'freq': 1200, 'used_ns': 1, 'used_ratio': 0.6},
        ],
        'sw_requested_state': [],
        'gpu_energy': 1,
    }
    blob = plistlib.dumps({'processor': proc, 'gpu': gpu}, fmt=plistlib.FMT_XML)
    out = parse_powermetrics_plist(blob)
    assert out is not None
    assert out['iokit:cpu_power'] == pytest.approx(2.0)
    assert out['iokit:gpu_power'] == pytest.approx(3.0)
    assert out['iokit:ane_power'] == pytest.approx(0.1)
    assert out['iokit:combined_power'] == pytest.approx(5.1)
    assert out['iokit:gpu_busy'] == pytest.approx(80.0)
    assert out['iokit:gpu_clock'] == pytest.approx(1200.0)
    assert out['psutil:cpu_freq'] == pytest.approx(2400.0)


def test_parse_gpu_only_no_processor_power() -> None:
    gpu = {
        'freq_hz': 800.0,
        'idle_ratio': 0.9,
        'dvfm_states': [{'freq': 389, 'used_ns': 1, 'used_ratio': 0.05}],
        'sw_requested_state': [],
        'gpu_energy': 1,
    }
    blob = plistlib.dumps({'gpu': gpu}, fmt=plistlib.FMT_XML)
    out = parse_powermetrics_plist(blob)
    assert out is not None
    assert 'iokit:gpu_power' not in out
    assert out['iokit:gpu_busy'] == pytest.approx(5.0)
    assert out['iokit:gpu_clock'] == pytest.approx(800.0)
