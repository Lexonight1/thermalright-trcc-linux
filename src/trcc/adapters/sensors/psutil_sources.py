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
import time

import psutil  # pyright: ignore[reportMissingImports]

from ...core.ports import CpuSource, MemorySource

log = logging.getLogger(__name__)


class PsutilCpu(CpuSource):
    """Usage + frequency from psutil.  Temp/power return None by default.

    Subclass and override `temp()` / `power()` with an OS-native
    thermal source (HwmonCpu, LhmCpu, etc.).
    """

    def __init__(self) -> None:
        self._warm = False
        try:
            self._name = psutil.cpu_info()[0].name  # type: ignore[attr-defined]
        except (psutil.Error, AttributeError, OSError):
            self._name = "CPU"

    @property
    def name(self) -> str:
        return self._name

    def temp(self) -> float | None:
        return None

    def usage(self) -> float | None:
        # First call needs an interval to bootstrap the delta
        if not self._warm:
            self._warm = True
            return float(psutil.cpu_percent(interval=0.08))
        return float(psutil.cpu_percent(interval=None))

    def freq(self) -> float | None:
        try:
            freq = psutil.cpu_freq()
            return float(freq.current) if freq else None
        except (psutil.Error, AttributeError, OSError):
            return None

    def power(self) -> float | None:
        return None


class PsutilMemory(MemorySource):
    """RAM metrics from psutil.  Works on every OS."""

    def used(self) -> float | None:
        try:
            return psutil.virtual_memory().used / (1024 * 1024)
        except (psutil.Error, AttributeError, OSError):
            return None

    def available(self) -> float | None:
        try:
            return psutil.virtual_memory().available / (1024 * 1024)
        except (psutil.Error, AttributeError, OSError):
            return None

    def total(self) -> float | None:
        try:
            return psutil.virtual_memory().total / (1024 * 1024)
        except (psutil.Error, AttributeError, OSError):
            return None

    def percent(self) -> float | None:
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
        self._disk_prev: tuple | None = None
        self._net_prev: tuple | None = None

    def poll(self, readings: dict[str, float]) -> None:
        now = time.monotonic()
        self._poll_disk(readings, now)
        self._poll_net(readings, now)

    def _poll_disk(self, readings: dict[str, float], now: float) -> None:
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
