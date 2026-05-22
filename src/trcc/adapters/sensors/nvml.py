"""NVIDIA GPU sources via pynvml.

pynvml is optional — not installed → no NVIDIA sensors.  Driver loaded
after app startup (GPU autostart) → late init retries each discovery
attempt until nvmlInit() succeeds.

One `NvidiaGpu` instance per physical GPU.  Always discrete (NVIDIA has
no integrated GPUs).
"""
from __future__ import annotations

import logging
import threading

from ...core.ports import GpuSource

log = logging.getLogger(__name__)


try:
    import pynvml  # pyright: ignore[reportMissingImports]
    _AVAILABLE = True
except ImportError:
    pynvml = None  # type: ignore[assignment]
    _AVAILABLE = False


_init_lock = threading.Lock()
_initialized = False


def _ensure_init() -> bool:
    """Lazy NVML init — retries until driver is loaded."""
    global _initialized
    if _initialized:
        return True
    if not _AVAILABLE or pynvml is None:
        return False
    with _init_lock:
        if _initialized:
            return True
        try:
            pynvml.nvmlInit()
            _initialized = True
            log.info("NVML initialized — NVIDIA GPU sensors available")
            return True
        except Exception as e:
            log.debug("NVML not ready: %s", e)
            return False


def discover_nvidia_gpus() -> list[GpuSource]:
    """Return one NvidiaGpu per card NVML sees.  Empty if no NVIDIA / no driver."""
    if not _ensure_init() or pynvml is None:
        return []
    gpus: list[GpuSource] = []
    try:
        count = pynvml.nvmlDeviceGetCount()
    except Exception:
        log.debug("nvmlDeviceGetCount failed", exc_info=True)
        return []
    for idx in range(count):
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
        except Exception:
            log.debug("nvmlDeviceGetHandleByIndex(%d) failed", idx, exc_info=True)
            continue
        gpus.append(NvidiaGpu(idx, handle))
    return gpus


class NvidiaGpu(GpuSource):
    """A single NVIDIA GPU — all readings routed through pynvml handles."""

    def __init__(self, index: int, handle: object) -> None:
        self._index = index
        self._handle = handle
        self._name_cache: str | None = None

    @property
    def key(self) -> str:
        return f"nvidia:{self._index}"

    @property
    def name(self) -> str:
        if self._name_cache is not None:
            return self._name_cache
        if pynvml is None:
            return f"NVIDIA GPU {self._index}"
        try:
            raw = pynvml.nvmlDeviceGetName(self._handle)
            self._name_cache = raw.decode() if isinstance(raw, bytes) else str(raw)
        except Exception:
            log.debug("nvmlDeviceGetName(%d) failed", self._index, exc_info=True)
            self._name_cache = f"NVIDIA GPU {self._index}"
        return self._name_cache

    @property
    def is_discrete(self) -> bool:
        return True

    def temp(self) -> float | None:
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetTemperature(
                self._handle, pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            log.debug("nvmlDeviceGetTemperature(%d) failed", self._index, exc_info=True)
            return None

    def usage(self) -> float | None:
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetUtilizationRates(self._handle).gpu)
        except Exception:
            log.debug("nvmlDeviceGetUtilizationRates(%d) failed", self._index, exc_info=True)
            return None

    def clock(self) -> float | None:
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetClockInfo(
                self._handle, pynvml.NVML_CLOCK_GRAPHICS))
        except Exception:
            log.debug("nvmlDeviceGetClockInfo(%d) failed", self._index, exc_info=True)
            return None

    def power(self) -> float | None:
        if pynvml is None:
            return None
        try:
            return pynvml.nvmlDeviceGetPowerUsage(self._handle) / 1000.0
        except Exception:
            log.debug("nvmlDeviceGetPowerUsage(%d) failed", self._index, exc_info=True)
            return None

    def fan(self) -> float | None:
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetFanSpeed(self._handle))
        except Exception:
            log.debug("nvmlDeviceGetFanSpeed(%d) failed", self._index, exc_info=True)
            return None

    def vram_used(self) -> float | None:
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetMemoryInfo(self._handle).used) / (1024 * 1024)
        except Exception:
            log.debug("nvmlDeviceGetMemoryInfo.used(%d) failed", self._index, exc_info=True)
            return None

    def vram_total(self) -> float | None:
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetMemoryInfo(self._handle).total) / (1024 * 1024)
        except Exception:
            log.debug("nvmlDeviceGetMemoryInfo.total(%d) failed", self._index, exc_info=True)
            return None
