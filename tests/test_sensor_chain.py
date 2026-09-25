"""``CpuSourceChain`` / ``GpuSourceChain`` / ``MemorySourceChain``."""
from __future__ import annotations

import pytest

from trcc.adapters.sensors.chain import (
    CpuSourceChain,
    GpuSourceChain,
    MemorySourceChain,
)
from trcc.core.ports import CpuSource, GpuSource, MemorySource


class _StubCpu(CpuSource):
    def __init__(self, *, name: str = "stub",
                 temp: float | None = None,
                 usage: float | None = None,
                 freq: float | None = None,
                 power: float | None = None) -> None:
        self._name = name
        self._temp, self._usage, self._freq, self._power = temp, usage, freq, power

    @property
    def name(self) -> str:
        return self._name

    def temp(self) -> float | None:
        return self._temp

    def usage(self) -> float | None:
        return self._usage

    def freq(self) -> float | None:
        return self._freq

    def power(self) -> float | None:
        return self._power


class _StubGpu(GpuSource):
    def __init__(self, *, key: str = "stub:0", name: str = "stub gpu",
                 discrete: bool = True, **readings: float | None) -> None:
        self._key, self._name, self._discrete = key, name, discrete
        self._r = readings

    @property
    def key(self) -> str:
        return self._key

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_discrete(self) -> bool:
        return self._discrete

    def temp(self) -> float | None:
        return self._r.get("temp")

    def usage(self) -> float | None:
        return self._r.get("usage")

    def clock(self) -> float | None:
        return self._r.get("clock")

    def power(self) -> float | None:
        return self._r.get("power")

    def fan(self) -> float | None:
        return self._r.get("fan")

    def fan_rpm(self) -> float | None:
        return self._r.get("fan_rpm")

    def vram_used(self) -> float | None:
        return self._r.get("vram_used")

    def vram_total(self) -> float | None:
        return self._r.get("vram_total")


class _StubMemory(MemorySource):
    def __init__(self, **readings: float | None) -> None:
        self._r = readings

    def used(self) -> float | None:
        return self._r.get("used")

    def available(self) -> float | None:
        return self._r.get("available")

    def total(self) -> float | None:
        return self._r.get("total")

    def percent(self) -> float | None:
        return self._r.get("percent")


# ── Empty chains reject construction ─────────────────────────────────


def test_cpu_chain_requires_at_least_one_source() -> None:
    with pytest.raises(ValueError, match="at least one source"):
        CpuSourceChain([])


def test_gpu_chain_requires_at_least_one_source() -> None:
    with pytest.raises(ValueError, match="at least one source"):
        GpuSourceChain([])


def test_memory_chain_requires_at_least_one_source() -> None:
    with pytest.raises(ValueError, match="at least one source"):
        MemorySourceChain([])


# ── CpuSourceChain — priority order, first non-None wins ────────────


def test_cpu_chain_uses_first_non_none() -> None:
    chain = CpuSourceChain([
        _StubCpu(name="hi", temp=42.0, usage=None),
        _StubCpu(name="lo", temp=None, usage=55.0),
    ])
    assert chain.temp() == 42.0     # first source supplies temp
    assert chain.usage() == 55.0    # second source supplies usage


def test_cpu_chain_per_method_independence() -> None:
    """Each method walks independently — usage from source 2 doesn't block temp from source 0."""
    chain = CpuSourceChain([
        _StubCpu(temp=70.0),
        _StubCpu(usage=88.0),
        _StubCpu(freq=4200.0),
        _StubCpu(power=95.0),
    ])
    assert chain.temp() == 70.0
    assert chain.usage() == 88.0
    assert chain.freq() == 4200.0
    assert chain.power() == 95.0


def test_cpu_chain_returns_none_when_all_sources_miss() -> None:
    chain = CpuSourceChain([_StubCpu(), _StubCpu(), _StubCpu()])
    assert chain.temp() is None
    assert chain.usage() is None


def test_cpu_chain_exceptions_skipped_silently() -> None:
    """A flaky source raising mid-read should not break the chain."""

    class _Boom(_StubCpu):
        def temp(self) -> float | None:
            raise RuntimeError("backend died")

    chain = CpuSourceChain([_Boom(), _StubCpu(temp=66.0)])
    assert chain.temp() == 66.0


def test_cpu_chain_name_reflects_active_source() -> None:
    """``name`` picks the first source that produced ANY reading."""
    chain = CpuSourceChain([
        _StubCpu(name="cold"),                      # no readings
        _StubCpu(name="warm", temp=50.0),           # reads temp
    ])
    assert chain.name == "warm"


def test_cpu_chain_name_falls_back_to_first_when_all_cold() -> None:
    chain = CpuSourceChain([
        _StubCpu(name="hi"),
        _StubCpu(name="lo"),
    ])
    assert chain.name == "hi"


# ── GpuSourceChain — identity from first source, readings cascaded ──


def test_gpu_chain_identity_from_first_source() -> None:
    chain = GpuSourceChain([
        _StubGpu(key="nvidia:0", name="RTX 4090", discrete=True),
        _StubGpu(key="alt", name="other", discrete=False),
    ])
    assert chain.key == "nvidia:0"
    assert chain.name == "RTX 4090"
    assert chain.is_discrete is True


def test_gpu_chain_readings_cascade() -> None:
    chain = GpuSourceChain([
        _StubGpu(temp=65.0),
        _StubGpu(usage=88.0, power=320.0),
        _StubGpu(fan=24.0, fan_rpm=2400.0, vram_used=8192.0, vram_total=24576.0),
    ])
    assert chain.temp() == 65.0
    assert chain.usage() == 88.0
    assert chain.power() == 320.0
    assert chain.fan() == 24.0
    assert chain.fan_rpm() == 2400.0
    assert chain.vram_used() == 8192.0
    assert chain.vram_total() == 24576.0


# ── MemorySourceChain ───────────────────────────────────────────────


def test_memory_chain_first_non_none_per_method() -> None:
    chain = MemorySourceChain([
        _StubMemory(used=4096.0),
        _StubMemory(total=32768.0, percent=12.5),
    ])
    assert chain.used() == 4096.0
    assert chain.total() == 32768.0
    assert chain.percent() == 12.5
    assert chain.available() is None


# ── provides(): which quantities a chain actually reads ─────────────
#
# A chain overrides every quantity in order to FORWARD it, so the inherited
# ``QuantitySource.provides`` default — "did this class override the port?" —
# answers ``True`` for a chain no matter what its members can do.  These drive
# that directly, because every platform that chains (Windows / macOS / BSD) is
# a platform where unreadable quantities actually occur, while Linux (which the
# suite runs on) neither chains nor has any.  Without these the bug is invisible
# here and wrong everywhere else.


class _PartialCpu(CpuSource):
    """Reads temp ONLY — the other three are left to the port's default.

    Shaped after the real thing: ``SmcCpu`` / ``SysctlCpu`` / ``WmiAcpiCpu``
    each read 1 of CpuSource's 4.
    """

    @property
    def name(self) -> str:
        return "partial"

    def temp(self) -> float | None:
        return 41.0


class _BareGpu(GpuSource):
    """Reads NONE of the 7 optional quantities — shaped after
    ``WmiVideoControllerGpu``, which carried 7 ``return None`` stubs."""

    @property
    def key(self) -> str:
        return "bare:0"

    @property
    def name(self) -> str:
        return "bare gpu"

    @property
    def is_discrete(self) -> bool:
        return False


def test_a_source_reports_only_the_quantities_it_overrides() -> None:
    partial = _PartialCpu()
    assert partial.provides("temp") is True
    assert [q for q in ("usage", "freq", "power") if partial.provides(q)] == []


def test_an_abstract_quantity_always_reports_provided() -> None:
    """``rpm`` / ``key`` / ``name`` are abstract, so they cannot be unwritten."""
    assert _StubGpu().provides("is_discrete") is True


def test_provides_rejects_a_quantity_the_port_never_declared() -> None:
    """A typo must not read as a real "unsupported" — it would drop a sensor."""
    assert _StubGpu().provides("vram_totl") is False


def test_cpu_chain_provides_what_any_member_provides() -> None:
    """THE regression this pair exists for.

    Measured before the override existed: a chain around sources that read
    nothing reported every quantity as provided.
    """
    chain = CpuSourceChain([_PartialCpu(), _PartialCpu()])
    assert chain.provides("temp") is True
    assert [q for q in ("usage", "freq", "power") if chain.provides(q)] == []

    mixed = CpuSourceChain([_PartialCpu(), _StubCpu(usage=12.0)])
    assert [q for q in ("temp", "usage", "freq", "power")
            if mixed.provides(q)] == ["temp", "usage", "freq", "power"]


def test_gpu_chain_provides_what_any_member_provides() -> None:
    bare = GpuSourceChain([_BareGpu(), _BareGpu()])
    assert [q for q in ("temp", "usage", "clock", "power", "fan",
                        "vram_used", "vram_total") if bare.provides(q)] == []

    rescued = GpuSourceChain([_BareGpu(), _StubGpu()])
    assert rescued.provides("temp") is True


def test_every_delegating_chain_overrides_provides() -> None:
    """A future ``FanSourceChain`` must not inherit the wrong answer.

    Structural on purpose: the two behavioural tests above cover the chains
    that exist, and this covers the one somebody adds next.
    """
    from trcc.core.ports import QuantitySource

    forgot = [
        cls.__name__
        for cls in (CpuSourceChain, GpuSourceChain, MemorySourceChain)
        if issubclass(cls, QuantitySource) and "provides" not in vars(cls)
    ]
    assert not forgot, (
        "a delegating source that does not override `provides` reports its "
        "OWN overrides, so it claims to read every quantity its members "
        f"cannot: {forgot}"
    )
