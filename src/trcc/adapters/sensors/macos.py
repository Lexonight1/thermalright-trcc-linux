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

from ...core.ports import CpuSource, FanSource, GpuSource
from ._macos_hid import (
    MacosHidCpu,
    MacosHidGpu,
    _HidSnapshot,
    hid_layer_ready,
)
from ._powermetrics import (
    PowermetricsCpu,
    PowermetricsGpu,
    _PowermetricsSnapshot,
)
from ._smc import (
    APPLE_SILICON_CPU_TEMP_KEYS,
    APPLE_SILICON_GPU_TEMP_KEYS,
    INTEL_CPU_TEMP_KEYS,
    INTEL_GPU_TEMP_KEYS,
    SMCClient,
    SmcClientPort,
)
from .aggregator import BaselineSensors
from .chain import CpuSourceChain, GpuSourceChain
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
# SmcFan — one SMC fan key wrapped as a FanSource
# =========================================================================


_FAN_NAME_TEMPLATE = "Apple SMC Fan {index}"


class SmcFan(FanSource):
    """One SMC fan (``F0Ac``, ``F1Ac`` …) exposed as the FanSource port.

    Apple Silicon and Intel both expose actual fan RPMs at
    ``F{i}Ac``; the count is at ``FNum``.  Fanless models
    (MacBook Air with M-series) report ``FNum == 0`` and this
    source never materialises.
    """

    def __init__(
        self,
        index: int,
        *,
        client: SmcClientPort | None = None,
    ) -> None:
        self._index = index
        self._client: SmcClientPort = client if client is not None else SMCClient()
        if not self._client.connected:
            self._client.open()

    @property
    def key(self) -> str:
        return f"smc:fan{self._index}"

    @property
    def name(self) -> str:
        return _FAN_NAME_TEMPLATE.format(index=self._index)

    def rpm(self) -> int | None:
        value = self._client.read_fan_rpm(f"F{self._index}Ac")
        if value is None:
            return None
        return int(value)

    def percent(self) -> float | None:
        # SMC F{i}Mn (min) / F{i}Mx (max) can compute a percent; legacy
        # didn't bother, and most Mac fan UIs report RPM directly.
        return None


def discover_smc_fans(client: SmcClientPort) -> list[FanSource]:
    """Probe ``FNum`` and materialise one ``SmcFan`` per discovered fan."""
    log.info("discover_smc_fans: called")
    if not client.connected:
        log.debug("discover_smc_fans: SMC not connected — no fans")
        return []
    n_fans = client.read_key_uint32("FNum")
    if n_fans is None:
        log.debug("discover_smc_fans: FNum returned None — no SMC fan support")
        return []
    if not 0 < n_fans < 16:
        log.warning(
            "discover_smc_fans: FNum=%d out of plausible range, skipping",
            n_fans,
        )
        return []
    fans: list[FanSource] = []
    for i in range(n_fans):
        sample = client.read_fan_rpm(f"F{i}Ac")
        if sample is None:
            log.debug("discover_smc_fans: F%dAc returned None — skipping", i)
            continue
        fans.append(SmcFan(i, client=client))
        log.info("discover_smc_fans: F%dAc reads %.0f RPM — registering", i, sample)
    log.info("discover_smc_fans: %d fans discovered", len(fans))
    return fans


# =========================================================================
# Factory
# =========================================================================


def _gpu_chain_key_and_name() -> tuple[str, str]:
    """Pick the GpuSource key + display name based on Mac architecture."""
    log.debug("_gpu_chain_key_and_name: called")
    if platform.machine() == "arm64":
        return "apple:0", "Apple Silicon GPU"
    return "intel:0", "Intel GPU"


def build_macos_sensors() -> BaselineSensors:
    """Compose HID + powermetrics + SMC on top of the psutil baseline.

    Source priority by metric:

    ============  ==================================================
    CPU temp      HID (Apple Silicon) → SMC (Intel) → none
    CPU usage     psutil (universal)
    CPU freq      powermetrics (Apple Silicon, requires helper) → psutil
    CPU power     powermetrics (Apple Silicon, requires helper) → none
    GPU temp      HID (Apple Silicon) → SMC (Intel) → NVML (discrete)
    GPU usage     powermetrics (Apple Silicon) → NVML (discrete)
    GPU clock     powermetrics (Apple Silicon) → NVML (discrete)
    GPU power     powermetrics (Apple Silicon) → NVML (discrete)
    Fans          SMC FNum / F{i}Ac (universal where present)
    ============  ==================================================

    Every source short-circuits to ``None`` when its backend isn't
    present, so the chain degrades gracefully on Intel (no HID), on
    Apple Silicon without the powermetrics helper, etc.

    **Hardware unverified** — per CLAUDE.md macOS protocol, a
    release advertising these sources should wait for a reporter to
    confirm on real hardware before shipping.
    """
    log.info("build_macos_sensors: called")
    client = SMCClient()
    client.open()                       # idempotent; failure → fallthrough

    hid_snap = _HidSnapshot()
    pm_snap = _PowermetricsSnapshot()

    cpu: CpuSource = CpuSourceChain([
        MacosHidCpu(hid_snap),          # HID die temp (Apple Silicon)
        SmcCpu(client=client),          # SMC keys (Intel + AS opt-in)
        PowermetricsCpu(pm_snap),       # power + freq (Apple Silicon)
        PsutilCpu(),                    # universal usage + freq baseline
    ])

    gpus: list[GpuSource] = _build_macos_gpus(client, hid_snap, pm_snap)
    fans = discover_smc_fans(client)

    log.info(
        "macOS sensors: hid_ready=%s gpus=%d fans=%d "
        "(Apple Silicon keys %s)",
        hid_layer_ready(), len(gpus), len(fans),
        "ENABLED" if _apple_silicon_enabled() else "disabled",
    )

    return BaselineSensors(
        cpu=cpu, memory=PsutilMemory(), gpus=gpus, fans=fans,
    )


def _build_macos_gpus(
    client: SmcClientPort,
    hid_snap: _HidSnapshot,
    pm_snap: _PowermetricsSnapshot,
) -> list[GpuSource]:
    """One GpuSourceChain per discovered GPU.

    Apple Silicon: HID temp + powermetrics usage/clock/power blended
    into a single ``apple:0`` chain — the aggregator's vendor key
    dedup keeps these three sources from showing as separate GPUs.

    Intel Mac with discrete NVIDIA: NVML reports a ``nvidia:N``
    chain; SMC adds an ``intel:0`` iGPU chain.
    """
    log.debug("_build_macos_gpus: called")
    gpu_key, gpu_name = _gpu_chain_key_and_name()
    if platform.machine() == "arm64":
        apple_chain = GpuSourceChain([
            MacosHidGpu(hid_snap),
            PowermetricsGpu(pm_snap, model_name=gpu_name),
        ])
        out: list[GpuSource] = [apple_chain]
        out.extend(discover_nvidia_gpus())   # rare on Apple Silicon
        return out

    # Intel: NVML + an SMC iGPU entry when no NVML key collides with intel:0
    gpus: list[GpuSource] = list(discover_nvidia_gpus())
    if not any(g.key == gpu_key for g in gpus):
        gpus.append(SmcGpu(client=client, key=gpu_key, name=gpu_name))
    return gpus
