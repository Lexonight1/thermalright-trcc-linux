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
from collections.abc import Callable
from typing import Any

from ...core.logs import per_frame, recurring_failure
from ...core.ports import GpuSource
from .gpu_detect import nvidia_gpu_present

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


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


def _cannot_tell() -> None:
    """The presence answer of a runtime given no probe: unknown."""
    log.debug("_cannot_tell: no NVIDIA presence probe given")


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
    ``import_error`` is why, for the one-time warning.  ``nvidia_present``
    answers whether the host has an NVIDIA GPU at all (``None``: cannot tell),
    asked only when something failed: on an AMD-only box a missing reader or
    a missing driver library is the normal state, not a fault to warn about
    (#231, #224 -- every such user was told "NVIDIA GPU present").
    """

    def __init__(self, module: Any, import_error: str | None,
                 nvidia_present: Callable[[], bool | None] | None = None,
                 ) -> None:
        self._pynvml = module
        self._import_error = import_error
        self._nvidia_present = nvidia_present or _cannot_tell
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
                if self._nvidia_present() is False:
                    log.debug("pynvml not importable (%s) — no NVIDIA GPU on "
                              "this host, nothing to read",
                              self._import_error or "ImportError")
                else:
                    log.warning(
                        "pynvml not importable in this interpreter (%s) — "
                        "NVIDIA GPU sensors unavailable; install nvidia-ml-py "
                        "into trcc's environment",
                        self._import_error or "ImportError",
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
                    self._warn_init_failure(e)
                return False

    def _warn_init_failure(self, e: Exception) -> None:
        """Say why NVML would not start -- as a fault only where a card is."""
        present = self._nvidia_present()
        log.debug("_warn_init_failure: nvidia_present=%s", present)
        if present is False:
            log.debug("NVML init failed (%s) — no NVIDIA GPU on this host, "
                      "nothing to read", e)
        elif present:
            log.warning("NVIDIA GPU present but NVML init failed: %s — %s",
                        e, _nvml_fix_hint(e, self._pynvml))
        else:
            log.warning("NVML init failed: %s — if this machine has an NVIDIA "
                        "GPU: %s", e, _nvml_fix_hint(e, self._pynvml))

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
_runtime = _NvmlRuntime(pynvml, _import_error, nvidia_gpu_present)


#: The shape of :func:`nvml_init_state` — ``(reader_available, initialized,
#: last_error)``.  Callers that want to be testable take one of these rather
#: than reaching for the module function, so a health check can be asked
#: "what would you say about a card whose driver is fine?" without one.
GpuStateFn = Callable[[], tuple[bool, bool, str | None]]


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
        log.debug("__init__: index=%s handle=%s", index, handle)
        self._index = index
        self._handle = handle
        self._name_cache: str | None = None

    @property
    def key(self) -> str:
        frame_log.debug("key")
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
        frame_log.debug("is_discrete")
        return True

    def temp(self) -> float | None:
        frame_log.debug("temp: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetTemperature(
                self._handle, pynvml.NVML_TEMPERATURE_GPU))
        except Exception:
            recurring_failure(log, "nvmlDeviceGetTemperature(%d) failed", self._index)
            return None

    def usage(self) -> float | None:
        frame_log.debug("usage: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetUtilizationRates(self._handle).gpu)
        except Exception:
            recurring_failure(log, "nvmlDeviceGetUtilizationRates(%d) failed", self._index)
            return None

    def clock(self) -> float | None:
        frame_log.debug("clock: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetClockInfo(
                self._handle, pynvml.NVML_CLOCK_GRAPHICS))
        except Exception:
            recurring_failure(log, "nvmlDeviceGetClockInfo(%d) failed", self._index)
            return None

    def power(self) -> float | None:
        frame_log.debug("power: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return pynvml.nvmlDeviceGetPowerUsage(self._handle) / 1000.0
        except Exception:
            recurring_failure(log, "nvmlDeviceGetPowerUsage(%d) failed", self._index)
            return None

    def fan(self) -> float | None:
        frame_log.debug("fan: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetFanSpeed(self._handle))
        except Exception:
            recurring_failure(log, "nvmlDeviceGetFanSpeed(%d) failed", self._index)
            return None

    def fan_rpm(self) -> float | None:
        # Only recent drivers export nvmlDeviceGetFanSpeedRPM; older ones raise
        # FunctionNotFound, and GPUFAN then falls back to the duty percent.
        frame_log.debug("fan_rpm: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetFanSpeedRPM(self._handle))
        except Exception:
            recurring_failure(log, "nvmlDeviceGetFanSpeedRPM(%d) failed", self._index)
            return None

    def vram_used(self) -> float | None:
        frame_log.debug("vram_used: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetMemoryInfo(self._handle).used) / (1024 * 1024)
        except Exception:
            recurring_failure(log, "nvmlDeviceGetMemoryInfo.used(%d) failed", self._index)
            return None

    def vram_total(self) -> float | None:
        frame_log.debug("vram_total: idx=%d", self._index)
        if pynvml is None:
            return None
        try:
            return float(pynvml.nvmlDeviceGetMemoryInfo(self._handle).total) / (1024 * 1024)
        except Exception:
            recurring_failure(log, "nvmlDeviceGetMemoryInfo.total(%d) failed", self._index)
            return None
