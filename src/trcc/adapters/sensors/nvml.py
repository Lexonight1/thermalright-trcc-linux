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
_init_error: str | None = None
_warned_init_failure = False

# Canonical fix for an NVML version mismatch (driver updated without reboot) —
# referenced by both the runtime warning and the doctor's GPU check.
NVML_RELOAD_HINT = (
    "reboot, or reload the driver module: sudo modprobe -r nvidia_uvm "
    "nvidia_drm nvidia_modeset nvidia && sudo modprobe nvidia"
)


def _is_transient_nvml_error(e: Exception) -> bool:
    """DRIVER_NOT_LOADED — the GPU may power on after startup (autostart);
    safe to retry quietly.  Any other code won't fix itself on retry."""
    code = getattr(e, "value", None)
    not_loaded = getattr(pynvml, "NVML_ERROR_DRIVER_NOT_LOADED", 9)
    return code == not_loaded


def _nvml_fix_hint(e: Exception) -> str:
    """Actionable one-liner for a non-transient NVML init failure."""
    code = getattr(e, "value", None)
    mismatch = getattr(pynvml, "NVML_ERROR_LIB_RM_VERSION_MISMATCH", 18)
    if code == mismatch:
        return ("kernel NVIDIA module and userspace libnvidia-ml are out of "
                "sync (driver updated without reboot) — " + NVML_RELOAD_HINT)
    return "Check the NVIDIA driver is installed and matches the running kernel"


def _ensure_init() -> bool:
    """Lazy NVML init — retries until driver is loaded."""
    global _initialized, _init_error, _warned_init_failure
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
            _init_error = None
            log.info("NVML initialized — NVIDIA GPU sensors available")
            return True
        except Exception as e:
            _init_error = str(e)
            # DRIVER_NOT_LOADED is the normal "GPU autostart" case — stay quiet
            # and retry.  A version mismatch (driver updated, no reboot) or any
            # other error won't resolve on retry, so warn ONCE at WARNING with
            # the fix — otherwise it's invisible at the default log level and
            # the GPU silently never reports (gpu:[]).
            if _is_transient_nvml_error(e):
                log.debug("NVML not ready (transient): %s", e)
            elif not _warned_init_failure:
                _warned_init_failure = True
                log.warning(
                    "NVIDIA GPU present but NVML init failed: %s — %s",
                    e, _nvml_fix_hint(e),
                )
            return False


def nvml_init_state() -> tuple[bool, bool, str | None]:
    """``(reader_available, initialized, last_error)`` for the doctor check.

    ``reader_available`` — pynvml importable.  ``initialized`` — ``nvmlInit``
    has succeeded.  ``last_error`` — the most recent init failure message (or
    ``None`` once initialized).  Triggers an idempotent init attempt so a
    late-loaded driver is reflected.
    """
    _ensure_init()
    return _AVAILABLE, _initialized, _init_error


def discover_nvidia_gpus() -> list[GpuSource]:
    """Return one NvidiaGpu per card NVML sees.  Empty if no NVIDIA / no driver."""
    log.info("discover_nvidia_gpus: called")
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
        log.debug("temp: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetTemperature(
                self._handle, pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            log.debug("nvmlDeviceGetTemperature(%d) failed", self._index, exc_info=True)
            return None

    def usage(self) -> float | None:
        log.debug("usage: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetUtilizationRates(self._handle).gpu)
        except Exception:
            log.debug("nvmlDeviceGetUtilizationRates(%d) failed", self._index, exc_info=True)
            return None

    def clock(self) -> float | None:
        log.debug("clock: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetClockInfo(
                self._handle, pynvml.NVML_CLOCK_GRAPHICS))
        except Exception:
            log.debug("nvmlDeviceGetClockInfo(%d) failed", self._index, exc_info=True)
            return None

    def power(self) -> float | None:
        log.debug("power: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return pynvml.nvmlDeviceGetPowerUsage(self._handle) / 1000.0
        except Exception:
            log.debug("nvmlDeviceGetPowerUsage(%d) failed", self._index, exc_info=True)
            return None

    def fan(self) -> float | None:
        log.debug("fan: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetFanSpeed(self._handle))
        except Exception:
            log.debug("nvmlDeviceGetFanSpeed(%d) failed", self._index, exc_info=True)
            return None

    def vram_used(self) -> float | None:
        log.debug("vram_used: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetMemoryInfo(self._handle).used) / (1024 * 1024)
        except Exception:
            log.debug("nvmlDeviceGetMemoryInfo.used(%d) failed", self._index, exc_info=True)
            return None

    def vram_total(self) -> float | None:
        log.debug("vram_total: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetMemoryInfo(self._handle).total) / (1024 * 1024)
        except Exception:
            log.debug("nvmlDeviceGetMemoryInfo.total(%d) failed", self._index, exc_info=True)
            return None
