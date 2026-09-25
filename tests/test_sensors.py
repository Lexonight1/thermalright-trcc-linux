"""Sensor aggregator — normalized key shape, primary GPU resolver."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from trcc.adapters.sensors import hwmon
from trcc.adapters.sensors.aggregator import BaselineSensors
from trcc.core.models import MIN_REFRESH_INTERVAL_S, percent_only
from trcc.core.ports import CpuSource, DiskSource, DramSource, FanSource, GpuSource

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
    """GPUFAN is the picked GPU's fan in RPM, like the Windows app -- never a
    motherboard fan, never its duty percent under "RPM" (#145/#207)."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[FakeGpu(0)],
        fans=[FakeFan("fan1", 1200), FakeFan("fan2", 800)],
    )
    m = s.snapshot()
    assert m.fan_gpu == 1500.0                     # FakeGpu.fan_rpm()
    # motherboard fans fill cpu/ssd/sys2, never the gpu slot
    assert (m.fan_cpu, m.fan_ssd, m.fan_sys2) == (1200, 800, 0)


def test_fan_slots_reach_the_overlay_readings() -> None:
    """The LCD draws from ``read_all``, which had no fan slot keys at all: a
    CPUFAN or GPUFAN element was blank on every host, every tick (#145)."""
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[FakeGpu(0)],
        fans=[FakeFan("fan1", 1200), FakeFan("fan2", 800)],
    )
    r = s.read_all()
    assert {k: r[k] for k in ("fan:cpu", "fan:gpu", "fan:ssd", "fan:sys2")} == {
        "fan:cpu": 1200, "fan:gpu": 1500.0, "fan:ssd": 800, "fan:sys2": 0.0}
    assert "fan:gpu:percent" not in r


def test_gpu_fan_without_rpm_is_offered_as_a_percent() -> None:
    """A driver with a duty cycle only (old NVIDIA, some amdgpu) fills the
    percent slot; the RPM slot stays absent so nothing draws "42 RPM"."""
    gpu = FakeGpu(0)
    gpu.values["fan_rpm"] = None
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[gpu], fans=[])
    r = s.read_all()
    assert "fan:gpu" not in r
    assert r["fan:gpu:percent"] == 42.0
    assert s.snapshot().fan_gpu == 0.0


@pytest.mark.parametrize(("readings", "expected"), [
    ({"fan:gpu:percent": 30.0}, 30.0),                       # percent only
    ({"fan:gpu": 999.0, "fan:gpu:percent": 30.0}, None),     # RPM wins
    ({}, None),                                              # nothing
])
def test_percent_only(readings: dict[str, float], expected: float | None) -> None:
    assert percent_only(readings, "fan:gpu") == expected


def test_amdgpu_fan_reads_rpm_and_duty_separately(tmp_path: Path) -> None:
    """``fan()`` used to return None whenever an RPM existed and the RPM was
    never read at all; now each quantity comes from its own file."""
    d = tmp_path / "hwmon3"
    d.mkdir()
    (d / "name").write_text("amdgpu\n")
    (d / "fan1_input").write_text("1650")
    (d / "pwm1").write_text("51")
    gpu = hwmon.AmdGpu(0, hwmon.HwmonDevice(d), None)
    assert gpu.fan_rpm() == 1650.0
    assert gpu.fan() == 20.0                       # 51/255
    (d / "fan1_input").unlink()
    assert gpu.fan_rpm() is None
    assert gpu.fan() == 20.0


def _fan_node(root: Path, name: str, driver: str, rpm: int) -> hwmon.HwmonDevice:
    d = root / name
    d.mkdir()
    (d / "name").write_text(f"{driver}\n")
    (d / "fan1_input").write_text(str(rpm))
    return hwmon.HwmonDevice(d)


def test_gpu_hwmon_fans_excluded_from_device_pool(tmp_path: Path) -> None:
    """A graphics card's own fan is never counted as a case fan.  The pool
    skipped keys containing "gpu", which caught amdgpu and let nouveau's and
    Intel Arc's (xe) fans fill CPUFAN; now each fan declares ``on_gpu``."""
    devices = [
        _fan_node(tmp_path, "hwmon1", "amdgpu", 1500),
        _fan_node(tmp_path, "hwmon2", "nouveau", 1400),
        _fan_node(tmp_path, "hwmon3", "xe", 1300),
        _fan_node(tmp_path, "hwmon4", "nct6798", 1000),
    ]
    fans = hwmon.discover_fans(devices)
    assert [f.on_gpu for f in fans] == [True, True, True, False]
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=fans)
    m = s.snapshot()
    assert (m.fan_cpu, m.fan_ssd, m.fan_sys2) == (1000, 0, 0)


def _nouveau_node(root: Path, **files: str) -> hwmon.HwmonDevice:
    d = root / "hwmon5"
    d.mkdir()
    (d / "name").write_text("nouveau\n")
    for name, value in files.items():
        (d / name).write_text(value)
    return hwmon.HwmonDevice(d)


def test_nouveau_gpu_reads_the_drivers_own_units(tmp_path: Path) -> None:
    """nouveau_hwmon.c: temp1 m°C, fan1 RPM, pwm1 ALREADY a percent (nvkm
    clamps to 0-100 -- the shared /255 reader would show 30% as 12%), power1
    µW in ``power1_input`` (there is no ``power1_average``)."""
    node = _nouveau_node(tmp_path, temp1_input="61000", fan1_input="1320",
                         pwm1="30", power1_input="87500000")
    (gpu,) = hwmon.discover_nouveau_gpus([node])
    assert (gpu.key, gpu.is_discrete) == ("nouveau:0", True)
    assert (gpu.temp(), gpu.fan_rpm(), gpu.fan(), gpu.power()) == (61.0, 1320.0, 30.0, 87.5)


def test_nouveau_gpu_declines_what_nouveau_never_exposes(tmp_path: Path) -> None:
    """No usage, clock or VRAM -- declared, so ``unsupported()`` names them
    rather than a 0 being shown; missing files read as None, never raise."""
    (gpu,) = hwmon.discover_nouveau_gpus([_nouveau_node(tmp_path, temp1_input="50000")])
    assert [q for q in ("usage", "clock", "vram_used", "vram_total") if gpu.provides(q)] == []
    assert (gpu.temp(), gpu.fan_rpm(), gpu.fan(), gpu.power()) == (50.0, None, None, None)
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(), gpus=[gpu], fans=[])
    assert {"gpu:primary:usage", "gpu:nouveau:0:clock"} <= s.unsupported()
    assert s.read_all()["gpu:primary:temp"] == 50.0


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


# ── Memory channel clock — MemoryClock → memory:clock → mem_clock ────


class _FakeMemoryClock:
    """Stub for the cached memory clock source (mhz)."""

    def __init__(self, mhz: float | None) -> None:
        self._mhz = mhz

    def clock(self) -> float | None:
        return self._mhz


def test_memory_clock_flows_to_snapshot() -> None:
    s = BaselineSensors(
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
        memory_clock=_FakeMemoryClock(2404.0),
    )

    assert s.read_all()["memory:clock"] == 2404.0
    assert s.snapshot().mem_clock == 2404.0


def test_memory_clock_absent_without_a_clock_source() -> None:
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

    # LEVEL, not just the substring: ``set_preferred_disk`` logs the pin at
    # INFO before the block below, and that line contains the key too — so a
    # substring-only filter counts it whenever the root logger happens to be
    # at DEBUG.  ~15 tests in test_diagnostics.py call ``configure_logging``,
    # which sets the root level and never restores it, so whether this test
    # passed depended on what xdist scheduled into the same worker first.
    warned = [r for r in caplog.records
              if r.levelno == logging.WARNING
              and "nvme_UNPLUGGED" in r.getMessage()]
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

    # Level-filtered for the same reason as the test above.
    warned = [r for r in caplog.records
              if r.levelno == logging.WARNING
              and "nvme0" in r.getMessage()]
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


# ── unsupported(): the third kind of nothing ────────────────────────
#
# ``read_all()`` omits a key for three different reasons and the consumer could
# not tell them apart: the host has no such sensor, the read found nothing this
# tick, or the read raised.  Only the FIRST is static, and these pin that
# distinction — including the trap that makes it dangerous to get wrong.


class _TempOnlyCpu(CpuSource):
    """Reads temp only — shaped after ``SmcCpu`` / ``SysctlCpu`` (1 of 4)."""

    @property
    def name(self) -> str:
        return "temp-only"

    def temp(self) -> float | None:
        return 44.0


class _NoReadingsGpu(GpuSource):
    """Reads none of the 7 — shaped after ``WmiVideoControllerGpu``."""

    def __init__(self, key: str = "wmi:0") -> None:
        self._key = key

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return "no-readings gpu"

    @property
    def is_discrete(self) -> bool:
        return True


def test_unsupported_is_empty_when_every_backend_reads_everything() -> None:
    """The Linux shape, and the reason this needs non-Linux fixtures at all."""
    s = BaselineSensors(cpu=FakeCpu(), memory=FakeMemory(),
                        gpus=[FakeGpu(0, discrete=True, vendor="nvidia")],
                        fans=[])
    assert s.unsupported() == frozenset()


def test_unsupported_names_every_key_no_backend_can_read() -> None:
    s = BaselineSensors(cpu=_TempOnlyCpu(), memory=FakeMemory(),
                        gpus=[_NoReadingsGpu()], fans=[])
    missing = s.unsupported()

    assert {"cpu:usage", "cpu:freq", "cpu:power"} <= missing
    assert "cpu:temp" not in missing, "the one quantity it DOES read"

    # Every alias the catalog advertises for that GPU, not just the indexed one
    # — a theme references `gpu:primary:temp`, the picker offers `gpu:0:temp`,
    # and the vendor alias `gpu:wmi:0:temp` is what a saved binding holds.
    for prefix in ("gpu:0", "gpu:wmi:0", "gpu:primary"):
        assert f"{prefix}:temp" in missing, prefix
        assert f"{prefix}:vram_total" in missing, prefix


def test_unsupported_excludes_ports_with_no_optional_quantity() -> None:
    """``memory:*`` / ``disk:temp`` are abstract everywhere, so they can never
    be unsupported — a backend cannot decline them and still be constructible."""
    s = BaselineSensors(cpu=_TempOnlyCpu(), memory=FakeMemory(),
                        gpus=[], fans=[],
                        disks=[FakeDisk("nvme0", 40.0)])
    assert not {k for k in s.unsupported()
                if k.startswith(("memory:", "disk:"))}


def test_unsupported_is_computed_once_and_kept() -> None:
    """It is asked per ``discover()`` — every 2 s from qtgui's picker — and the
    answer is a property of the CLASSES, so recomputing it is pure waste."""
    s = BaselineSensors(cpu=_TempOnlyCpu(), memory=FakeMemory(), gpus=[], fans=[])
    assert s.unsupported() is s.unsupported()


def test_unsupported_never_reports_a_key_that_is_merely_absent_this_tick() -> None:
    """THE trap, and the reason this is not a diff against ``read_all()``.

    A source that IMPLEMENTS a quantity but answers ``None`` right now is the
    ``0`` state, not the ``-1`` state.  Anything that omits or unbinds a sensor
    keys off this set, so folding a transient miss into it would let one cold
    poll persist a decision — the rate-derived keys (``disk:read``, ``net:up``,
    energy-counter ``cpu:power``) are legitimately missing from the FIRST poll
    and present from the second.
    """
    cpu = FakeCpu()
    cpu.values["power"] = None          # implemented, but reads nothing now
    s = BaselineSensors(cpu=cpu, memory=FakeMemory(), gpus=[], fans=[])
    assert s.read_all().get("cpu:power") is None, "absent from the readings"
    assert "cpu:power" not in s.unsupported(), (
        "a quantity the backend implements is never unsupported, however "
        "often it answers None"
    )


# ── discover() must not advertise what the host cannot read ─────────


def test_discover_withholds_unsupported_keys() -> None:
    """A sensor that can never hold a value must not reach any face.

    ``SensorReading.value`` is a plain ``float``, so an unreadable key could
    only be advertised as ``0.0`` — indistinguishable from a real zero.
    """
    s = BaselineSensors(cpu=_TempOnlyCpu(), memory=FakeMemory(),
                        gpus=[_NoReadingsGpu()], fans=[])
    ids = {r.sensor_id for r in s.discover()}

    assert "cpu:temp" in ids
    assert not ids & {"cpu:usage", "cpu:freq", "cpu:power"}
    assert not {i for i in ids if i.startswith("gpu:")}


def test_discover_still_advertises_a_key_that_is_merely_absent_now() -> None:
    """The transient case must survive, or a cold poll would hide a sensor.

    ``disk:read`` / ``net:up`` / energy-counter ``cpu:power`` are rate-derived
    and legitimately missing from the FIRST poll.  They are implemented, so
    they stay advertised and simply read ``0.0`` until the second sample.
    """
    cpu = FakeCpu()
    cpu.values["power"] = None
    s = BaselineSensors(cpu=cpu, memory=FakeMemory(), gpus=[], fans=[])

    ids = {r.sensor_id for r in s.discover()}
    assert "cpu:power" in ids, "implemented but empty right now — still offered"
    assert {"disk:read", "net:up"} <= ids


# ── Poll cadence must be changeable in flight ────────────────────────


def test_set_interval_changes_the_poll_cadence_while_the_thread_runs() -> None:
    """The user's refresh-interval lever must reach the SWEEP, not just the
    publish.

    ``_interval_s`` used to be written only by ``__init__`` and
    ``start_polling`` — and ``start_polling`` early-returns once its thread is
    alive.  So a ``SetRefreshInterval`` moved the broadcast cadence and left
    the sensor sweep running at whatever rate it booted with, for the life of
    the process: a user who raised the interval to save CPU kept paying every
    sweep, and one who lowered it got duplicate broadcasts off a cache that
    refreshed half as often.

    Counting SWEEPS, not reading ``_interval_s``: a value assertion passes
    whether or not the sleeping thread ever acts on the new number.  This
    starts the thread SLOW (60 s — it polls once, then sleeps well past the
    end of this test) and then asks for fast.  Nothing but a woken thread
    honouring the new cadence can make the count move.

    MUTATION CHECK: drop the ``_wake.set()`` from ``_interval_changed`` and
    this fails with 0 further polls (measured, not predicted) — the thread is
    still asleep on its original 60 s.
    """
    s, cpu = _counting_sensors()
    s.start_polling(60.0)
    try:
        idle = threading.Event()
        for _ in range(500):                  # bounded: never hang the suite
            if s._last_poll:                  # the bootstrap poll landed
                break
            idle.wait(0.01)
        assert s._last_poll, "background poll never landed"
        settled = cpu.reads

        s.set_interval(MIN_REFRESH_INTERVAL_S)

        for _ in range(500):                  # ~5 s ceiling for >=2 fast polls
            if cpu.reads >= settled + 2:
                break
            idle.wait(0.01)
        assert cpu.reads >= settled + 2, (
            f"sweep cadence ignored the change — {cpu.reads - settled} poll(s) "
            f"after set_interval({MIN_REFRESH_INTERVAL_S}s); the thread is "
            f"still sleeping out its original 60 s"
        )
    finally:
        s.stop_polling()


def test_set_interval_clamps_to_the_floor_like_start_polling_does() -> None:
    """One writer, one clamp — the sweep can never outrun the published range.

    ``start_polling`` clamped and ``set_interval`` is now the writer it
    delegates to, so the floor has to live in the delegate or the second path
    would be the unclamped one.
    """
    s, _ = _counting_sensors()

    s.set_interval(0.001)

    assert s._interval_s == MIN_REFRESH_INTERVAL_S


# ── #279: the MC-3 shows the speed the memory RUNS, not its nameplate ──────


def _memory_clock(monkeypatch, *, configured, spd_mhz=2404):
    """A MemoryClock whose two sources are scripted; returns (clock, calls)."""
    from types import SimpleNamespace

    from trcc.adapters.sensors.hwmon import MemoryClock

    calls: list[str] = []

    def dmi():
        calls.append("dmi")
        return configured

    monkeypatch.setattr("trcc.adapters.system.linux.configured_memory_mts", dmi)
    monkeypatch.setattr("trcc.adapters.system.spd.read_spd_timings",
                        lambda: SimpleNamespace(mhz=spd_mhz))
    return MemoryClock(), calls


def test_memory_clock_prefers_the_configured_speed(monkeypatch) -> None:
    clock, _ = _memory_clock(monkeypatch, configured=8000)
    assert clock.clock() == 4000.0              # 8000 MT/s, DDR -> 4000 MHz


def test_memory_clock_falls_back_to_the_spd_nameplate(monkeypatch) -> None:
    clock, _ = _memory_clock(monkeypatch, configured=None, spd_mhz=2404)
    assert clock.clock() == 2404.0


def test_memory_clock_reads_once_and_only_when_asked(monkeypatch) -> None:
    """dmidecode goes through pkexec — not at construction, and not per tick."""
    clock, calls = _memory_clock(monkeypatch, configured=8000)
    assert calls == []
    clock.clock()
    clock.clock()
    assert calls == ["dmi"]


def test_the_mc3_panel_shows_the_configured_speed(monkeypatch) -> None:
    """The number the reporter reads: LC1 phase 1 is mem_clock x memory_ratio."""
    from trcc.core.models import HardwareMetrics
    from trcc.services.led_segment import LC1Display

    clock, _ = _memory_clock(monkeypatch, configured=8000)
    shown = LC1Display().compute_mask(
        HardwareMetrics(mem_clock=clock.clock() or 0.0), phase=1)

    expected = [False] * LC1Display.mask_size
    expected[LC1Display.MTNO] = True
    LC1Display()._encode_4digit(8000, LC1Display.ALL_DIGITS, expected)
    assert shown == expected


# ── #282: drivers that keep their sensor files on hwmonN/device/ ────────────
#
# The reporter's sch5636 registers /sys/class/hwmon/hwmonN but leaves every
# *_input -- and `name` -- on the platform device, i.e. hwmonN/device/.
# `sensors` (and psutil) look there; our scanner did not, so the chip showed no
# fans and was named "hwmon1".


def _legacy_node(root: Path, name: str = "hwmon1") -> Path:
    """A node shaped like the reporter's: bare, with everything under device/."""
    node = root / name
    dev = node / "device"
    dev.mkdir(parents=True)
    (dev / "name").write_text("sch5636\n")
    (dev / "fan1_input").write_text("1514\n")
    (dev / "fan3_input").write_text("1836\n")
    (dev / "temp1_input").write_text("50000\n")
    return node


def test_a_legacy_node_reads_its_name_and_fans_from_device(tmp_path: Path) -> None:
    dev = hwmon.HwmonDevice(_legacy_node(tmp_path))

    assert dev.driver == "sch5636"
    fans = hwmon.discover_fans([dev])
    assert [f.rpm() for f in fans] == [1514, 1836]
    assert dev.read_temp(1) == 50.0


def test_a_modern_node_keeps_reading_itself_even_with_a_device_dir(
    tmp_path: Path,
) -> None:
    """NVMe publishes inputs on the node AND device/serial -- stay on the node.

    Pinned with a conflicting reading under device/ too: whenever the node has
    its own inputs, the node wins.  (A mutation preferring device/ survived
    until this line was added.)
    """
    node = tmp_path / "hwmon2"
    (node / "device").mkdir(parents=True)
    (node / "name").write_text("nvme\n")
    (node / "temp1_input").write_text("41000\n")
    (node / "device" / "serial").write_text("S123\n")
    (node / "device" / "temp1_input").write_text("99000\n")

    dev = hwmon.HwmonDevice(node)
    assert (dev.attrs, dev.driver, dev.read_temp(1)) == (node, "nvme", 41.0)


def test_a_node_with_no_inputs_anywhere_is_wrapped_as_before(tmp_path: Path) -> None:
    node = tmp_path / "hwmon3"
    node.mkdir()
    dev = hwmon.HwmonDevice(node)
    assert (dev.attrs, dev.driver) == (node, "hwmon3")


def test_build_linux_sensors_offers_a_nouveau_gpu(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The factory wires nouveau beside amdgpu and i915/xe.  Before, no backend
    read it AND the board reader skipped the chip as GPU-owned, so a nouveau
    card's temperature was shown nowhere."""
    from trcc.adapters.sensors import aggregator

    node = _nouveau_node(tmp_path, temp1_input="58000")
    monkeypatch.setattr(aggregator, "scan_hwmon_devices", lambda: [node])
    monkeypatch.setattr(aggregator, "discover_nvidia_gpus", lambda: [])
    s = aggregator.build_linux_sensors()
    assert [g.key for g in s.gpus()] == ["nouveau:0"]
    assert s.read_all()["gpu:primary:temp"] == 58.0
