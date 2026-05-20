"""macOS sensor factory — SMC temperature on top of the psutil baseline.

Priority chain:

  1. SmcCpu / SmcGpu — temperature via Apple SMC (IOKit).  Intel keys
                       enabled by default; Apple Silicon keys behind
                       ``TRCC_NEXT_APPLE_SILICON_SMC=1`` until a real
                       reporter confirms them on Apple Silicon hardware.
  2. PsutilCpu       — usage / freq baseline; covers all Macs.

Memory + GPU baseline mirror Linux / Windows / BSD: psutil for memory,
NVML for any discrete NVIDIA (rare on Mac), no fans wired (SMC FNum
discovery is reporter-deferred).
"""
from __future__ import annotations

import logging
import os
import platform
from collections.abc import Iterable

from ...core.ports import CpuSource, GpuSource
from ._smc import (
    APPLE_SILICON_CPU_TEMP_KEYS,
    APPLE_SILICON_GPU_TEMP_KEYS,
    INTEL_CPU_TEMP_KEYS,
    INTEL_GPU_TEMP_KEYS,
    SMCClient,
    SmcClientPort,
)
from .aggregator import BaselineSensors
from .chain import CpuSourceChain
from .nvml import discover_nvidia_gpus
from .psutil_sources import PsutilCpu, PsutilMemory

log = logging.getLogger(__name__)


_APPLE_SILICON_ENV_FLAG = "TRCC_NEXT_APPLE_SILICON_SMC"


def _apple_silicon_enabled() -> bool:
    """True when ``TRCC_NEXT_APPLE_SILICON_SMC=1`` is set.

    Apple Silicon SMC keys are undocumented + chip-revision-specific;
    we ship the table but keep it opt-in until a reporter confirms it
    on each chip generation.  Intel keys ship enabled by default.
    """
    return os.environ.get(_APPLE_SILICON_ENV_FLAG) == "1"


def _select_cpu_temp_keys() -> tuple[str, ...]:
    keys: list[str] = list(INTEL_CPU_TEMP_KEYS)
    if _apple_silicon_enabled():
        keys.extend(APPLE_SILICON_CPU_TEMP_KEYS)
    return tuple(keys)


def _select_gpu_temp_keys() -> tuple[str, ...]:
    keys: list[str] = list(INTEL_GPU_TEMP_KEYS)
    if _apple_silicon_enabled():
        keys.extend(APPLE_SILICON_GPU_TEMP_KEYS)
    return tuple(keys)


# =========================================================================
# SmcCpu — hottest temperature across a candidate key set
# =========================================================================


class SmcCpu(CpuSource):
    """CPU temperature via Apple SMC.

    Probes every CPU temperature key in the candidate list and returns
    the hottest reading — matches the legacy ``MacOSSensorEnumerator``
    behavior + how every other SMC tool (Stats / iStat / iSMC) reports
    "the" CPU temperature on machines with multiple sensors.

    Returns ``None`` for usage / freq / power; ``PsutilCpu`` covers
    those after this source in the chain.
    """

    def __init__(
        self,
        client: SmcClientPort | None = None,
        *,
        keys: Iterable[str] | None = None,
    ) -> None:
        self._client: SmcClientPort = client if client is not None else SMCClient()
        self._keys: tuple[str, ...] = (
            tuple(keys) if keys is not None else _select_cpu_temp_keys()
        )
        # Lazy-open at construction so the chain doesn't pay the IOKit
        # cost per tick; failure is silent (chain falls through).
        if not self._client.connected:
            self._client.open()

    @property
    def name(self) -> str:
        return "Apple SMC (CPU)"

    def temp(self) -> float | None:
        """Hottest valid SMC reading across the candidate keys."""
        if not self._client.connected:
            return None
        hottest: float | None = None
        for key in self._keys:
            value = self._client.read_key_float(key)
            if value is None:
                continue
            # SMC occasionally returns sentinel garbage (0, huge negatives,
            # NaN) for unimplemented keys; clamp to a sane sensor range
            # so the chain doesn't surface noise as "real" temperature.
            if not (-40.0 <= value <= 150.0):
                continue
            if hottest is None or value > hottest:
                hottest = value
        return hottest

    def usage(self) -> float | None:
        return None

    def freq(self) -> float | None:
        return None

    def power(self) -> float | None:
        return None


# =========================================================================
# SmcGpu — single GPU view via SMC
# =========================================================================


class SmcGpu(GpuSource):
    """Per-Mac GPU temperature via SMC.

    Apple Silicon Macs have one integrated GPU + no discrete GPU
    socket; Intel Macs often have Iris + an optional dGPU.  We expose
    a single ``intel:0`` key when running on an Intel SMC + a single
    ``apple:0`` when running on Apple Silicon — the aggregator collides
    with NVML-discovered NVIDIA cards by vendor key, so a Mac Pro with
    a discrete card still gets two GPU entries.
    """

    def __init__(
        self,
        client: SmcClientPort | None = None,
        *,
        keys: Iterable[str] | None = None,
        key: str = "intel:0",
        name: str = "Apple SMC (GPU)",
        discrete: bool = False,
    ) -> None:
        self._client: SmcClientPort = client if client is not None else SMCClient()
        self._keys: tuple[str, ...] = (
            tuple(keys) if keys is not None else _select_gpu_temp_keys()
        )
        self._key = key
        self._name = name
        self._discrete = discrete
        if not self._client.connected:
            self._client.open()

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
        if not self._client.connected:
            return None
        hottest: float | None = None
        for key in self._keys:
            value = self._client.read_key_float(key)
            if value is None:
                continue
            if not (-40.0 <= value <= 150.0):
                continue
            if hottest is None or value > hottest:
                hottest = value
        return hottest

    # SMC doesn't expose GPU usage / clock / power / fan / vram in any
    # consistent way across Mac generations — those fall through.
    def usage(self) -> float | None: return None
    def clock(self) -> float | None: return None
    def power(self) -> float | None: return None
    def fan(self) -> float | None: return None
    def vram_used(self) -> float | None: return None
    def vram_total(self) -> float | None: return None


# =========================================================================
# Factory
# =========================================================================


def _gpu_chain_key_and_name() -> tuple[str, str]:
    """Pick the GpuSource key + display name based on Mac architecture."""
    if platform.machine() == "arm64":
        return "apple:0", "Apple Silicon GPU"
    return "intel:0", "Intel GPU"


def build_macos_sensors() -> BaselineSensors:
    """Compose SMC temperature on top of the psutil + NVML baseline."""
    client = SMCClient()
    client.open()                       # idempotent; failure → fallthrough

    cpu: CpuSource = CpuSourceChain([SmcCpu(client=client), PsutilCpu()])

    gpus: list[GpuSource] = list(discover_nvidia_gpus())
    # Add the SMC GPU if no NVML GPU already covers it (Apple Silicon /
    # Intel iGPU); the aggregator's vendor-key dedup keeps duplicates
    # out when both report.
    gpu_key, gpu_name = _gpu_chain_key_and_name()
    if not any(g.key == gpu_key for g in gpus):
        gpus.append(SmcGpu(client=client, key=gpu_key, name=gpu_name))

    if _apple_silicon_enabled():
        log.info("macOS sensors: SMC chain ready (Apple Silicon keys ENABLED via "
                 "TRCC_NEXT_APPLE_SILICON_SMC=1)")
    else:
        log.info("macOS sensors: SMC chain ready (Intel keys only; set "
                 "TRCC_NEXT_APPLE_SILICON_SMC=1 to opt in to Apple Silicon)")

    return BaselineSensors(
        cpu=cpu, memory=PsutilMemory(), gpus=gpus, fans=[],
    )
