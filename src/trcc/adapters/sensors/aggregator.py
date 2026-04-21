"""SensorEnumerator aggregators — compose sources into the flat dict view.

Takes CpuSource + MemorySource + GpuSource[] + FanSource[] and produces
the normalized keys overlays use:

    cpu:temp | cpu:usage | cpu:freq | cpu:power
    gpu:primary:temp | gpu:0:temp | gpu:nvidia:0:temp | gpu:amd:0:temp
    memory:used | memory:available | memory:total | memory:percent
    fan:cpu:rpm | fan:gpu:percent | fan:<key>:rpm
    disk:read | disk:write | disk:activity
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
from typing import List, Optional

from ...core.models import SensorReading
from ...core.ports import (
    CpuSource,
    FanSource,
    GpuSource,
    MemorySource,
    SensorEnumerator,
)
from .hwmon import (
    HwmonCpu,
    discover_amd_gpus,
    discover_fans,
    discover_intel_gpus,
    find_cpu_temp_device,
    scan_hwmon_devices,
)
from .nvml import discover_nvidia_gpus
from .psutil_sources import ComputedIo, PsutilCpu, PsutilMemory

log = logging.getLogger(__name__)


# ── Key mapping helpers ──────────────────────────────────────────────


def _store(readings: dict[str, float], key: str, value: Optional[float]) -> None:
    if value is not None:
        readings[key] = float(value)


def _cpu_keys() -> List[tuple[str, str, str]]:
    """(key, category, unit) triples for the 4 CPU readings."""
    return [
        ("cpu:temp", "temperature", "°C"),
        ("cpu:usage", "usage", "%"),
        ("cpu:freq", "clock", "MHz"),
        ("cpu:power", "power", "W"),
    ]


def _memory_keys() -> List[tuple[str, str, str]]:
    return [
        ("memory:used", "memory", "MB"),
        ("memory:available", "memory", "MB"),
        ("memory:total", "memory", "MB"),
        ("memory:percent", "memory", "%"),
    ]


def _gpu_reading_keys(prefix: str) -> List[tuple[str, str, str]]:
    return [
        (f"{prefix}:temp", "temperature", "°C"),
        (f"{prefix}:usage", "usage", "%"),
        (f"{prefix}:clock", "clock", "MHz"),
        (f"{prefix}:power", "power", "W"),
        (f"{prefix}:fan", "fan", "%"),
        (f"{prefix}:vram_used", "gpu_memory", "MB"),
        (f"{prefix}:vram_total", "gpu_memory", "MB"),
    ]


def _io_keys() -> List[tuple[str, str, str]]:
    return [
        ("disk:read", "disk_io", "MB/s"),
        ("disk:write", "disk_io", "MB/s"),
        ("disk:activity", "disk_io", "%"),
        ("net:up", "network_io", "KB/s"),
        ("net:down", "network_io", "KB/s"),
        ("net:total_up", "network_io", "MB"),
        ("net:total_down", "network_io", "MB"),
    ]


def _time_keys() -> List[tuple[str, str, str]]:
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
                 cpu: Optional[CpuSource] = None,
                 memory: Optional[MemorySource] = None,
                 gpus: Optional[List[GpuSource]] = None,
                 fans: Optional[List[FanSource]] = None) -> None:
        self._cpu = cpu or PsutilCpu()
        self._memory = memory or PsutilMemory()
        self._gpus: List[GpuSource] = gpus if gpus is not None else discover_nvidia_gpus()
        self._fans: List[FanSource] = fans or []
        self._io = ComputedIo()
        self._lock = threading.Lock()
        self._readings: dict[str, float] = {}
        self._poll_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval_s: float = 2.0
        self._gpus.sort(key=lambda g: (not g.is_discrete, g.key))

    # ── Structured access ──────────────────────────────────────────

    def cpu(self) -> CpuSource:
        return self._cpu

    def memory(self) -> MemorySource:
        return self._memory

    def gpus(self) -> List[GpuSource]:
        return list(self._gpus)

    def fans(self) -> List[FanSource]:
        return list(self._fans)

    # ── Flat dict view ─────────────────────────────────────────────

    def discover(self) -> List[SensorReading]:
        """Return one SensorReading per normalized key with current values."""
        current = self.read_all()
        readings: List[SensorReading] = []

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

        for key, cat, unit in _io_keys() + _time_keys():
            readings.append(SensorReading(
                sensor_id=key, category=cat,
                value=current.get(key, 0.0), unit=unit,
            ))

        return readings

    def read_all(self) -> dict[str, float]:
        with self._lock:
            if self._readings:
                return dict(self._readings)
        self._poll_once()
        with self._lock:
            return dict(self._readings)

    def read_one(self, sensor_id: str) -> Optional[float]:
        with self._lock:
            return self._readings.get(sensor_id)

    # ── Polling ────────────────────────────────────────────────────

    def start_polling(self, interval_s: float = 2.0) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._interval_s = max(0.5, interval_s)
        self._stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="sensor-poll")
        self._poll_thread.start()
        log.debug("sensor polling started (interval=%.1fs)", self._interval_s)

    def stop_polling(self) -> None:
        self._stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=3)
            self._poll_thread = None

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                log.exception("sensor poll iteration failed")
            self._stop.wait(self._interval_s)

    def _poll_once(self) -> None:
        r: dict[str, float] = {}

        # CPU
        _store(r, "cpu:temp", self._cpu.temp())
        _store(r, "cpu:usage", self._cpu.usage())
        _store(r, "cpu:freq", self._cpu.freq())
        _store(r, "cpu:power", self._cpu.power())

        # Memory
        _store(r, "memory:used", self._memory.used())
        _store(r, "memory:available", self._memory.available())
        _store(r, "memory:total", self._memory.total())
        _store(r, "memory:percent", self._memory.percent())

        # GPUs — one reading set per indexed position, plus vendor key alias,
        # plus primary alias pointing at the same underlying readings.
        primary = self.primary_gpu()
        for idx, gpu in enumerate(self._gpus):
            temp = gpu.temp()
            usage = gpu.usage()
            clock = gpu.clock()
            power = gpu.power()
            fan = gpu.fan()
            vram_used = gpu.vram_used()
            vram_total = gpu.vram_total()
            for prefix in (f"gpu:{idx}", f"gpu:{gpu.key}"):
                _store(r, f"{prefix}:temp", temp)
                _store(r, f"{prefix}:usage", usage)
                _store(r, f"{prefix}:clock", clock)
                _store(r, f"{prefix}:power", power)
                _store(r, f"{prefix}:fan", fan)
                _store(r, f"{prefix}:vram_used", vram_used)
                _store(r, f"{prefix}:vram_total", vram_total)
            if gpu is primary:
                _store(r, "gpu:primary:temp", temp)
                _store(r, "gpu:primary:usage", usage)
                _store(r, "gpu:primary:clock", clock)
                _store(r, "gpu:primary:power", power)
                _store(r, "gpu:primary:fan", fan)
                _store(r, "gpu:primary:vram_used", vram_used)
                _store(r, "gpu:primary:vram_total", vram_total)

        # Fans
        for fan in self._fans:
            _store(r, f"fan:{fan.key}:rpm", fan.rpm())
            _store(r, f"fan:{fan.key}:percent", fan.percent())

        # IO + time
        self._io.poll(r)
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

    def _poll_extra(self, readings: dict[str, float]) -> None:
        """Override to add OS-native readings not covered by cpu/memory/gpus/fans."""


# ── LinuxSensors — baseline + hwmon-discovered Linux sources ─────────


def build_linux_sensors() -> BaselineSensors:
    """Factory: scan hwmon + DRM + NVIDIA, compose a full Linux enumerator.

    Falls back to BaselineSensors if /sys/class/hwmon doesn't exist (VM,
    non-Linux accidentally calling this).
    """
    hwmon_devices = scan_hwmon_devices()
    psutil_cpu = PsutilCpu()
    cpu = HwmonCpu(psutil_cpu, find_cpu_temp_device(hwmon_devices))
    gpus: List[GpuSource] = []
    gpus.extend(discover_nvidia_gpus())
    gpus.extend(discover_amd_gpus(hwmon_devices))
    gpus.extend(discover_intel_gpus(hwmon_devices))
    fans = discover_fans(hwmon_devices)
    log.info("Linux sensors: cpu_temp=%s, gpus=%d, fans=%d",
             "yes" if cpu.temp() is not None else "no",
             len(gpus), len(fans))
    return BaselineSensors(cpu=cpu, memory=PsutilMemory(),
                           gpus=gpus, fans=fans)
