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
from typing import Any

from ...core.ports import GpuSource

log = logging.getLogger(__name__)


try:
    import pynvml  # pyright: ignore[reportMissingImports]
    _import_error: str | None = None
except ImportError as e:
    pynvml = None  # type: ignore[assignment]
    # Recorded, not logged here — logging isn't configured at module-import
    # time.  Surfaced lazily (once) the first time init is attempted.
    _import_error = str(e)

# Canonical fix for an NVML version mismatch (driver updated without reboot) —
# referenced by both the runtime warning and the doctor's GPU check.
NVML_RELOAD_HINT = (
    "reboot, or reload the driver module: sudo modprobe -r nvidia_uvm "
    "nvidia_drm nvidia_modeset nvidia && sudo modprobe nvidia"
)


def _is_transient_nvml_error(e: Exception, module: Any) -> bool:
    """DRIVER_NOT_LOADED — the GPU may power on after startup (autostart);
    safe to retry quietly.  Any other code won't fix itself on retry.

    ``module`` is the pynvml module to read the constant from, passed in
    rather than read off the global so this stays a pure function of its
    arguments.  ``None`` (pynvml absent) falls back to NVML's real code.
    """
    code = getattr(e, "value", None)
    not_loaded = getattr(module, "NVML_ERROR_DRIVER_NOT_LOADED", 9)
    transient = code == not_loaded
    log.debug("_is_transient_nvml_error: code=%s not_loaded=%s → %s",
              code, not_loaded, transient)
    return transient


def _nvml_fix_hint(e: Exception, module: Any) -> str:
    """Actionable one-liner for a non-transient NVML init failure.

    ``module`` as in :func:`_is_transient_nvml_error`.
    """
    code = getattr(e, "value", None)
    mismatch = getattr(module, "NVML_ERROR_LIB_RM_VERSION_MISMATCH", 18)
    if code == mismatch:
        log.debug("_nvml_fix_hint: code=%s is a version mismatch", code)
        return ("kernel NVIDIA module and userspace libnvidia-ml are out of "
                "sync (driver updated without reboot) — " + NVML_RELOAD_HINT)
    log.debug("_nvml_fix_hint: code=%s — generic driver advice", code)
    return "Check the NVIDIA driver is installed and matches the running kernel"


class _NvmlRuntime:
    """One lazy ``nvmlInit``, and the state that attempt leaves behind.

    NVML is a per-process resource, so the module below holds exactly one of
    these.  It is an *object* rather than the four module globals it replaced
    because those globals were shared mutable state with no owner: anything
    that tried to init — the doctor, a debug report, a GPU discovery — left
    its outcome behind for every later reader in that process, permanently.
    Nobody could ask "what does a fresh start see?" without inheriting
    whatever had already run, which is precisely how a real driver fault on
    one machine turned into a failing unit test about an unrelated code path.

    ``module`` is the pynvml module (or ``None`` when it would not import);
    ``import_error`` is why, for the one-time warning.
    """

    def __init__(self, module: Any, import_error: str | None) -> None:
        self._pynvml = module
        self._import_error = import_error
        self._lock = threading.Lock()
        self._initialized = False
        self._error: str | None = None
        self._warned_init_failure = False
        self._warned_unavailable = False
        log.debug("_NvmlRuntime: available=%s import_error=%s",
                  module is not None, import_error)

    def ensure_init(self) -> bool:
        """Lazy NVML init — retries until the driver is loaded."""
        if self._initialized:
            return True
        if self._pynvml is None:
            # pynvml itself couldn't be imported in this interpreter — the #161
            # case (card present, reader missing).  Warn ONCE with the fix so
            # it's visible at the default log level instead of a silent gpu:[].
            if not self._warned_unavailable:
                self._warned_unavailable = True
                log.warning(
                    "pynvml not importable in this interpreter (%s) — NVIDIA "
                    "GPU sensors unavailable; install nvidia-ml-py into trcc's "
                    "environment", self._import_error or "ImportError",
                )
            else:
                log.debug("ensure_init: pynvml unavailable (already warned)")
            return False
        with self._lock:
            if self._initialized:
                return True
            try:
                self._pynvml.nvmlInit()
                self._initialized = True
                self._error = None
                log.info("NVML initialized — NVIDIA GPU sensors available")
                return True
            except Exception as e:
                self._error = str(e)
                # DRIVER_NOT_LOADED is the normal "GPU autostart" case — stay
                # quiet and retry.  A version mismatch (driver updated, no
                # reboot) or any other error won't resolve on retry, so warn
                # ONCE at WARNING with the fix — otherwise it's invisible at
                # the default log level and the GPU silently never reports.
                if _is_transient_nvml_error(e, self._pynvml):
                    log.debug("NVML not ready (transient): %s", e)
                elif not self._warned_init_failure:
                    self._warned_init_failure = True
                    log.warning(
                        "NVIDIA GPU present but NVML init failed: %s — %s",
                        e, _nvml_fix_hint(e, self._pynvml),
                    )
                return False

    def state(self) -> tuple[bool, bool, str | None]:
        """``(reader_available, initialized, last_error)`` for this runtime.

        Triggers an idempotent init attempt so a late-loaded driver is
        reflected.  ``last_error`` is *this* runtime's own most recent init
        failure — never one inherited from another caller.
        """
        self.ensure_init()
        available = self._pynvml is not None
        log.debug("_NvmlRuntime.state: available=%s initialized=%s error=%s",
                  available, self._initialized, self._error)
        return available, self._initialized, self._error


#: The process's NVML runtime.  ``nvmlInit`` really is per-process, so one is
#: correct — the point of the class is that it is no longer the *only* one
#: constructible.
_runtime = _NvmlRuntime(pynvml, _import_error)


def nvml_init_state() -> tuple[bool, bool, str | None]:
    """``(reader_available, initialized, last_error)`` for the doctor check.

    ``reader_available`` — pynvml importable.  ``initialized`` — ``nvmlInit``
    has succeeded.  ``last_error`` — the most recent init failure message (or
    ``None`` once initialized).  Reports the process runtime; callers wanting
    an isolated one build their own ``_NvmlRuntime``.
    """
    state = _runtime.state()
    log.debug("nvml_init_state: available=%s initialized=%s error=%s", *state)
    return state


def discover_nvidia_gpus() -> list[GpuSource]:
    """Return one NvidiaGpu per card NVML sees.  Empty if no NVIDIA / no driver."""
    log.info("discover_nvidia_gpus: called")
    if not _runtime.ensure_init() or pynvml is None:
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
