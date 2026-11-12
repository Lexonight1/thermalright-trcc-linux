"""CPU clock on Windows, from the performance counters Task Manager reads.

psutil's ``cpu_freq().current`` on Windows is the NOMINAL clock -- psutil
documents it as real-time only on Linux -- so a Ryzen 7 5800X3D read 3401 MHz
at idle and under load alike (#339).  Windows publishes the clock that runs as
two PDH counters:

    \\Processor Information(_Total)\\Processor Frequency       nominal MHz
    \\Processor Information(_Total)\\% Processor Performance   cycles run per
                                                             nominal cycle, in %

Their product over 100 is the effective clock: the ratio Prometheus'
windows_exporter documents for the same pair (``docs/collector.cpu.md``:
frequency x processor performance / mperf).  ``% Processor Performance`` passes
100 under boost, so it is read with ``PDH_FMT_NOCAP100``.  ``_Total`` averages
every logical processor, which is what ``cpu:freq`` is on Linux too (psutil
averages ``scaling_cur_freq``).

No admin and no driver: ``win32pdh``, which the GPU usage reader already uses
(``_d3dkmt._Pdh``).  Off Windows it is not there, the source says so once and
reads nothing, and the chain falls through.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ...core.logs import per_frame, recurring_failure
from ...core.ports import CpuSource

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

FREQUENCY = r"\Processor Information(_Total)\Processor Frequency"
PERFORMANCE = r"\Processor Information(_Total)\% Processor Performance"


def _win32pdh() -> Any:
    """The ``win32pdh`` module -- ``ImportError`` off Windows."""
    import win32pdh  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    log.debug("_win32pdh: imported")
    return win32pdh


class _ClockCounters:
    """The two counters in one PDH query, primed: a rate needs two samples."""

    def __init__(self, pdh: Any) -> None:
        log.debug("_ClockCounters: opening the CPU clock query")
        self._pdh = pdh
        self._query = pdh.OpenQuery()
        self._frequency = pdh.AddEnglishCounter(self._query, FREQUENCY)
        self._performance = pdh.AddEnglishCounter(self._query, PERFORMANCE)
        pdh.CollectQueryData(self._query)

    def mhz(self) -> float:
        """Nominal MHz x performance / 100, since the last call."""
        pdh = self._pdh
        pdh.CollectQueryData(self._query)
        _, nominal = pdh.GetFormattedCounterValue(self._frequency, pdh.PDH_FMT_DOUBLE)
        _, percent = pdh.GetFormattedCounterValue(
            self._performance, pdh.PDH_FMT_DOUBLE | pdh.PDH_FMT_NOCAP100)
        frame_log.debug("_ClockCounters.mhz: %.0f MHz nominal x %.1f %%", nominal, percent)
        return nominal * percent / 100.0


class PdhCpu(CpuSource):
    """The CPU clock from Windows' performance counters -- nothing else."""

    def __init__(self, pdh: Callable[[], Any] = _win32pdh) -> None:
        log.debug("PdhCpu.__init__")
        self._pdh = pdh
        self._counters: _ClockCounters | None = None
        self._settled = False

    @property
    def name(self) -> str:
        frame_log.debug("PdhCpu.name")
        return "Windows performance counters"

    def _ready(self) -> _ClockCounters | None:
        """The counters, opened on the first read; None where they cannot be."""
        if not self._settled:
            self._settled = True
            try:
                self._counters = _ClockCounters(self._pdh())
                log.info("PdhCpu: the CPU clock from %s x %s", FREQUENCY, PERFORMANCE)
            except Exception as e:
                # ImportError off Windows; a pywintypes.error where the
                # Processor Information counters are missing or disabled.
                log.info("PdhCpu: no CPU clock counters -- %s: %s", type(e).__name__, e)
        return self._counters

    def freq(self) -> float | None:
        frame_log.debug("PdhCpu.freq")
        counters = self._ready()
        if counters is None:
            return None
        try:
            mhz = counters.mhz()
        except Exception:
            # A rate read too soon after the last one, or the counters gone.
            recurring_failure(log, "PdhCpu.freq: read failed")
            return None
        if mhz <= 0:
            frame_log.debug("PdhCpu.freq: %.1f MHz is no reading", mhz)
            return None
        return round(mhz)
