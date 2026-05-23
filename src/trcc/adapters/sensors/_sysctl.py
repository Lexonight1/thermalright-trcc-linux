"""BSD sysctl-backed sensor source.

CPU temperature on the BSDs lives in sysctl under three different
names depending on the OS:

  FreeBSD   ``dev.cpu.N.temperature``       (one per core, value ``47.5C``)
  OpenBSD   ``hw.sensors.cpuN.temp0``       (one per cpu, value ``38.50 degC``)
  NetBSD    ``machdep.cpu_temperature``     (single value — added per reporter)

When ``dev.cpu`` temperature is absent (older boards, no ``coretemp``
driver loaded), FreeBSD still exposes ACPI thermal-zone readings at
``hw.acpi.thermal.tzN.temperature``; we parse those as a fallback so a
CPU temperature is reported on boards where only ACPI knows the die
temp.

Only temperature is exposed here; ``psutil`` covers usage / freq across
all three BSDs and goes in the chain right after this source.

DI seam: every call routes through a ``runner`` callable that returns
the raw ``sysctl -a`` output.  Production binds it to ``subprocess.run``;
tests inject canned strings so the parsing logic runs from Linux.
"""
from __future__ import annotations

import logging
import platform
import re
import subprocess
from collections.abc import Callable

from ...core.ports import CpuSource

log = logging.getLogger(__name__)


# (compiled_regex, value_scrub_chars) per OS — the regex captures core
# index group 1 + raw value group 2; the scrub strips trailing-unit
# noise like FreeBSD's ``C`` or OpenBSD's ``degC``.
_FREEBSD_RE = re.compile(
    r"^dev\.cpu\.(\d+)\.temperature:\s*([\d.]+)", re.MULTILINE,
)
_OPENBSD_RE = re.compile(
    r"^hw\.sensors\.cpu(\d+)\.temp0:\s*([\d.]+)", re.MULTILINE,
)
_NETBSD_RE = re.compile(
    r"^machdep\.cpu_temperature:\s*()([\d.]+)", re.MULTILINE,
)
# FreeBSD-only ACPI thermal-zone fallback.  Used when dev.cpu has no
# temperature lines (e.g. boards where ``coretemp`` is not loaded).
_FREEBSD_ACPI_TZ_RE = re.compile(
    r"^hw\.acpi\.thermal\.tz(\d+)\.temperature:\s*([\d.]+)", re.MULTILINE,
)


_BY_SYSTEM: dict[str, re.Pattern[str]] = {
    "FreeBSD":   _FREEBSD_RE,
    "OpenBSD":   _OPENBSD_RE,
    "NetBSD":    _NETBSD_RE,
    "DragonFly": _FREEBSD_RE,        # DragonFlyBSD uses FreeBSD's names
}

# Per-system fallback patterns, tried only when the primary returned no
# readings.  FreeBSD gets the ACPI thermal-zone parser.
_FALLBACK_BY_SYSTEM: dict[str, re.Pattern[str]] = {
    "FreeBSD":   _FREEBSD_ACPI_TZ_RE,
    "DragonFly": _FREEBSD_ACPI_TZ_RE,
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
        self._fallback_pattern = _FALLBACK_BY_SYSTEM.get(self._system)
        if self._pattern is None:
            log.debug("SysctlCpu: no temperature pattern for system %r",
                      self._system)

    @property
    def name(self) -> str:
        return f"sysctl ({self._system})"

    def temp(self) -> float | None:
        """Hottest CPU core temperature in °C, or None.

        Parses every line matching the OS-specific regex and returns
        the maximum.  When no cores report a numeric value (sysctl
        missing or empty output), tries the OS's fallback pattern —
        on FreeBSD that's the ACPI thermal-zone reading at
        ``hw.acpi.thermal.tzN.temperature`` — before giving up and
        letting the chain fall through.
        """
        if self._pattern is None:
            return None
        output = self._run()
        if not output:
            log.debug("SysctlCpu.temp: sysctl produced no output")
            return None
        best = _hottest(output, self._pattern)
        if best is not None:
            return best
        if self._fallback_pattern is not None:
            fallback = _hottest(output, self._fallback_pattern)
            if fallback is not None:
                log.info(
                    "SysctlCpu.temp: primary pattern empty, using ACPI "
                    "thermal-zone fallback (%.1f °C)", fallback,
                )
                return fallback
        log.debug("SysctlCpu.temp: no readings parsed from sysctl output")
        return None

    def usage(self) -> float | None:
        return None

    def freq(self) -> float | None:
        return None

    def power(self) -> float | None:
        return None
