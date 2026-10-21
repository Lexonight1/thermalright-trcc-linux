"""W1 + W2: AMD and Intel GPUs on Windows -- D3DKMT/PDH for every vendor, ADL for AMD.

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

from trcc.adapters.sensors import _adl, _d3dkmt
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
])
def test_layouts_match_the_sdk_headers(struct: type, size: int) -> None:
    assert ctypes.sizeof(struct) == size


def test_perf_data_fields_sit_where_the_header_puts_them() -> None:
    assert (_d3dkmt._PerfData.FanRPM.offset, _d3dkmt._PerfData.Temperature.offset) == (48, 56)


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
          caps: tuple[int, int] = (0, 0), software: bool = False) -> dict[int, Any]:
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

    return {_d3dkmt._KMTQAITYPE_ADAPTERTYPE: set_kind,
            _d3dkmt._KMTQAITYPE_PHYSICALADAPTERDEVICEIDS: set_ids,
            _d3dkmt._KMTQAITYPE_ADAPTERREGISTRYINFO: set_name,
            _d3dkmt._KMTQAITYPE_GETSEGMENTSIZE: set_vram,
            _d3dkmt._KMTQAITYPE_ADAPTERPERFDATA_CAPS: set_caps,
            _d3dkmt._KMTQAITYPE_ADAPTERPERFDATA: set_perf}


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
    monkeypatch.setattr(win, "discover_d3dkmt_gpus",
                        lambda: _d3dkmt.discover_d3dkmt_gpus(lambda: kmt, lambda: _FakePdh()))

    (card,) = win._build_windows_gpu_chains()

    assert (card.key, card.temp(), card.hotspot(), card.power(), card.vram_total()) == (
        "amd:0", 50.0, 71.0, 210.0, 16384.0)
