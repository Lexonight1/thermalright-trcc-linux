"""WindowsSensorSource — strategy-chain ABC for Windows sensor data sources.

Mirrors ``PlatformFactory`` / ``ProtocolFactory`` / ``DeviceFactory`` exactly:

    @WindowsSensorSource.register('hwinfo')
    class HWiNFOSource(WindowsSensorSource):
        priority = 10
        ...

The enumerator walks the registry in priority order and asks each source
``probe()``. Live sources ``contribute()`` their sensors. Adding a new
source is one new file with one decorator — zero touchpoints in the
enumerator or platform code.

Why a chain (and not just LHM):
- LHM bundles WinRing0, which Microsoft is increasingly flagging
  (see Neowin / Aqua-Computer threads). Single source of failure.
- HWiNFO64 (when the user has it) has its own signed driver and is the
  faster, more accurate source — but we can't redistribute it.
- ``MSAcpi_ThermalZoneTemperature`` is always available, works on a
  fraction of hardware, but never gets banned.

Layered priorities, fail-soft. We never have *nothing*.

Layer: adapter (Windows-specific). Allowed to import from sibling Windows
helpers (``_windows_wmi``) and core; never from other OS adapters.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from operator import attrgetter
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from trcc.legacy.adapters.system.windows.enumerator import WindowsSensorEnumerator

log = logging.getLogger(__name__)


class WindowsSensorSource(ABC):
    """One Windows sensor data source. Self-registers via ``@register('key')``.

    Subclass contract — three class attributes + two abstract methods:

    Attributes
    ----------
    priority : int
        Lower = tried first.  Reserve ranges so insertions don't reshuffle:
        ``10`` HWiNFO64, ``20`` LibreHardwareMonitor, ``30`` MSAcpi WMI.
    name : str
        Display name for logs ("HWiNFO64", "LibreHardwareMonitor", "MSAcpi").
    provides_gpu : bool
        Whether this source registers GPU temperature/usage sensors.  Used by
        the enumerator so the base pynvml fallback can skip when a higher-
        priority source already covered NVIDIA.

    Methods
    -------
    probe() -> bool
        Fast, non-destructive availability check.  No side effects on False;
        cheap setup (handle caching) on True is fine.
    contribute(enum) -> None
        Register ``SensorInfo`` entries on ``enum._sensors`` and bind a poll
        callback by overriding ``poll(enum, readings)`` — the enumerator
        invokes it each tick.
    stop() -> None
        Optional cleanup (close handles, terminate spawned subprocesses).
        Default no-op.
    """

    _registry: ClassVar[dict[str, type[WindowsSensorSource]]] = {}

    # Subclass-supplied class attributes (defaults are deliberately bad
    # so a forgotten override fails loudly).
    priority: ClassVar[int] = 1000
    name: ClassVar[str] = "<unnamed>"
    provides_gpu: ClassVar[bool] = False

    @classmethod
    def register(cls, key: str):
        """Mark a subclass as a registered source under ``key``.

        ``key`` is a short stable identifier ("lhm", "hwinfo", "msacpi") —
        used for log lines and de-duplication.  Re-registering a key
        replaces the prior entry (useful for tests that swap in fakes).
        """
        def deco(sub: type[WindowsSensorSource]) -> type[WindowsSensorSource]:
            cls._registry[key] = sub
            return sub
        return deco

    @classmethod
    def in_priority_order(cls) -> list[WindowsSensorSource]:
        """Return one fresh instance per registered source, lowest priority first.

        Iteration order is stable; ties broken by registration order via
        ``dict`` insertion semantics (deterministic on Python 3.7+).
        """
        return sorted(
            (sub() for sub in cls._registry.values()),
            key=attrgetter('priority'),
        )

    # ── Abstract API ───────────────────────────────────────────────────────

    @abstractmethod
    def probe(self) -> bool:
        """Is this source available right now?

        Called once per ``WindowsSensorEnumerator.discover()`` invocation.
        Must not raise — return ``False`` on any error.  Implementations
        may cache handles internally; ``contribute()`` will be called
        immediately after a truthy probe.
        """

    @abstractmethod
    def contribute(self, enum: WindowsSensorEnumerator) -> None:
        """Register this source's sensors on the enumerator.

        Append ``SensorInfo`` entries to ``enum._sensors``.  Bind any
        per-tick reading logic via ``enum._register_poll(self.poll)`` so
        the enumerator can invoke it each cycle.
        """

    def poll(self, enum: WindowsSensorEnumerator,
             readings: dict[str, float]) -> None:
        """Read live sensor values into the readings dict.

        Default no-op — sources that only register static sensors (e.g.
        date/time) don't need to override.  Sources with live data must
        override and have ``contribute()`` call ``enum._register_poll(self.poll)``.
        """

    def stop(self) -> None:
        """Cleanup hook — release handles, terminate subprocesses, etc.

        Called once on enumerator stop.  Default no-op.
        """


__all__ = ['WindowsSensorSource']
