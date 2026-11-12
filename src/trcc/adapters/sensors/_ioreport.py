"""Apple Silicon power and GPU residency via IOReport. No root.

``powermetrics`` reads the same counters, and it refuses to run unless
the process is root (or a root helper is installed). The counters
themselves are not privileged. ``libIOReport.dylib`` is the library
``powermetrics`` calls, and a normal user can call it too. That is the
path macmon and actop use.

What this source publishes, measured on an M1 Ultra running macOS 26
without root:

* CPU package power — every Energy Model channel whose name ends with
  ``CPU Energy`` (``CPU Energy`` on one die, ``DIE_0_CPU Energy`` plus
  ``DIE_1_CPU Energy`` on Ultra). Cluster rows (``EACC_CPU``,
  ``PACC0_CPU``) are the same joules broken apart, so they are not added.
* GPU power — the channel named exactly ``GPU Energy``. Its unit is
  ``nJ`` on M1 Ultra; CPU package channels are ``mJ``. The unit label
  is what the conversion trusts.
* GPU active percent — residency of the ``GPUPH`` performance-state
  histogram. The ``OFF`` state is idle.
* GPU clock — the same histogram, weighted by the ``pmgr`` DVFS
  table (``voltage-states9``). On this M1 Ultra that table is
  388.8, 486, 648, 777.6, 972 and 1296 MHz, and ``P1`` is the first
  of those. A state the table cannot name withholds the number.
  ``powermetrics`` remains the fallback when the table is absent.

A reading needs two samples. The first call stores a baseline and
returns nothing; the next call, one metrics tick later, divides the
counter delta by the elapsed time.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from ...core.logs import per_frame
from ...core.ports import CpuSource, GpuSource

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


_ENERGY_GROUP = "Energy Model"
_GPU_GROUP = "GPU Stats"
_GPU_STATE_GROUP = "GPU Performance States"
_GPU_STATE_CHANNEL = "GPUPH"
_FORMAT_SIMPLE = 1
_FORMAT_HISTOGRAM = 2
_MAX_STATES = 64
_MIN_DT_S = 0.05
_MAX_WATTS = 500.0
_JOULES = {"nJ": 1e-9, "uJ": 1e-6, "mJ": 1e-3, "J": 1.0}
_kCFStringEncodingUTF8 = 0x0800_0100
# ``voltage-states9`` is pairs of (Hz, mV) on M1–M3 and (kHz, mV) on
# M4 and later.  The two encodings do not overlap: a real clock in Hz
# is at least tens of millions, and the same clock in kHz is at most
# a few million.
_HZ_THRESHOLD = 50_000_000
_MIN_GPU_MHZ = 20.0
_MAX_GPU_MHZ = 4_000.0
_IDLE_STATES = frozenset({"OFF", "IDLE", "DOWN"})
# Awake time spent in a P-state the DVFS table does not list.  Above
# this the number would be missing the state the GPU actually used.
_UNMAPPED_FRACTION = 0.01
_PMGR_DVFS_KEY = "voltage-states9"
_clock_withheld: str | None = None


def joules_to_watts(delta: int, unit: str, dt: float) -> float | None:
    """Counter delta in *unit* over *dt* seconds, as watts.

    A negative delta is a counter reset and is dropped. A result above
    ``_MAX_WATTS`` is a unit mix-up (the histogram channels on this
    group are not energy) and is dropped too.
    """
    log.debug("joules_to_watts: delta=%s unit=%s dt=%s", delta, unit, dt)
    scale = _JOULES.get(unit)
    if scale is None or dt < _MIN_DT_S or delta < 0:
        return None
    watts = (delta * scale) / dt
    if watts > _MAX_WATTS:
        return None
    return watts


def split_power(
    rows: list[tuple[str, str, int]], dt: float,
) -> tuple[float | None, float | None]:
    """``(cpu_watts, gpu_watts)`` from Energy Model deltas.

    *rows* is ``(name, unit, delta)``. Only the package channels count.
    """
    log.debug("split_power: rows=%d dt=%s", len(rows), dt)
    cpu = 0.0
    gpu = 0.0
    cpu_seen = False
    gpu_seen = False
    for name, unit, delta in rows:
        if name.endswith("CPU Energy"):
            watts = joules_to_watts(delta, unit, dt)
            if watts is None:
                continue
            cpu += watts
            cpu_seen = True
        elif name == "GPU Energy":
            watts = joules_to_watts(delta, unit, dt)
            if watts is None:
                continue
            gpu += watts
            gpu_seen = True
    return (cpu if cpu_seen else None, gpu if gpu_seen else None)


def active_percent(states: list[tuple[str, int]]) -> float | None:
    """Percent of residency spent outside the ``OFF`` state.

    *states* is ``(name, residency_delta)``. A zero total means the
    histogram did not move, which is "no sample", not "0% busy".
    """
    log.debug("active_percent: states=%s", states)
    total = 0
    idle = 0
    for name, residency in states:
        if residency <= 0:
            continue
        total += residency
        if name.upper() == "OFF":
            idle += residency
    if total <= 0:
        return None
    return 100.0 * (total - idle) / total


def dvfs_table_mhz(blob: bytes) -> tuple[float, ...]:
    """Non-zero clocks in *blob*, in P-state order, as MHz.

    *blob* is ``voltage-states9``: little-endian pairs of frequency
    and millivolts.  The leading pair is 0 Hz (the ``OFF`` state) and
    is dropped.  ``P1`` is the first value returned.  A decoded clock
    outside the range of an Apple GPU is a unit mix-up, and the whole
    table is dropped rather than shown.
    """
    log.debug("dvfs_table_mhz: bytes=%d", len(blob))
    if len(blob) < 8 or len(blob) % 8:
        return ()
    hertz = [
        int.from_bytes(blob[i:i + 4], "little")
        for i in range(0, len(blob), 8)
    ]
    present = [hz for hz in hertz if hz > 0]
    if not present:
        return ()
    scale = 1_000_000.0 if max(present) >= _HZ_THRESHOLD else 1_000.0
    table = tuple(hz / scale for hz in present)
    if any(not _MIN_GPU_MHZ <= mhz <= _MAX_GPU_MHZ for mhz in table):
        log.debug("dvfs_table_mhz: rejected %s", table)
        return ()
    log.debug("dvfs_table_mhz: %s", table)
    return table


def _warn_clock_once(reason: str) -> None:
    """One WARNING for a clock this process cannot name."""
    global _clock_withheld
    log.debug("_warn_clock_once: %s", reason)
    if _clock_withheld == reason:
        return
    _clock_withheld = reason
    log.warning("GPU clock withheld: %s", reason)


def effective_mhz(
    states: list[tuple[str, int]], table: tuple[float, ...],
) -> float | None:
    """Residency-weighted MHz of the awake P-states.

    ``table[n - 1]`` is the frequency of ``Pn``.  ``OFF``, ``IDLE`` and
    ``DOWN`` are not clocked: a sample that only sat in those is 0 MHz,
    and a sample whose counters did not move is no sample.  More than
    1% of the awake residency in a state the table cannot name
    withholds the figure.
    """
    log.debug("effective_mhz: states=%d table=%d", len(states), len(table))
    if not table:
        return None
    awake = 0
    named = 0
    unmapped = 0
    weighted = 0.0
    for name, delta in states:
        if delta <= 0 or name.upper() in _IDLE_STATES:
            continue
        awake += delta
        index = int(name[1:]) - 1 if (
            len(name) > 1 and name[0] in "Pp" and name[1:].isdigit()
        ) else -1
        if 0 <= index < len(table):
            weighted += delta * table[index]
            named += delta
        else:
            unmapped += delta
    if awake <= 0:
        moved = any(delta > 0 for _name, delta in states)
        log.debug("effective_mhz: idle moved=%s", moved)
        return 0.0 if moved else None
    if unmapped / awake > _UNMAPPED_FRACTION:
        _warn_clock_once(
            f"{unmapped / awake:.1%} of awake residency is in a P-state "
            f"the DVFS table ({len(table)} entries) cannot name",
        )
        return None
    if named <= 0:
        return None
    mhz = weighted / named
    log.debug("effective_mhz: %.1f MHz", mhz)
    return mhz


def _pmgr_property(name: str) -> bytes:
    """The raw ``pmgr`` property *name*, or empty when it is not there.

    The node is world-readable.  This does not open a user client and
    does not need root.
    """
    log.debug("_pmgr_property: %s", name)
    if sys.platform != "darwin":
        return b""
    iokit_path = ctypes.util.find_library("IOKit")
    if not iokit_path:
        log.info("_pmgr_property: IOKit not found")
        return b""
    try:
        iokit = ctypes.CDLL(iokit_path)
        cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        )
    except OSError as exc:
        log.warning("_pmgr_property: library did not load (%s)", exc)
        return b""
    void = ctypes.c_void_p
    iokit.IOServiceNameMatching.argtypes = [ctypes.c_char_p]
    iokit.IOServiceNameMatching.restype = void
    iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, void]
    iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
    iokit.IORegistryEntryCreateCFProperty.argtypes = [
        ctypes.c_uint, void, void, ctypes.c_uint32,
    ]
    iokit.IORegistryEntryCreateCFProperty.restype = void
    iokit.IOObjectRelease.argtypes = [ctypes.c_uint]
    iokit.IOObjectRelease.restype = ctypes.c_int
    cf.CFStringCreateWithCString.argtypes = [
        void, ctypes.c_char_p, ctypes.c_uint32,
    ]
    cf.CFStringCreateWithCString.restype = void
    cf.CFRelease.argtypes = [void]
    cf.CFGetTypeID.argtypes = [void]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFDataGetTypeID.argtypes = []
    cf.CFDataGetTypeID.restype = ctypes.c_ulong
    cf.CFDataGetLength.argtypes = [void]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetBytePtr.argtypes = [void]
    cf.CFDataGetBytePtr.restype = void

    matching = iokit.IOServiceNameMatching(b"pmgr")
    if not matching:
        log.info("_pmgr_property: no pmgr matching dictionary")
        return b""
    # IOServiceGetMatchingService consumes the matching dictionary.
    service = int(iokit.IOServiceGetMatchingService(0, matching))
    if not service:
        log.info("_pmgr_property: pmgr service absent")
        return b""
    key = cf.CFStringCreateWithCString(
        None, name.encode("utf-8"), _kCFStringEncodingUTF8,
    )
    if not key:
        iokit.IOObjectRelease(service)
        log.info("_pmgr_property: could not name %s", name)
        return b""
    data = iokit.IORegistryEntryCreateCFProperty(service, key, None, 0)
    cf.CFRelease(key)
    iokit.IOObjectRelease(service)
    if not data:
        log.info("_pmgr_property: %s absent", name)
        return b""
    try:
        if int(cf.CFGetTypeID(data)) != int(cf.CFDataGetTypeID()):
            log.info("_pmgr_property: %s is not CFData", name)
            return b""
        length = int(cf.CFDataGetLength(data))
        ptr = cf.CFDataGetBytePtr(data)
        if length <= 0 or not ptr:
            return b""
        return ctypes.string_at(ptr, length)
    finally:
        cf.CFRelease(data)


def read_gpu_dvfs_mhz() -> tuple[float, ...]:
    """This Mac's GPU clocks from ``voltage-states9``, or empty."""
    log.info("read_gpu_dvfs_mhz")
    blob = _pmgr_property(_PMGR_DVFS_KEY)
    table = dvfs_table_mhz(blob)
    if table:
        log.info(
            "GPU DVFS table: %s MHz",
            ", ".join(f"{mhz:.1f}" for mhz in table),
        )
    else:
        log.info(
            "GPU DVFS table absent — clock stays with powermetrics",
        )
    return table


def _keep_energy(name: str) -> bool:
    frame_log.debug("_keep_energy: name=%s", name)
    return name.endswith("CPU Energy") or name == "GPU Energy"


@dataclass(frozen=True)
class _RawSample:
    """One IOReport snapshot, reduced to the channels this source uses."""

    t: float
    energy: tuple[tuple[str, str, int], ...]
    gpu_states: tuple[tuple[str, int], ...]


def derive(
    prev: _RawSample, cur: _RawSample,
    *,
    gpu_mhz: tuple[float, ...] = (),
) -> dict[str, float]:
    """Power, GPU residency and, when *gpu_mhz* is set, GPU clock."""
    log.debug("derive: dt=%s gpu_table=%d", cur.t - prev.t, len(gpu_mhz))
    dt = cur.t - prev.t
    previous = {name: value for name, _unit, value in prev.energy}
    rows: list[tuple[str, str, int]] = []
    for name, unit, value in cur.energy:
        if name in previous:
            rows.append((name, unit, value - previous[name]))
    cpu, gpu = split_power(rows, dt)
    out: dict[str, float] = {}
    if cpu is not None:
        out["cpu_power"] = cpu
    if gpu is not None:
        out["gpu_power"] = gpu
    if len(cur.gpu_states) == len(prev.gpu_states) and cur.gpu_states:
        deltas = [
            (name, cur_res - prev_res)
            for (name, prev_res), (_name, cur_res)
            in zip(prev.gpu_states, cur.gpu_states, strict=True)
        ]
        usage = active_percent(deltas)
        if usage is not None:
            out["gpu_usage"] = usage
        if gpu_mhz:
            clock = effective_mhz(deltas, gpu_mhz)
            if clock is not None:
                out["gpu_clock"] = clock
    return out


# ── Darwin binding ───────────────────────────────────────────────────


class _IOReportLib:
    """ctypes handles. Absent off macOS, or when the dylib will not load."""

    def __init__(self) -> None:
        log.debug("_IOReportLib.__init__")
        self.ready = False
        self._cf: ctypes.CDLL | None = None
        self._io: ctypes.CDLL | None = None
        self._bind()

    def _bind(self) -> None:
        log.debug("_bind")
        if sys.platform != "darwin":
            log.debug("IOReport: not darwin — source will return None")
            return
        try:
            cf = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
            )
            io = ctypes.CDLL("libIOReport.dylib")
        except OSError as exc:
            log.warning("IOReport: library did not load (%s)", exc)
            return
        void = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            void, ctypes.c_char_p, ctypes.c_uint32,
        ]
        cf.CFStringCreateWithCString.restype = void
        cf.CFRelease.argtypes = [void]
        cf.CFDictionaryCreateMutableCopy.argtypes = [
            void, ctypes.c_long, void,
        ]
        cf.CFDictionaryCreateMutableCopy.restype = void
        cf.CFDictionaryGetValue.argtypes = [void, void]
        cf.CFDictionaryGetValue.restype = void
        cf.CFArrayGetCount.argtypes = [void]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [void, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = void
        cf.CFStringGetCString.argtypes = [
            void, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32,
        ]
        cf.CFStringGetCString.restype = ctypes.c_bool
        io.IOReportCopyChannelsInGroup.argtypes = [
            void, void, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64,
        ]
        io.IOReportCopyChannelsInGroup.restype = void
        io.IOReportCreateSubscription.argtypes = [
            void, void, ctypes.POINTER(void), ctypes.c_uint64, void,
        ]
        io.IOReportCreateSubscription.restype = void
        io.IOReportCreateSamples.argtypes = [void, void, void]
        io.IOReportCreateSamples.restype = void
        io.IOReportSimpleGetIntegerValue.argtypes = [void, void]
        io.IOReportSimpleGetIntegerValue.restype = ctypes.c_int64
        io.IOReportChannelGetChannelName.argtypes = [void]
        io.IOReportChannelGetChannelName.restype = void
        io.IOReportChannelGetSubGroup.argtypes = [void]
        io.IOReportChannelGetSubGroup.restype = void
        io.IOReportChannelGetUnitLabel.argtypes = [void]
        io.IOReportChannelGetUnitLabel.restype = void
        io.IOReportChannelGetFormat.argtypes = [void]
        io.IOReportChannelGetFormat.restype = ctypes.c_int
        io.IOReportStateGetCount.argtypes = [void]
        io.IOReportStateGetCount.restype = ctypes.c_uint32
        io.IOReportStateGetResidency.argtypes = [void, ctypes.c_uint32]
        io.IOReportStateGetResidency.restype = ctypes.c_uint64
        io.IOReportStateGetNameForIndex.argtypes = [void, ctypes.c_uint32]
        io.IOReportStateGetNameForIndex.restype = void
        self._cf = cf
        self._io = io
        self.ready = True
        log.info("IOReport: libIOReport.dylib bound")

    def _cfstr(self, text: str) -> ctypes.c_void_p | None:
        log.debug("_cfstr: text=%s", text)
        assert self._cf is not None
        return self._cf.CFStringCreateWithCString(
            None, text.encode("utf-8"), _kCFStringEncodingUTF8,
        )

    def _cstr(self, ref: ctypes.c_void_p | None) -> str:
        frame_log.debug("_cstr")
        assert self._cf is not None
        if not ref:
            return ""
        buf = ctypes.create_string_buffer(256)
        if self._cf.CFStringGetCString(
            ref, buf, len(buf), _kCFStringEncodingUTF8,
        ):
            return buf.value.decode("utf-8", errors="replace")
        return ""

    def subscribe(self, group: str) -> tuple[ctypes.c_void_p, ctypes.c_void_p] | None:
        """A subscription for *group*, or None when the group is absent."""
        log.info("IOReport.subscribe: group=%s", group)
        assert self._cf is not None and self._io is not None
        label = self._cfstr(group)
        if not label:
            return None
        try:
            channels = self._io.IOReportCopyChannelsInGroup(
                label, None, 0, 0, 0,
            )
        finally:
            self._cf.CFRelease(label)
        if not channels:
            log.info("IOReport.subscribe: group %s has no channels", group)
            return None
        mutable = self._cf.CFDictionaryCreateMutableCopy(None, 0, channels)
        self._cf.CFRelease(channels)
        if not mutable:
            return None
        subbed = ctypes.c_void_p()
        sub = self._io.IOReportCreateSubscription(
            None, mutable, ctypes.byref(subbed), 0, None,
        )
        self._cf.CFRelease(mutable)
        if not sub or not subbed.value:
            log.info("IOReport.subscribe: group %s did not subscribe", group)
            return None
        return sub, subbed

    def sample_group(
        self, sub: ctypes.c_void_p, subbed: ctypes.c_void_p,
    ) -> tuple[list[tuple[str, str, int]], list[tuple[str, int]]]:
        """``(energy rows, GPUPH states)`` from one subscription."""
        log.debug("sample_group")
        assert self._cf is not None and self._io is not None
        sample = self._io.IOReportCreateSamples(sub, subbed, None)
        if not sample:
            return [], []
        try:
            return self._walk(sample)
        finally:
            self._cf.CFRelease(sample)

    def _walk(
        self, sample: ctypes.c_void_p,
    ) -> tuple[list[tuple[str, str, int]], list[tuple[str, int]]]:
        log.debug("_walk")
        assert self._cf is not None and self._io is not None
        key = self._cfstr("IOReportChannels")
        if not key:
            return [], []
        try:
            array = self._cf.CFDictionaryGetValue(sample, key)
        finally:
            self._cf.CFRelease(key)
        if not array:
            return [], []
        energy: list[tuple[str, str, int]] = []
        states: list[tuple[str, int]] = []
        count = int(self._cf.CFArrayGetCount(array))
        for index in range(count):
            channel = self._cf.CFArrayGetValueAtIndex(array, index)
            if not channel:
                continue
            name = self._cstr(self._io.IOReportChannelGetChannelName(channel))
            fmt = int(self._io.IOReportChannelGetFormat(channel))
            if fmt == _FORMAT_SIMPLE and _keep_energy(name):
                unit = self._cstr(self._io.IOReportChannelGetUnitLabel(channel))
                value = int(self._io.IOReportSimpleGetIntegerValue(channel, None))
                energy.append((name, unit, value))
                continue
            subgroup = self._cstr(self._io.IOReportChannelGetSubGroup(channel))
            if (
                fmt == _FORMAT_HISTOGRAM
                and subgroup == _GPU_STATE_GROUP
                and name == _GPU_STATE_CHANNEL
            ):
                states = self._states(channel)
        return energy, states

    def _states(self, channel: ctypes.c_void_p) -> list[tuple[str, int]]:
        log.debug("_states")
        assert self._io is not None
        count = int(self._io.IOReportStateGetCount(channel))
        if not 0 < count <= _MAX_STATES:
            log.debug("IOReport: GPUPH state count %s ignored", count)
            return []
        out: list[tuple[str, int]] = []
        for index in range(count):
            name = self._cstr(self._io.IOReportStateGetNameForIndex(channel, index))
            residency = int(self._io.IOReportStateGetResidency(channel, index))
            out.append((name, residency))
        return out


class _Engine:
    """One subscription pair for the process, plus the previous sample."""

    def __init__(self, lib: _IOReportLib | None = None) -> None:
        log.debug("_Engine.__init__")
        self._lib = lib if lib is not None else _IOReportLib()
        self._energy: tuple[ctypes.c_void_p, ctypes.c_void_p] | None = None
        self._gpu: tuple[ctypes.c_void_p, ctypes.c_void_p] | None = None
        self._prev: _RawSample | None = None
        self._announced = False
        self._gpu_mhz: tuple[float, ...] = ()
        if self._lib.ready:
            self._energy = self._lib.subscribe(_ENERGY_GROUP)
            self._gpu = self._lib.subscribe(_GPU_GROUP)
            self._gpu_mhz = read_gpu_dvfs_mhz()
            log.info(
                "IOReport: energy=%s gpu_stats=%s gpu_clocks=%d",
                self._energy is not None, self._gpu is not None,
                len(self._gpu_mhz),
            )

    def read(self) -> dict[str, float]:
        """The latest delta, or empty when this is the baseline sample."""
        log.debug("_Engine.read")
        if not self._lib.ready or (self._energy is None and self._gpu is None):
            return {}
        try:
            current = self._take()
        except Exception as exc:
            log.warning("IOReport sample failed: %s", exc)
            return {}
        previous = self._prev
        self._prev = current
        if previous is None:
            log.info("IOReport: baseline stored — power and GPU usage on the next sample")
            return {}
        values = derive(previous, current, gpu_mhz=self._gpu_mhz)
        if values and not self._announced:
            self._announced = True
            log.info(
                "IOReport: unprivileged cpu_power=%s gpu_power=%s "
                "gpu_usage=%s gpu_clock=%s",
                values.get("cpu_power"), values.get("gpu_power"),
                values.get("gpu_usage"), values.get("gpu_clock"),
            )
        return values

    def _take(self) -> _RawSample:
        log.debug("_take")
        assert self._lib is not None
        energy: list[tuple[str, str, int]] = []
        states: list[tuple[str, int]] = []
        if self._energy is not None:
            got_energy, _got_states = self._lib.sample_group(*self._energy)
            energy.extend(got_energy)
        if self._gpu is not None:
            _got_energy, got_states = self._lib.sample_group(*self._gpu)
            energy.extend(_got_energy)
            states = got_states
        return _RawSample(time.monotonic(), tuple(energy), tuple(states))


_ENGINE: _Engine | None = None


def _live_read() -> dict[str, float]:
    """Process-wide engine. Built on the first metrics tick."""
    log.debug("_live_read")
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = _Engine()
    return _ENGINE.read()


class _IOReportSnapshot:
    """TTL cache shared by the CPU and GPU sources.

    *reader* is the test seam: production calls :func:`_live_read`.
    """

    def __init__(
        self,
        *,
        reader: Callable[[], dict[str, float]] | None = None,
        ttl_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        log.debug("_IOReportSnapshot.__init__: ttl=%s", ttl_seconds)
        self._read = reader if reader is not None else _live_read
        self._ttl = ttl_seconds
        self._clock = clock
        self._last_at = -1.0
        self._values: dict[str, float] = {}

    def get(self, key: str) -> float | None:
        frame_log.debug("get: key=%s", key)
        self._refresh_if_stale()
        return self._values.get(key)

    def _refresh_if_stale(self) -> None:
        log.debug("_refresh_if_stale")
        now = self._clock()
        if self._last_at >= 0 and (now - self._last_at) < self._ttl:
            return
        self._values = self._read()
        self._last_at = now


class IOReportCpu(CpuSource):
    """CPU package power. Temperature, usage and frequency stay elsewhere."""

    def __init__(self, snapshot: _IOReportSnapshot) -> None:
        log.debug("IOReportCpu.__init__")
        self._snap = snapshot

    @property
    def name(self) -> str:
        frame_log.debug("name")
        return "IOReport (CPU)"

    def power(self) -> float | None:
        frame_log.debug("power")
        return self._snap.get("cpu_power")


class IOReportGpu(GpuSource):
    """Integrated GPU power, residency and clock. Same ``apple:0`` key."""

    def __init__(
        self, snapshot: _IOReportSnapshot, *, model_name: str = "Apple GPU",
    ) -> None:
        log.debug("IOReportGpu.__init__: model_name=%s", model_name)
        self._snap = snapshot
        self._name = model_name

    @property
    def key(self) -> str:
        frame_log.debug("key")
        return "apple:0"

    @property
    def name(self) -> str:
        frame_log.debug("name")
        return self._name

    @property
    def is_discrete(self) -> bool:
        frame_log.debug("is_discrete")
        return False

    def usage(self) -> float | None:
        frame_log.debug("usage")
        return self._snap.get("gpu_usage")

    def power(self) -> float | None:
        frame_log.debug("power")
        return self._snap.get("gpu_power")

    def clock(self) -> float | None:
        frame_log.debug("clock")
        return self._snap.get("gpu_clock")
