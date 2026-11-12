"""PdhCpu on Linux, over a fake ``win32pdh`` that behaves as PDH does where it
matters: a percent counter is capped at 100 unless ``PDH_FMT_NOCAP100`` asks
otherwise (#339)."""
from __future__ import annotations

from typing import Any

from trcc.adapters.sensors._pdh_cpu import FREQUENCY, PERFORMANCE, PdhCpu
from trcc.adapters.sensors.chain import CpuSourceChain
from trcc.core.ports import CpuSource


class _FakePdh:
    """``win32pdh`` with one machine's two clock counters."""

    PDH_FMT_DOUBLE = 0x00000200
    PDH_FMT_NOCAP100 = 0x00008000

    def __init__(self, nominal: float, performance: float) -> None:
        self.values = {FREQUENCY: nominal, PERFORMANCE: performance}
        self.added: list[str] = []
        self.collects = 0
        self.fail = False

    def OpenQuery(self) -> str:
        return "query"

    def AddEnglishCounter(self, query: str, path: str) -> str:
        self.added.append(path)
        return path

    def CollectQueryData(self, query: str) -> None:
        self.collects += 1
        if self.fail:
            raise RuntimeError("The data is not valid (PDH_INVALID_DATA)")

    def GetFormattedCounterValue(self, counter: str, fmt: int) -> tuple[int, float]:
        value = self.values[counter]
        if counter == PERFORMANCE and not fmt & self.PDH_FMT_NOCAP100:
            value = min(value, 100.0)
        return fmt, value


def test_a_boosting_cpu_reads_its_running_clock_not_its_base() -> None:
    """#339: a 5800X3D read 3401 MHz, its base clock, at any load.

    MUTATION CHECK: drop PDH_FMT_NOCAP100 -> 3400, the clock never passes base."""
    pdh = _FakePdh(nominal=3400.0, performance=133.3)
    assert PdhCpu(lambda: pdh).freq() == 4532
    assert pdh.added == [r"\Processor Information(_Total)\Processor Frequency",
                         r"\Processor Information(_Total)\% Processor Performance"]


def test_an_idle_cpu_reads_below_base() -> None:
    assert PdhCpu(lambda: _FakePdh(nominal=3400.0, performance=60.0)).freq() == 2040


def test_the_counters_are_primed_once_and_sampled_each_read() -> None:
    """A rate needs two samples: one when the query opens, one per read."""
    pdh = _FakePdh(nominal=3400.0, performance=100.0)
    cpu = PdhCpu(lambda: pdh)
    for _ in range(3):
        cpu.freq()
    assert pdh.collects == 4


def test_no_win32pdh_is_no_reading_and_is_not_retried() -> None:
    """Off Windows the import fails once; asking again every tick would retry
    an import per sensor read.

    MUTATION CHECK: drop ``self._settled = True`` in PdhCpu._ready -> 3 calls."""
    calls: list[int] = []

    def missing() -> Any:
        calls.append(1)
        raise ImportError("No module named 'win32pdh'")

    cpu = PdhCpu(missing)
    assert [cpu.freq() for _ in range(3)] == [None, None, None]
    assert calls == [1]


def test_a_failed_or_empty_sample_is_no_reading() -> None:
    """A rate read too soon, or a counter answering 0, is not a clock of 0.
    One good read first: a failure while the query opens is a different path
    (no counters at all), and this test once passed without reaching either.

    MUTATION CHECK: drop the ``mhz <= 0`` guard -> 0."""
    pdh = _FakePdh(nominal=3400.0, performance=100.0)
    cpu = PdhCpu(lambda: pdh)
    assert cpu.freq() == 3400
    pdh.fail = True
    assert cpu.freq() is None
    pdh.fail, pdh.values[PERFORMANCE] = False, 0.0
    assert cpu.freq() is None


def test_it_says_it_reads_the_clock_and_nothing_else() -> None:
    cpu = PdhCpu(lambda: _FakePdh(nominal=3400.0, performance=100.0))
    assert [q for q in ("temp", "usage", "freq", "power") if cpu.provides(q)] == ["freq"]


class _NominalClock(CpuSource):
    """psutil on Windows: the base clock, whatever the CPU does."""

    @property
    def name(self) -> str:
        return "psutil"

    def freq(self) -> float | None:
        return 3401.0


def test_in_the_chain_the_running_clock_wins_over_psutils_base() -> None:
    pdh = _FakePdh(nominal=3400.0, performance=125.0)
    assert CpuSourceChain([PdhCpu(lambda: pdh), _NominalClock()]).freq() == 4250
