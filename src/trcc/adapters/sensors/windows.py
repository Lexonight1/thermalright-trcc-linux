"""Windows sensor factory — compose the per-OS strategy chain.

Priority order (matches legacy ``WindowsSensorSource.in_priority_order``):

  1. HWiNFO64 shared memory — best quality when the user runs HWiNFO.
  2. PawnIO — the CPU's own registers through the signed PawnIO driver the
     installer lays down: package temperature and power, nothing to run.
  3. LibreHardwareMonitor WMI namespace — a user's own LHM 0.9.4 or older
     (0.9.5 removed WMI).
  4. MSAcpi ACPI thermal zone — Windows-native fallback, temp-only.
  5. psutil / pynvml — universal baseline.

Each strategy returns ``None`` when its backend isn't present, so the
chain naturally degrades.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from ...core.ports import CpuSource, DiskSource, GpuSource
from ._adl import discover_adl_gpus
from ._d3dkmt import discover_d3dkmt_gpus
from ._hwinfo import HwinfoCpu, discover_hwinfo_gpus
from ._igcl import discover_igcl_gpus
from ._lhm import LhmCpu, discover_lhm_disks, discover_lhm_gpus
from ._msacpi import WmiAcpiCpu
from ._pawnio_cpu import PawnioCpu
from ._storage_query import discover_windows_disks
from ._superio import discover_superio
from ._wmi_gpu import discover_wmi_gpus
from .aggregator import BaselineSensors
from .chain import CpuSourceChain, GpuSourceChain
from .nvml import discover_nvidia_gpus
from .psutil_sources import PsutilCpu, PsutilMemory

log = logging.getLogger(__name__)


def build_windows_sensors(
    thread_context: Callable[[], AbstractContextManager[None]] = nullcontext,
    pawnio: PawnioCpu | None = None,
    superio: Callable[[], dict[str, Any]] | None = None,
) -> BaselineSensors:
    """Construct a BaselineSensors with the full Windows source chain.

    ``thread_context`` is the OS poll-thread setup (Windows passes its COM
    apartment so WMI reads work off the main thread); defaults to a no-op
    so the builder stays callable without a Platform.  *pawnio* is the
    platform's choice -- through TRCC's service when not elevated -- and
    *superio* likewise the board's Super I/O sample, or None without one.
    """
    log.info("build_windows_sensors: pawnio=%s", "given" if pawnio else "direct")
    cpu: CpuSource = CpuSourceChain([
        HwinfoCpu(),
        pawnio or PawnioCpu(),
        LhmCpu(),
        WmiAcpiCpu(),
        PsutilCpu(),
    ])
    gpus = _build_windows_gpu_chains()
    disks = _build_windows_disks()
    fans, board = discover_superio(superio)
    log.info("Windows sensors: cpu chain ready, gpus=%d, disks=%d, fans=%d, board=%d",
             len(gpus), len(disks), len(fans), len(board))
    return BaselineSensors(
        cpu=cpu, memory=PsutilMemory(), gpus=gpus, fans=fans, disks=disks,
        board_temps=board, thread_context=thread_context,
    )


def _build_windows_disks() -> list[DiskSource]:
    """Windows' own drive temperatures, then any drive only LHM can read.

    One drive must not appear twice in the disk picker, so an LHM drive is
    kept only when no Windows reading carries its name.  LHM still covers a
    drive whose driver does not answer the temperature property.
    """
    log.debug("_build_windows_disks: called")
    native = discover_windows_disks()
    names = {_disk_name(d) for d in native}
    extra = [d for d in discover_lhm_disks() if _disk_name(d) not in names]
    log.info("_build_windows_disks: %d from Windows, %d more from LHM", len(native), len(extra))
    return [*native, *extra]


def _disk_name(disk: DiskSource) -> str:
    """A drive's model as two readers may spell it: case and runs of spaces aside."""
    name = " ".join(disk.name.lower().split())
    log.debug("_disk_name: %s -> %r", disk.key, name)
    return name


def _build_windows_gpu_chains() -> list[GpuSource]:
    """One GpuSourceChain per detected GPU: vendor library (NVML, ADL, IGCL),
    HWiNFO, LHM, then Windows' own numbers (D3DKMT + PDH, every vendor).

    The aggregator dedups via vendor-normalized keys (``nvidia:0``,
    ``amd:0``, ``intel:0``) — each chain reads its key from the first
    source in the list, so put the strictest identity first, and a reading
    comes from the first member that has it.  AMD and Intel cards had nothing
    here but their name until ADL and D3DKMT joined (2026-10-20).
    """
    log.debug("_build_windows_gpu_chains: called")
    nvml = discover_nvidia_gpus()
    adl = discover_adl_gpus()
    igcl = discover_igcl_gpus()
    lhm = discover_lhm_gpus()
    hwinfo = discover_hwinfo_gpus()
    windows = discover_d3dkmt_gpus()

    by_key: dict[str, list[GpuSource]] = {}
    for source in [*nvml, *adl, *igcl, *hwinfo, *lhm, *windows]:
        by_key.setdefault(source.key, []).append(source)

    chains: list[GpuSource] = []
    for sources in by_key.values():
        if len(sources) == 1:
            chains.append(sources[0])
        else:
            chains.append(GpuSourceChain(sources))

    # Last resort: no live backend saw a GPU (AMD/Intel card with no
    # HWiNFO/LHM running, non-NVIDIA so pynvml is N/A).  Surface the
    # adapter by name via Win32_VideoController so the picker isn't empty.
    if not chains:
        chains = discover_wmi_gpus()
        log.info("_build_windows_gpu_chains: live backends empty — "
                 "WMI fallback yielded %d adapter(s)", len(chains))
    return chains
