"""``AdlGpu`` -- AMD cards on Windows, through AMD's own library.

Every AMD driver installs ``atiadlxx.dll`` (ADL, the AMD Display Library);
AMD's own software reads its sensor log from it.  One call,
``ADL2_New_QueryPMLogData_Get``, returns 256 (supported, value) slots --
temperatures in °C, clocks in MHz, power in W, fan in RPM and %, activity in % --
for the card, with no admin.  AMD marks that call deprecated in favour of a
shared-memory variant, but it is still what LibreHardwareMonitor reads
(``AmdGpu.cs``) on RDNA3 and RDNA4.  Without it an AMD card on Windows showed
only its name unless HWiNFO or LibreHardwareMonitor ran beside TRCC
(2026-10-20).

Sensor numbers from the ADL SDK's ``adl_defines.h`` (MIT): ``ADLSensorType``.
Two rules from LHM: power is BOARD_POWER where the card reports it (RDNA3 on)
and ASIC_POWER before; the edge temperature falls back to TEMPERATURE_GFX.
Keys are ``amd:<n>``, n counting physical cards by PCI bus, so one card chains
with D3DKMT's and HWiNFO's readings of it.

ADLX, AMD's newer library, is not used: its licence allows distribution in
object code only and forbids use that requires a free-software licence, which
GPL-3 is.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from ...core.logs import per_frame
from ...core.ports import GpuSource
from ._wmi_gpu import _is_discrete

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_ADL_OK = 0
_ADL_MAX_PATH = 256
_PMLOG_MAX = 256
_AMD = 0x1002
# adl_defines.h ADLSensorType
_GFXCLK, _EDGE, _MEM, _FAN_RPM, _FAN_PCT = 1, 8, 9, 14, 15
_ACTIVITY_GFX, _ASIC_POWER, _HOTSPOT, _TEMP_GFX, _BOARD_POWER = 19, 23, 27, 28, 73
#: One PMLog read serves every reading of a card for this long.
_SAMPLE_TTL_S = 0.9


class _AdapterInfo(ctypes.Structure):
    """adl_structures.h ``AdapterInfo`` (Windows layout, 1572 bytes)."""

    _fields_ = [("iSize", ctypes.c_int), ("iAdapterIndex", ctypes.c_int),
                ("strUDID", ctypes.c_char * _ADL_MAX_PATH), ("iBusNumber", ctypes.c_int),
                ("iDeviceNumber", ctypes.c_int), ("iFunctionNumber", ctypes.c_int),
                ("iVendorID", ctypes.c_int), ("strAdapterName", ctypes.c_char * _ADL_MAX_PATH),
                ("strDisplayName", ctypes.c_char * _ADL_MAX_PATH), ("iPresent", ctypes.c_int),
                ("iExist", ctypes.c_int), ("strDriverPath", ctypes.c_char * _ADL_MAX_PATH),
                ("strDriverPathExt", ctypes.c_char * _ADL_MAX_PATH),
                ("strPNPString", ctypes.c_char * _ADL_MAX_PATH), ("iOSDisplayIndex", ctypes.c_int)]


class _Sensor(ctypes.Structure):
    _fields_ = [("supported", ctypes.c_int), ("value", ctypes.c_int)]


class _PMLogData(ctypes.Structure):
    """adl_structures.h ``ADLPMLogDataOutput``."""

    _fields_ = [("size", ctypes.c_int), ("sensors", _Sensor * _PMLOG_MAX)]


#: TRCC's GPU quantity -> the PMLog slots that answer it, first supported wins.
_SLOTS: dict[str, tuple[int, ...]] = {
    "temp": (_EDGE, _TEMP_GFX), "hotspot": (_HOTSPOT,), "mem_temp": (_MEM,),
    "usage": (_ACTIVITY_GFX,), "clock": (_GFXCLK,), "power": (_BOARD_POWER, _ASIC_POWER),
    "fan_rpm": (_FAN_RPM,), "fan": (_FAN_PCT,),
}


def readings(log_data: _PMLogData) -> dict[str, float]:
    """The PMLog slots this card supports, as TRCC's GPU quantities."""
    sensors = log_data.sensors
    found: dict[str, float] = {}
    for quantity, slots in _SLOTS.items():
        for index in slots:
            if sensors[index].supported:
                found[quantity] = float(sensors[index].value)
                break
    frame_log.debug("readings: %s", found)
    return found


class _Adl:
    """atiadlxx.dll -- the only part that needs Windows and an AMD driver."""

    def __init__(self) -> None:
        log.debug("_Adl: loading atiadlxx.dll")
        dll = ctypes.CDLL("atiadlxx.dll")
        self._buffers: list[Any] = []
        # ADL_MAIN_MALLOC_CALLBACK: void* __stdcall (int).  The buffers stay
        # referenced for the process: ADL never frees what it asks for.
        self._alloc = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_int)(  # pyright: ignore[reportAttributeAccessIssue]
            self._malloc)
        self._context = ctypes.c_void_p()
        create = dll.ADL2_Main_Control_Create
        create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        if create(self._alloc, 1, ctypes.byref(self._context)) != _ADL_OK:
            raise OSError("ADL2_Main_Control_Create failed")
        self._count = dll.ADL2_Adapter_NumberOfAdapters_Get
        self._info = dll.ADL2_Adapter_AdapterInfo_Get
        self._pmlog = dll.ADL2_New_QueryPMLogData_Get
        self._vram = dll.ADL2_Adapter_DedicatedVRAMUsage_Get
        for fn in (self._count, self._info, self._pmlog, self._vram):
            fn.restype = ctypes.c_int

    def _malloc(self, size: int) -> int:
        buffer = ctypes.create_string_buffer(max(1, size))
        self._buffers.append(buffer)
        frame_log.debug("_Adl._malloc: %d bytes", size)
        return ctypes.addressof(buffer)

    def adapters(self) -> list[_AdapterInfo]:
        """Every ADL adapter -- one per display output, so cards repeat."""
        count = ctypes.c_int()
        if self._count(self._context, ctypes.byref(count)) != _ADL_OK or count.value <= 0:
            log.info("_Adl.adapters: none")
            return []
        array = (_AdapterInfo * count.value)()
        if self._info(self._context, array, ctypes.sizeof(array)) != _ADL_OK:
            log.warning("_Adl.adapters: ADL2_Adapter_AdapterInfo_Get failed")
            return []
        log.debug("_Adl.adapters: %d", count.value)
        return list(array)

    def pmlog(self, index: int) -> _PMLogData | None:
        data = _PMLogData()
        status = self._pmlog(self._context, index, ctypes.byref(data))
        frame_log.debug("_Adl.pmlog: adapter %d -> %d", index, status)
        return data if status == _ADL_OK else None

    def vram_used_mb(self, index: int) -> float | None:
        used = ctypes.c_int()
        status = self._vram(self._context, index, ctypes.byref(used))
        frame_log.debug("_Adl.vram_used_mb: adapter %d -> %d (%d MB)", index, status, used.value)
        return float(used.value) if status == _ADL_OK and used.value > 0 else None


class AdlGpu(GpuSource):
    """One physical AMD card, read through ADL's sensor log."""

    def __init__(self, adl: Any, adapter_index: int, ordinal: int, name: str,
                 clock: Callable[[], float] = time.monotonic) -> None:
        log.info("AdlGpu: amd:%d %s (ADL adapter %d)", ordinal, name, adapter_index)
        self._adl, self._index, self._ordinal, self._name = adl, adapter_index, ordinal, name
        self._clock = clock
        self._lock = threading.Lock()
        self._at = -1e9
        self._values: dict[str, float] = {}
        self._discrete = _is_discrete(name)

    @property
    def key(self) -> str:
        frame_log.debug("AdlGpu.key")
        return f"amd:{self._ordinal}"

    @property
    def name(self) -> str:
        frame_log.debug("AdlGpu.name")
        return self._name

    @property
    def is_discrete(self) -> bool:
        frame_log.debug("AdlGpu.is_discrete")
        return self._discrete

    def _read(self, quantity: str) -> float | None:
        with self._lock:
            now = self._clock()
            if now - self._at >= _SAMPLE_TTL_S:
                self._at = now
                data = self._adl.pmlog(self._index)
                self._values = readings(data) if data is not None else {}
                if (vram := self._adl.vram_used_mb(self._index)) is not None:
                    self._values["vram_used"] = vram
            value = self._values.get(quantity)
        frame_log.debug("AdlGpu._read: %s %s -> %s", self.key, quantity, value)
        return value

    def temp(self) -> float | None:
        frame_log.debug("AdlGpu.temp: %s", self.key)
        return self._read("temp")

    def hotspot(self) -> float | None:
        frame_log.debug("AdlGpu.hotspot: %s", self.key)
        return self._read("hotspot")

    def mem_temp(self) -> float | None:
        frame_log.debug("AdlGpu.mem_temp: %s", self.key)
        return self._read("mem_temp")

    def usage(self) -> float | None:
        frame_log.debug("AdlGpu.usage: %s", self.key)
        return self._read("usage")

    def clock(self) -> float | None:
        frame_log.debug("AdlGpu.clock: %s", self.key)
        return self._read("clock")

    def power(self) -> float | None:
        frame_log.debug("AdlGpu.power: %s", self.key)
        return self._read("power")

    def fan(self) -> float | None:
        frame_log.debug("AdlGpu.fan: %s", self.key)
        return self._read("fan")

    def fan_rpm(self) -> float | None:
        frame_log.debug("AdlGpu.fan_rpm: %s", self.key)
        return self._read("fan_rpm")

    def vram_used(self) -> float | None:
        frame_log.debug("AdlGpu.vram_used: %s", self.key)
        return self._read("vram_used")


def physical_cards(adapters: list[Any]) -> list[Any]:
    """One AMD adapter per physical card (ADL lists one per display output),
    ordered by PCI bus so the ordinals are stable."""
    seen: dict[tuple[int, int, int], Any] = {}
    for info in adapters:
        if info.iVendorID != _AMD or not info.iPresent:
            continue
        seen.setdefault((info.iBusNumber, info.iDeviceNumber, info.iFunctionNumber), info)
    cards = [seen[k] for k in sorted(seen)]
    log.debug("physical_cards: %d of %d adapter entries", len(cards), len(adapters))
    return cards


def discover_adl_gpus(adl_factory: Callable[[], Any] = _Adl) -> list[GpuSource]:
    """One :class:`AdlGpu` per AMD card whose sensor log answers; [] without an
    AMD driver."""
    log.info("discover_adl_gpus: called")
    try:
        adl = adl_factory()
    except (OSError, AttributeError) as e:
        log.info("discover_adl_gpus: no AMD driver library (%s)", e)
        return []
    gpus: list[GpuSource] = []
    for ordinal, info in enumerate(physical_cards(adl.adapters())):
        data = adl.pmlog(info.iAdapterIndex)
        if data is None or not readings(data):
            log.warning("discover_adl_gpus: %s reports no sensors through ADL -- skipped",
                        info.strAdapterName.decode(errors="replace"))
            continue
        gpus.append(AdlGpu(adl, info.iAdapterIndex, ordinal,
                           info.strAdapterName.decode(errors="replace").strip()))
    log.info("discover_adl_gpus: %d AMD GPU(s)", len(gpus))
    return gpus
