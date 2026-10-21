"""W1 + W2: AMD and Intel GPUs on Windows -- D3DKMT/PDH for every vendor, ADL for
AMD, IGCL for Intel.

Until 2026-10-20 an AMD or Intel card on Windows showed its name and nothing
else unless HWiNFO or LibreHardwareMonitor ran beside TRCC.  The native calls
need Windows; what is pinned here, on any OS, is our side: struct layouts
against the SDK headers, how raw values become readings, and which reader a
card's value comes from.
"""
from __future__ import annotations

import ctypes
from types import SimpleNamespace
from typing import Any

import pytest

from trcc.adapters.sensors import _adl, _d3dkmt, _igcl
from trcc.adapters.sensors import windows as win

# ── struct layouts: sizes the headers imply ─────────────────────────────────


@pytest.mark.parametrize(("struct", "size"), [
    (_adl._AdapterInfo, 1572),          # adl_structures.h AdapterInfo, Windows layout
    (_adl._PMLogData, 4 + 256 * 8),     # ADLPMLogDataOutput
    (_d3dkmt._AdapterInfo, 20),         # D3DKMT_ADAPTERINFO
    (_d3dkmt._DeviceIds, 28),           # D3DKMT_QUERY_DEVICE_IDS
    (_d3dkmt._SegmentSize, 24),         # D3DKMT_SEGMENTSIZEINFO
    (_d3dkmt._PerfData, 64),            # D3DKMT_ADAPTER_PERFDATA, ULONGLONGs 8-aligned
    (_d3dkmt._PerfDataCaps, 40),        # D3DKMT_ADAPTER_PERFDATACAPS
    (_d3dkmt._AdapterAddress, 12),      # D3DKMT_ADAPTERADDRESS
    (_igcl._InitArgs, 36),              # ctl_init_args_t
    (_igcl._Properties, 320),           # ctl_device_adapter_properties_t
    (_igcl._Item, 24),                  # ctl_oc_telemetry_item_t: bool, 2 enums, 8-B union
    (_igcl._Psu, 56),                   # ctl_psu_info_t
    (_igcl._Telemetry, 1024),           # ctl_power_telemetry_t
])
def test_layouts_match_the_sdk_headers(struct: type, size: int) -> None:
    assert ctypes.sizeof(struct) == size


def test_perf_data_fields_sit_where_the_header_puts_them() -> None:
    assert (_d3dkmt._PerfData.FanRPM.offset, _d3dkmt._PerfData.Temperature.offset) == (48, 56)
    tel, props = _igcl._Telemetry, _igcl._Properties
    assert (tel.totalCardEnergyCounter.offset, tel.fanSpeed.offset,
            tel.vramWriteBandwidth.offset) == (384, 688, 1000)
    assert (props.name.offset, props.graphics_adapter_properties.offset,
            props.adapter_bdf.offset) == (88, 188, 200)


# ── ADL: PMLog slots -> readings ────────────────────────────────────────────


def _pmlog(**slots: int) -> _adl._PMLogData:
    data = _adl._PMLogData()
    for index, value in slots.items():
        data.sensors[int(index.lstrip("s"))] = _adl._Sensor(1, value)
    return data


def test_board_power_wins_and_the_edge_falls_back_to_gfx() -> None:
    """LHM's two rules: BOARD_POWER (73) where reported, ASIC (23) before;
    EDGE (8) else TEMPERATURE_GFX (28).  MUTATION CHECK: swap the power order."""
    data = _pmlog(s1=2450, s28=61, s9=70, s27=84, s14=1100, s15=35, s19=97, s23=180, s73=290)
    assert _adl.readings(data) == {
        "temp": 61.0, "hotspot": 84.0, "mem_temp": 70.0, "usage": 97.0, "clock": 2450.0,
        "power": 290.0, "fan_rpm": 1100.0, "fan": 35.0}


def test_a_slot_the_card_does_not_support_is_absent_not_zero() -> None:
    assert _adl.readings(_pmlog(s8=55)) == {"temp": 55.0}


def _adl_info(index: int, bus: int, vendor: int = 0x1002, present: int = 1,
              name: bytes = b"AMD Radeon RX 9070 XT") -> Any:
    return SimpleNamespace(iAdapterIndex=index, iBusNumber=bus, iDeviceNumber=0,
                           iFunctionNumber=0, iVendorID=vendor, iPresent=present,
                           strAdapterName=name)


def test_one_card_per_bus_slot_in_bus_order() -> None:
    """ADL lists one entry per display output.  MUTATION CHECK: no dedupe -> 3."""
    infos = [_adl_info(4, 7), _adl_info(0, 3), _adl_info(1, 3), _adl_info(2, 9, vendor=0x10DE),
             _adl_info(3, 5, present=0)]
    assert [c.iAdapterIndex for c in _adl.physical_cards(infos)] == [0, 4]


class _FakeAdl:
    def __init__(self, cards: dict[int, _adl._PMLogData | None]) -> None:
        self.cards, self.reads = cards, 0

    def adapters(self) -> list[Any]:
        return [_adl_info(i, bus=i) for i in self.cards]

    def pmlog(self, index: int) -> _adl._PMLogData | None:
        self.reads += 1
        return self.cards[index]

    def vram_used_mb(self, index: int) -> float | None:
        return 4096.0


def test_a_card_with_no_sensor_log_is_skipped_and_the_rest_read() -> None:
    adl = _FakeAdl({0: _pmlog(s8=50, s73=120), 1: _adl._PMLogData()})
    gpus = _adl.discover_adl_gpus(lambda: adl)
    assert [(g.key, g.temp(), g.power(), g.vram_used()) for g in gpus] == [
        ("amd:0", 50.0, 120.0, 4096.0)]


def test_one_sensor_read_serves_every_reading_within_the_ttl() -> None:
    """MUTATION CHECK: read the log per quantity -- 3 reads, not 1."""
    adl = _FakeAdl({0: _pmlog(s8=50, s73=120, s19=40)})
    gpu = _adl.AdlGpu(adl, 0, 0, "AMD Radeon RX 9070 XT", clock=lambda: 100.0)
    _ = (gpu.temp(), gpu.power(), gpu.usage())
    assert adl.reads == 1


def test_no_amd_driver_is_no_amd_gpus() -> None:
    def missing() -> Any:
        raise OSError("atiadlxx.dll not found")
    assert _adl.discover_adl_gpus(missing) == []


# ── D3DKMT + PDH ────────────────────────────────────────────────────────────


def test_usage_is_the_busiest_engine_type_summed_over_processes() -> None:
    """Task Manager's rule.  MUTATION CHECK: sum all engine types -- 100, not 70."""
    luid = "luid_0x00000000_0x0000C2B1_phys_0"
    engines = {f"pid_10_{luid}_eng_0_engtype_3D": 40.0,
               f"pid_22_{luid}_eng_0_engtype_3D": 30.0,
               f"pid_10_{luid}_eng_5_engtype_VideoDecode": 55.0,
               "pid_10_luid_0x00000000_0x00009999_phys_0_eng_0_engtype_3D": 5.0}
    assert _d3dkmt.usage_by_luid(engines) == {
        "0x00000000_0x0000c2b1": 70.0, "0x00000000_0x00009999": 5.0}


def test_a_luid_matches_its_counters_whatever_the_hex_case() -> None:
    """The first cut upper-cased the counters' LUIDs -- '0x' became '0X' -- and
    no adapter ever found its usage.  MUTATION CHECK: .upper() again."""
    luid = _d3dkmt.luid_text(0, 0xC2B1)
    usage = _d3dkmt.usage_by_luid({"pid_1_luid_0x00000000_0x0000C2B1_phys_0_eng_0_engtype_3D": 9.0})
    assert usage == {luid: 9.0}


def test_dedicated_memory_is_in_megabytes() -> None:
    assert _d3dkmt.bytes_by_luid({"luid_0x00000000_0x0000C2B1_phys_0": 512 * 1024 * 1024}) == {
        "0x00000000_0x0000c2b1": 512.0}


class _FakeKmt:
    """D3DKMT answering per adapter handle: {handle: {type: filler}}."""

    def __init__(self, adapters: dict[int, dict[int, Any]]) -> None:
        self._answers = adapters
        self.perf_queries = 0

    def adapters(self) -> list[Any]:
        return [_d3dkmt._AdapterInfo(h, _d3dkmt._Luid(0xC2B1 + h, 0), 1, 0) for h in self._answers]

    def query(self, handle: int, kind: int, data: Any) -> bool:
        fill = self._answers[handle].get(kind)
        if kind == _d3dkmt._KMTQAITYPE_ADAPTERPERFDATA:
            self.perf_queries += 1
        if fill is None:
            return False
        fill(data)
        return True


def _card(vendor: int, name: str, *, temp: int = 0, fan: int = 0,
          caps: tuple[int, int] = (0, 0), software: bool = False,
          pci: tuple[int, int, int] | None = None) -> dict[int, Any]:
    def set_ids(d: Any) -> None:
        d.VendorID = vendor

    def set_name(d: Any) -> None:
        d.AdapterString = name

    def set_kind(d: Any) -> None:
        d.value = _d3dkmt._SOFTWARE_DEVICE if software else 1

    def set_vram(d: Any) -> None:
        d.DedicatedVideoMemorySize = 16 * 1024 ** 3

    def set_caps(d: Any) -> None:
        d.MaxFanRPM, d.TemperatureMax = caps

    def set_perf(d: Any) -> None:
        d.Temperature, d.FanRPM, d.Power = temp, fan, 873       # Power: tenths of a %

    def set_address(d: Any) -> None:
        d.BusNumber, d.DeviceNumber, d.FunctionNumber = pci or (0, 0, 0)

    answers = {_d3dkmt._KMTQAITYPE_ADAPTERTYPE: set_kind,
            _d3dkmt._KMTQAITYPE_PHYSICALADAPTERDEVICEIDS: set_ids,
            _d3dkmt._KMTQAITYPE_ADAPTERREGISTRYINFO: set_name,
            _d3dkmt._KMTQAITYPE_GETSEGMENTSIZE: set_vram,
            _d3dkmt._KMTQAITYPE_ADAPTERPERFDATA_CAPS: set_caps,
            _d3dkmt._KMTQAITYPE_ADAPTERPERFDATA: set_perf}
    if pci is not None:
        answers[_d3dkmt._KMTQAITYPE_ADAPTERADDRESS] = set_address
    return answers


class _FakePdh:
    def sample(self) -> tuple[dict[str, float], dict[str, float]]:
        return {"0x00000000_0x0000c2b2": 63.0}, {"0x00000000_0x0000c2b2": 2048.0}


def test_every_vendor_reads_through_windows_and_software_adapters_do_not() -> None:
    """Keys count each vendor's adapters; the basic render driver is skipped.
    Temperature is deci-°C; Power (tenths of a PERCENT) is never offered as watts.

    MUTATION CHECK: drop the /10 -- 650, not 65."""
    kmt = _FakeKmt({
        1: _card(0x8086, "Intel(R) Arc(TM) A770 Graphics", temp=650, fan=1500, caps=(3000, 105)),
        2: _card(0x1414, "Microsoft Basic Render Driver", software=True),
        3: _card(0x8086, "Intel(R) UHD Graphics 770"),
    })
    gpus = _d3dkmt.discover_d3dkmt_gpus(lambda: kmt, lambda: _FakePdh())
    arc, uhd = gpus
    assert [(g.key, g.name, g.is_discrete) for g in gpus] == [
        ("intel:0", "Intel(R) Arc(TM) A770 Graphics", True),
        ("intel:1", "Intel(R) UHD Graphics 770", False)]
    assert (arc.temp(), arc.fan_rpm(), arc.usage(), arc.vram_used(), arc.vram_total(),
            arc.power()) == (65.0, 1500.0, 63.0, 2048.0, 16384.0, None)
    assert (uhd.temp(), uhd.fan_rpm()) == (None, None)       # no perf caps: not offered


def test_a_vendors_cards_count_by_pci_address_not_enumeration_order() -> None:
    """Windows may list the Arc before the iGPU; ADL and IGCL count by PCI
    bus, so D3DKMT must too or the two cards swap readings in the chain.

    MUTATION CHECK: drop the sort -- the Arc becomes intel:0."""
    kmt = _FakeKmt({
        1: _card(0x8086, "Intel(R) Arc(TM) A770 Graphics", pci=(3, 0, 0)),
        2: _card(0x8086, "Intel(R) UHD Graphics 770", pci=(0, 2, 0)),
    })
    gpus = _d3dkmt.discover_d3dkmt_gpus(lambda: kmt, lambda: _FakePdh())
    assert [(g.key, g.name) for g in gpus] == [
        ("intel:0", "Intel(R) UHD Graphics 770"),
        ("intel:1", "Intel(R) Arc(TM) A770 Graphics")]


def test_an_adapter_off_the_pci_bus_sorts_after_every_one_on_it() -> None:
    """All-ones is "no address" -- what the win11 VM's render driver answered.

    MUTATION CHECK: drop the all-ones test -- pci reads (4294967295, 65535, 65535)."""
    kmt = _FakeKmt({1: _card(0x8086, "Intel(R) UHD Graphics 770",
                             pci=(0xFFFFFFFF, 0xFFFF, 0xFFFF))})
    (info,) = kmt.adapters()
    adapter = _d3dkmt.describe(kmt, info)
    assert adapter is not None
    assert adapter.pci is None


def test_no_pdh_still_gives_temperature_and_total_vram() -> None:
    kmt = _FakeKmt({1: _card(0x1002, "AMD Radeon RX 7800 XT", temp=520, caps=(0, 110))})

    def no_pdh() -> Any:
        raise ImportError("win32pdh")

    (gpu,) = _d3dkmt.discover_d3dkmt_gpus(lambda: kmt, no_pdh)
    assert (gpu.key, gpu.temp(), gpu.usage(), gpu.vram_total()) == ("amd:0", 52.0, None, 16384.0)


def test_off_windows_there_is_no_d3dkmt() -> None:
    def missing() -> Any:
        raise AttributeError("WinDLL")
    assert _d3dkmt.discover_d3dkmt_gpus(missing) == []


# ── the chain: which reader a value comes from ──────────────────────────────


def test_an_amd_card_reads_from_adl_first_then_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADL has hotspot and watts; D3DKMT fills what ADL lacks (total VRAM).

    MUTATION CHECK: put the Windows reader ahead of ADL -- temp reads 52, not 50."""
    adl = _FakeAdl({0: _pmlog(s8=50, s27=71, s73=210)})
    kmt = _FakeKmt({1: _card(0x1002, "AMD Radeon RX 9070 XT", temp=520, caps=(0, 110))})
    monkeypatch.setattr(win, "discover_nvidia_gpus", lambda: [])
    monkeypatch.setattr(win, "discover_lhm_gpus", lambda: [])
    monkeypatch.setattr(win, "discover_hwinfo_gpus", lambda: [])
    monkeypatch.setattr(win, "discover_adl_gpus", lambda: _adl.discover_adl_gpus(lambda: adl))
    monkeypatch.setattr(win, "discover_igcl_gpus", lambda: [])
    monkeypatch.setattr(win, "discover_d3dkmt_gpus",
                        lambda: _d3dkmt.discover_d3dkmt_gpus(lambda: kmt, lambda: _FakePdh()))

    (card,) = win._build_windows_gpu_chains()

    assert (card.key, card.temp(), card.hotspot(), card.power(), card.vram_total()) == (
        "amd:0", 50.0, 71.0, 210.0, 16384.0)


# ── IGCL: Intel's telemetry -> readings ─────────────────────────────────────

_DOUBLE, _INT32 = 9, 4
_RPM, _PERCENT = 9, 11


def _item(item: Any, value: float, *, kind: int = _DOUBLE, units: int = 0) -> None:
    item.bSupported, item.type, item.units = True, kind, units
    setattr(item.value, _igcl._VALUE_FIELDS[kind], value)


def _telemetry(t: float, *, energy: float | None = None, card_energy: float | None = None,
               activity: float | None = None, temp: float | None = None,
               fan: tuple[float, int] | None = None) -> Any:
    data = _igcl._Telemetry()
    _item(data.timeStamp, t, units=7)
    for item, value in ((data.gpuEnergyCounter, energy), (data.totalCardEnergyCounter, card_energy),
                        (data.globalActivityCounter, activity),
                        (data.gpuCurrentTemperature, temp)):
        if value is not None:
            _item(item, value)
    if fan is not None:
        _item(data.fanSpeed[0], fan[0], units=fan[1])
    return data


def test_an_item_reads_the_union_member_its_type_names() -> None:
    data = _igcl._Telemetry()
    _item(data.gpuCurrentTemperature, 61, kind=_INT32)
    assert _igcl.item_value(data.gpuCurrentTemperature) == 61.0
    assert _igcl.item_value(data.vramCurrentTemperature) is None     # not supported: absent


def test_power_and_usage_are_counter_change_over_time() -> None:
    """Watts = joules / seconds, usage = busy seconds / seconds, as Intel's
    sample computes them; the first read has no rate yet.

    MUTATION CHECK: drop the *100 -- usage reads 0.25, not 25."""
    first = _igcl.sample(_telemetry(10.0, energy=500.0, activity=3.0, temp=55.0))
    later = _igcl.sample(_telemetry(12.0, energy=560.0, activity=3.5, temp=57.0))
    assert _igcl.readings({}, first) == {"temp": 55.0}
    assert _igcl.readings(first, later) == {"temp": 57.0, "power": 30.0, "usage": 25.0}


def test_whole_card_energy_wins_over_the_gpu_chips() -> None:
    a = _igcl.sample(_telemetry(1.0, energy=100.0, card_energy=200.0))
    b = _igcl.sample(_telemetry(2.0, energy=150.0, card_energy=390.0))
    assert _igcl.readings(a, b)["power"] == 190.0


def test_a_counter_that_went_backwards_gives_no_rate() -> None:
    """A driver reset restarts the counters; a negative watt is not a reading."""
    a = _igcl.sample(_telemetry(1.0, energy=900.0))
    b = _igcl.sample(_telemetry(2.0, energy=5.0))
    assert "power" not in _igcl.readings(a, b)


@pytest.mark.parametrize(("units", "quantity"), [(_RPM, "fan_rpm"), (_PERCENT, "fan")])
def test_the_fan_is_rpm_or_percent_by_its_units(units: int, quantity: str) -> None:
    assert _igcl.readings({}, _igcl.sample(_telemetry(1.0, fan=(42.0, units)))) == {quantity: 42.0}


class _FakeIgcl:
    """IGCL with {handle: (vendor, device_type, bdf, integrated, name, telemetry or None)}."""

    def __init__(self, devices: dict[int, tuple[Any, ...]]) -> None:
        self._devices = devices
        self.reads = 0

    def devices(self) -> list[int]:
        return list(self._devices)

    def properties(self, handle: int) -> Any:
        vendor, kind, bdf, integrated, name, _ = self._devices[handle]
        props = _igcl._Properties(pci_vendor_id=vendor, device_type=kind, name=name.encode(),
                                  graphics_adapter_properties=int(integrated))
        props.adapter_bdf.bus, props.adapter_bdf.device, props.adapter_bdf.function = bdf
        return props

    def telemetry(self, handle: int) -> Any:
        self.reads += 1
        return self._devices[handle][5]


def test_intel_gpus_count_by_pci_address_and_non_gpus_are_skipped() -> None:
    """The iGPU sits at bus 0 and is intel:0 even when IGCL lists the Arc first
    -- D3DKMT counts the same way.  A GPU with no telemetry keeps its number.

    MUTATION CHECK: drop the bdf sort -- the Arc becomes intel:0."""
    igcl = _FakeIgcl({
        0xA: (0x8086, 1, (3, 0, 0), False, "Intel(R) Arc(TM) B580 Graphics", _telemetry(1.0, temp=48.0)),
        0xB: (0x8086, 1, (0, 2, 0), True, "Intel(R) UHD Graphics 770", _telemetry(1.0, temp=41.0)),
        0xC: (0x8086, 2, (0, 4, 0), False, "not graphics", _telemetry(1.0, temp=1.0)),
        0xD: (0x10DE, 1, (1, 0, 0), False, "not Intel", _telemetry(1.0, temp=1.0)),
    })
    gpus = _igcl.discover_igcl_gpus(lambda: igcl)
    assert [(g.key, g.name, g.is_discrete, g.temp()) for g in gpus] == [
        ("intel:0", "Intel(R) UHD Graphics 770", False, 41.0),
        ("intel:1", "Intel(R) Arc(TM) B580 Graphics", True, 48.0)]

    silent = _FakeIgcl({
        0xB: (0x8086, 1, (0, 2, 0), True, "Intel(R) UHD Graphics 770", None),
        0xA: (0x8086, 1, (3, 0, 0), False, "Intel(R) Arc(TM) B580 Graphics", _telemetry(1.0, temp=48.0)),
    })
    assert [g.key for g in _igcl.discover_igcl_gpus(lambda: silent)] == ["intel:1"]


def test_one_telemetry_read_serves_every_reading_within_the_ttl() -> None:
    igcl = _FakeIgcl({1: (0x8086, 1, (3, 0, 0), False, "Arc", _telemetry(1.0, temp=50.0))})
    now = [100.0]
    gpu = _igcl.IgclGpu(igcl, 1, 0, "Arc", True, clock=lambda: now[0])
    for read in (gpu.temp, gpu.clock, gpu.power):
        read()
    assert igcl.reads == 1
    now[0] += 1.0
    gpu.temp()
    assert igcl.reads == 2


def test_no_intel_driver_is_no_intel_gpus() -> None:
    def missing() -> Any:
        raise OSError("ControlLib.dll not found")

    assert _igcl.discover_igcl_gpus(missing) == []


def test_an_intel_card_reads_from_igcl_first_then_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """IGCL has the temperature; D3DKMT fills VRAM, which IGCL does not report.

    MUTATION CHECK: put the Windows reader ahead of IGCL -- temp reads 65, not 48."""
    igcl = _FakeIgcl({1: (0x8086, 1, (3, 0, 0), False, "Intel(R) Arc(TM) A770 Graphics",
                          _telemetry(1.0, temp=48.0))})
    kmt = _FakeKmt({1: _card(0x8086, "Intel(R) Arc(TM) A770 Graphics", temp=650, caps=(0, 105),
                             pci=(3, 0, 0))})
    for name in ("discover_nvidia_gpus", "discover_lhm_gpus", "discover_hwinfo_gpus",
                 "discover_adl_gpus"):
        monkeypatch.setattr(win, name, lambda: [])
    monkeypatch.setattr(win, "discover_igcl_gpus", lambda: _igcl.discover_igcl_gpus(lambda: igcl))
    monkeypatch.setattr(win, "discover_d3dkmt_gpus",
                        lambda: _d3dkmt.discover_d3dkmt_gpus(lambda: kmt, lambda: _FakePdh()))

    (card,) = win._build_windows_gpu_chains()

    assert (card.key, card.temp(), card.vram_total(), card.vram_used()) == (
        "intel:0", 48.0, 16384.0, 2048.0)
