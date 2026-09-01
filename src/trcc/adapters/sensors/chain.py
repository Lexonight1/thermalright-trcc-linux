"""Sensor strategy chains — try multiple sources, first non-None wins.

A chain *is* a ``CpuSource`` / ``GpuSource`` / ``MemorySource`` — it
satisfies the same role contract and can be DI'd into ``BaselineSensors``
in place of a single concrete source.  Per-OS factories build their
chain with native sources first, baseline (psutil + nvml) last::

    cpu = CpuSourceChain([HwinfoCpu(), LhmCpu(), WmiAcpiCpu(), PsutilCpu()])
    memory = MemorySourceChain([HwinfoMemory(), PsutilMemory()])

The chain itself is OS-blind — the Windows / macOS / BSD platforms each
construct one with their own sources.  Same pattern as legacy's
``WindowsSensorSource`` priority chain, generalized.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from ...core.logs import per_frame
from ...core.ports import CpuSource, GpuSource, MemorySource

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class CpuSourceChain(CpuSource):
    """Priority-ordered ``CpuSource`` chain.

    Each method walks ``sources`` in order and returns the first non-None
    reading.  Highest-priority source goes first; the baseline (psutil)
    should always be last so something is always available.
    """

    def __init__(self, sources: Sequence[CpuSource]) -> None:
        if not sources:
            raise ValueError("CpuSourceChain requires at least one source")
        self._sources: list[CpuSource] = list(sources)

    @property
    def name(self) -> str:
        """Display name — first source that actually reads a temperature.

        Falls back to the highest-priority source's name when nothing
        has read yet (e.g. at cold boot before the first poll).
        """
        for source in self._sources:
            if source.temp() is not None or source.usage() is not None:
                return source.name
        return self._sources[0].name

    def temp(self) -> float | None:
        frame_log.debug("CpuSourceChain.temp: called")
        return _first_not_none(self._sources, "temp")

    def usage(self) -> float | None:
        frame_log.debug("CpuSourceChain.usage: called")
        return _first_not_none(self._sources, "usage")

    def freq(self) -> float | None:
        frame_log.debug("CpuSourceChain.freq: called")
        return _first_not_none(self._sources, "freq")

    def power(self) -> float | None:
        frame_log.debug("CpuSourceChain.power: called")
        return _first_not_none(self._sources, "power")


class GpuSourceChain(GpuSource):
    """Priority-ordered ``GpuSource`` chain for one GPU's reading set.

    All sources in the chain must describe the same physical GPU —
    typically a primary CPU package on a system with multiple sensor
    backends (HWiNFO, LHM, NVML all reading the same NVIDIA card).
    ``key`` + ``name`` come from the first source; that's the canonical
    identity used by the aggregator's primary-GPU alias.
    """

    def __init__(self, sources: Sequence[GpuSource]) -> None:
        if not sources:
            raise ValueError("GpuSourceChain requires at least one source")
        self._sources: list[GpuSource] = list(sources)

    @property
    def key(self) -> str:
        return self._sources[0].key

    @property
    def name(self) -> str:
        return self._sources[0].name

    @property
    def is_discrete(self) -> bool:
        return self._sources[0].is_discrete

    def temp(self) -> float | None:
        frame_log.debug("GpuSourceChain.temp: key=%s", self._sources[0].key)
        return _first_not_none(self._sources, "temp")

    def usage(self) -> float | None:
        frame_log.debug("GpuSourceChain.usage: key=%s", self._sources[0].key)
        return _first_not_none(self._sources, "usage")

    def clock(self) -> float | None:
        frame_log.debug("GpuSourceChain.clock: key=%s", self._sources[0].key)
        return _first_not_none(self._sources, "clock")

    def power(self) -> float | None:
        frame_log.debug("GpuSourceChain.power: key=%s", self._sources[0].key)
        return _first_not_none(self._sources, "power")

    def fan(self) -> float | None:
        frame_log.debug("GpuSourceChain.fan: key=%s", self._sources[0].key)
        return _first_not_none(self._sources, "fan")

    def vram_used(self) -> float | None:
        frame_log.debug("GpuSourceChain.vram_used: key=%s", self._sources[0].key)
        return _first_not_none(self._sources, "vram_used")

    def vram_total(self) -> float | None:
        frame_log.debug("GpuSourceChain.vram_total: key=%s", self._sources[0].key)
        return _first_not_none(self._sources, "vram_total")


class MemorySourceChain(MemorySource):
    """Priority-ordered ``MemorySource`` chain."""

    def __init__(self, sources: Sequence[MemorySource]) -> None:
        if not sources:
            raise ValueError("MemorySourceChain requires at least one source")
        self._sources: list[MemorySource] = list(sources)

    def used(self) -> float | None:
        frame_log.debug("MemorySourceChain.used: called")
        return _first_not_none(self._sources, "used")

    def available(self) -> float | None:
        frame_log.debug("MemorySourceChain.available: called")
        return _first_not_none(self._sources, "available")

    def total(self) -> float | None:
        frame_log.debug("MemorySourceChain.total: called")
        return _first_not_none(self._sources, "total")

    def percent(self) -> float | None:
        frame_log.debug("MemorySourceChain.percent: called")
        return _first_not_none(self._sources, "percent")


def _first_not_none(sources: Sequence[object], method: str) -> float | None:
    """Walk *sources*, call ``getattr(source, method)()``, return first non-None.

    Exceptions from a source are caught + logged; the chain continues with
    the next source so one flaky strategy doesn't break the whole pipeline.
    """
    for source in sources:
        try:
            value = getattr(source, method)()
        except Exception:
            log.debug("Chain source %r raised on %s()",
                      type(source).__name__, method, exc_info=True)
            continue
        if value is not None:
            return value
    return None
