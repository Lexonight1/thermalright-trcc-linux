"""Sensor aggregator — normalized key shape, primary GPU resolver."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from trcc.adapters.sensors import hwmon
from trcc.adapters.sensors.aggregator import BaselineSensors
from trcc.core.ports import DiskSource, DramSource

from .conftest import FakeCpu, FakeGpu, FakeMemory


class FakeDisk(DiskSource):
    """One storage temp source for the aggregator tests."""

    def __init__(self, key: str, temp: float | None, name: str = "Fake SSD") -> None:
        self._key, self._temp, self._name = key, temp, name

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return self._name

    def temp(self) -> float | None:
        return self._temp


class FakeDram(DramSource):
    """One DIMM temp source for the aggregator tests."""

    def __init__(self, key: str, temp: float | None, name: str = "Fake DIMM") -> None:
        self._key, self._temp, self._name = key, temp, name

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return self._name

    def temp(self) -> float | None:
        return self._temp

# ── snapshot() — typed HardwareMetrics, collapse policy ──────────────


def test_snapshot_single_cpu_scalars_equal_source() -> None:
    # The n=1 identity: collapse is max()/avg/sum over the cpus list, and
    # for ONE element each reduces to that element — like ``x ** 1 == x``.
    # So today's single-source snapshot yields scalars identical to a
    # direct cpu().<metric>() read; the plural shape changes nothing until
    # sources widen.  This is what makes option A provably safe.
    cpu = FakeCpu()
    m = _sensors_with().snapshot()
    assert m.cpu_temp == cpu.temp() == 42.0
    assert m.cpu_percent == cpu.usage() == 15.0
    assert m.cpu_freq == cpu.freq() == 3200.0
    assert m.cpu_power == cpu.power() == 65.0
    # Plural list present, single-element, faithful to the source.
    assert len(m.cpus) == 1
    assert (m.cpus[0].name, m.cpus[0].temp, m.cpus[0].usage,
            m.cpus[0].freq, m.cpus[0].power) == ("Fake CPU", 42.0, 15.0,
                                                 3200.0, 65.0)


def test_snapshot_plural_gpus_listed_scalar_collapses_to_primary() -> None:
    integrated = FakeGpu(0, discrete=False, vendor="intel")
    discrete = FakeGpu(1, discrete=True, vendor="nvidia")
    discrete.values["temp"] = 70.0          # distinguish from integrated 55
    s = _sensors_with(gpus=[integrated, discrete])
    m = s.snapshot()
    # Both GPUs faithfully listed (aggregator sorts discrete-first).
    assert len(m.gpus) == 2
    assert {g.temp for g in m.gpus} == {55.0, 70.0}
    # Scalar collapses to the PRIMARY (discrete) card, not the iGPU.
    assert m.gpu_temp == 70.0
    assert m.gpu_usage == 30.0
    assert m.gpu_clock == 1800.0


def test_snapshot_absent_gpu_yields_zero_scalars_empty_list() -> None:
    m = _sensors_with(gpus=[]).snapshot()
    assert m.gpus == []
    assert m.gpu_temp == 0.0
    assert m.gpu_usage == 0.0
    assert m.gpu_clock == 0.0


def test_snapshot_folds_io_from_readings_and_embeds_dict() -> None:
    s = _sensors_with()
    m = s.snapshot()
    # disk/net have no typed source — snapshot folds them from read_all().
    assert m.disk_read == m.readings.get("disk:read", 0.0)
    assert m.net_up == m.readings.get("net:up", 0.0)
    # Full flat dict embedded for the system-info dashboard.
    assert "cpu:temp" in m.readings and "memory:percent" in m.readings


def test_snapshot_degrades_raising_source_not_throws() -> None:
    """A RAISING sensor (RAPL energy_uj PermissionError — the #139 class) must
    NOT take down snapshot().  If it did, the per-tick SensorsUpdated publish
    would die and every metric in the UI would blank to `--`.  The raising
    reading degrades to 0.0; its siblings on the same source survive."""
    cpu = FakeCpu()

    def boom() -> float:
        raise PermissionError(13, "Permission denied")   # the #139 shape

    cpu.power = boom            # type: ignore[method-assign]
    s = BaselineSensors(cpu=cpu, memory=FakeMemory(), gpus=[], fans=[])

    m = s.snapshot()            # must NOT raise

    assert m.cpu_power == 0.0   # the raising reading degraded
    assert m.cpu_temp == 42.0   # siblings on the same source unaffected
    assert m.cpu_percent == 15.0
    assert m.cpus[0].power == 0.0


def _sensors_with(gpus=None) -> BaselineSensors:
    return BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(),
        gpus=gpus or [], fans=[],
    )


# ── Disk temperature — DiskSource → disk:temp → snapshot.disk_temp ───


def test_disk_temp_collapses_to_hottest_drive() -> None:
    """N DiskSources fold to the single hottest as ``disk:temp`` (model has one
    disk_temp slot; the hottest drive is the one most likely to throttle)."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
        disks=[FakeDisk("nvme0", 41.0), FakeDisk("nvme1", 58.0)],
    )

    assert s.read_all()["disk:temp"] == 58.0
    assert s.snapshot().disk_temp == 58.0


def test_disk_temp_absent_when_no_disk_source() -> None:
    """No DiskSource → no ``disk:temp`` key, and snapshot's disk_temp stays 0.0
    (the pre-fix behaviour for boxes with no readable drive sensor)."""
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[])

    assert "disk:temp" not in s.read_all()
    assert s.snapshot().disk_temp == 0.0


def test_disk_temp_skips_unreadable_drive() -> None:
    """A drive whose temp reads None is skipped, not folded as 0.0."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
        disks=[FakeDisk("nvme0", None), FakeDisk("sata0", 47.0)],
    )

    assert s.read_all()["disk:temp"] == 47.0


def test_disk_temp_in_discover_catalog() -> None:
    """``disk:temp`` is a declared metric so the overlay picker can offer it."""
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[])

    ids = {r.sensor_id for r in s.discover()}
    assert "disk:temp" in ids


# ── Linux hwmon disk discovery (nvme / drivetemp) ────────────────────


def _hwmon_dir(root: Path, dirname: str, driver: str, *,
               temp1_milli: int | None = None,
               temp1_label: str | None = None) -> hwmon.HwmonDevice:
    d = root / dirname
    d.mkdir()
    (d / "name").write_text(f"{driver}\n")
    if temp1_milli is not None:
        (d / "temp1_input").write_text(str(temp1_milli))
    if temp1_label is not None:
        (d / "temp1_label").write_text(f"{temp1_label}\n")
    return hwmon.HwmonDevice(d)


def test_discover_disk_temp_matches_nvme_and_drivetemp(tmp_path: Path) -> None:
    """nvme + drivetemp nodes become DiskSources; non-storage drivers don't."""
    devices = [
        _hwmon_dir(tmp_path, "hwmon0", "coretemp", temp1_milli=45000),   # CPU
        _hwmon_dir(tmp_path, "hwmon1", "nvme", temp1_milli=35850,
                   temp1_label="Composite"),
        _hwmon_dir(tmp_path, "hwmon2", "drivetemp", temp1_milli=41000),  # SATA SSD
    ]

    disks = hwmon.discover_disk_temp(devices)

    assert {d.key for d in disks} == {
        "hwmon:nvme:temp1", "hwmon:drivetemp:temp1",
    }
    by_key = {d.key: d for d in disks}
    assert by_key["hwmon:nvme:temp1"].temp() == 35.85
    assert by_key["hwmon:nvme:temp1"].name == "Composite"
    assert by_key["hwmon:drivetemp:temp1"].temp() == 41.0


def test_discover_disk_temp_skips_node_without_temp1(tmp_path: Path) -> None:
    """An nvme node exposing no temp1_input is skipped (not a 0.0 source)."""
    devices = [_hwmon_dir(tmp_path, "hwmon0", "nvme")]   # no temp1_input

    assert hwmon.discover_disk_temp(devices) == []


# ── DRAM (SPD-hub) temperature — DramSource → memory:temp → mem_temp ──


def test_dram_temp_collapses_to_hottest_dimm() -> None:
    """N DramSources fold to the single hottest as ``memory:temp`` (model has
    one mem_temp slot; the hottest DIMM is the one most likely to throttle)."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
        dram=[FakeDram("dimm0", 27.0), FakeDram("dimm1", 39.0)],
    )

    assert s.read_all()["memory:temp"] == 39.0
    assert s.snapshot().mem_temp == 39.0


def test_dram_temp_absent_when_no_dram_source() -> None:
    """No DramSource → no ``memory:temp`` key, and snapshot's mem_temp stays 0.0
    (the pre-fix behaviour for boxes with no readable DIMM sensor)."""
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[])

    assert "memory:temp" not in s.read_all()
    assert s.snapshot().mem_temp == 0.0


def test_dram_temp_skips_unreadable_dimm() -> None:
    """A DIMM whose temp reads None is skipped, not folded as 0.0."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
        dram=[FakeDram("dimm0", None), FakeDram("dimm1", 31.0)],
    )

    assert s.read_all()["memory:temp"] == 31.0


def test_dram_temp_in_discover_catalog() -> None:
    """``memory:temp`` is a declared metric so the overlay picker can offer it."""
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[])

    ids = {r.sensor_id for r in s.discover()}
    assert "memory:temp" in ids


# ── Linux hwmon DRAM discovery (spd5118 / jc42) ──────────────────────


def test_discover_dram_temp_matches_spd5118_and_jc42(tmp_path: Path) -> None:
    """spd5118 + jc42 nodes become DramSources; coretemp + ee1004 don't."""
    devices = [
        _hwmon_dir(tmp_path, "hwmon0", "coretemp", temp1_milli=45000),    # CPU
        _hwmon_dir(tmp_path, "hwmon1", "spd5118", temp1_milli=27250),     # DDR5
        _hwmon_dir(tmp_path, "hwmon2", "jc42", temp1_milli=33000),        # DDR4
        _hwmon_dir(tmp_path, "hwmon3", "ee1004"),                         # SPD EEPROM
    ]

    dram = hwmon.discover_dram_temp(devices)

    assert {d.key for d in dram} == {
        "hwmon:spd5118:hwmon1:temp1", "hwmon:jc42:hwmon2:temp1",
    }
    by_key = {d.key: d for d in dram}
    assert by_key["hwmon:spd5118:hwmon1:temp1"].temp() == 27.25
    assert by_key["hwmon:jc42:hwmon2:temp1"].temp() == 33.0


def test_discover_dram_temp_distinct_keys_for_matched_dimms(tmp_path: Path) -> None:
    """Two spd5118 nodes (matched DIMMs) yield DISTINCT keys — the dir name
    disambiguates them so their per-source failure bookkeeping stays separate."""
    devices = [
        _hwmon_dir(tmp_path, "hwmon2", "spd5118", temp1_milli=27250),
        _hwmon_dir(tmp_path, "hwmon3", "spd5118", temp1_milli=28000),
    ]

    keys = {d.key for d in hwmon.discover_dram_temp(devices)}

    assert keys == {
        "hwmon:spd5118:hwmon2:temp1", "hwmon:spd5118:hwmon3:temp1",
    }


def test_discover_dram_temp_skips_node_without_temp1(tmp_path: Path) -> None:
    """A spd5118 node exposing no temp1_input is skipped (not a 0.0 source)."""
    devices = [_hwmon_dir(tmp_path, "hwmon0", "spd5118")]   # no temp1_input

    assert hwmon.discover_dram_temp(devices) == []


# ── Memory channel clock — SpdClock → memory:clock → mem_clock ───────


class _FakeSpdClock:
    """Stub for the cached SPD clock source (mhz)."""

    def __init__(self, mhz: float | None) -> None:
        self._mhz = mhz

    def clock(self) -> float | None:
        return self._mhz


def test_memory_clock_flows_to_snapshot() -> None:
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
        spd_clock=_FakeSpdClock(2404.0),
    )

    assert s.read_all()["memory:clock"] == 2404.0
    assert s.snapshot().mem_clock == 2404.0


def test_memory_clock_absent_without_spd_clock() -> None:
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[])

    assert "memory:clock" not in s.read_all()
    assert s.snapshot().mem_clock == 0.0


def test_memory_clock_in_discover_catalog() -> None:
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[])

    ids = {r.sensor_id for r in s.discover()}
    assert "memory:clock" in ids


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


def test_raising_source_degrades_not_crashes() -> None:
    """A sensor read that RAISES (locked/wedged node) must degrade to a
    missing reading — never propagate and crash the whole poll (the read
    path feeds GUI launch + render ticks).  Issue #139 class."""
    cpu = FakeCpu()

    def boom() -> float:
        raise PermissionError(13, "Permission denied")   # the #139 shape

    cpu.power = boom            # type: ignore[method-assign]
    s = BaselineSensors(cpu=cpu, memory=FakeMemory(), gpus=[], fans=[])

    r = s.read_all()            # must NOT raise

    assert "cpu:power" not in r     # the raising reading is dropped
    assert "cpu:temp" in r          # siblings on the same source survive
    assert "memory:used" in r       # other sources unaffected


# ── RAPL CPU package power (energy-counter delta) ───────────────────


def _rapl_with(paths: list[Path]) -> hwmon._RaplCpuPower:
    """A _RaplCpuPower with discovery stubbed to *paths* (no sysfs)."""
    r = hwmon._RaplCpuPower.__new__(hwmon._RaplCpuPower)
    r._paths = paths
    r._last = None
    r._lock = threading.Lock()
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
