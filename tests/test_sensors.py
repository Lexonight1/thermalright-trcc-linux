"""Sensor aggregator — normalized key shape, primary GPU resolver."""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.sensors import hwmon
from trcc.adapters.sensors.aggregator import BaselineSensors

from .conftest import FakeCpu, FakeGpu, FakeMemory


def _sensors_with(gpus=None) -> BaselineSensors:
    return BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(),
        gpus=gpus or [], fans=[],
    )


def test_read_all_produces_normalized_cpu_keys() -> None:
    s = _sensors_with()
    r = s.read_all()

    assert r["cpu:temp"] == 42.0
    assert r["cpu:usage"] == 15.0
    assert r["cpu:freq"] == 3200.0
    assert r["cpu:power"] == 65.0


def test_read_all_produces_normalized_memory_keys() -> None:
    s = _sensors_with()
    r = s.read_all()

    assert r["memory:used"] == 8192.0
    assert r["memory:available"] == 24576.0
    assert r["memory:total"] == 32768.0
    assert r["memory:percent"] == 25.0


def test_gpu_readings_available_under_three_key_shapes() -> None:
    """Every GPU reading must be reachable by index, vendor-key, AND primary alias."""
    gpu = FakeGpu(0, discrete=True, vendor="nvidia")
    s = _sensors_with(gpus=[gpu])

    r = s.read_all()

    # Indexed access
    assert r["gpu:0:temp"] == 55.0
    # Vendor-keyed access
    assert r["gpu:nvidia:0:temp"] == 55.0
    # Primary alias
    assert r["gpu:primary:temp"] == 55.0


def test_primary_gpu_prefers_discrete() -> None:
    igpu = FakeGpu(0, discrete=False, vendor="intel")
    dgpu = FakeGpu(0, discrete=True, vendor="nvidia")

    # Pass in wrong order — aggregator sorts discrete first
    s = _sensors_with(gpus=[igpu, dgpu])
    primary = s.primary_gpu()

    assert primary is not None
    assert primary.key == "nvidia:0"
    assert s.read_all()["gpu:primary:temp"] == dgpu.temp()


def test_primary_gpu_falls_back_to_igpu_when_no_discrete() -> None:
    igpu = FakeGpu(0, discrete=False, vendor="intel")
    s = _sensors_with(gpus=[igpu])

    primary = s.primary_gpu()

    assert primary is not None
    assert primary.key == "intel:0"


def test_primary_gpu_is_none_on_headless() -> None:
    s = _sensors_with(gpus=[])

    assert s.primary_gpu() is None
    # No gpu:primary:* keys should appear
    r = s.read_all()
    assert not any(k.startswith("gpu:primary:") for k in r)


def test_discover_contains_one_reading_per_declared_key() -> None:
    s = _sensors_with(gpus=[FakeGpu(0, discrete=True, vendor="nvidia")])

    readings = s.discover()

    ids = {r.sensor_id for r in readings}
    # Minimum expected keys
    expected = {
        "cpu:temp", "cpu:usage", "cpu:freq", "cpu:power",
        "memory:used", "memory:percent",
        "gpu:0:temp", "gpu:nvidia:0:temp", "gpu:primary:temp",
        "time:hour", "date:year",
    }
    missing = expected - ids
    assert not missing, f"missing normalized keys: {missing}"


def test_none_values_omitted_from_flat_dict() -> None:
    """Source returning None for a reading must not produce an entry."""
    cpu = FakeCpu()
    cpu.values["power"] = None   # type: ignore[assignment]
    s = BaselineSensors(cpu=cpu, memory=FakeMemory(), gpus=[], fans=[])

    r = s.read_all()

    assert "cpu:power" not in r
    assert "cpu:temp" in r       # other readings unaffected


# ── RAPL CPU package power (energy-counter delta) ───────────────────


def _rapl_with(paths: list[Path]) -> hwmon._RaplCpuPower:
    """A _RaplCpuPower with discovery stubbed to *paths* (no sysfs)."""
    r = hwmon._RaplCpuPower.__new__(hwmon._RaplCpuPower)
    r._paths = paths
    r._last = None
    return r


def test_rapl_no_domains_always_returns_none() -> None:
    """No readable package domain → power is None, never an error."""
    r = _rapl_with([])
    assert r.read() is None
    assert r.read() is None


def test_rapl_seeds_then_computes_watts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First read seeds the baseline (None); the next read is Δenergy/Δt.

    +2,000,000 µJ over +1.0 s = 2.0 W.
    """
    r = _rapl_with([Path("/fake/energy_uj")])
    energies = iter([1_000_000.0, 3_000_000.0])
    times = iter([100.0, 101.0])
    monkeypatch.setattr(hwmon, "_read_float", lambda _p: next(energies))
    monkeypatch.setattr(hwmon.time, "monotonic", lambda: next(times))

    assert r.read() is None          # seed
    assert r.read() == pytest.approx(2.0)


def test_rapl_sums_multiple_package_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-socket: energy is summed across package domains before the delta.

    Two sockets each +1,500,000 µJ over +1.0 s = 3.0 W total.
    """
    r = _rapl_with([Path("/fake/p0"), Path("/fake/p1")])
    # read order per tick: p0, p1 → tick1 totals 2.0M, tick2 totals 5.0M
    vals = iter([1_000_000.0, 1_000_000.0, 2_500_000.0, 2_500_000.0])
    times = iter([10.0, 11.0])
    monkeypatch.setattr(hwmon, "_read_float", lambda _p: next(vals))
    monkeypatch.setattr(hwmon.time, "monotonic", lambda: next(times))

    assert r.read() is None
    assert r.read() == pytest.approx(3.0)


def test_rapl_drops_counter_wraparound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A negative delta (counter wrapped) yields None, not a negative wattage."""
    r = _rapl_with([Path("/fake/energy_uj")])
    energies = iter([5_000_000.0, 1_000_000.0])   # decreases → wrap
    times = iter([100.0, 101.0])
    monkeypatch.setattr(hwmon, "_read_float", lambda _p: next(energies))
    monkeypatch.setattr(hwmon.time, "monotonic", lambda: next(times))

    assert r.read() is None          # seed
    assert r.read() is None          # wrap dropped


def test_rapl_unreadable_counter_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A path that became unreadable (permission) bails the tick as None."""
    r = _rapl_with([Path("/fake/energy_uj")])
    monkeypatch.setattr(hwmon, "_read_float", lambda _p: None)
    monkeypatch.setattr(hwmon.time, "monotonic", lambda: 100.0)
    assert r.read() is None
