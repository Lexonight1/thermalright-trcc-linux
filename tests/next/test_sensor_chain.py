"""``CpuSourceChain`` / ``GpuSourceChain`` / ``MemorySourceChain``."""
from __future__ import annotations

import pytest

from trcc.next.adapters.sensors.chain import (
    CpuSourceChain,
    GpuSourceChain,
    MemorySourceChain,
)
from trcc.next.core.ports import CpuSource, GpuSource, MemorySource


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
        _StubGpu(fan=2400.0, vram_used=8192.0, vram_total=24576.0),
    ])
    assert chain.temp() == 65.0
    assert chain.usage() == 88.0
    assert chain.power() == 320.0
    assert chain.fan() == 2400.0
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
