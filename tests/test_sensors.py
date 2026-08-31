"""Sensor aggregator — normalized key shape, primary GPU resolver."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from trcc.adapters.sensors import hwmon
from trcc.adapters.sensors.aggregator import BaselineSensors
from trcc.core.ports import DiskSource, DramSource, FanSource

from .conftest import FakeCpu, FakeGpu, FakeMemory


class FakeDisk(DiskSource):
    """One storage temp source for the aggregator tests."""

    def __init__(self, key: str, temp: float | None, name: str = "Fake SSD") -> None:
        self._key, self._temp, self._name = key, temp, name
        # Poll count — a pinned selection must not stop the OTHERS being read,
        # because ``_read`` carries their per-source failure bookkeeping.
        self.reads = 0

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return self._name

    def temp(self) -> float | None:
        self.reads += 1
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


class FakeFan(FanSource):
    """One fan header for the aggregator tests."""

    def __init__(self, key: str, rpm: int | None, name: str = "Fake Fan") -> None:
        self._key, self._rpm, self._name = key, rpm, name

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return self._name

    def rpm(self) -> int | None:
        return self._rpm

    def percent(self) -> float | None:
        return None

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


# ── Fan RPM — FanSource → snapshot.fan_cpu/gpu/ssd/sys2 ──────────────


def test_device_fans_fill_cpu_ssd_sys2_in_discovery_order() -> None:
    """The motherboard fans populate CPU/SSD/SYS2 in discovery order (Linux
    has no fanN_label, so slot = position).  The GPU slot is NOT drawn from
    this pool — it follows the picked GPU (#145/#207)."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[],
        fans=[FakeFan("fan1", 1200), FakeFan("fan2", 800),
              FakeFan("fan3", 600)],
    )

    m = s.snapshot()
    # No GPU → gpu slot 0; the three fans fill cpu/ssd/sys2 in order.
    assert (m.fan_cpu, m.fan_gpu, m.fan_ssd, m.fan_sys2) == (1200, 0, 800, 600)


def test_fans_skip_stopped_headers() -> None:
    """A 0-RPM (or unreadable) header is an empty slot, not a fan — it is
    skipped so the next spinning fan fills the slot instead of a false 0."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[],
        fans=[FakeFan("fan1", 0), FakeFan("fan2", None),
              FakeFan("fan3", 950)],
    )

    m = s.snapshot()
    assert (m.fan_cpu, m.fan_gpu, m.fan_ssd, m.fan_sys2) == (950, 0, 0, 0)


def test_fans_absent_leaves_slots_zero() -> None:
    """No FanSource and no GPU → all four slots stay 0.0 (the pre-fix default,
    now reached only when the board truly has no readable fan)."""
    m = _sensors_with().snapshot()
    assert (m.fan_cpu, m.fan_gpu, m.fan_ssd, m.fan_sys2) == (0, 0, 0, 0)


def test_gpu_fan_slot_follows_picked_gpu() -> None:
    """The GPU fan slot reads the picked GPU's fan (a duty-cycle percent),
    not a motherboard fan — it follows the GPU picker (#145/#207)."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[FakeGpu(0)],
        fans=[FakeFan("fan1", 1200), FakeFan("fan2", 800)],
    )
    m = s.snapshot()
    assert m.fan_gpu == 42.0                       # FakeGpu.fan() percent
    # motherboard fans fill cpu/ssd/sys2, never the gpu slot
    assert (m.fan_cpu, m.fan_ssd, m.fan_sys2) == (1200, 800, 0)


def test_gpu_hwmon_fan_excluded_from_device_pool() -> None:
    """A GPU's own hwmon fan (amdgpu) is never double-counted as a case fan:
    the gpu slot comes from the GPU source, so 'gpu'-keyed headers are skipped
    from the CPU/SSD/SYS2 pool."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[],
        fans=[FakeFan("hwmon:amdgpu:fan1", 1500),
              FakeFan("hwmon:nct6798:fan1", 1000)],
    )
    m = s.snapshot()
    # amdgpu header skipped; only the nct header fills the first device slot.
    assert (m.fan_cpu, m.fan_ssd, m.fan_sys2) == (1000, 0, 0)


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


def _mk(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _hwmon_dir(root: Path, dirname: str, driver: str, *,
               temp1_milli: int | None = None,
               temp1_label: str | None = None,
               serial: str | None = None) -> hwmon.HwmonDevice:
    d = root / dirname
    d.mkdir()
    (d / "name").write_text(f"{driver}\n")
    if temp1_milli is not None:
        (d / "temp1_input").write_text(str(temp1_milli))
    if temp1_label is not None:
        (d / "temp1_label").write_text(f"{temp1_label}\n")
    if serial is not None:
        # NVMe publishes ``device/serial`` with trailing padding, as the real
        # node does — the reader must strip it.
        (d / "device").mkdir()
        (d / "device" / "serial").write_text(f"{serial}     \n")
    return hwmon.HwmonDevice(d)


def test_discover_disk_temp_matches_nvme_and_drivetemp(tmp_path: Path) -> None:
    """nvme + drivetemp nodes become DiskSources; non-storage drivers don't."""
    devices = [
        _hwmon_dir(tmp_path, "hwmon0", "coretemp", temp1_milli=45000),   # CPU
        _hwmon_dir(tmp_path, "hwmon1", "nvme", temp1_milli=35850,
                   temp1_label="Composite", serial="S73HNJ0XA10424V"),
        _hwmon_dir(tmp_path, "hwmon2", "drivetemp", temp1_milli=41000),  # SATA SSD
    ]

    disks = hwmon.discover_disk_temp(devices)

    # The NVMe keys on its SERIAL (stable across boots); the SATA node publishes
    # none, so it falls back to the hwmon dir name — unique, boot-unstable.
    assert {d.key for d in disks} == {
        "hwmon:nvme:S73HNJ0XA10424V:temp1", "hwmon:drivetemp:hwmon2:temp1",
    }
    by_key = {d.key: d for d in disks}
    assert by_key["hwmon:nvme:S73HNJ0XA10424V:temp1"].temp() == 35.85
    assert by_key["hwmon:nvme:S73HNJ0XA10424V:temp1"].name == "Composite"
    assert by_key["hwmon:drivetemp:hwmon2:temp1"].temp() == 41.0


def test_two_nvme_drives_get_distinct_keys(tmp_path: Path) -> None:
    """THE bug this key format exists to fix.

    ``HwmonDisk.key`` was ``hwmon:{driver}:temp1`` — driver only — so a box
    with two NVMe drives produced ONE key for both.  ``HwmonDram`` ten lines
    below in the same module had already been fixed for exactly this ("matched
    DIMMs share a driver, so a driver-only key would collide across modules
    and conflate their per-source read-failure bookkeeping"); disks had the
    identical exposure and were missed.

    The aggregator's only use of the key is
    ``self._read(disk.temp, f"disk:{disk.key}:temp")``, so a collision merged
    two drives' failure bookkeeping into one entry.  It also made a persisted
    disk SELECTION impossible, which is why this lands before that feature.
    """
    devices = [
        _hwmon_dir(tmp_path, "hwmon1", "nvme", temp1_milli=35850,
                   temp1_label="Composite", serial="SERIAL_AAA"),
        _hwmon_dir(tmp_path, "hwmon4", "nvme", temp1_milli=52000,
                   temp1_label="Composite", serial="SERIAL_BBB"),
    ]

    disks = hwmon.discover_disk_temp(devices)

    keys = {d.key for d in disks}
    assert len(keys) == 2, f"two NVMe drives must not share a key — got {keys}"
    assert keys == {
        "hwmon:nvme:SERIAL_AAA:temp1", "hwmon:nvme:SERIAL_BBB:temp1",
    }
    # And the readings stay attached to the right drive.
    by_key = {d.key: d.temp() for d in disks}
    assert by_key["hwmon:nvme:SERIAL_AAA:temp1"] == 35.85
    assert by_key["hwmon:nvme:SERIAL_BBB:temp1"] == 52.0


def test_disk_key_is_stable_when_the_hwmon_number_moves(tmp_path: Path) -> None:
    """A drive keeps its key when hwmon renumbers it — what persistence needs.

    ``hwmonN`` ordering is not stable across boots, so keying on the directory
    (the DRAM fix) gives uniqueness but not stability.  The serial gives both.
    """
    before = hwmon.discover_disk_temp([
        _hwmon_dir(_mk(tmp_path / "boot1"), "hwmon3", "nvme",
                   temp1_milli=35000, serial="SERIAL_AAA"),
    ])
    after = hwmon.discover_disk_temp([
        _hwmon_dir(_mk(tmp_path / "boot2"), "hwmon7", "nvme",
                   temp1_milli=35000, serial="SERIAL_AAA"),
    ])

    assert before[0].key == after[0].key == "hwmon:nvme:SERIAL_AAA:temp1"


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


# ── Label-based GPU temperature resolution (Intel xe / Arc) ──────────


def _hwmon_temps(root: Path, dirname: str, driver: str,
                 channels: dict[int, tuple[int, str | None]]) -> hwmon.HwmonDevice:
    """Build a hwmon node with arbitrary tempN channels.

    ``channels`` maps channel index -> (millidegrees, label|None).  Unlike
    ``_hwmon_dir`` this never writes a temp1 unless the caller asks for one,
    so it can reproduce the xe layout (lowest channel is temp2="pkg").
    """
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(f"{driver}\n")
    for idx, (milli, label) in channels.items():
        (d / f"temp{idx}_input").write_text(str(milli))
        if label is not None:
            (d / f"temp{idx}_label").write_text(f"{label}\n")
    return hwmon.HwmonDevice(d)


# The real Intel Arc Pro B70 (xe driver) channel layout: no temp1 at all,
# temp2="pkg" is the package/die sensor.  (Trimmed from the 21 real channels.)
_XE_CHANNELS = {
    2: (52000, "pkg"),
    3: (54000, "vram"),
    4: (40000, "mctrl"),
    5: (54000, "pcie"),
    6: (50000, "vram_ch_0"),
}


def test_read_temp1_is_none_on_xe_layout(tmp_path: Path) -> None:
    """Regression: the old read_temp(1) reads a nonexistent temp1_input."""
    dev = _hwmon_temps(tmp_path, "hwmon8", "xe", _XE_CHANNELS)

    assert dev.read_temp(1) is None


def test_read_temp_labeled_prefers_pkg_on_xe(tmp_path: Path) -> None:
    """Package sensor (temp2='pkg') resolves via label, not channel number."""
    dev = _hwmon_temps(tmp_path, "hwmon8", "xe", _XE_CHANNELS)

    assert dev.read_temp_labeled() == 52.0


def test_read_temp_labeled_exact_match_not_substring(tmp_path: Path) -> None:
    """'vram' must not select the 'vram_ch_0' channel when no earlier label
    matches — exact label match only."""
    dev = _hwmon_temps(tmp_path, "hwmon8", "xe", {
        3: (54000, "vram"),        # exact 'vram'
        6: (48000, "vram_ch_0"),   # must be ignored by the 'vram' preference
    })

    assert dev.read_temp_labeled() == 54.0


def test_read_temp_labeled_falls_back_to_lowest_channel(tmp_path: Path) -> None:
    """A driver exposing temps with no matching label uses the lowest channel."""
    dev = _hwmon_temps(tmp_path, "hwmon0", "i915", {
        1: (47000, None),
        2: (61000, "junction"),   # not in the preference list
    })

    assert dev.read_temp_labeled() == 47.0


def test_read_temp_labeled_none_when_no_channels(tmp_path: Path) -> None:
    """No temp*_input at all → None (not a crash, not 0.0)."""
    d = tmp_path / "hwmon8"
    d.mkdir()
    (d / "name").write_text("xe\n")

    assert hwmon.HwmonDevice(d).read_temp_labeled() is None


def test_intel_arc_temp_reads_package_via_label(tmp_path: Path) -> None:
    """IntelGpu.temp() now surfaces the xe package temp instead of None."""
    dev = _hwmon_temps(tmp_path, "hwmon8", "xe", _XE_CHANNELS)
    gpu = hwmon.IntelGpu(0, dev, drm_card=None, driver="xe")

    assert gpu.key == "intel:arc:0"
    assert gpu.temp() == 52.0


def test_intel_gpu_temp_none_without_hwmon(tmp_path: Path) -> None:
    """A DRM-only iGPU (no hwmon node) still reports None, not a crash."""
    gpu = hwmon.IntelGpu(0, None, drm_card=None, driver="i915")

    assert gpu.temp() is None


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
    # These tests pin a fixed path set — disable lazy re-discovery so an
    # empty stub stays empty regardless of the host's real RAPL nodes (#194).
    r._next_rediscover = float("inf")
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


# ── RAPL CPU power lazy re-discovery (#194) ──────────────────────────


def test_rapl_rediscovers_after_setup(tmp_path, monkeypatch) -> None:
    """When RAPL starts empty (driver/perm not ready) and becomes available
    later — e.g. the user just ran `trcc setup` — _RaplCpuPower picks it up on
    the next throttled read instead of staying dark until restart (#194)."""
    monkeypatch.setattr(hwmon._RaplCpuPower, "_discover", staticmethod(list))
    rapl = hwmon._RaplCpuPower()
    assert rapl._paths == []
    assert rapl.read() is None                       # still empty

    # Setup ran: a readable energy counter now exists.
    energy = tmp_path / "energy_uj"
    energy.write_text("1000000")
    monkeypatch.setattr(
        hwmon._RaplCpuPower, "_discover", staticmethod(lambda: [energy]),
    )
    rapl._next_rediscover = 0.0                       # open the throttle
    rapl.read()                                       # re-discovers + seeds
    assert rapl._paths == [energy]


def test_rapl_rediscovery_is_throttled(monkeypatch) -> None:
    """The empty-case re-scan is rate-limited so polling stays cheap (#194)."""
    calls = {"n": 0}

    def _count() -> list:
        calls["n"] += 1
        return []

    monkeypatch.setattr(hwmon._RaplCpuPower, "_discover", staticmethod(_count))
    rapl = hwmon._RaplCpuPower()                      # discover #1 (in __init__)
    rapl.read()                                       # discover #2 (throttle was 0)
    rapl.read()                                       # throttled — no discover
    assert calls["n"] == 2


# ── Cache freshness — read_all/read_one must not serve boot-time values ──


class CountingCpu(FakeCpu):
    """FakeCpu that records how many times the aggregator actually polled it."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def temp(self) -> float | None:
        self.reads += 1
        return self.values["temp"]


def _counting_sensors() -> tuple[BaselineSensors, CountingCpu]:
    cpu = CountingCpu()
    return BaselineSensors(cpu=cpu, memory=FakeMemory(), gpus=[], fans=[]), cpu


def test_read_all_refreshes_a_cache_older_than_the_poll_interval() -> None:
    """A stale cache must be re-polled, or a headless render loop runs forever
    on the values it read at launch.

    ``read_all`` used to return its cached dict unconditionally once filled,
    and only ``start_polling`` — whose sole caller is ``MetricsLoop``, started
    by the daemon/gui/qtgui and by no CLI or API entry point — ever refreshed
    it.  So ``trcc led play`` drove an LED cooler's colours and segment readout
    from the temperature at launch, indefinitely (#270).

    MUTATION CHECK: restore the early ``if self._readings: return`` in
    ``read_all`` and this fails with 42.0 != 77.0 — the reported symptom.
    """
    s, cpu = _counting_sensors()

    assert s.read_all()["cpu:temp"] == 42.0
    cpu.values["temp"] = 77.0                 # the hardware moved
    s._last_poll -= s._interval_s + 1.0       # age the cache past its interval

    assert s.read_all()["cpu:temp"] == 77.0


def test_read_all_serves_a_still_fresh_cache_without_repolling() -> None:
    """Freshness is a TTL, not "poll every call" — the cache still does its job.

    Guards the opposite regression: deleting the cache instead of expiring it
    would re-poll every hwmon node on each frame of a 30 Hz render loop.
    """
    s, cpu = _counting_sensors()

    s.read_all()
    polled = cpu.reads
    cpu.values["temp"] = 77.0                 # moved, but the TTL has not run out

    assert s.read_all()["cpu:temp"] == 42.0   # served from cache
    assert cpu.reads == polled                # and nothing was re-read


def test_a_live_poll_thread_owns_the_cadence_so_readers_never_inline_poll() -> None:
    """The gui/daemon path must be untouched: a running poll thread already
    keeps the cache current, so a reader must never pay for an inline poll —
    even when the cache looks ancient by the clock."""
    s, cpu = _counting_sensors()
    s.start_polling(60.0)                     # polls once, then waits out the interval
    try:
        idle = threading.Event()
        for _ in range(500):                  # bounded: never hang the suite
            if s._last_poll:                  # the thread committed a poll
                break
            idle.wait(0.01)
        assert s._last_poll, "background poll never landed"
        settled = cpu.reads
        s._last_poll = 0.0                    # ancient — would force a poll if checked

        s.read_all()
        s.read_one("cpu:temp")

        assert cpu.reads == settled           # the thread owns it; readers paid nothing
    finally:
        s.stop_polling()


def test_read_one_polls_instead_of_returning_none_forever() -> None:
    """``read_one`` read the cache without ever polling, so on a fresh
    enumerator — one nobody had called ``read_all`` on — it returned None for
    every sensor, for the life of the process."""
    s, _ = _counting_sensors()

    assert s.read_one("cpu:temp") == 42.0


# ── Disk SELECTION — the feature `disk_index` never delivered ────────
#
# NOTE ON SHAPE, because the obvious test is WRONG here: `read_all()` is
# cache-gated (`_refresh_if_stale` re-polls only once `age >= interval_s`), so
# looping `snapshot()` N times is ONE poll and therefore ONE call to
# `preferred_disk()`.  A warn-once assertion written that way passes even with
# the dedupe deleted.  So the dedupe is tested on `preferred_disk()` directly —
# the unit that dedupes, called the way a live poll thread calls it — and the
# SELECTION is tested through `snapshot()`, one poll per fresh enumerator.


def _sensors_with_disks(*disks):
    from trcc.adapters.sensors.aggregator import BaselineSensors
    return BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
                           disks=list(disks))


def test_disk_temp_is_the_hottest_when_nothing_is_pinned() -> None:
    """The default is unchanged — that is the point of asserting it.

    "Hottest" was the ONLY rule until 2026-08-31.  It is now the fallback, and
    a fallback nobody tests is a fallback free to drift.
    """
    s = _sensors_with_disks(FakeDisk("nvme0", 41.0), FakeDisk("nvme1", 58.0))

    assert s.snapshot().disk_temp == 58.0


def test_a_pinned_disk_beats_a_hotter_one() -> None:
    """THE feature, in one assertion.

    Without it the panel shows whichever drive is hottest regardless of the
    user's choice — which is what every release before this one did.
    """
    s = _sensors_with_disks(FakeDisk("nvme0", 41.0), FakeDisk("nvme1", 58.0))

    s.set_preferred_disk("nvme0")

    assert s.snapshot().disk_temp == 41.0, (
        "the pinned drive must win over the hotter one"
    )


def test_a_vanished_pin_falls_back_to_the_hottest() -> None:
    """An unplugged drive must not blank the metric."""
    s = _sensors_with_disks(FakeDisk("nvme0", 41.0), FakeDisk("nvme1", 58.0))
    s.set_preferred_disk("nvme_UNPLUGGED")

    assert s.snapshot().disk_temp == 58.0


def test_a_vanished_pin_warns_ONCE_across_many_polls(caplog) -> None:
    """The dedupe, tested on the unit that dedupes.

    ``MetricsLoop`` refreshes every ~2 s, so each refresh calls this once; an
    un-deduped warning is a log line every two seconds, burying the one-shot
    lines a ``trcc report`` is read for.

    Driven directly rather than through ``snapshot()`` ON PURPOSE — the reading
    cache would collapse N snapshots into one poll and this would pass with the
    dedupe removed.
    """
    import logging
    s = _sensors_with_disks(FakeDisk("nvme1", 58.0))
    s.set_preferred_disk("nvme_UNPLUGGED")

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            assert s.preferred_disk() is None

    warned = [r for r in caplog.records if "nvme_UNPLUGGED" in r.getMessage()]
    assert len(warned) == 1, (
        f"expected ONE warning across five polls, got {len(warned)}"
    )


def test_a_pin_that_comes_back_re_arms_the_warning(caplog) -> None:
    """A returning drive restores the reading AND re-arms the warning."""
    import logging
    present = FakeDisk("nvme0", 41.0)
    s = _sensors_with_disks(FakeDisk("nvme1", 58.0))
    s.set_preferred_disk("nvme0")

    with caplog.at_level(logging.WARNING):
        assert s.preferred_disk() is None          # absent -> warn (1)
        s._disks.append(present)
        assert s.preferred_disk() is present       # back -> no warn, re-armed
        s._disks.remove(present)
        assert s.preferred_disk() is None          # gone again -> warn (2)

    warned = [r for r in caplog.records if "nvme0" in r.getMessage()]
    assert len(warned) == 2, (
        "a returning drive must re-arm the warning so its next disappearance "
        f"is reported again — got {len(warned)}"
    )


def test_every_disk_is_still_polled_when_one_is_pinned() -> None:
    """Pinning must not stop POLLING the others.

    ``_read`` carries per-source failure bookkeeping, so reading only the
    chosen drive would silently drop the other drives' diagnostics — which is
    why the selection is applied AFTER the comprehension, not instead of it.
    """
    a, b = FakeDisk("nvme0", 41.0), FakeDisk("nvme1", 58.0)
    s = _sensors_with_disks(a, b)
    s.set_preferred_disk("nvme0")

    s.snapshot()

    assert a.reads >= 1 and b.reads >= 1, (
        f"both drives must be polled; got nvme0={a.reads} nvme1={b.reads}"
    )
