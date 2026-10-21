"""``D3dkmtGpu`` -- every GPU on Windows, through Windows itself.

Windows keeps the numbers Task Manager's GPU tab shows for every vendor, so
this is the reading an AMD or Intel card gets with no vendor tool running --
until 2026-10-20 such a card showed its name and nothing else (the WMI
fallback).  Three sources, none needing admin:

* **D3DKMT** (``gdi32.dll``, WDDM 2.4 / Windows 10 1803+): the adapters, their
  vendor and dedicated VRAM, and -- where the driver fills them -- temperature
  and fan RPM (``KMTQAITYPE_ADAPTERPERFDATA``).  Its ``Power`` field is tenths
  of a PERCENT, not watts, so it is not offered as power.
* **PDH** (``win32pdh``): ``GPU Engine`` utilisation and ``GPU Adapter Memory``
  dedicated usage, per adapter LUID -- Task Manager's own counters.  Usage is
  the busiest engine type, as Task Manager shows it.

Layouts from System Informer's ``d3dkmthk.h`` (the Windows DDK header) and
Microsoft Learn ``D3DKMT_ADAPTER_PERFDATA``.  Keys are ``<vendor>:<n>``, n
counting that vendor's adapters by PCI address, as ADL and IGCL count theirs, so
one card chains with the vendor library's readings of it (``windows.py``).  In
enumeration order an Intel iGPU and an Arc card -- or a Ryzen APU and a Radeon --
could take each other's numbers, and one card's readings show on the other.
"""
from __future__ import annotations

import ctypes
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ...core.logs import per_frame
from ...core.ports import GpuSource
from ._wmi_gpu import _is_discrete

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_VENDORS = {0x10DE: "nvidia", 0x1002: "amd", 0x8086: "intel"}
_KMTQAITYPE_GETSEGMENTSIZE = 3
_KMTQAITYPE_ADAPTERADDRESS = 6
_NO_PCI_BUS = 0xFFFFFFFF
_KMTQAITYPE_ADAPTERREGISTRYINFO = 8
_KMTQAITYPE_ADAPTERTYPE = 15
_KMTQAITYPE_PHYSICALADAPTERDEVICEIDS = 31
_KMTQAITYPE_ADAPTERPERFDATA = 62
_KMTQAITYPE_ADAPTERPERFDATA_CAPS = 63
_SOFTWARE_DEVICE = 1 << 2          # D3DKMT_ADAPTERTYPE.SoftwareDevice
#: How long one PDH / perf-data sample serves every reading of every GPU.
_SAMPLE_TTL_S = 0.9


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _AdapterAddress(ctypes.Structure):
    """``D3DKMT_ADAPTERADDRESS``: the adapter's PCI bus, device, function."""

    _fields_ = [("BusNumber", ctypes.c_uint32), ("DeviceNumber", ctypes.c_uint32),
                ("FunctionNumber", ctypes.c_uint32)]


class _AdapterInfo(ctypes.Structure):
    _fields_ = [("hAdapter", ctypes.c_uint32), ("AdapterLuid", _Luid),
                ("NumOfSources", ctypes.c_uint32),
                ("bPrecisePresentRegionsPreferred", ctypes.c_int32)]


class _EnumAdapters2(ctypes.Structure):
    _fields_ = [("NumAdapters", ctypes.c_uint32), ("pAdapters", ctypes.c_void_p)]


class _QueryAdapterInfo(ctypes.Structure):
    _fields_ = [("hAdapter", ctypes.c_uint32), ("Type", ctypes.c_int32),
                ("pPrivateDriverData", ctypes.c_void_p),
                ("PrivateDriverDataSize", ctypes.c_uint32)]


class _DeviceIds(ctypes.Structure):
    _fields_ = [("PhysicalAdapterIndex", ctypes.c_uint32), ("VendorID", ctypes.c_uint32),
                ("DeviceID", ctypes.c_uint32), ("SubVendorID", ctypes.c_uint32),
                ("SubSystemID", ctypes.c_uint32), ("RevisionID", ctypes.c_uint32),
                ("BusType", ctypes.c_uint32)]


class _RegistryInfo(ctypes.Structure):
    _fields_ = [("AdapterString", ctypes.c_wchar * 260), ("BiosString", ctypes.c_wchar * 260),
                ("DacType", ctypes.c_wchar * 260), ("ChipType", ctypes.c_wchar * 260)]


class _SegmentSize(ctypes.Structure):
    _fields_ = [("DedicatedVideoMemorySize", ctypes.c_uint64),
                ("DedicatedSystemMemorySize", ctypes.c_uint64),
                ("SharedSystemMemorySize", ctypes.c_uint64)]


class _PerfData(ctypes.Structure):
    _fields_ = [("PhysicalAdapterIndex", ctypes.c_uint32),
                ("MemoryFrequency", ctypes.c_uint64), ("MaxMemoryFrequency", ctypes.c_uint64),
                ("MaxMemoryFrequencyOC", ctypes.c_uint64), ("MemoryBandwidth", ctypes.c_uint64),
                ("PCIEBandwidth", ctypes.c_uint64), ("FanRPM", ctypes.c_uint32),
                ("Power", ctypes.c_uint32), ("Temperature", ctypes.c_uint32),
                ("PowerStateOverride", ctypes.c_uint8)]


class _PerfDataCaps(ctypes.Structure):
    _fields_ = [("PhysicalAdapterIndex", ctypes.c_uint32),
                ("MaxMemoryBandwidth", ctypes.c_uint64), ("MaxPCIEBandwidth", ctypes.c_uint64),
                ("MaxFanRPM", ctypes.c_uint32), ("TemperatureMax", ctypes.c_uint32),
                ("TemperatureWarning", ctypes.c_uint32)]


@dataclass(frozen=True, slots=True)
class Adapter:
    """One hardware adapter, as D3DKMT names it."""

    handle: int
    luid: str                      # "0x00000000_0x0000c2b1": PDH's spelling, lower-cased
    vendor: str
    name: str
    vram_total_mb: float | None
    has_temp: bool
    has_fan: bool
    pci: tuple[int, int, int] | None = None   # bus, device, function


def luid_text(high: int, low: int) -> str:
    """A LUID as PDH's instance names spell it -- ``0xHIGH_0xLOW``, 8 hex digits
    each -- lower-cased, as :func:`usage_by_luid` keys it, so case cannot split them."""
    text = f"0x{high & 0xFFFFFFFF:08x}_0x{low & 0xFFFFFFFF:08x}"
    log.debug("luid_text: %s", text)
    return text


_ENGINE = re.compile(r"luid_(0x[0-9A-Fa-f]{8}_0x[0-9A-Fa-f]{8})_phys_\d+_eng_\d+_engtype_(.*)$")
_MEMORY = re.compile(r"luid_(0x[0-9A-Fa-f]{8}_0x[0-9A-Fa-f]{8})_phys_\d+$")


def usage_by_luid(engines: Mapping[str, float]) -> dict[str, float]:
    """Per-adapter usage from ``GPU Engine`` instances, as Task Manager shows it:
    each engine type summed over processes, the busiest type, capped at 100."""
    per_type: dict[tuple[str, str], float] = {}
    for instance, value in engines.items():
        if (m := _ENGINE.search(instance)) is not None:
            key = (m.group(1).lower(), m.group(2))
            per_type[key] = per_type.get(key, 0.0) + value
    usage: dict[str, float] = {}
    for (luid, _engtype), total in per_type.items():
        usage[luid] = min(100.0, max(usage.get(luid, 0.0), total))
    frame_log.debug("usage_by_luid: %d instance(s) -> %s", len(engines), usage)
    return usage


def bytes_by_luid(memory: Mapping[str, float]) -> dict[str, float]:
    """Per-adapter ``GPU Adapter Memory`` dedicated usage, in MB."""
    used = {m.group(1).lower(): value / (1024 * 1024)
            for instance, value in memory.items()
            if (m := _MEMORY.search(instance)) is not None}
    frame_log.debug("bytes_by_luid: %s", used)
    return used


class _Kmt:
    """gdi32's D3DKMT calls -- the only part that needs Windows."""

    def __init__(self) -> None:
        log.debug("_Kmt: binding gdi32")
        gdi32 = ctypes.WinDLL("gdi32")  # pyright: ignore[reportAttributeAccessIssue]
        self._enum = gdi32.D3DKMTEnumAdapters2
        self._query = gdi32.D3DKMTQueryAdapterInfo
        self._enum.argtypes = [ctypes.POINTER(_EnumAdapters2)]
        self._query.argtypes = [ctypes.POINTER(_QueryAdapterInfo)]
        self._enum.restype = self._query.restype = ctypes.c_int32

    def query(self, handle: int, kind: int, data: Any) -> bool:
        """One ``D3DKMTQueryAdapterInfo``; True on STATUS_SUCCESS."""
        request = _QueryAdapterInfo(handle, kind, ctypes.addressof(data), ctypes.sizeof(data))
        status = self._query(ctypes.byref(request))
        if status != 0:
            frame_log.debug("_Kmt.query: type %d -> NTSTATUS 0x%08X", kind, status & 0xFFFFFFFF)
        return status == 0

    def adapters(self) -> list[_AdapterInfo]:
        """Every adapter D3DKMT enumerates -- software ones included."""
        count = _EnumAdapters2(0, None)
        if self._enum(ctypes.byref(count)) != 0 or not count.NumAdapters:
            log.info("_Kmt.adapters: none enumerated")
            return []
        array = (_AdapterInfo * count.NumAdapters)()
        request = _EnumAdapters2(count.NumAdapters, ctypes.addressof(array))
        if self._enum(ctypes.byref(request)) != 0:
            log.warning("_Kmt.adapters: D3DKMTEnumAdapters2 failed on the second call")
            return []
        log.debug("_Kmt.adapters: %d", request.NumAdapters)
        return list(array[:request.NumAdapters])


class _Pdh:
    """Task Manager's GPU counters through ``win32pdh``."""

    def __init__(self) -> None:
        import win32pdh  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
        log.debug("_Pdh: opening the GPU counter query")
        self._pdh = win32pdh
        self._query = win32pdh.OpenQuery()
        self._engine = win32pdh.AddEnglishCounter(
            self._query, r"\GPU Engine(*)\Utilization Percentage")
        self._memory = win32pdh.AddEnglishCounter(
            self._query, r"\GPU Adapter Memory(*)\Dedicated Usage")
        win32pdh.CollectQueryData(self._query)     # a rate needs a first sample

    def sample(self) -> tuple[dict[str, float], dict[str, float]]:
        """(usage % per LUID, dedicated MB per LUID)."""
        pdh = self._pdh
        pdh.CollectQueryData(self._query)
        fmt = pdh.PDH_FMT_DOUBLE | pdh.PDH_FMT_NOCAP100
        engines = pdh.GetFormattedCounterArray(self._engine, fmt)
        memory = pdh.GetFormattedCounterArray(self._memory, fmt)
        frame_log.debug("_Pdh.sample: %d engine, %d memory instance(s)",
                        len(engines), len(memory))
        return usage_by_luid(engines), bytes_by_luid(memory)


class GpuSampler:
    """One sample of every GPU's readings, shared, at most once per TTL."""

    def __init__(self, kmt: Any, pdh: Any, clock: Callable[[], float] = time.monotonic) -> None:
        log.debug("GpuSampler: pdh=%s", pdh is not None)
        self._kmt, self._pdh, self._clock = kmt, pdh, clock
        self._lock = threading.Lock()
        self._at = -1e9
        self._usage: dict[str, float] = {}
        self._vram: dict[str, float] = {}
        self._perf: dict[int, tuple[float | None, float | None]] = {}
        self._adapters: list[Adapter] = []

    def watch(self, adapters: Iterable[Adapter]) -> None:
        """The adapters whose perf data each sample reads."""
        self._adapters = list(adapters)
        log.debug("GpuSampler.watch: %d adapter(s)", len(self._adapters))

    def _refresh(self) -> None:
        now = self._clock()
        if now - self._at < _SAMPLE_TTL_S:
            return
        self._at = now
        if self._pdh is not None:
            try:
                self._usage, self._vram = self._pdh.sample()
            except Exception as e:
                frame_log.debug("GpuSampler: PDH sample failed: %s", e)
                self._usage, self._vram = {}, {}
        for adapter in self._adapters:
            if adapter.has_temp or adapter.has_fan:
                self._perf[adapter.handle] = self._perf_of(adapter)
        frame_log.debug("GpuSampler._refresh: %d usage, %d vram", len(self._usage), len(self._vram))

    def _perf_of(self, adapter: Adapter) -> tuple[float | None, float | None]:
        frame_log.debug("GpuSampler._perf_of: %s", adapter.name)
        perf = _PerfData()
        if not self._kmt.query(adapter.handle, _KMTQAITYPE_ADAPTERPERFDATA, perf):
            return None, None
        return (perf.Temperature / 10.0 if adapter.has_temp and perf.Temperature else None,
                float(perf.FanRPM) if adapter.has_fan else None)

    def read(self, adapter: Adapter) -> tuple[float | None, float | None,
                                               float | None, float | None]:
        """(usage %, VRAM used MB, temperature °C, fan RPM) for *adapter*."""
        frame_log.debug("GpuSampler.read: %s", adapter.luid)
        with self._lock:
            self._refresh()
            temp, fan = self._perf.get(adapter.handle, (None, None))
            return self._usage.get(adapter.luid), self._vram.get(adapter.luid), temp, fan


class D3dkmtGpu(GpuSource):
    """One hardware adapter: Windows' own numbers for it."""

    def __init__(self, adapter: Adapter, index: int, sampler: GpuSampler) -> None:
        log.info("D3dkmtGpu: %s:%d %s (vram %s MB, temp %s, fan %s)", adapter.vendor, index,
                 adapter.name, adapter.vram_total_mb, adapter.has_temp, adapter.has_fan)
        self._adapter, self._index, self._sampler = adapter, index, sampler
        self._discrete = _is_discrete(adapter.name)

    @property
    def key(self) -> str:
        frame_log.debug("D3dkmtGpu.key")
        return f"{self._adapter.vendor}:{self._index}"

    @property
    def name(self) -> str:
        frame_log.debug("D3dkmtGpu.name")
        return self._adapter.name

    @property
    def is_discrete(self) -> bool:
        frame_log.debug("D3dkmtGpu.is_discrete")
        return self._discrete

    def usage(self) -> float | None:
        frame_log.debug("D3dkmtGpu.usage: %s", self.key)
        return self._sampler.read(self._adapter)[0]

    def vram_used(self) -> float | None:
        frame_log.debug("D3dkmtGpu.vram_used: %s", self.key)
        return self._sampler.read(self._adapter)[1]

    def temp(self) -> float | None:
        frame_log.debug("D3dkmtGpu.temp: %s", self.key)
        return self._sampler.read(self._adapter)[2]

    def fan_rpm(self) -> float | None:
        frame_log.debug("D3dkmtGpu.fan_rpm: %s", self.key)
        return self._sampler.read(self._adapter)[3]

    def vram_total(self) -> float | None:
        frame_log.debug("D3dkmtGpu.vram_total: %s", self.key)
        return self._adapter.vram_total_mb


def describe(kmt: Any, info: Any) -> Adapter | None:
    """*info* (a ``D3DKMT_ADAPTERINFO``) as an :class:`Adapter`, or None for a
    software adapter or a vendor we do not key."""
    handle = info.hAdapter
    kind = ctypes.c_uint32()
    if kmt.query(handle, _KMTQAITYPE_ADAPTERTYPE, kind) and kind.value & _SOFTWARE_DEVICE:
        log.debug("describe: adapter %d is a software device -- skipped", handle)
        return None
    ids = _DeviceIds()
    vendor = _VENDORS.get(ids.VendorID) if kmt.query(
        handle, _KMTQAITYPE_PHYSICALADAPTERDEVICEIDS, ids) else None
    if vendor is None:
        log.info("describe: adapter %d vendor 0x%04X -- not a GPU vendor we key",
                 handle, ids.VendorID)
        return None
    registry = _RegistryInfo()
    name = (registry.AdapterString.strip() if kmt.query(
        handle, _KMTQAITYPE_ADAPTERREGISTRYINFO, registry) else "") or f"{vendor.upper()} GPU"
    segments = _SegmentSize()
    vram = (segments.DedicatedVideoMemorySize / (1024 * 1024) if kmt.query(
        handle, _KMTQAITYPE_GETSEGMENTSIZE, segments)
        and segments.DedicatedVideoMemorySize else None)
    caps = _PerfDataCaps()
    has_caps = kmt.query(handle, _KMTQAITYPE_ADAPTERPERFDATA_CAPS, caps)
    address = _AdapterAddress()
    # An adapter off the PCI bus answers all-ones (win11 VM, 2026-10-20: the
    # basic render driver gave 0xFFFFFFFF, 0xFFFF, 0xFFFF).
    pci = ((address.BusNumber, address.DeviceNumber, address.FunctionNumber)
           if kmt.query(handle, _KMTQAITYPE_ADAPTERADDRESS, address)
           and address.BusNumber != _NO_PCI_BUS else None)
    adapter = Adapter(handle=handle,
                      luid=luid_text(info.AdapterLuid.HighPart, info.AdapterLuid.LowPart),
                      vendor=vendor, name=name, vram_total_mb=vram,
                      has_temp=has_caps and caps.TemperatureMax > 0,
                      has_fan=has_caps and caps.MaxFanRPM > 0, pci=pci)
    log.info("describe: %s", adapter)
    return adapter


def pci_order(adapter: Adapter) -> tuple[int, int, int]:
    """Sort key: by PCI address, an adapter without one after every one with."""
    order = adapter.pci or (1 << 32, 0, 0)
    log.debug("pci_order: %s -> %s", adapter.luid, order)
    return order


def discover_d3dkmt_gpus(kmt_factory: Callable[[], Any] = _Kmt,
                         pdh_factory: Callable[[], Any] = _Pdh) -> list[GpuSource]:
    """One :class:`D3dkmtGpu` per hardware adapter; [] off Windows or before 1803."""
    log.info("discover_d3dkmt_gpus: called")
    try:
        kmt = kmt_factory()
    except (OSError, AttributeError) as e:
        log.info("discover_d3dkmt_gpus: D3DKMT unavailable (%s)", e)
        return []
    try:
        pdh = pdh_factory()
    except Exception as e:      # win32pdh absent, or no GPU counters on this build
        log.warning("discover_d3dkmt_gpus: no GPU usage counters (%s) -- "
                    "usage and VRAM used stay empty", e)
        pdh = None
    adapters = sorted((a for info in kmt.adapters() if (a := describe(kmt, info)) is not None),
                      key=pci_order)
    sampler = GpuSampler(kmt, pdh)
    sampler.watch(adapters)
    counts: dict[str, int] = {}
    gpus: list[GpuSource] = []
    for adapter in adapters:
        index = counts.get(adapter.vendor, 0)
        counts[adapter.vendor] = index + 1
        gpus.append(D3dkmtGpu(adapter, index, sampler))
    log.info("discover_d3dkmt_gpus: %d GPU(s)", len(gpus))
    return gpus
