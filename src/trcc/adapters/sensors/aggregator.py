"""SensorEnumerator aggregators — compose sources into the flat dict view.

Takes CpuSource + MemorySource + GpuSource[] + FanSource[] and produces
the normalized keys overlays use:

    cpu:temp | cpu:usage | cpu:freq | cpu:power
    gpu:primary:temp | gpu:0:temp | gpu:nvidia:0:temp | gpu:amd:0:temp
    memory:used | memory:available | memory:total | memory:percent
    fan:<key>:rpm | fan:<key>:percent | fan:{cpu,gpu,ssd,sys2}
    disk:temp | disk:read | disk:write | disk:activity
    net:up | net:down | net:total_up | net:total_down
    time:{hour,minute,second} | date:{year,month,day,dow}

`BaselineSensors` — psutil + nvml only, no OS-native thermals.  Used as
a fallback on any OS before its native sensor sources are ported.

`LinuxSensors` — adds hwmon + DRM sensors on top of the baseline.  Lands
immediately once hwmon.py is wired.
"""
from __future__ import annotations

import datetime
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext

from ...core.logs import per_frame
from ...core.models import (
    DEFAULT_REFRESH_INTERVAL_S,
    SensorReading,
)
from ...core.ports import (
    BoardTempSource,
    CpuSource,
    DiskSource,
    DramSource,
    FanSource,
    GpuSource,
    MemorySource,
    QuantitySource,
    SensorEnumerator,
)
from .hwmon import (
    HwmonCpu,
    MemoryClock,
    discover_amd_gpus,
    discover_disk_temp,
    discover_dram_temp,
    discover_fans,
    discover_intel_gpus,
    discover_nouveau_gpus,
    find_cpu_temp_device,
    scan_hwmon_devices,
)
from .nvml import discover_nvidia_gpus
from .psutil_sources import (
    ComputedIo,
    PsutilCpu,
    PsutilMemory,
    discover_board_temps,
)

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


# Vendor priority when GPUs tie on discreteness.  An NVML-reported NVIDIA is
# ALWAYS genuinely discrete (NVIDIA ships no consumer iGPUs), so it must win
# over an AMD/Intel APU that only *looks* discrete via a large UMA framebuffer
# — otherwise "amd:0" beats "nvidia:0" on the alphabetical tiebreak and the
# integrated GPU becomes primary over the real card (#157: an RTX 5090 lost to
# a Raphael iGPU with a big UMA allocation).
_GPU_VENDOR_RANK = {"nvidia": 0, "amd": 1, "intel": 2}


def _gpu_order(gpu: GpuSource) -> tuple[bool, int, str]:
    """Sort key: discrete first, then nvidia > amd > intel, then key."""
    log.debug("_gpu_order: gpu=%s", gpu)
    vendor = gpu.key.split(":", 1)[0]
    return (not gpu.is_discrete, _GPU_VENDOR_RANK.get(vendor, 9), gpu.key)


# ── Key mapping helpers ──────────────────────────────────────────────


def _store(readings: dict[str, float], key: str, value: float | None) -> None:
    # Log what this call DID, never the accumulator it was handed: the
    # dict grows through a sweep, so dumping it makes one reading cost a
    # kilobyte and climb.  MEASURED 2026-09-18 across a live log: four
    # lines of this shape were 90% of ALL log bytes, and the 1 MB x 5 ring
    # turned over every ~90 s -- so `trcc report` after any incident older
    # than a minute held nothing but the last seconds of sensor polls.
    # This one alone was 4.37 MB over 5,035 lines, 867 bytes to record one
    # number.
    #
    # And on the FRAME FAMILY, because trimming the payload was only half of
    # it.  MEASURED 2026-09-19: this line still fired 53 times per tick -- 53
    # of that tick's 57 records -- because every reading is stored under up to
    # three alias keys (indexed, vendor, primary).  ``_read`` already logs the
    # same ``label = value`` once per actual read and already on the frame
    # family, so all 53 duplicated a line that WAS gated.  The tick went
    # 6,217 -> 174 bytes; a 1 MB ring segment, from 5.6 min to 3.3 hours.
    frame_log.debug("_store: %s=%s", key, value)
    if value is not None:
        readings[key] = float(value)


def _cpu_keys() -> list[tuple[str, str, str]]:
    """(key, category, unit) triples for the 4 CPU readings."""
    log.debug("_cpu_keys")
    return [
        ("cpu:temp", "temperature", "°C"),
        ("cpu:usage", "usage", "%"),
        ("cpu:freq", "clock", "MHz"),
        ("cpu:power", "power", "W"),
    ]


def _memory_keys() -> list[tuple[str, str, str]]:
    log.debug("_memory_keys")
    return [
        ("memory:used", "memory", "MB"),
        ("memory:available", "memory", "MB"),
        ("memory:total", "memory", "MB"),
        ("memory:percent", "memory", "%"),
        ("memory:temp", "temperature", "°C"),
        ("memory:clock", "clock", "MHz"),
    ]


def _gpu_reading_keys(prefix: str) -> list[tuple[str, str, str]]:
    log.debug("_gpu_reading_keys: prefix=%s", prefix)
    return [
        (f"{prefix}:temp", "temperature", "°C"),
        (f"{prefix}:usage", "usage", "%"),
        (f"{prefix}:clock", "clock", "MHz"),
        (f"{prefix}:power", "power", "W"),
        (f"{prefix}:fan", "fan", "%"),
        (f"{prefix}:fan_rpm", "fan", "RPM"),
        (f"{prefix}:vram_used", "gpu_memory", "MB"),
        (f"{prefix}:vram_total", "gpu_memory", "MB"),
    ]


def _io_keys() -> list[tuple[str, str, str]]:
    log.debug("_io_keys")
    return [
        ("disk:temp", "temperature", "°C"),
        ("disk:read", "disk_io", "MB/s"),
        ("disk:write", "disk_io", "MB/s"),
        ("disk:activity", "disk_io", "%"),
        ("net:up", "network_io", "KB/s"),
        ("net:down", "network_io", "KB/s"),
        ("net:total_up", "network_io", "MB"),
        ("net:total_down", "network_io", "MB"),
    ]


def _time_keys() -> list[tuple[str, str, str]]:
    log.debug("_time_keys")
    return [
        ("time:hour", "datetime", ""),
        ("time:minute", "datetime", ""),
        ("time:second", "datetime", ""),
        ("date:year", "datetime", ""),
        ("date:month", "datetime", ""),
        ("date:day", "datetime", ""),
        ("date:dow", "datetime", ""),
    ]


# ── BaselineSensors — works on any OS ────────────────────────────────


class BaselineSensors(SensorEnumerator):
    """psutil + nvml + datetime + computed I/O.  No OS-native thermals.

    Subclasses add native temp/fan sources by overriding `_extra_sources()`
    and `_poll_extra(readings)`.
    """

    def __init__(self,
                 cpu: CpuSource | None = None,
                 memory: MemorySource | None = None,
                 gpus: list[GpuSource] | None = None,
                 fans: list[FanSource] | None = None,
                 disks: list[DiskSource] | None = None,
                 dram: list[DramSource] | None = None,
                 board_temps: list[BoardTempSource] | None = None,
                 memory_clock: MemoryClock | None = None,
                 thread_context: Callable[[], AbstractContextManager[None]]
                     = nullcontext) -> None:
        self._cpu = cpu or PsutilCpu()
        self._memory = memory or PsutilMemory()
        self._gpus: list[GpuSource] = gpus if gpus is not None else discover_nvidia_gpus()
        self._fans: list[FanSource] = fans or []
        self._disks: list[DiskSource] = disks or []
        self._dram: list[DramSource] = dram or []
        self._board: list[BoardTempSource] = board_temps or []
        self._memory_clock = memory_clock
        # Per-thread OS setup the poll thread enters before touching OS
        # sensor APIs (Windows → COM apartment for WMI; others → no-op).
        # Injected as a narrow callable so this OS-neutral aggregator never
        # depends on Platform — see memory project_threadinit_com_design.
        self._thread_context = thread_context
        self._io = ComputedIo()
        self._lock = threading.Lock()
        self._readings: dict[str, float] = {}
        # When ``_readings`` was last filled (monotonic).  0.0 = never, which
        # reads as infinitely stale — see ``_refresh_if_stale``.
        self._last_poll: float = 0.0
        self._poll_thread: threading.Thread | None = None
        # Set by ``start_polling``; called after each completed sweep.
        self._on_sweep: Callable[[], None] | None = None
        self._stop = threading.Event()
        # Set by ``_interval_changed`` to cut a sleeping poll loop's wait
        # short.  ``_stop`` is the authoritative shutdown flag and is checked
        # separately after every wake — same discrimination ``MetricsLoop``
        # makes between "stop" and "the cadence moved".
        self._wake = threading.Event()
        # Labels whose read has already raised — warn once, then DEBUG, so a
        # persistently-broken sensor doesn't spam a line every poll interval.
        self._read_failures: set[str] = set()
        # Which normalized keys no backend here can read.  A STATIC fact about
        # the classes in hand, so it is computed once on demand and kept —
        # ``discover()`` would otherwise recompute it on every 2-second poll.
        self._unsupported: frozenset[str] | None = None
        self._gpus.sort(key=_gpu_order)
        if self._gpus:
            log.info("GPU order: %s (primary auto-pick = first discrete)",
                     [g.key for g in self._gpus])

    def _read(
        self, fn: Callable[[], float | None], label: str,
    ) -> float | None:
        """Call one sensor read, degrading a raising source to None.

        A flaky or permission-locked sensor (e.g. root-only RAPL
        ``energy_uj``, a wedged hwmon node) must never take the whole poll
        — and thus the GUI launch / render tick that calls ``read_all()``
        — down with it.  First failure per label warns; later ones are
        DEBUG.  Issue #139 class.
        """
        try:
            value = fn()
        except Exception as e:
            if label in self._read_failures:
                log.debug("sensor read %s still failing: %s", label, e)
            else:
                self._read_failures.add(label)
                log.warning("sensor read %s failed (%s) — skipping this "
                            "reading; further failures at DEBUG", label, e)
            return None
        # The ONE place that holds both the label and the reading, so it is the
        # one place that can say what a sensor actually resolved to.  The 30
        # per-source entry lines say only that a function ran; this says what it
        # found, once, for all 31 reads a tick makes.  Outside the ``try`` on
        # purpose: a logging fault must not be mistaken for a sensor fault.
        frame_log.debug("%s = %s", label, value)
        return value

    # ── Structured access ──────────────────────────────────────────

    def cpu(self) -> CpuSource:
        frame_log.debug("cpu: called")
        return self._cpu

    def memory(self) -> MemorySource:
        frame_log.debug("memory: called")
        return self._memory

    def gpus(self) -> list[GpuSource]:
        frame_log.debug("gpus: count=%d", len(self._gpus))
        return list(self._gpus)

    def fans(self) -> list[FanSource]:
        frame_log.debug("fans: count=%d", len(self._fans))
        return list(self._fans)

    def disks(self) -> list[DiskSource]:
        frame_log.debug("disks: count=%d", len(self._disks))
        return list(self._disks)

    # ── Flat dict view ─────────────────────────────────────────────

    def _optional_reads(self) -> Iterator[tuple[str, QuantitySource, str]]:
        """``(normalized key, source, quantity)`` for every OPTIONAL reading.

        Only the three ports that HAVE optional quantities appear —
        ``memory:*``, ``disk:temp`` and ``memory:temp`` come from ports whose
        every reading is abstract, so no backend can decline them and they can
        never be unsupported.

        The key spellings come from the same catalog helpers ``discover()``
        uses, so the format lives in one place; only the per-source prefix is
        rebuilt, exactly as ``discover()`` rebuilds it.
        """
        log.debug("_optional_reads: gpus=%d fans=%d",
                  len(self._gpus), len(self._fans))
        for key, _, _ in _cpu_keys():
            yield key, self._cpu, key.rsplit(":", 1)[1]
        for idx, gpu in enumerate(self._gpus):
            for prefix in (f"gpu:{idx}", f"gpu:{gpu.key}"):
                for key, _, _ in _gpu_reading_keys(prefix):
                    yield key, gpu, key.rsplit(":", 1)[1]
        if (primary := self.primary_gpu()) is not None:
            for key, _, _ in _gpu_reading_keys("gpu:primary"):
                yield key, primary, key.rsplit(":", 1)[1]
        for fan in self._fans:
            yield f"fan:{fan.key}:percent", fan, "percent"

    def unsupported(self) -> frozenset[str]:
        """Normalized keys no backend here can read.  Computed once, kept.

        The INFO line is the point of the whole exercise: on a host where a
        metric can never have a value, a reporter's log now names it and says
        which backend declined it, instead of leaving an overlay warning that
        reads the same as a sensor that merely failed this tick.
        """
        if self._unsupported is None:
            declined: dict[str, str] = {}
            for key, source, quantity in self._optional_reads():
                if not source.provides(quantity):
                    declined[key] = type(source).__name__
            self._unsupported = frozenset(declined)
            if declined:
                log.info(
                    "unsupported: %d key(s) no backend on this host can read "
                    "— %s", len(declined),
                    ", ".join(f"{k} ({v})" for k, v in sorted(declined.items())),
                )
            else:
                log.info("unsupported: every advertised quantity has a backend "
                         "that reads it")
        return self._unsupported

    def discover(self) -> list[SensorReading]:
        """One SensorReading per normalized key this host CAN read.

        The catalog is static — the same 25 CPU / memory / IO / time keys are
        built on every machine — so advertising it verbatim offered sensors that
        can never hold a value, at ``value=0.0`` because ``SensorReading.value``
        is a plain ``float`` and cannot say "no reading".  Three consumers
        believed it: ``ListSensors`` (and so the API and the pickers) offered
        them, ``auto_map`` bound dashboard rows to them — 16 of its 20 exact-id
        targets are static catalog keys, so its documented "target not available
        on this host stays unbound, the panel renders ``--``" could not fire —
        and a bound row then shows ``0`` forever.

        Withholding the UNSUPPORTED ones fixes all three at once, and does it
        without a DTO change: the bus simply stops offering a sensor that cannot
        exist, and every face is correct for free.

        **It filters on ``unsupported()``, never on "missing from
        ``read_all()``".**  That second set is derived from ONE tick and is
        transient — the rate-derived keys need two samples — while ``auto_map``
        PERSISTS what it binds.  Filtering on a tick would let a cold start
        write an unbound row into the saved config for a sensor that works fine.
        """
        # Entry line, no content — and `discover` is NOT one-shot: qtgui's
        # SensorPickerWidget dispatches ReadSensors() on a 2-second QTimer.
        # DEBUG, not INFO: the frame family sits at INFO by default, so an
        # .info() here would still write a record every 2s.
        frame_log.debug("discover: called")
        current = self.read_all()
        readings: list[SensorReading] = []

        for key, cat, unit in _cpu_keys():
            readings.append(SensorReading(
                sensor_id=key, category=cat,
                value=current.get(key, 0.0), unit=unit, label=self._cpu.name,
            ))

        for key, cat, unit in _memory_keys():
            readings.append(SensorReading(
                sensor_id=key, category=cat,
                value=current.get(key, 0.0), unit=unit, label="Memory",
            ))

        for idx, gpu in enumerate(self._gpus):
            label = gpu.name
            # indexed keys
            for key, cat, unit in _gpu_reading_keys(f"gpu:{idx}"):
                readings.append(SensorReading(
                    sensor_id=key, category=cat,
                    value=current.get(key, 0.0), unit=unit, label=label,
                ))
            # vendor keys
            for key, cat, unit in _gpu_reading_keys(f"gpu:{gpu.key}"):
                readings.append(SensorReading(
                    sensor_id=key, category=cat,
                    value=current.get(key, 0.0), unit=unit, label=label,
                ))

        # primary GPU alias
        primary = self.primary_gpu()
        if primary is not None:
            for key, cat, unit in _gpu_reading_keys("gpu:primary"):
                readings.append(SensorReading(
                    sensor_id=key, category=cat,
                    value=current.get(key, 0.0), unit=unit, label=primary.name,
                ))

        for fan in self._fans:
            for metric, cat, unit in (("rpm", "fan", "RPM"),
                                      ("percent", "fan", "%")):
                key = f"fan:{fan.key}:{metric}"
                readings.append(SensorReading(
                    sensor_id=key, category=cat,
                    value=current.get(key, 0.0), unit=unit, label=fan.name,
                ))

        # Board / super-I/O temperatures.  Plural and user-chosen, exactly
        # like fans -- and read off the SAME chip, which is the part that made
        # the gap invisible: we already opened an nct6xxx for its fans and
        # walked past its dozen temperature inputs (#259, #282).
        for board in self._board:
            key = f"board:{board.key}:temp"
            readings.append(SensorReading(
                sensor_id=key, category="temperature",
                value=current.get(key, 0.0), unit="°C", label=board.name,
            ))

        for key, cat, unit in _io_keys() + _time_keys():
            readings.append(SensorReading(
                sensor_id=key, category=cat,
                value=current.get(key, 0.0), unit=unit,
            ))

        withheld = self.unsupported()
        advertised = [r for r in readings if r.sensor_id not in withheld]
        frame_log.debug("discover: %d reading(s), %d withheld as unsupported",
                        len(advertised), len(readings) - len(advertised))
        return advertised

    def read_all(self) -> dict[str, float]:
        """Every current reading, refreshing the cache first if it is stale."""
        frame_log.debug("read_all: cached=%d", len(self._readings))
        self._refresh_if_stale()
        with self._lock:
            return dict(self._readings)

    def read_one(self, sensor_id: str) -> float | None:
        """One current reading, refreshing the cache first if it is stale."""
        log.debug("read_one: sensor_id=%s", sensor_id)
        self._refresh_if_stale()
        with self._lock:
            return self._readings.get(sensor_id)

    def _refresh_if_stale(self) -> None:
        """Poll inline when nothing else is keeping ``_readings`` current.

        The cache had no expiry: ``read_all`` polled once, when it was empty,
        then returned that same dict for the life of the process.  Only
        ``start_polling`` refreshed it, and its one caller is ``MetricsLoop``
        — which the daemon, gui and qtgui start and no CLI or API entry point
        does.  So every long-running headless render loop ran on boot-time
        values: ``trcc led play`` drove an LED cooler's colours and its
        segment readout from the temperature at launch (#270), and ``display
        play`` did the same to LCD metric overlays.  ``read_one`` was worse
        still — it read the cache without ever polling, so on a fresh
        enumerator it returned None indefinitely.

        A live poll thread owns the cadence and its cache is fresh by
        definition, so the gui/daemon path returns here without taking the
        lock and pays nothing.
        """
        if self._poll_thread is not None and self._poll_thread.is_alive():
            frame_log.debug("_refresh_if_stale: poll thread owns the cadence")
            return
        with self._lock:
            age = time.monotonic() - self._last_poll
            fresh = bool(self._readings) and age < self._interval_s
        if fresh:
            frame_log.debug("_refresh_if_stale: cache %.2fs old < %.2fs interval",
                            age, self._interval_s)
            return
        frame_log.debug("_refresh_if_stale: cache %.2fs old >= %.2fs interval "
                        "and no poll thread — polling inline",
                        age, self._interval_s)
        self._poll_once()

    # ── Polling ────────────────────────────────────────────────────

    def start_polling(
        self, interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
        on_sweep: Callable[[], None] | None = None,
    ) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            log.debug("sensor polling already running — start_polling ignored")
            return
        log.info("start_polling: on_sweep listener=%s",
                 "yes" if on_sweep is not None else "none")
        self._on_sweep = on_sweep
        # Delegate rather than assign: ``set_interval`` owns the clamp, so a
        # second writer here is how the floor gets skipped on one path only.
        self.set_interval(interval_s)
        self._stop.clear()
        self._wake.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="sensor-poll")
        self._poll_thread.start()
        log.info("sensor polling started (interval=%.1fs)", self._interval_s)

    def _interval_changed(self) -> None:
        """Cut a sleeping poll loop's wait short so the new cadence starts now.

        Without this the thread honours the change only after sleeping out the
        OLD interval — up to 100 s of the sweep running at the wrong rate
        while the broadcast it feeds already runs at the new one.
        """
        log.debug("_interval_changed: waking the poll loop")
        self._wake.set()

    def stop_polling(self) -> None:
        if not (self._poll_thread and self._poll_thread.is_alive()):
            log.debug("sensor polling not running — stop_polling ignored")
            return
        log.info("sensor polling: stopping")
        self._stop.set()
        self._wake.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=3)
            self._poll_thread = None

    def _notify_swept(self) -> None:
        """Tell the listener a sweep just produced fresh readings.

        In the ``else`` of the poll's ``try`` deliberately: a sweep that
        RAISED produced nothing, so waking a consumer would hand it the
        previous pass's data while implying it is new.

        Guarded because this runs on the poll thread and a listener fault
        must not kill the cadence every other consumer depends on — the
        thread dying silently is how ``read_all`` went back to returning
        boot-time values (#270).
        """
        if self._on_sweep is None:
            return
        try:
            self._on_sweep()
        except Exception:
            log.exception("sensor sweep listener raised — cadence continues")

    def _poll_loop(self) -> None:
        log.debug("_poll_loop: starting interval=%.1fs", self._interval_s)
        # Enter the OS thread context ONCE for the poll thread's lifetime
        # (CoInitialize on Windows; no-op elsewhere).  WMI handles created
        # inside the loop are then born in this thread's apartment.
        with self._thread_context():
            while not self._stop.is_set():
                try:
                    self._poll_once()
                except Exception:
                    log.exception("sensor poll iteration failed")
                else:
                    self._notify_swept()
                # Re-read the interval every pass: ``set_interval`` may have
                # moved it while this iteration was polling.
                self._wake.wait(self._interval_s)
                self._wake.clear()

    def _poll_once(self) -> None:
        frame_log.debug("_poll_once: called")
        r: dict[str, float] = {}

        # CPU
        _store(r, "cpu:temp", self._read(self._cpu.temp, "cpu:temp"))
        _store(r, "cpu:usage", self._read(self._cpu.usage, "cpu:usage"))
        _store(r, "cpu:freq", self._read(self._cpu.freq, "cpu:freq"))
        _store(r, "cpu:power", self._read(self._cpu.power, "cpu:power"))

        # Memory
        _store(r, "memory:used", self._read(self._memory.used, "memory:used"))
        _store(r, "memory:available",
               self._read(self._memory.available, "memory:available"))
        _store(r, "memory:total",
               self._read(self._memory.total, "memory:total"))
        _store(r, "memory:percent",
               self._read(self._memory.percent, "memory:percent"))

        # GPUs — one reading set per indexed position, plus vendor key alias,
        # plus primary alias pointing at the same underlying readings.
        primary = self.primary_gpu()
        for idx, gpu in enumerate(self._gpus):
            temp = self._read(gpu.temp, f"gpu:{idx}:temp")
            usage = self._read(gpu.usage, f"gpu:{idx}:usage")
            clock = self._read(gpu.clock, f"gpu:{idx}:clock")
            power = self._read(gpu.power, f"gpu:{idx}:power")
            fan = self._read(gpu.fan, f"gpu:{idx}:fan")
            fan_rpm = self._read(gpu.fan_rpm, f"gpu:{idx}:fan_rpm")
            vram_used = self._read(gpu.vram_used, f"gpu:{idx}:vram_used")
            vram_total = self._read(gpu.vram_total, f"gpu:{idx}:vram_total")
            for prefix in (f"gpu:{idx}", f"gpu:{gpu.key}"):
                _store(r, f"{prefix}:temp", temp)
                _store(r, f"{prefix}:usage", usage)
                _store(r, f"{prefix}:clock", clock)
                _store(r, f"{prefix}:power", power)
                _store(r, f"{prefix}:fan", fan)
                _store(r, f"{prefix}:fan_rpm", fan_rpm)
                _store(r, f"{prefix}:vram_used", vram_used)
                _store(r, f"{prefix}:vram_total", vram_total)
            if gpu is primary:
                _store(r, "gpu:primary:temp", temp)
                _store(r, "gpu:primary:usage", usage)
                _store(r, "gpu:primary:clock", clock)
                _store(r, "gpu:primary:power", power)
                _store(r, "gpu:primary:fan", fan)
                _store(r, "gpu:primary:fan_rpm", fan_rpm)
                _store(r, "gpu:primary:vram_used", vram_used)
                _store(r, "gpu:primary:vram_total", vram_total)

        # Fans
        for fan in self._fans:
            _store(r, f"fan:{fan.key}:rpm",
                   self._read(fan.rpm, f"fan:{fan.key}:rpm"))
            _store(r, f"fan:{fan.key}:percent",
                   self._read(fan.percent, f"fan:{fan.key}:percent"))
        r.update(self.fan_slots(r))

        # Disk temperature — one DiskSource per drive; the model carries a
        # single ``disk_temp`` slot, so collapse to the HOTTEST drive (the one
        # most likely to throttle), mirroring cpu_temp = hottest socket.
        # EVERY disk is read every tick, deliberately: ``_read`` carries the
        # per-source failure bookkeeping, so polling only the chosen drive would
        # silently drop the others' diagnostics.  The choice is applied AFTER.
        disk_temps = {
            disk.key: t for disk in self._disks
            if (t := self._read(disk.temp, f"disk:{disk.key}:temp")) is not None
        }
        if disk_temps:
            # The user's pinned drive when present and readable, else the
            # HOTTEST — the only rule until 2026-08-31, and still the default.
            # ``preferred_disk()`` answers presence only; the fallback lives
            # here because "hottest" is a property of THESE readings, which the
            # port would otherwise have to take a second time.
            chosen = self.preferred_disk()
            _store(r, "disk:temp",
                   disk_temps[chosen.key]
                   if chosen is not None and chosen.key in disk_temps
                   else max(disk_temps.values()))

        # DRAM temperature — one DramSource per DIMM; the model carries a single
        # ``mem_temp`` slot, so collapse to the HOTTEST module, mirroring disk.
        for board in self._board:
            _store(r, f"board:{board.key}:temp",
                   self._read(board.temp, f"board:{board.key}:temp"))

        dram_temps = [
            t for d in self._dram
            if (t := self._read(d.temp, f"memory:{d.key}:temp")) is not None
        ]
        if dram_temps:
            _store(r, "memory:temp", max(dram_temps))

        # Memory channel clock — static SPD nameplate (cached at construction).
        if self._memory_clock is not None:
            _store(r, "memory:clock",
                   self._read(self._memory_clock.clock, "memory:clock"))

        # IO + time
        try:
            self._io.poll(r)
        except Exception as e:
            if "io" in self._read_failures:
                log.debug("sensor read io still failing: %s", e)
            else:
                self._read_failures.add("io")
                log.warning("sensor read io failed (%s) — skipping disk/net "
                            "stats; further failures at DEBUG", e)
        now = datetime.datetime.now()
        r["time:hour"] = float(now.hour)
        r["time:minute"] = float(now.minute)
        r["time:second"] = float(now.second)
        r["date:year"] = float(now.year)
        r["date:month"] = float(now.month)
        r["date:day"] = float(now.day)
        r["date:dow"] = float(now.weekday())

        # Subclass extras
        self._poll_extra(r)

        with self._lock:
            self._readings = r
            self._last_poll = time.monotonic()

    def _poll_extra(self, readings: dict[str, float]) -> None:
        """Override to add OS-native readings not covered by cpu/memory/gpus/fans."""


# ── LinuxSensors — baseline + hwmon-discovered Linux sources ─────────


def build_linux_sensors() -> BaselineSensors:
    """Factory: scan hwmon + DRM + NVIDIA, compose a full Linux enumerator.

    Falls back to BaselineSensors if /sys/class/hwmon doesn't exist (VM,
    non-Linux accidentally calling this).
    """
    log.info("build_linux_sensors: called")
    hwmon_devices = scan_hwmon_devices()
    psutil_cpu = PsutilCpu()
    cpu = HwmonCpu(psutil_cpu, find_cpu_temp_device(hwmon_devices))
    gpus: list[GpuSource] = []
    gpus.extend(discover_nvidia_gpus())
    gpus.extend(discover_amd_gpus(hwmon_devices))
    gpus.extend(discover_intel_gpus(hwmon_devices))
    gpus.extend(discover_nouveau_gpus(hwmon_devices))
    fans = discover_fans(hwmon_devices)
    disks = discover_disk_temp(hwmon_devices)
    dram = discover_dram_temp(hwmon_devices)
    board_temps = discover_board_temps()
    memory_clock = MemoryClock()
    log.info("Linux sensors: cpu_temp=%s, gpus=%d, fans=%d, disks=%d, dram=%d "
             "(mem_clock read on first poll)",
             "yes" if cpu.temp() is not None else "no",
             len(gpus), len(fans), len(disks), len(dram))
    return BaselineSensors(cpu=cpu, memory=PsutilMemory(),
                           gpus=gpus, fans=fans, disks=disks, dram=dram,
                           board_temps=board_temps, memory_clock=memory_clock)
