"""BSD sensor factory — sysctl temperature + fans on top of the psutil baseline.

Priority chain:

  1. SysctlCpu  — CPU temperature via sysctl ``dev.cpu.N.temperature``
                  (FreeBSD / DragonFly) or ``hw.sensors.cpuN.temp0``
                  (OpenBSD), with NetBSD's ``machdep.cpu_temperature``
                  ready for reporter confirmation.
  2. PsutilCpu  — usage / freq baseline; covers all three BSDs.

Fans come from OpenBSD's structured ``hw.sensors.*`` framework — one
shared sysctl snapshot feeds every fan source so a polling tick costs
one ``sysctl -a`` call regardless of how many fans the board exposes.
FreeBSD's fan readings live behind board-specific drivers
(``dev.aibs.0.fan.N``, IPMI) and aren't enumerated here — donor
hardware reports are welcome.

GPU + memory wiring mirrors the Linux + Windows factories — psutil
handles memory, NVML covers NVIDIA discrete GPUs; per-vendor BSD GPU
sensors are deferred (no donor hardware yet).
"""
from __future__ import annotations

import logging
import platform

from ...core.ports import CpuSource, FanSource
from ._sysctl import SysctlCpu, _SysctlSnapshot, discover_openbsd_fans
from .aggregator import BaselineSensors
from .chain import CpuSourceChain
from .nvml import discover_nvidia_gpus
from .psutil_sources import PsutilCpu, PsutilMemory

log = logging.getLogger(__name__)


def build_bsd_sensors() -> BaselineSensors:
    """Construct a BaselineSensors with the BSD sysctl + psutil chain.

    Fans are only enumerated on OpenBSD — its ``hw.sensors.*`` framework
    is the only universal BSD fan-RPM source.
    """
    system = platform.system()
    snapshot = _SysctlSnapshot()
    cpu: CpuSource = CpuSourceChain([SysctlCpu(), PsutilCpu()])
    gpus = discover_nvidia_gpus()
    fans: list[FanSource] = []
    if system == "OpenBSD":
        fans = discover_openbsd_fans(snapshot=snapshot)
    log.info("BSD sensors: cpu chain ready, gpus=%d, fans=%d",
             len(gpus), len(fans))
    return BaselineSensors(
        cpu=cpu, memory=PsutilMemory(), gpus=gpus, fans=fans,
    )
