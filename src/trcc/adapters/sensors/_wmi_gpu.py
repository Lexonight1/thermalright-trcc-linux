"""Last-resort GPU enumeration via WMI ``Win32_VideoController``.

When no live sensor backend (pynvml / HWiNFO / LibreHardwareMonitor)
reports a GPU — e.g. an AMD/Intel card with none of those tools running
— the GPU picker would otherwise show "No GPU detected".  ``Win32_-
VideoController`` always lists every adapter by name, so we surface it as
a name-only :class:`GpuSource`: real ``key`` / ``name`` / ``is_discrete``,
but every live reading is ``None`` (the ABC contract is ``float | None``,
so this is a faithful "name known, no telemetry" source, not a stub).

Chained LAST in ``_build_windows_gpu_chains`` — only consulted when the
live backends found nothing, mirroring legacy ``windows/enumerator.py::
_wmi_video_controller_gpus``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ...core.ports import GpuSource

log = logging.getLogger(__name__)

# Name fragments that mark a dedicated card.  Anything unmatched is
# treated as integrated (the safe default — an iGPU never claims a fan).
_DISCRETE_MARKERS: tuple[str, ...] = (
    "RTX", "GTX", "GEFORCE", "QUADRO", "TITAN",
    "RADEON RX", "RADEON PRO", "FIREPRO", "ARC",
)


def _default_handle_factory() -> Any:
    """One-shot ``root\\cimv2`` WMI handle; ``None`` when wmi is absent."""
    from ..system._windows_wmi import wmi_handle
    try:
        return wmi_handle()
    except ImportError:
        log.debug("wmi package unavailable — no WMI GPU enumeration")
        return None
    except Exception as e:
        log.debug("WMI handle for GPU enumeration failed: %s", type(e).__name__)
        return None


def _is_discrete(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _DISCRETE_MARKERS)


class WmiVideoControllerGpu(GpuSource):
    """One ``Win32_VideoController`` adapter — name only, no telemetry."""

    def __init__(self, index: int, name: str) -> None:
        self._index = index
        self._name = name
        self._discrete = _is_discrete(name)

    @property
    def key(self) -> str:
        return f"wmi:{self._index}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_discrete(self) -> bool:
        return self._discrete

    # Win32_VideoController carries no live sensor data — every reading is
    # genuinely unavailable from this backend (AdapterRAM caps at 4 GiB and
    # is unreliable, so VRAM is omitted too).
    def temp(self) -> float | None: return None
    def usage(self) -> float | None: return None
    def clock(self) -> float | None: return None
    def power(self) -> float | None: return None
    def fan(self) -> float | None: return None
    def vram_used(self) -> float | None: return None
    def vram_total(self) -> float | None: return None


def discover_wmi_gpus(
    handle_factory: Callable[[], Any] = _default_handle_factory,
) -> list[GpuSource]:
    """Enumerate display adapters via ``Win32_VideoController`` (name only)."""
    log.info("discover_wmi_gpus: called")
    handle = handle_factory()
    if handle is None:
        return []
    gpus: list[GpuSource] = []
    try:
        for i, vc in enumerate(handle.Win32_VideoController()):
            name = (str(vc.Name).strip() if vc.Name is not None else "")
            if not name:
                continue
            gpus.append(WmiVideoControllerGpu(i, name))
    except Exception as e:  # WMI/COM surface is wide
        log.debug("Win32_VideoController query failed: %s", type(e).__name__)
        return []
    log.info("discover_wmi_gpus: %d adapter(s)", len(gpus))
    return gpus
