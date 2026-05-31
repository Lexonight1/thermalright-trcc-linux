"""BSD sysctl-backed sensor sources.

CPU temperature on the BSDs lives in sysctl under three different
names depending on the OS:

  FreeBSD   ``dev.cpu.N.temperature``       (one per core, value ``47.5C``)
  OpenBSD   ``hw.sensors.cpuN.temp0``       (one per cpu, value ``38.50 degC``)
  NetBSD    ``machdep.cpu_temperature``     (single value — added per reporter)

ACPI thermal zones (``hw.acpi.thermal.tzN.temperature`` /
``hw.sensors.acpitzN.temp0``) are intentionally EXCLUDED — they can
report chassis / GPU / system-board temperatures, not specifically the
CPU die.

Fan RPM on OpenBSD lives in the structured ``hw.sensors.*`` framework
(see asiabsdcon2009 sensors paper).  Every fan driver (``lm``, ``it``,
``ipmi``, ``aibs``, ``aiboost``, etc.) lays its fans out at
``hw.sensors.<driver><idx>.fan<n>: <RPM> RPM`` — same wire shape
across drivers, so one regex parses them all.  Discovered fans appear
in :class:`~trcc.adapters.sensors.aggregator.BaselineSensors` as
``fan:sysctl:<driver><idx>:fan<n>:rpm`` readings.

FreeBSD has no universal fan sysctl — `aibs(4)` exposes ASUS-board
fans at ``dev.aibs.0.fan.N`` but it's not standard; users on other
boards get no fan readings.  We don't ship a FreeBSD fan parser for
that reason; donor-hardware-driven addition if a reporter asks.

Only temperature + fan RPM are exposed here; ``psutil`` covers usage /
freq + memory across all three BSDs and goes in the chain right after
the temperature source.

DI seam: every call routes through a ``runner`` callable that returns
the raw ``sysctl -a`` output.  Production binds it to ``subprocess.run``;
tests inject canned strings so the parsing logic runs from Linux.
"""
from __future__ import annotations

import logging
import platform
import re
import subprocess
import time
from collections.abc import Callable

from ...core.ports import CpuSource, FanSource

log = logging.getLogger(__name__)


# Per-OS CPU temperature regex.  The regex captures core index in
# group 1 + raw value in group 2; FreeBSD/OpenBSD trail unit chars
# (``C`` / `` degC``) which ``float()`` ignores when parsed from the
# numeric portion only.
_FREEBSD_RE = re.compile(
    r"^dev\.cpu\.(\d+)\.temperature:\s*([\d.]+)", re.MULTILINE,
)
_OPENBSD_RE = re.compile(
    r"^hw\.sensors\.cpu(\d+)\.temp0:\s*([\d.]+)", re.MULTILINE,
)
_NETBSD_RE = re.compile(
    r"^machdep\.cpu_temperature:\s*()([\d.]+)", re.MULTILINE,
)


_BY_SYSTEM: dict[str, re.Pattern[str]] = {
    "FreeBSD":   _FREEBSD_RE,
    "OpenBSD":   _OPENBSD_RE,
    "NetBSD":    _NETBSD_RE,
    "DragonFly": _FREEBSD_RE,        # DragonFlyBSD uses FreeBSD's names
}


def _default_runner() -> str:
    """Run ``sysctl -a`` and return its stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["sysctl", "-a"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.debug("sysctl -a failed", exc_info=True)
        return ""
    if result.returncode != 0:
        log.debug("sysctl -a exited %d", result.returncode)
        return ""
    return result.stdout


def _pattern_for(system: str) -> re.Pattern[str] | None:
    return _BY_SYSTEM.get(system)


def _hottest(output: str, pattern: re.Pattern[str]) -> float | None:
    """Parse all matches and return the max numeric value, or None."""
    best: float | None = None
    for match in pattern.finditer(output):
        try:
            value = float(match.group(2))
        except (TypeError, ValueError):
            continue
        if best is None or value > best:
            best = value
    return best


class SysctlCpu(CpuSource):
    """CPU temperature via sysctl on BSD.

    Returns ``None`` for everything except ``temp()`` — the chain
    relays usage/freq/power to ``PsutilCpu`` after this source.
    """

    def __init__(
        self,
        *,
        runner: Callable[[], str] = _default_runner,
        system: str | None = None,
    ) -> None:
        self._run = runner
        self._system = system if system is not None else platform.system()
        self._pattern = _pattern_for(self._system)
        if self._pattern is None:
            log.debug("SysctlCpu: no temperature pattern for system %r",
                      self._system)

    @property
    def name(self) -> str:
        return f"sysctl ({self._system})"

    def temp(self) -> float | None:
        """Hottest CPU core temperature in °C, or None.

        Parses every line matching the OS-specific CPU-core regex and
        returns the maximum.  ACPI thermal zones are NOT considered
        here — they live behind ``hw.acpi.thermal.tzN`` /
        ``hw.sensors.acpitzN`` and report non-CPU temperatures (system
        board, chassis, GPU); G7's system-temp source handles those.
        """
        if self._pattern is None:
            return None
        output = self._run()
        if not output:
            log.debug("SysctlCpu.temp: sysctl produced no output")
            return None
        best = _hottest(output, self._pattern)
        if best is None:
            log.debug("SysctlCpu.temp: no CPU readings parsed")
        return best

    def usage(self) -> float | None:
        return None

    def freq(self) -> float | None:
        return None

    def power(self) -> float | None:
        return None


# ── OpenBSD hw.sensors fan parser ────────────────────────────────────


# Driver index group 1 (e.g. "lm0", "it0", "ipmi0", "aibs0"); fan
# slot group 2; RPM group 3.  Driver name = letters+digits anchored at
# end so e.g. ``lm0`` and ``lm10`` both match.
_OPENBSD_FAN_RE = re.compile(
    r"^hw\.sensors\.([a-z]+\d+)\.fan(\d+):\s*(\d+)\s+RPM", re.MULTILINE,
)


class _SysctlSnapshot:
    """Shared, TTL-cached ``sysctl -a`` output.

    Multiple sensor sources (CPU, fans, future memory/voltage) share
    one snapshot so a polling tick costs one ``sysctl -a`` call, not
    N+1.  TTL defaults to 1.0 s — slightly under the BaselineSensors
    poll interval (2.0 s) so each poll always sees fresh data while
    siblings within a tick share the same read.

    DI seam: ``runner`` returns the raw output; tests inject canned
    strings.  ``clock`` lets tests advance time deterministically.
    """

    __slots__ = ("_cached", "_cached_at", "_clock", "_run", "_ttl")

    def __init__(
        self,
        *,
        runner: Callable[[], str] | None = None,
        ttl_s: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Resolve runner at call time so tests can monkeypatch
        # ``_default_runner`` without rewiring construction sites.
        self._run = runner if runner is not None else _default_runner
        self._ttl = ttl_s
        self._clock = clock
        self._cached: str = ""
        self._cached_at: float = -1.0

    def output(self) -> str:
        now = self._clock()
        if self._cached_at >= 0 and (now - self._cached_at) < self._ttl:
            return self._cached
        self._cached = self._run()
        self._cached_at = now
        return self._cached


class SysctlFan(FanSource):
    """One fan via OpenBSD ``hw.sensors.<driver><idx>.fan<n>``."""

    __slots__ = ("_driver", "_idx", "_key", "_label", "_snapshot")

    def __init__(
        self,
        snapshot: _SysctlSnapshot,
        driver: str,
        idx: int,
        label: str | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._driver = driver
        self._idx = idx
        self._label = label or f"{driver} fan{idx}"
        self._key = f"sysctl:{driver}:fan{idx}"

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return self._label

    def rpm(self) -> int | None:
        output = self._snapshot.output()
        for match in _OPENBSD_FAN_RE.finditer(output):
            if match.group(1) == self._driver and int(match.group(2)) == self._idx:
                try:
                    return int(match.group(3))
                except (TypeError, ValueError):
                    return None
        return None

    def percent(self) -> float | None:
        # OpenBSD hw.sensors framework doesn't expose PWM duty cycle.
        return None


def discover_openbsd_fans(
    *,
    snapshot: _SysctlSnapshot | None = None,
    runner: Callable[[], str] | None = None,
) -> list[FanSource]:
    """Enumerate OpenBSD ``hw.sensors.*.fanN`` sources.

    Returns an empty list on non-OpenBSD systems (regex won't match) or
    when no fan-exposing driver is loaded.  Each fan holds a reference
    to a shared snapshot so a poll tick runs ``sysctl -a`` once even
    with many fans.
    """
    log.info("discover_openbsd_fans: called")
    snap = snapshot or _SysctlSnapshot(runner=runner or _default_runner)
    output = snap.output()
    seen: dict[tuple[str, int], SysctlFan] = {}
    for match in _OPENBSD_FAN_RE.finditer(output):
        driver = match.group(1)
        idx = int(match.group(2))
        if (driver, idx) in seen:
            continue
        seen[(driver, idx)] = SysctlFan(snap, driver, idx)
    log.info("OpenBSD hw.sensors fans discovered: %d", len(seen))
    return list(seen.values())
