"""psutil-backed sensor sources — universal across OSes.

Gives us:
    * CPU usage + frequency (every machine Python runs on)
    * Memory used/available/total/percent
    * Disk I/O rate helper
    * Network I/O rate helper

Does NOT cover CPU temperature or power — those need OS-native
sources (hwmon on Linux, LHM on Windows, SMC on macOS, sysctl on BSD).
The CPU class here returns None for temp/power and is typically
subclassed (HwmonCpu adds temp on Linux).
"""
from __future__ import annotations

import logging
import threading
import time

import psutil  # pyright: ignore[reportMissingImports]

from ...core.logs import per_frame
from ...core.ports import BoardTempSource, CpuSource, MemorySource

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class PsutilCpu(CpuSource):
    """Usage + frequency from psutil.  Temp/power return None by default.

    Subclass and override `temp()` / `power()` with an OS-native
    thermal source (HwmonCpu, LhmCpu, etc.).
    """

    def __init__(self) -> None:
        log.debug("__init__")
        self._warm = False
        try:
            self._name = psutil.cpu_info()[0].name  # type: ignore[attr-defined]
        except (psutil.Error, AttributeError, OSError):
            self._name = "CPU"

    @property
    def name(self) -> str:
        frame_log.debug("name")
        return self._name

    def temp(self) -> float | None:
        frame_log.debug("temp: called")
        return None

    def usage(self) -> float | None:
        frame_log.debug("usage: warm=%s", self._warm)
        # First call needs an interval to bootstrap the delta
        if not self._warm:
            self._warm = True
            return float(psutil.cpu_percent(interval=0.08))
        return float(psutil.cpu_percent(interval=None))

    def freq(self) -> float | None:
        frame_log.debug("freq: called")
        try:
            freq = psutil.cpu_freq()
            return float(freq.current) if freq else None
        except (psutil.Error, AttributeError, OSError):
            return None

    def power(self) -> float | None:
        frame_log.debug("power: called")
        return None


class PsutilMemory(MemorySource):
    """RAM metrics from psutil.  Works on every OS."""

    def used(self) -> float | None:
        frame_log.debug("used: called")
        try:
            return psutil.virtual_memory().used / (1024 * 1024)
        except (psutil.Error, AttributeError, OSError):
            return None

    def available(self) -> float | None:
        frame_log.debug("available: called")
        try:
            return psutil.virtual_memory().available / (1024 * 1024)
        except (psutil.Error, AttributeError, OSError):
            return None

    def total(self) -> float | None:
        frame_log.debug("total: called")
        try:
            return psutil.virtual_memory().total / (1024 * 1024)
        except (psutil.Error, AttributeError, OSError):
            return None

    def percent(self) -> float | None:
        frame_log.debug("percent: called")
        try:
            return float(psutil.virtual_memory().percent)
        except (psutil.Error, AttributeError, OSError):
            return None


# ── Computed I/O rates (free function; aggregator owns the delta state) ──


class ComputedIo:
    """Disk + network I/O rate computation via psutil counter deltas.

    Aggregator owns one instance; calls `poll(readings_dict)` each tick
    and the instance maintains the previous-counter state internally.
    """

    def __init__(self) -> None:
        log.debug("__init__")
        self._disk_prev: tuple | None = None
        self._net_prev: tuple | None = None

    def poll(self, readings: dict[str, float]) -> None:
        frame_log.debug("poll: called")
        now = time.monotonic()
        self._poll_disk(readings, now)
        self._poll_net(readings, now)

    def _poll_disk(self, readings: dict[str, float], now: float) -> None:
        # Entry says only that we are polling; the readings it ADDS are logged
        # on the way out.  Dumping the accumulator here cost 1,797 bytes a
        # call -- see ``_store``.
        frame_log.debug("_poll_disk: now=%s", now)
        try:
            disk = psutil.disk_io_counters()
        except (psutil.Error, AttributeError, OSError):
            return
        if disk is None:
            return
        if self._disk_prev:
            prev_disk, prev_time = self._disk_prev
            dt = now - prev_time
            if dt > 0:
                readings["disk:read"] = (
                    (disk.read_bytes - prev_disk.read_bytes) / (dt * 1024 * 1024))
                readings["disk:write"] = (
                    (disk.write_bytes - prev_disk.write_bytes) / (dt * 1024 * 1024))
                if hasattr(disk, "busy_time") and hasattr(prev_disk, "busy_time"):
                    busy_ms = disk.busy_time - prev_disk.busy_time
                    readings["disk:activity"] = min(100.0, busy_ms / (dt * 10))
        self._disk_prev = (disk, now)

    def _poll_net(self, readings: dict[str, float], now: float) -> None:
        frame_log.debug("_poll_net: now=%s", now)
        try:
            net = psutil.net_io_counters()
        except (psutil.Error, AttributeError, OSError):
            return
        if net is None:
            return
        readings["net:total_up"] = net.bytes_sent / (1024 * 1024)
        readings["net:total_down"] = net.bytes_recv / (1024 * 1024)
        if self._net_prev:
            prev_net, prev_time = self._net_prev
            dt = now - prev_time
            if dt > 0:
                readings["net:up"] = (
                    (net.bytes_sent - prev_net.bytes_sent) / (dt * 1024))
                readings["net:down"] = (
                    (net.bytes_recv - prev_net.bytes_recv) / (dt * 1024))
        self._net_prev = (net, now)


# ── Motherboard / super-I/O temperatures (#259, #282) ────────────────────────

#: Chips a ROLE-typed source already owns.  Skipped so a DIMM does not appear
#: twice, once as ``memory:temp`` and once as a nameless board sensor.  Kept in
#: step with ``hwmon._CPU_DRIVERS`` / ``_DISK_DRIVERS`` / ``_DRAM_DRIVERS`` and
#: the GPU drivers beside them.
_ROLE_OWNED_CHIPS = frozenset({
    "amdgpu", "nouveau", "i915", "xe",          # gpu
    "nvme", "drivetemp",                        # disk
    "spd5118", "jc42",                          # dram
})

#: CPU chips own only their FIRST input -- ``temp1``, the one ``HwmonCpu``
#: reads as ``cpu:temp`` (Tctl on k10temp, "Package id 0" on coretemp).  They
#: were claimed whole, so k10temp's Tccd* and coretemp's "Core N" were read by
#: nothing at all (#301); the rest now reach the board family like any input.
_CPU_CHIPS = frozenset({"coretemp", "k10temp", "zenpower"})


def sensor_slug(chip: str, label: str, index: int) -> str:
    """A stable, readable key for one board input.

    The LABEL is the identity a user recognises -- they are looking for
    ``T_SENSOR1``, and on this hardware that is an ``AUXTIN``.  Falls back to
    the positional index only when the chip publishes no label, because a bare
    ``temp7`` tells the user nothing and two unlabelled chips would collide.
    """
    log.debug("sensor_slug: chip=%s label=%r index=%d", chip, label, index)
    base = label.strip() or f"temp{index}"
    cleaned = "".join(c if c.isalnum() else "_" for c in base).strip("_").lower()
    return f"{chip}_{cleaned}" if cleaned else f"{chip}_temp{index}"


#: How long one ``sensors_temperatures()`` result is reused.
#:
#: This started at 0.25 s, chosen so the scan could collapse ONE poll's nine
#: board reads and "never serve a second poll a stale scan".  That fixed the
#: 9-rescans-per-poll regression but left the bigger one: every poll still
#: paid a full scan.
#:
#: MEASURED, and it is the most expensive thing a sweep does --
#: ``sensors_temperatures()`` opens **260 of the sweep's 316 files**, because
#: it reads ``temp_input``, ``temp_max``, ``temp_crit``, ``temp_label`` and
#: ``name`` for ALL 35 temperature sensors on the machine to serve 9 board
#: readings that need one file each.  Every other source family (gpus, fans,
#: disks, dram, spd clock) contributes ~0 opens.
#:
#: So the scan spans several polls now: **316 -> 99.3 opens per sweep, -69%**,
#: with all 9 board keys still present on every poll (a cached scan still
#: answers every sensor, so nothing downstream sees a key disappear and render
#: "--").  The cost is granularity: a motherboard temperature can be up to
#: this old.  They move over tens of seconds, and this is the one number to
#: turn if that is ever too coarse.
#:
#: Cadence lives HERE and nowhere else, deliberately.  ``chips()`` rescans
#: whenever ANY source is due, so nine sources each holding their own schedule
#: would drift apart and re-create a rescan on every poll.  One scan, one
#: timestamp, lockstep by construction.
_SCAN_TTL_S = 5.0


class _TemperatureScan:
    """One ``psutil.sensors_temperatures()`` result, shared by the sources.

    ``sensors_temperatures()`` returns EVERY chip on every call, so a source
    that calls it to read its own chip pays for all of them.  Each board source
    called it independently, which made the cost scale with how many sensors
    the board exposes: 9 sources on the dev box meant 9 full rescans per poll,
    ~2,500 file opens, and a metric poll that went from 27.7 ms to 180.8 ms
    when board temperatures landed (49b8143c) -- 1.4% to 9.0% of a core, with
    nothing measuring it.

    One instance is shared by every source ``discover_board_temps`` builds, so
    a poll scans once however many sensors the board has.
    """

    def __init__(self, ttl_s: float = _SCAN_TTL_S) -> None:
        log.debug("_TemperatureScan.__init__: ttl=%.2fs", ttl_s)
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._chips: dict[str, list] | None = None
        self._read_at = 0.0

    def chips(self) -> dict[str, list]:
        """The current scan, rescanning only when the cached one has expired.

        Returns ``{}`` when psutil cannot answer -- callers read their own chip
        out of it, and a missing chip already means "no reading".
        """
        with self._lock:
            age = time.monotonic() - self._read_at
            if self._chips is not None and age < self._ttl_s:
                frame_log.debug("_TemperatureScan.chips: cached (%.3fs old)", age)
                return self._chips
            try:
                self._chips = psutil.sensors_temperatures()
            except (psutil.Error, AttributeError, OSError) as e:
                log.debug("_TemperatureScan.chips: psutil unreadable (%s)", e)
                self._chips = {}
            self._read_at = time.monotonic()
            frame_log.debug("_TemperatureScan.chips: rescanned, %d chip(s)",
                            len(self._chips))
            return self._chips


class PsutilBoardTemp(BoardTempSource):
    """One board temperature, read through psutil rather than raw sysfs.

    psutil is already a hard dependency here (CPU, memory and network all come
    from it), and its ``sensors_temperatures`` does two things our own hwmon
    scanner does not:

    * it globs ``/sys/class/hwmon/hwmon*/device/temp*_*`` as well as the plain
      path -- **the exact fallback #282 asked for**, for chips that hang their
      inputs one directory deeper (his Fujitsu ``sch5636``);
    * it falls back to ``/sys/class/thermal/thermal_zone*`` when hwmon yields
      nothing at all.

    So this reads a wider set than a bespoke parser would, on every OS psutil
    supports, for no new dependency.
    """

    def __init__(self, chip: str, label: str, index: int,
                 scan: _TemperatureScan | None = None) -> None:
        log.debug("PsutilBoardTemp: chip=%s label=%s index=%d shared_scan=%s",
                  chip, label, index, scan is not None)
        self._chip = chip
        self._label = label
        self._index = index
        self._key = sensor_slug(chip, label, index)
        # Own scan when constructed alone (tests, a single ad-hoc source);
        # discover_board_temps hands every source the SAME one.
        self._scan = scan if scan is not None else _TemperatureScan()

    @property
    def key(self) -> str:
        frame_log.debug("key: %s", self._key)
        return self._key

    @property
    def name(self) -> str:
        frame_log.debug("name: %s", self._label)
        return f"{self._label or f'temp{self._index}'} ({self._chip})"

    def temp(self) -> float | None:
        entries = self._scan.chips().get(self._chip, [])
        frame_log.debug("PsutilBoardTemp.temp: %s has %d entry(ies)",
                        self._chip, len(entries))
        for i, entry in enumerate(entries, start=1):
            if i == self._index:
                return float(entry.current) if entry.current else None
        return None


def discover_board_temps() -> list[BoardTempSource]:
    """Every labelled board temperature the OS will admit to.

    **Zero readings are dropped, and that is not cosmetic.**  On this desk the
    ``nct6798`` publishes twelve inputs and four of them
    (``PCH_CHIP_TEMP``, ``PCH_CPU_TEMP``, ``PCH_MCH_TEMP``,
    ``PCH_CHIP_CPU_MAX_TEMP``) read exactly ``0.0`` -- the documented signature
    of a header the board never wired.  lm-sensors users mask those with
    per-board ``ignore`` directives in ``/etc/sensors.d``, which we cannot
    ship, so the filter has to live here or every one of those users is handed
    four dead sensors to choose between.

    Exactly ``0.0`` only.  A real probe reading a cold room is a low number,
    not a zero, so a range check would throw away the very sensor #259 is
    asking for.
    """
    sources: list[BoardTempSource] = []
    # Every source this builds shares ONE scan, so a poll rescans once no
    # matter how many sensors the board exposes.
    scan = _TemperatureScan()
    try:
        chips = psutil.sensors_temperatures()
    except (psutil.Error, AttributeError, OSError) as e:
        log.info("discover_board_temps: unavailable (%s)", e)
        return sources
    for chip, entries in sorted(chips.items()):
        if chip in _ROLE_OWNED_CHIPS:
            continue
        for index, entry in enumerate(entries, start=1):
            if chip in _CPU_CHIPS and index == 1:
                log.debug("discover_board_temps: %s/%s is cpu:temp's", chip,
                          entry.label or index)
                continue
            if not entry.current:
                log.debug("discover_board_temps: %s/%s reads 0 — unconnected "
                          "header, skipped", chip, entry.label or index)
                continue
            sources.append(
                PsutilBoardTemp(chip, entry.label or "", index, scan))
    log.info("discover_board_temps: %d board sensor(s) across %d chip(s), "
             "one shared scan", len(sources), len(chips))
    return sources
