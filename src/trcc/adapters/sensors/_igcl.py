"""``IgclGpu`` -- Intel GPUs on Windows, through Intel's own library.

Every Intel graphics driver installs ``ControlLib.dll`` (IGCL, the Intel
Graphics Control Library) in System32; Intel's own control panel reads its
telemetry from it.  One call, ``ctlPowerTelemetryGet``, returns the card's
temperature, clock, fan and VRAM temperature directly, and energy and activity
as running counters -- power is energy over time, usage activity time over
time, as Intel's ``Sample_TelemetryAPP.cpp`` computes them.  No admin.

Layouts from ``igcl_api.h`` (intel/drivers.gpu.control-library, MIT).  Only the
installed DLL is called; none of Intel's code ships with TRCC.  It is loaded
from System32 alone, as Intel's own loader (``cApiWrapper.cpp``) does, so a
``ControlLib.dll`` beside TRCC is never picked up.

Keys are ``intel:<n>``, n counting Intel graphics devices by PCI address, as
D3DKMT counts them, so one card chains with Windows' readings of it.
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

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_CTL_OK = 0
_INTEL = 0x8086
_APP_VERSION = (1 << 16) | 1            # CTL_MAKE_VERSION(1, 1)
_INIT_USE_LEVEL_ZERO = 1 << 0           # telemetry needs Level Zero
_DEVICE_TYPE_GRAPHICS = 1
_ADAPTER_INTEGRATED = 1 << 0            # CTL_ADAPTER_PROPERTIES_FLAG_INTEGRATED
_PROPERTIES_VERSION = 2                 # the first that fills adapter_bdf
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
_UNITS_RPM, _UNITS_PERCENT = 9, 11
_FAN_COUNT = _PSU_COUNT = 5
_NAME_LEN, _RESERVED_LEN = 100, 108
#: One telemetry read serves every reading of a card for this long.
_SAMPLE_TTL_S = 0.9


class _InitArgs(ctypes.Structure):
    """``ctl_init_args_t``."""

    _fields_ = [("Size", ctypes.c_uint32), ("Version", ctypes.c_uint8),
                ("AppVersion", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("SupportedVersion", ctypes.c_uint32), ("ApplicationUID", ctypes.c_uint8 * 16)]


class _Bdf(ctypes.Structure):
    _fields_ = [("bus", ctypes.c_uint8), ("device", ctypes.c_uint8), ("function", ctypes.c_uint8)]


class _Properties(ctypes.Structure):
    """``ctl_device_adapter_properties_t``."""

    _fields_ = [("Size", ctypes.c_uint32), ("Version", ctypes.c_uint8),
                ("pDeviceID", ctypes.c_void_p), ("device_id_size", ctypes.c_uint32),
                ("device_type", ctypes.c_uint32), ("supported_subfunction_flags", ctypes.c_uint32),
                ("driver_version", ctypes.c_uint64), ("firmware_version", ctypes.c_uint64 * 3),
                ("pci_vendor_id", ctypes.c_uint32), ("pci_device_id", ctypes.c_uint32),
                ("rev_id", ctypes.c_uint32), ("num_eus_per_sub_slice", ctypes.c_uint32),
                ("num_sub_slices_per_slice", ctypes.c_uint32), ("num_slices", ctypes.c_uint32),
                ("name", ctypes.c_char * _NAME_LEN),
                ("graphics_adapter_properties", ctypes.c_uint32), ("Frequency", ctypes.c_uint32),
                ("pci_subsys_id", ctypes.c_uint16), ("pci_subsys_vendor_id", ctypes.c_uint16),
                ("adapter_bdf", _Bdf), ("num_xe_cores", ctypes.c_uint32),
                ("reserved", ctypes.c_char * _RESERVED_LEN)]


class _Value(ctypes.Union):
    """``ctl_data_value_t``."""

    _fields_ = [("data8", ctypes.c_int8), ("datau8", ctypes.c_uint8),
                ("data16", ctypes.c_int16), ("datau16", ctypes.c_uint16),
                ("data32", ctypes.c_int32), ("datau32", ctypes.c_uint32),
                ("data64", ctypes.c_int64), ("datau64", ctypes.c_uint64),
                ("datafloat", ctypes.c_float), ("datadouble", ctypes.c_double)]


#: ``ctl_data_type_t`` -> the union member holding it (CTL_DATA_TYPE_INT8 = 0 ...).
_VALUE_FIELDS = tuple(field[0] for field in _Value._fields_)


class _Item(ctypes.Structure):
    """``ctl_oc_telemetry_item_t``."""

    _fields_ = [("bSupported", ctypes.c_bool), ("units", ctypes.c_uint32),
                ("type", ctypes.c_uint32), ("value", _Value)]


class _Psu(ctypes.Structure):
    _fields_ = [("bSupported", ctypes.c_bool), ("psuType", ctypes.c_uint32),
                ("energyCounter", _Item), ("voltage", _Item)]


_FLAGS = ("PowerLimited", "TemperatureLimited", "CurrentLimited", "VoltageLimited",
          "UtilizationLimited")


class _Telemetry(ctypes.Structure):
    """``ctl_power_telemetry_t`` (1024 bytes)."""

    _fields_ = [("Size", ctypes.c_uint32), ("Version", ctypes.c_uint8),
                *((name, _Item) for name in (
                    "timeStamp", "gpuEnergyCounter", "gpuVoltage", "gpuCurrentClockFrequency",
                    "gpuCurrentTemperature", "globalActivityCounter",
                    "renderComputeActivityCounter", "mediaActivityCounter")),
                *((f"gpu{flag}", ctypes.c_bool) for flag in _FLAGS),
                *((name, _Item) for name in (
                    "vramEnergyCounter", "vramVoltage", "vramCurrentClockFrequency",
                    "vramCurrentEffectiveFrequency", "vramReadBandwidthCounter",
                    "vramWriteBandwidthCounter", "vramCurrentTemperature")),
                *((f"vram{flag}", ctypes.c_bool) for flag in _FLAGS),
                ("totalCardEnergyCounter", _Item), ("psu", _Psu * _PSU_COUNT),
                ("fanSpeed", _Item * _FAN_COUNT),
                *((name, _Item) for name in (
                    "gpuVrTemp", "vramVrTemp", "saVrTemp", "gpuEffectiveClock",
                    "gpuOverVoltagePercent", "gpuPowerPercent", "gpuTemperaturePercent",
                    "vramReadBandwidth", "vramWriteBandwidth"))]


def item_value(item: _Item) -> float | None:
    """One telemetry item as a number, or None where the card does not report it."""
    if not item.bSupported or item.type >= len(_VALUE_FIELDS):
        frame_log.debug("item_value: unsupported (type %d)", item.type)
        return None
    value = float(getattr(item.value, _VALUE_FIELDS[item.type]))
    frame_log.debug("item_value: %s", value)
    return value


def sample(telemetry: _Telemetry) -> dict[str, float]:
    """The raw numbers in one telemetry read -- counters still as counters."""
    raw = {
        "time": item_value(telemetry.timeStamp),
        "energy": item_value(telemetry.totalCardEnergyCounter)
        if telemetry.totalCardEnergyCounter.bSupported
        else item_value(telemetry.gpuEnergyCounter),
        "activity": item_value(telemetry.globalActivityCounter),
        "temp": item_value(telemetry.gpuCurrentTemperature),
        "mem_temp": item_value(telemetry.vramCurrentTemperature),
        "clock": item_value(telemetry.gpuCurrentClockFrequency),
    }
    fan = telemetry.fanSpeed[0]
    if fan.units in (_UNITS_RPM, _UNITS_PERCENT):
        raw["fan_rpm" if fan.units == _UNITS_RPM else "fan"] = item_value(fan)
    found = {k: v for k, v in raw.items() if v is not None}
    frame_log.debug("sample: %s", found)
    return found


def readings(before: dict[str, float], now: dict[str, float]) -> dict[str, float]:
    """TRCC's GPU quantities from two samples: the direct ones from *now*,
    power and usage from the counters' change since *before*."""
    found = {k: now[k] for k in ("temp", "mem_temp", "clock", "fan_rpm", "fan") if k in now}
    elapsed = now.get("time", 0.0) - before.get("time", now.get("time", 0.0))
    if elapsed > 0:
        for quantity, counter, scale in (("power", "energy", 1.0), ("usage", "activity", 100.0)):
            if counter in now and counter in before and now[counter] >= before[counter]:
                found[quantity] = (now[counter] - before[counter]) / elapsed * scale
        if "usage" in found:
            found["usage"] = min(found["usage"], 100.0)
    frame_log.debug("readings: %s over %.3f s", found, elapsed)
    return found


class _Igcl:
    """ControlLib.dll -- the only part that needs Windows and an Intel driver."""

    def __init__(self) -> None:
        log.debug("_Igcl: loading ControlLib.dll from System32")
        dll = ctypes.CDLL("ControlLib.dll", winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
        self._enumerate = dll.ctlEnumerateDevices
        self._properties = dll.ctlGetDeviceProperties
        self._telemetry = dll.ctlPowerTelemetryGet
        init = dll.ctlInit
        for fn in (init, self._enumerate, self._properties, self._telemetry):
            fn.restype = ctypes.c_uint32
        args = _InitArgs(Size=ctypes.sizeof(_InitArgs), Version=0, AppVersion=_APP_VERSION,
                         flags=_INIT_USE_LEVEL_ZERO)
        self._api = ctypes.c_void_p()
        if (status := init(ctypes.byref(args), ctypes.byref(self._api))) != _CTL_OK:
            raise OSError(f"ctlInit failed: 0x{status:08X}")
        log.info("_Igcl: ControlLib version 0x%08X", args.SupportedVersion)

    def devices(self) -> list[int]:
        """Every device adapter handle IGCL knows."""
        count = ctypes.c_uint32(0)
        if self._enumerate(self._api, ctypes.byref(count), None) != _CTL_OK or not count.value:
            log.info("_Igcl.devices: none")
            return []
        handles = (ctypes.c_void_p * count.value)()
        if self._enumerate(self._api, ctypes.byref(count), handles) != _CTL_OK:
            log.warning("_Igcl.devices: ctlEnumerateDevices failed")
            return []
        log.debug("_Igcl.devices: %d", count.value)
        return [h for h in handles[:count.value] if h]

    def properties(self, handle: int) -> _Properties | None:
        luid = (ctypes.c_uint8 * 8)()
        props = _Properties(Size=ctypes.sizeof(_Properties), Version=_PROPERTIES_VERSION,
                            pDeviceID=ctypes.cast(luid, ctypes.c_void_p), device_id_size=8)
        status = self._properties(ctypes.c_void_p(handle), ctypes.byref(props))
        log.debug("_Igcl.properties: device %#x -> 0x%08X", handle, status)
        return props if status == _CTL_OK else None

    def telemetry(self, handle: int) -> _Telemetry | None:
        data = _Telemetry(Size=ctypes.sizeof(_Telemetry), Version=0)
        status = self._telemetry(ctypes.c_void_p(handle), ctypes.byref(data))
        frame_log.debug("_Igcl.telemetry: device %#x -> 0x%08X", handle, status)
        return data if status == _CTL_OK else None


class IgclGpu(GpuSource):
    """One Intel graphics device, read through IGCL's power telemetry."""

    def __init__(self, igcl: Any, handle: int, ordinal: int, name: str, discrete: bool,
                 clock: Callable[[], float] = time.monotonic, *,
                 supported: frozenset[str] | None = None) -> None:
        log.info("IgclGpu: intel:%d %s (%s) reads %s", ordinal, name,
                 "discrete" if discrete else "integrated",
                 sorted(supported) if supported is not None else "?")
        #: What THIS card's telemetry reported at discovery; None: unknown.
        self._supported = supported
        self._igcl, self._handle, self._ordinal, self._name = igcl, handle, ordinal, name
        self._discrete, self._clock = discrete, clock
        self._lock = threading.Lock()
        self._at = -1e9
        self._last: dict[str, float] = {}
        self._values: dict[str, float] = {}

    @property
    def key(self) -> str:
        frame_log.debug("IgclGpu.key")
        return f"intel:{self._ordinal}"

    @property
    def name(self) -> str:
        frame_log.debug("IgclGpu.name")
        return self._name

    @property
    def is_discrete(self) -> bool:
        frame_log.debug("IgclGpu.is_discrete")
        return self._discrete

    def _read(self, quantity: str) -> float | None:
        with self._lock:
            now = self._clock()
            if now - self._at >= _SAMPLE_TTL_S:
                self._at = now
                data = self._igcl.telemetry(self._handle)
                current = sample(data) if data is not None else {}
                self._values = readings(self._last, current)
                self._last = current
            value = self._values.get(quantity)
        frame_log.debug("IgclGpu._read: %s %s -> %s", self.key, quantity, value)
        return value

    def provides(self, quantity: str) -> bool:
        """For THIS card: what its first telemetry read carried (power and usage
        need their counter AND the timestamp).  The class answer offered every
        quantity on every Intel GPU."""
        if self._supported is not None and quantity in _TELEMETRY_QUANTITIES:
            answer = quantity in self._supported
        else:
            answer = super().provides(quantity)
        log.debug("IgclGpu.provides: %s %s -> %s", self.key, quantity, answer)
        return answer

    def temp(self) -> float | None:
        frame_log.debug("IgclGpu.temp: %s", self.key)
        return self._read("temp")

    def mem_temp(self) -> float | None:
        frame_log.debug("IgclGpu.mem_temp: %s", self.key)
        return self._read("mem_temp")

    def usage(self) -> float | None:
        frame_log.debug("IgclGpu.usage: %s", self.key)
        return self._read("usage")

    def clock(self) -> float | None:
        frame_log.debug("IgclGpu.clock: %s", self.key)
        return self._read("clock")

    def power(self) -> float | None:
        frame_log.debug("IgclGpu.power: %s", self.key)
        return self._read("power")

    def fan(self) -> float | None:
        frame_log.debug("IgclGpu.fan: %s", self.key)
        return self._read("fan")

    def fan_rpm(self) -> float | None:
        frame_log.debug("IgclGpu.fan_rpm: %s", self.key)
        return self._read("fan_rpm")


#: TRCC's GPU quantity -> the raw sample fields it needs.
_TELEMETRY_QUANTITIES: dict[str, tuple[str, ...]] = {
    "temp": ("temp",), "mem_temp": ("mem_temp",), "clock": ("clock",),
    "fan_rpm": ("fan_rpm",), "fan": ("fan",),
    "power": ("energy", "time"), "usage": ("activity", "time"),
}


def telemetry_supports(raw: dict[str, float]) -> frozenset[str]:
    """The quantities one :func:`sample` can feed."""
    supported = frozenset(q for q, needs in _TELEMETRY_QUANTITIES.items()
                          if all(k in raw for k in needs))
    log.debug("telemetry_supports: %s", sorted(supported))
    return supported


def graphics_devices(igcl: Any) -> list[tuple[int, _Properties]]:
    """Every Intel graphics device as (handle, properties), by PCI address."""
    found = []
    for handle in igcl.devices():
        props = igcl.properties(handle)
        if props is None or props.pci_vendor_id != _INTEL \
                or props.device_type != _DEVICE_TYPE_GRAPHICS:
            log.debug("graphics_devices: device %#x is not an Intel GPU -- skipped", handle)
            continue
        found.append((handle, props))
    found.sort(key=_bdf)
    log.debug("graphics_devices: %d Intel GPU(s)", len(found))
    return found


def _bdf(device: tuple[int, _Properties]) -> tuple[int, int, int]:
    bdf = device[1].adapter_bdf
    frame_log.debug("_bdf: %d:%d.%d", bdf.bus, bdf.device, bdf.function)
    return bdf.bus, bdf.device, bdf.function


def discover_igcl_gpus(igcl_factory: Callable[[], Any] = _Igcl) -> list[GpuSource]:
    """One :class:`IgclGpu` per Intel GPU whose telemetry answers; [] without
    an Intel driver."""
    log.info("discover_igcl_gpus: called")
    try:
        igcl = igcl_factory()
    except (OSError, AttributeError) as e:
        log.info("discover_igcl_gpus: no Intel driver library (%s)", e)
        return []
    gpus: list[GpuSource] = []
    for ordinal, (handle, props) in enumerate(graphics_devices(igcl)):
        name = props.name.decode(errors="replace").strip()
        data = igcl.telemetry(handle)
        if data is None or not (raw := sample(data)):
            log.warning("discover_igcl_gpus: %s reports no telemetry through IGCL -- skipped",
                        name)
            continue
        gpus.append(IgclGpu(igcl, handle, ordinal, name,
                            discrete=not props.graphics_adapter_properties & _ADAPTER_INTEGRATED,
                            supported=telemetry_supports(raw)))
    log.info("discover_igcl_gpus: %d Intel GPU(s)", len(gpus))
    return gpus
