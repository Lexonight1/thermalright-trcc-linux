"""BSD sensor factory — sysctl temperature on top of the psutil baseline.

Priority chain:

  1. SysctlCpu  — CPU temperature via sysctl ``dev.cpu.N.temperature``
                  (FreeBSD / DragonFly) or ``hw.sensors.cpuN.temp0``
                  (OpenBSD), with NetBSD's ``machdep.cpu_temperature``
                  ready for reporter confirmation.
  2. PsutilCpu  — usage / freq baseline; covers all three BSDs.

GPU + memory wiring mirrors the Linux + Windows factories — psutil
handles memory, NVML covers NVIDIA discrete GPUs; per-vendor BSD GPU
sensors are deferred (no donor hardware yet).
"""
from __future__ import annotations

import logging

from ...core.ports import CpuSource
from ._sysctl import SysctlCpu
from .aggregator import BaselineSensors
from .chain import CpuSourceChain
from .nvml import discover_nvidia_gpus
from .psutil_sources import PsutilCpu, PsutilMemory

log = logging.getLogger(__name__)


def build_bsd_sensors() -> BaselineSensors:
    """Construct a BaselineSensors with the BSD sysctl + psutil chain."""
    cpu: CpuSource = CpuSourceChain([SysctlCpu(), PsutilCpu()])
    gpus = discover_nvidia_gpus()
    log.info("BSD sensors: cpu chain ready, gpus=%d", len(gpus))
    return BaselineSensors(
        cpu=cpu, memory=PsutilMemory(), gpus=gpus, fans=[],
    )
