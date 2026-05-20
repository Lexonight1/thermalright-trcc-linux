"""Windows sensor sources — MSAcpi / LHM / HWiNFO via DI seams.

Each source ships with a factory parameter (``handle_factory`` for the
WMI sources, ``snapshot_factory`` for HWiNFO) so the protocol logic
is testable from a Linux dev box without ``wmi`` or Windows itself.
"""
from __future__ import annotations

import struct
from typing import Any

from trcc.next.adapters.sensors._hwinfo import (
    _ENTRY_VALUE_OFFSET,
    _HEADER_FMT,
    _HEADER_SIZE,
    _HWINFO_MAGIC,
    _NAME_LEN,
    _UNIT_LEN,
    TYPE_CLOCK,
    TYPE_POWER,
    TYPE_TEMP,
    TYPE_USAGE,
    HwinfoCpu,
    _parse_header,
    _snapshot_from_bytes,
)
from trcc.next.adapters.sensors._lhm import LhmCpu, discover_lhm_gpus
from trcc.next.adapters.sensors._msacpi import WmiAcpiCpu

# =========================================================================
# MSAcpi — thermal zones via root\wmi
# =========================================================================


class _FakeZone:
    def __init__(self, *, instance: str, deci_kelvin: float) -> None:
        self.InstanceName = instance
        self.CurrentTemperature = deci_kelvin


class _FakeMSAcpi:
    """Stand-in for the ``wmi.WMI(namespace='root\\wmi')`` handle."""

    def __init__(self, zones: list[_FakeZone]) -> None:
        self._zones = zones

    def MSAcpi_ThermalZoneTemperature(self) -> list[_FakeZone]:
        return self._zones


def _msacpi_factory(zones: list[_FakeZone]):
    return lambda: _FakeMSAcpi(zones)


def test_msacpi_returns_hottest_zone_in_celsius() -> None:
    # 3132 deci-K = 313.2 K = 40.05 °C ; 3197 deci-K = 46.55 °C
    cpu = WmiAcpiCpu(handle_factory=_msacpi_factory([
        _FakeZone(instance="ACPI\\ThermalZone\\TZ00_0", deci_kelvin=3132),
        _FakeZone(instance="ACPI\\ThermalZone\\TZ01_0", deci_kelvin=3197),
    ]))
    temp = cpu.temp()
    assert temp is not None
    assert round(temp, 2) == 46.55     # the hotter zone wins


def test_msacpi_returns_none_when_no_zones() -> None:
    cpu = WmiAcpiCpu(handle_factory=_msacpi_factory([]))
    assert cpu.temp() is None


def test_msacpi_returns_none_when_handle_factory_none() -> None:
    cpu = WmiAcpiCpu(handle_factory=lambda: None)
    assert cpu.temp() is None


def test_msacpi_exposes_only_temperature() -> None:
    """ACPI doesn't publish usage/freq/power — those must be None."""
    cpu = WmiAcpiCpu(handle_factory=_msacpi_factory([
        _FakeZone(instance="TZ", deci_kelvin=3000),
    ]))
    assert cpu.usage() is None
    assert cpu.freq() is None
    assert cpu.power() is None


def test_msacpi_survives_bad_reading() -> None:
    """A row whose CurrentTemperature isn't numeric should be skipped, not crash."""
    bad_zone = _FakeZone(instance="TZ", deci_kelvin=0)
    bad_zone.CurrentTemperature = "not a number"        # type: ignore[assignment]
    cpu = WmiAcpiCpu(handle_factory=_msacpi_factory([bad_zone]))
    assert cpu.temp() is None


# =========================================================================
# LHM — Hardware / Sensor rows under root\LibreHardwareMonitor
# =========================================================================


class _FakeLhmSensor:
    def __init__(self, *, name: str, sensor_type: str, value: float | None) -> None:
        self.Name = name
        self.SensorType = sensor_type
        self.Value = value


class _FakeLhmHardware:
    def __init__(
        self, *,
        identifier: str, name: str, hw_type: str,
        sensors: list[_FakeLhmSensor],
    ) -> None:
        self.Identifier = identifier
        self.Name = name
        self.HardwareType = hw_type
        self._sensors = sensors


class _FakeLhmNamespace:
    """Stand-in for the ``wmi.WMI(namespace='root\\LibreHardwareMonitor')`` handle."""

    def __init__(self, hardware: list[_FakeLhmHardware]) -> None:
        self._hw = hardware

    def Hardware(self, Identifier: str | None = None) -> list[_FakeLhmHardware]:
        if Identifier is None:
            return self._hw
        return [h for h in self._hw if h.Identifier == Identifier]

    def Sensor(self, Parent: str) -> list[_FakeLhmSensor]:
        for h in self._hw:
            if h.Identifier == Parent:
                return h._sensors
        return []


def _lhm_cpu_namespace() -> _FakeLhmNamespace:
    """One CPU row with the canonical four sensor types LhmCpu queries."""
    return _FakeLhmNamespace([
        _FakeLhmHardware(
            identifier="/intelcpu/0", name="Intel Core i9-13900K", hw_type="Cpu",
            sensors=[
                _FakeLhmSensor(name="CPU Core #1", sensor_type="Temperature", value=55.0),
                _FakeLhmSensor(name="CPU Core #2", sensor_type="Temperature", value=78.0),
                _FakeLhmSensor(name="CPU Package", sensor_type="Temperature", value=70.0),
                _FakeLhmSensor(name="CPU Total",  sensor_type="Load", value=42.5),
                _FakeLhmSensor(name="CPU Core #1", sensor_type="Load", value=100.0),
                _FakeLhmSensor(name="CPU Core #1", sensor_type="Clock", value=4900.0),
                _FakeLhmSensor(name="CPU Core #2", sensor_type="Clock", value=5200.0),
                _FakeLhmSensor(name="CPU Package", sensor_type="Power", value=145.0),
                _FakeLhmSensor(name="CPU Cores",   sensor_type="Power", value=120.0),
            ],
        ),
    ])


def test_lhm_cpu_temp_returns_max_core_temp() -> None:
    cpu = LhmCpu(handle_factory=lambda: _lhm_cpu_namespace())
    assert cpu.temp() == 78.0       # core 2 is hottest


def test_lhm_cpu_usage_prefers_cpu_total() -> None:
    cpu = LhmCpu(handle_factory=lambda: _lhm_cpu_namespace())
    assert cpu.usage() == 42.5      # "CPU Total" sensor wins


def test_lhm_cpu_freq_is_max_core_clock() -> None:
    cpu = LhmCpu(handle_factory=lambda: _lhm_cpu_namespace())
    assert cpu.freq() == 5200.0


def test_lhm_cpu_power_prefers_package() -> None:
    cpu = LhmCpu(handle_factory=lambda: _lhm_cpu_namespace())
    assert cpu.power() == 145.0


def test_lhm_cpu_returns_none_when_namespace_unavailable() -> None:
    cpu = LhmCpu(handle_factory=lambda: None)
    assert cpu.temp() is None
    assert cpu.usage() is None
    assert cpu.freq() is None
    assert cpu.power() is None


def test_lhm_cpu_skips_when_no_cpu_hardware_row() -> None:
    """LHM running but no CPU sensor row (just GPU?) → temp() None."""
    ns = _FakeLhmNamespace([
        _FakeLhmHardware(identifier="/gpu-nvidia/0", name="RTX 4090",
                         hw_type="GpuNvidia", sensors=[]),
    ])
    cpu = LhmCpu(handle_factory=lambda: ns)
    assert cpu.temp() is None


def test_lhm_cpu_name_reflects_hardware() -> None:
    cpu = LhmCpu(handle_factory=lambda: _lhm_cpu_namespace())
    assert "Intel Core i9-13900K" in cpu.name


def test_lhm_discover_gpus_normalizes_vendor_keys() -> None:
    ns = _FakeLhmNamespace([
        _FakeLhmHardware(identifier="/gpu-nvidia/0", name="GeForce RTX 4090",
                         hw_type="GpuNvidia", sensors=[]),
        _FakeLhmHardware(identifier="/gpu-amd/0", name="Radeon RX 7900 XTX",
                         hw_type="GpuAmd", sensors=[]),
        _FakeLhmHardware(identifier="/gpu-intel/0", name="Intel UHD 770",
                         hw_type="GpuIntel", sensors=[]),
    ])
    gpus = discover_lhm_gpus(handle_factory=lambda: ns)
    keys = [g.key for g in gpus]
    assert keys == ["nvidia:0", "amd:0", "intel:0"]
    # Intel UHD is integrated, not discrete
    intel_gpu = next(g for g in gpus if g.key == "intel:0")
    assert intel_gpu.is_discrete is False
    nvidia_gpu = next(g for g in gpus if g.key == "nvidia:0")
    assert nvidia_gpu.is_discrete is True


def test_lhm_discover_gpus_handles_missing_namespace() -> None:
    assert discover_lhm_gpus(handle_factory=lambda: None) == []


# =========================================================================
# HWiNFO — shared-memory layout
# =========================================================================


def _build_hwinfo_mmf(
    entries: list[tuple[int, int, str, float]],
    sensors: list[str] | None = None,
) -> bytes:
    """Build a minimal MMF byte buffer matching the HWiNFO format.

    *entries* is a list of (entry_type, sensor_index, entry_name, value).
    *sensors* is a list of sensor section row names (each becomes one
    Sensor record).
    """
    sensors = sensors or []
    sec_size = 8 + _NAME_LEN * 2            # id+instance + 2× name fields
    ent_size = 12 + _NAME_LEN * 2 + _UNIT_LEN + 4 * 8

    sec_count = len(sensors)
    ent_count = len(entries)
    sec_off = _HEADER_SIZE
    ent_off = sec_off + sec_count * sec_size

    header = struct.pack(
        _HEADER_FMT,
        _HWINFO_MAGIC,        # magic
        2,                    # version
        0,                    # version2
        0,                    # last_update (int64)
        sec_off, sec_size, sec_count,
        ent_off, ent_size, ent_count,
    )

    sensor_section = b""
    for s_name in sensors:
        block = struct.pack("<II", 0, 0)
        block += _name_pad(s_name)              # name_orig
        block += _name_pad(s_name)              # name_user
        sensor_section += block

    entry_section = b""
    for entry_type, sensor_index, name, value in entries:
        block = struct.pack("<III", entry_type, sensor_index, 0)
        block += _name_pad(name)                # name_orig
        block += _name_pad(name)                # name_user
        block += b"\x00" * _UNIT_LEN            # unit
        block += struct.pack("<dddd", value, value, value, value)
        entry_section += block

    return header + sensor_section + entry_section


def _name_pad(name: str) -> bytes:
    raw = name.encode("latin-1")
    return raw + b"\x00" * (_NAME_LEN - len(raw))


def test_hwinfo_parse_header_decodes_magic() -> None:
    buf = _build_hwinfo_mmf(entries=[], sensors=[])
    header = _parse_header(buf)
    assert header.magic == _HWINFO_MAGIC


def test_hwinfo_parse_header_rejects_short_buffer() -> None:
    import pytest

    with pytest.raises(ValueError, match="too short"):
        _parse_header(b"\x00" * (_HEADER_SIZE - 1))


def test_hwinfo_parse_header_rejects_bad_magic() -> None:
    import pytest

    bad = struct.pack(_HEADER_FMT, 0xDEADBEEF, 0, 0, 0, _HEADER_SIZE, 0, 0, _HEADER_SIZE, 0, 0)
    with pytest.raises(ValueError, match="magic mismatch"):
        _parse_header(bad)


def test_hwinfo_snapshot_indexes_entries_and_values() -> None:
    """A snapshot built from the canned MMF buffer should surface live values."""
    buf = _build_hwinfo_mmf(
        sensors=["CPU [#0]: AMD Ryzen 9 7950X"],
        entries=[
            (TYPE_TEMP,  0, "CPU Package",  78.5),
            (TYPE_USAGE, 0, "Total CPU Usage", 42.0),
            (TYPE_CLOCK, 0, "CPU Core 1 Clock",  4900.0),
            (TYPE_POWER, 0, "CPU Package Power", 165.0),
        ],
    )
    snap = _snapshot_from_bytes(buf)
    assert snap.find(TYPE_TEMP, entry_name_contains="cpu package") == 78.5
    assert snap.find(TYPE_USAGE, entry_name_contains="total cpu usage") == 42.0
    assert snap.find(TYPE_CLOCK, entry_name_contains="cpu core") == 4900.0
    assert snap.find(TYPE_POWER, entry_name_contains="package power") == 165.0


def test_hwinfo_cpu_pulls_through_snapshot_factory() -> None:
    """HwinfoCpu reads from whatever snapshot the factory returns."""
    buf = _build_hwinfo_mmf(
        sensors=["CPU [#0]: AMD Ryzen 9 7950X"],
        entries=[
            (TYPE_TEMP,  0, "CPU Package",      72.0),
            (TYPE_TEMP,  0, "CPU Core 1",       65.0),
            (TYPE_USAGE, 0, "Total CPU Usage",  88.0),
            (TYPE_CLOCK, 0, "CPU Core 1 Clock", 5050.0),
            (TYPE_CLOCK, 0, "CPU Core 2 Clock", 5200.0),
            (TYPE_POWER, 0, "CPU Package Power", 142.0),
        ],
    )
    snap = _snapshot_from_bytes(buf)
    cpu = HwinfoCpu(snapshot_factory=lambda: snap)
    assert cpu.temp() == 72.0
    assert cpu.usage() == 88.0
    assert cpu.freq() == 5200.0     # max core clock
    assert cpu.power() == 142.0


def test_hwinfo_cpu_returns_none_when_snapshot_factory_none() -> None:
    cpu = HwinfoCpu(snapshot_factory=lambda: None)
    assert cpu.temp() is None
    assert cpu.usage() is None
    assert cpu.freq() is None
    assert cpu.power() is None


def test_hwinfo_value_offset_constant_matches_layout() -> None:
    """Sanity: the value offset constant has to match the entry record layout."""
    assert _ENTRY_VALUE_OFFSET == 12 + _NAME_LEN * 2 + _UNIT_LEN


# =========================================================================
# Windows factory composes the chain correctly
# =========================================================================


def test_build_windows_sensors_constructs_chain(monkeypatch) -> None:
    """build_windows_sensors composes the four-strategy chain.

    We can't actually exercise HWiNFO/LHM/MSAcpi on Linux, but we can
    verify the factory wires them up without crashing — each strategy
    falls back to None on the dev box, the chain hands off to psutil.
    """
    from trcc.next.adapters.sensors import windows as win_factory

    # Force each Windows-specific factory to return None so no real WMI/MMF call happens
    monkeypatch.setattr(
        "trcc.next.adapters.sensors._msacpi._default_handle_factory",
        lambda: None,
    )
    monkeypatch.setattr(
        "trcc.next.adapters.sensors._lhm._default_handle_factory",
        lambda: None,
    )
    monkeypatch.setattr(
        "trcc.next.adapters.sensors._hwinfo._default_mapping_factory",
        lambda: None,
    )
    # Reset module-level HWiNFO snapshot cache so a previous test doesn't bleed in.
    from trcc.next.adapters.sensors import _hwinfo as hwmod
    hwmod._shared_snapshot = None

    sensors = win_factory.build_windows_sensors()
    cpu = sensors.cpu()
    # The chain falls through to PsutilCpu, which always answers usage on linux/win
    assert cpu.usage() is not None      # psutil baseline
    # Temp may be None on a VM/dev box; just verify no exception
    cpu.temp()


# ── housekeeping ─────────────────────────────────────────────────────


_ = Any  # quiet ruff if Any falls out of use
