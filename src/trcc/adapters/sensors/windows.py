"""Windows sensor factory — compose the per-OS strategy chain.

Priority order (matches legacy ``WindowsSensorSource.in_priority_order``):

  1. HWiNFO64 shared memory — best quality when the user runs HWiNFO.
  2. LibreHardwareMonitor WMI namespace — broad CPU + GPU coverage.
  3. MSAcpi ACPI thermal zone — Windows-native fallback, temp-only.
  4. psutil / pynvml — universal baseline.

Each strategy returns ``None`` when its backend isn't present, so the
chain naturally degrades.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

from ...core.ports import CpuSource, GpuSource
from ._hwinfo import HwinfoCpu, discover_hwinfo_gpus
from ._lhm import LhmCpu, discover_lhm_gpus
from ._msacpi import WmiAcpiCpu
from .aggregator import BaselineSensors
from .chain import CpuSourceChain, GpuSourceChain
from .nvml import discover_nvidia_gpus
from .psutil_sources import PsutilCpu, PsutilMemory

log = logging.getLogger(__name__)


def build_windows_sensors(
    thread_context: Callable[[], AbstractContextManager[None]] = nullcontext,
) -> BaselineSensors:
    """Construct a BaselineSensors with the full Windows source chain.

    ``thread_context`` is the OS poll-thread setup (Windows passes its COM
    apartment so WMI reads work off the main thread); defaults to a no-op
    so the builder stays callable without a Platform.
    """
    log.info("build_windows_sensors: called")
    cpu: CpuSource = CpuSourceChain([
        HwinfoCpu(),
        LhmCpu(),
        WmiAcpiCpu(),
        PsutilCpu(),
    ])
    gpus = _build_windows_gpu_chains()
    log.info("Windows sensors: cpu chain ready, gpus=%d", len(gpus))
    return BaselineSensors(
        cpu=cpu, memory=PsutilMemory(), gpus=gpus, fans=[],
        thread_context=thread_context,
    )


def _build_windows_gpu_chains() -> list[GpuSource]:
    """One GpuSourceChain per detected GPU, blending vendor + LHM + HWiNFO.

    The aggregator dedups via vendor-normalized keys (``nvidia:0``,
    ``amd:0``, ``intel:0``) — each chain reads its key from the first
    source in the list, so put the strictest identity first.
    """
    log.debug("_build_windows_gpu_chains: called")
    nvml = discover_nvidia_gpus()
    lhm = discover_lhm_gpus()
    hwinfo = discover_hwinfo_gpus()

    by_key: dict[str, list[GpuSource]] = {}
    for source in [*nvml, *hwinfo, *lhm]:
        by_key.setdefault(source.key, []).append(source)

    chains: list[GpuSource] = []
    for sources in by_key.values():
        if len(sources) == 1:
            chains.append(sources[0])
        else:
            chains.append(GpuSourceChain(sources))
    return chains
