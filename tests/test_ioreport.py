"""IOReport power and GPU-residency maths.

The Darwin binding only loads on macOS. These tests cover the pure
reduction: which Energy Model channels are the package, how units
become watts, how the ``OFF`` state is idle, and how the DVFS table
turns ``GPUPH`` residency into MHz.
"""
from __future__ import annotations

import struct

from trcc.adapters.sensors._ioreport import (
    IOReportGpu,
    _IOReportSnapshot,
    _RawSample,
    active_percent,
    derive,
    dvfs_table_mhz,
    effective_mhz,
    joules_to_watts,
    split_power,
)


def test_package_power_sums_dies_and_skips_cluster_rows() -> None:
    """Ultra publishes one ``CPU Energy`` per die. Cluster rows repeat it."""
    cpu, gpu = split_power(
        [
            ("DIE_0_EACC_CPU", "mJ", 100),
            ("DIE_0_CPU Energy", "mJ", 400),
            ("DIE_1_CPU Energy", "mJ", 100),
            ("GPU Energy", "nJ", 50_000_000),
            ("PCIe Port 0 Energy", "uJ", 1000),
        ],
        dt=1.0,
    )
    assert cpu == 0.5
    assert gpu == 0.05


def test_joules_to_watts_rejects_a_reset_and_a_unit_mixup() -> None:
    assert joules_to_watts(-1, "mJ", 1.0) is None
    assert joules_to_watts(10**12, "mJ", 0.4) is None
    assert joules_to_watts(10, "ticks", 1.0) is None
    assert joules_to_watts(10, "mJ", 0.01) is None


def test_active_percent_treats_off_as_idle() -> None:
    assert active_percent([("OFF", 90), ("P1", 10)]) == 10.0
    assert active_percent([("OFF", 0), ("P1", 0)]) is None


def test_derive_diffs_cumulative_counters() -> None:
    prev = _RawSample(
        t=1.0,
        energy=(
            ("DIE_0_CPU Energy", "mJ", 1000),
            ("GPU Energy", "nJ", 0),
        ),
        gpu_states=(("OFF", 1000), ("P1", 0)),
    )
    cur = _RawSample(
        t=2.0,
        energy=(
            ("DIE_0_CPU Energy", "mJ", 1500),
            ("GPU Energy", "nJ", 200_000_000),
        ),
        gpu_states=(("OFF", 1800), ("P1", 200)),
    )
    assert derive(prev, cur) == {
        "cpu_power": 0.5,
        "gpu_power": 0.2,
        "gpu_usage": 20.0,
    }


def test_dvfs_table_mhz_decodes_hz_pairs_and_khz_pairs() -> None:
    """M1 stores Hz. M4 stores kHz. The leading 0 Hz pair is OFF."""
    hz = struct.pack(
        "<6I",
        0, 400,
        388_800_000, 621,
        1_296_000_000, 887,
    )
    khz = struct.pack("<4I", 0, 125, 338_000, 615)
    decoded_hz = dvfs_table_mhz(hz)
    assert len(decoded_hz) == 2
    assert abs(decoded_hz[0] - 388.8) < 1e-6
    assert decoded_hz[1] == 1296.0
    assert dvfs_table_mhz(khz) == (338.0,)


def test_dvfs_table_mhz_rejects_a_value_that_is_not_a_gpu_clock() -> None:
    assert dvfs_table_mhz(struct.pack("<2I", 10_000, 500)) == ()
    assert dvfs_table_mhz(struct.pack("<2I", 4_100_000_000, 900)) == ()
    assert dvfs_table_mhz(b"\x00\x01") == ()


def test_effective_mhz_is_the_awake_weighted_mean() -> None:
    table = (400.0, 500.0, 600.0)
    assert effective_mhz(
        [("OFF", 800), ("P1", 100), ("P2", 0), ("P3", 100)], table,
    ) == 500.0
    assert effective_mhz([("OFF", 100), ("P1", 0)], table) == 0.0
    assert effective_mhz([("OFF", 0), ("P1", 0)], table) is None


def test_effective_mhz_withholds_an_unnamed_state() -> None:
    assert effective_mhz([("P1", 50), ("P7", 50)], (400.0,)) is None
    # Under 1% of the awake time, the unnamed tail is ignored.
    assert effective_mhz([("P1", 10_000), ("P7", 50)], (400.0,)) == 400.0


def test_derive_names_the_clock_from_the_dvfs_table() -> None:
    prev = _RawSample(t=1.0, energy=(), gpu_states=(("OFF", 0), ("P3", 0)))
    cur = _RawSample(t=2.0, energy=(), gpu_states=(("OFF", 900), ("P3", 100)))
    assert derive(prev, cur, gpu_mhz=(400.0, 500.0, 600.0))["gpu_clock"] == 600.0


def test_ioreport_gpu_clock_reads_the_snapshot() -> None:
    snap = _IOReportSnapshot(
        reader=lambda: {"gpu_clock": 648.0}, ttl_seconds=10.0,
    )
    assert IOReportGpu(snap).clock() == 648.0
