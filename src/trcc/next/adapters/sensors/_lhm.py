"""LibreHardwareMonitor sensor sources — read live values from LHM's WMI.

When ``LibreHardwareMonitor.exe`` is running (user-installed or future
bundled deploy), it publishes the full sensor tree to the
``root\\LibreHardwareMonitor`` WMI namespace.  This module reads from
that namespace and exposes the data through next/'s ``CpuSource`` /
``GpuSource`` ABCs.

Read-only: this port does NOT spawn LHM itself.  Bundling + autostart
is a deployment concern handled by the legacy installer today; next/'s
first cut consumes whatever LHM the user already has running and falls
through the chain when it's not there.  A future ``LhmSubprocess``
helper can layer spawning on top without changing this module.

Wire format ported from legacy ``src/trcc/adapters/system/windows/
sources/lhm.py``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ...core.ports import CpuSource, GpuSource

log = logging.getLogger(__name__)


_LHM_NAMESPACE = "root\\LibreHardwareMonitor"

# Sensor.SensorType strings emitted by LHM.  Stable across LHM versions.
_TYPE_TEMP = "Temperature"
_TYPE_LOAD = "Load"
_TYPE_CLOCK = "Clock"
_TYPE_POWER = "Power"
_TYPE_FAN = "Fan"
_TYPE_SMALL_DATA = "SmallData"   # used for vram_used / vram_total in MB
_TYPE_DATA = "Data"              # MemUsed/MemAvailable in GB

# Hardware.HardwareType strings.  CPU = exactly "Cpu"; GPUs come in
# vendor-flavoured variants (GpuNvidia, GpuAmd, GpuIntel) which the
# helper matches via ``startswith("Gpu")``.
_HW_CPU = "Cpu"
_HW_GPU_PREFIX = "Gpu"


def _default_handle_factory() -> Any:
    """Probe the ``root\\LibreHardwareMonitor`` namespace via WMI.

    Returns ``None`` when LHM isn't running or the ``wmi`` package isn't
    installed (non-Windows).  No subprocess spawn — that's deployment.
    """
    try:
        import wmi  # pyright: ignore[reportMissingImports]
    except ImportError:
        return None
    try:
        ns = wmi.WMI(namespace=_LHM_NAMESPACE)
        # Touch the namespace once so we fail fast when LHM isn't running.
        # ``Hardware()`` returns [] cheaply when LHM is up but idle.
        list(ns.Hardware())
    except Exception:
        log.debug("LHM namespace unavailable", exc_info=True)
        return None
    return ns


# =========================================================================
# Helpers — find sensors of a given type on a Hardware row
# =========================================================================


def _sensors_for(ns: Any, hw_row: Any, sensor_type: str) -> list[Any]:
    """List sensors of ``sensor_type`` whose parent is ``hw_row``.

    LHM's WMI schema: ``Sensor(Parent=<hw.Identifier>)``.  Filtering by
    SensorType after the fact (LHM doesn't accept it as a WHERE clause).
    """
    try:
        return [s for s in ns.Sensor(Parent=hw_row.Identifier)
                if str(s.SensorType) == sensor_type]
    except Exception:
        log.debug("LHM sensor query failed for %s/%s",
                  hw_row.Identifier, sensor_type, exc_info=True)
        return []


def _max_value(sensors: list[Any]) -> float | None:
    """Return the maximum ``Value`` field across a list of LHM sensors."""
    values = [float(s.Value) for s in sensors if s.Value is not None]
    return max(values) if values else None


def _sum_value(sensors: list[Any]) -> float | None:
    values = [float(s.Value) for s in sensors if s.Value is not None]
    return sum(values) if values else None


def _named_value(sensors: list[Any], name_contains: str) -> float | None:
    """First sensor whose Name contains *name_contains* (case-insensitive)."""
    needle = name_contains.lower()
    for s in sensors:
        if s.Value is None:
            continue
        if needle in str(s.Name).lower():
            return float(s.Value)
    return None


# =========================================================================
# LhmCpu
# =========================================================================


class LhmCpu(CpuSource):
    """CPU readings via LHM.

    Each method query the WMI namespace for the CPU's sensors.  Returns
    ``None`` when LHM isn't running, or when LHM is running but the CPU
    sensor isn't populated yet (cold boot, before the first refresh).
    """

    def __init__(
        self,
        *,
        handle_factory: Callable[[], Any] = _default_handle_factory,
    ) -> None:
        self._ns: Any = handle_factory()
        self._name: str = "LibreHardwareMonitor (CPU)"
        self._cache_cpu_row()

    def _cache_cpu_row(self) -> None:
        """Find + cache the CPU Hardware row's Identifier + Name."""
        self._cpu_id: str | None = None
        if self._ns is None:
            return
        try:
            for hw in self._ns.Hardware():
                if str(hw.HardwareType) == _HW_CPU:
                    self._cpu_id = str(hw.Identifier)
                    self._name = f"LHM: {hw.Name}"
                    return
        except Exception:
            log.debug("LHM cpu row enumeration failed", exc_info=True)

    @property
    def name(self) -> str:
        return self._name

    def _cpu_row(self) -> Any | None:
        if self._ns is None or self._cpu_id is None:
            return None
        try:
            rows = list(self._ns.Hardware(Identifier=self._cpu_id))
        except Exception:
            return None
        return rows[0] if rows else None

    def temp(self) -> float | None:
        """Hottest CPU core temperature in °C."""
        if (row := self._cpu_row()) is None:
            return None
        return _max_value(_sensors_for(self._ns, row, _TYPE_TEMP))

    def usage(self) -> float | None:
        """CPU total load 0-100 — LHM names this 'CPU Total'."""
        if (row := self._cpu_row()) is None:
            return None
        loads = _sensors_for(self._ns, row, _TYPE_LOAD)
        # Prefer the explicit "CPU Total" sensor; fall back to max across cores.
        return _named_value(loads, "total") or _max_value(loads)

    def freq(self) -> float | None:
        """Highest CPU clock in MHz."""
        if (row := self._cpu_row()) is None:
            return None
        return _max_value(_sensors_for(self._ns, row, _TYPE_CLOCK))

    def power(self) -> float | None:
        """Package power draw in W — LHM names this 'CPU Package'."""
        if (row := self._cpu_row()) is None:
            return None
        powers = _sensors_for(self._ns, row, _TYPE_POWER)
        return _named_value(powers, "package") or _max_value(powers)


# =========================================================================
# LhmGpu — one per LHM-detected GPU row
# =========================================================================


class LhmGpu(GpuSource):
    """GPU readings via LHM.

    Constructed per Hardware row that matches a ``Gpu*`` HardwareType.
    Multiple GPUs (e.g. iGPU + dGPU) get one instance each, keyed by
    LHM's Identifier so the aggregator can match by key.
    """

    def __init__(
        self,
        hardware_identifier: str,
        display_name: str,
        *,
        discrete: bool,
        handle_factory: Callable[[], Any] = _default_handle_factory,
    ) -> None:
        self._ns: Any = handle_factory()
        self._id = hardware_identifier
        self._display_name = display_name
        self._discrete = discrete

    @property
    def key(self) -> str:
        # LHM identifiers look like "/gpu-nvidia/0" — normalize to a
        # vendor key matching the rest of next/ ("nvidia:0").
        ident = self._id.lower().lstrip("/")
        if ident.startswith("gpu-nvidia"):
            return f"nvidia:{ident.rsplit('/', 1)[-1]}"
        if ident.startswith("gpu-amd"):
            return f"amd:{ident.rsplit('/', 1)[-1]}"
        if ident.startswith("gpu-intel"):
            return f"intel:{ident.rsplit('/', 1)[-1]}"
        return f"lhm:{ident}"

    @property
    def name(self) -> str:
        return self._display_name

    @property
    def is_discrete(self) -> bool:
        return self._discrete

    def _row(self) -> Any | None:
        if self._ns is None:
            return None
        try:
            rows = list(self._ns.Hardware(Identifier=self._id))
        except Exception:
            return None
        return rows[0] if rows else None

    def temp(self) -> float | None:
        if (row := self._row()) is None:
            return None
        temps = _sensors_for(self._ns, row, _TYPE_TEMP)
        # Prefer "GPU Core"; fall back to max across all GPU temps.
        return _named_value(temps, "core") or _max_value(temps)

    def usage(self) -> float | None:
        if (row := self._row()) is None:
            return None
        loads = _sensors_for(self._ns, row, _TYPE_LOAD)
        return _named_value(loads, "core") or _max_value(loads)

    def clock(self) -> float | None:
        if (row := self._row()) is None:
            return None
        clocks = _sensors_for(self._ns, row, _TYPE_CLOCK)
        return _named_value(clocks, "core") or _max_value(clocks)

    def power(self) -> float | None:
        if (row := self._row()) is None:
            return None
        return _max_value(_sensors_for(self._ns, row, _TYPE_POWER))

    def fan(self) -> float | None:
        if (row := self._row()) is None:
            return None
        return _max_value(_sensors_for(self._ns, row, _TYPE_FAN))

    def vram_used(self) -> float | None:
        if (row := self._row()) is None:
            return None
        return _named_value(
            _sensors_for(self._ns, row, _TYPE_SMALL_DATA), "used",
        )

    def vram_total(self) -> float | None:
        if (row := self._row()) is None:
            return None
        return _named_value(
            _sensors_for(self._ns, row, _TYPE_SMALL_DATA), "total",
        )


# =========================================================================
# discover_lhm_gpus — one constructor per GPU row LHM reports
# =========================================================================


def discover_lhm_gpus(
    *,
    handle_factory: Callable[[], Any] = _default_handle_factory,
) -> list[LhmGpu]:
    """Enumerate GPUs that LHM is currently reporting on.

    Returns ``[]`` when LHM isn't running.  Each returned GpuSource is
    independently queryable; the chain wraps them with vendor-native
    sources (NVML, etc.) for higher-quality readings.
    """
    ns = handle_factory()
    if ns is None:
        return []
    out: list[LhmGpu] = []
    try:
        for hw in ns.Hardware():
            ht = str(hw.HardwareType)
            if not ht.startswith(_HW_GPU_PREFIX):
                continue
            # iGPUs (typically GpuIntel) → non-discrete; dGPUs discrete.
            discrete = ht != "GpuIntel"
            out.append(LhmGpu(
                hardware_identifier=str(hw.Identifier),
                display_name=str(hw.Name),
                discrete=discrete,
                handle_factory=handle_factory,
            ))
    except Exception:
        log.debug("LHM GPU enumeration failed", exc_info=True)
    return out
