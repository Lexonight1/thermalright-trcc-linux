"""LibreHardwareMonitor sensor sources — read live values from LHM's WMI.

When ``LibreHardwareMonitor.exe`` is running (user-installed or
TRCC-bundled), it publishes the full sensor tree to the
``root\\LibreHardwareMonitor`` WMI namespace.  This module reads from
that namespace and exposes the data through next/'s ``CpuSource`` /
``GpuSource`` ABCs.

Subprocess auto-spawn (:class:`LhmSubprocess`) launches the bundled
``LibreHardwareMonitor.exe`` when the WMI namespace is missing — the
PyInstaller dist drops the binary at ``<exe-dir>/lhm/`` and the
spawned process registers the namespace within a few seconds.  Already-
running LHM (manually installed, autostart, sibling process) is
reused; ``stop()`` only terminates processes WE spawned.

Wire format ported from legacy ``src/trcc/adapters/system/windows/
sources/lhm.py``.
"""
from __future__ import annotations

import atexit
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core.ports import CpuSource, DiskSource, GpuSource

log = logging.getLogger(__name__)


_LHM_NAMESPACE = "root\\LibreHardwareMonitor"
_LHM_PROCESS_NAME = "LibreHardwareMonitor.exe"

# Window between spawn and the WMI namespace becoming queryable.  On a
# warm system LHM registers in ~2 s; first-run JIT can push it past
# that, hence the 10 s ceiling.
_WMI_READY_TIMEOUT_SEC = 10.0
_WMI_READY_INTERVAL_SEC = 0.5

# Sensor.SensorType strings emitted by LHM.  Stable across LHM versions.
_TYPE_TEMP = "Temperature"
_TYPE_LOAD = "Load"
_TYPE_CLOCK = "Clock"
_TYPE_POWER = "Power"
_TYPE_FAN = "Fan"
_TYPE_SMALL_DATA = "SmallData"   # used for vram_used / vram_total in MB
_TYPE_DATA = "Data"              # MemUsed/MemAvailable in GB

# Hardware.HardwareType strings.  CPU = exactly "Cpu"; GPUs come in
# vendor-flavoured variants (GpuNvidia, GpuAmd, GpuIntel) which the
# helper matches via ``startswith("Gpu")``.
_HW_CPU = "Cpu"
_HW_GPU_PREFIX = "Gpu"
_HW_STORAGE = "Storage"


def _probe_wmi_namespace() -> Any:
    """Single-attempt probe of ``root\\LibreHardwareMonitor`` via WMI.

    Returns the WMI handle on hit, ``None`` on miss (LHM not running,
    ``wmi`` package missing, COM error).  Cheap on success — LHM
    returns ``[]`` when up but idle.
    """
    try:
        import wmi  # pyright: ignore[reportMissingImports]
    except ImportError:
        return None
    try:
        ns = wmi.WMI(namespace=_LHM_NAMESPACE)
        list(ns.Hardware())
    except Exception:
        log.debug("LHM namespace unavailable", exc_info=True)
        return None
    return ns


def _lhm_exe_path() -> Path | None:
    """Locate the bundled ``LibreHardwareMonitor.exe``.

    Searches ``<exe-dir>/lhm/`` (PyInstaller dist layout) then the
    current working directory (dev mode).  Returns ``None`` when no
    bundled exe is present — graceful degradation rather than crash.
    """
    candidates = [
        Path(sys.executable).parent / "lhm" / _LHM_PROCESS_NAME,
        Path.cwd() / "lhm" / _LHM_PROCESS_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _spawn_lhm() -> subprocess.Popen[bytes] | None:
    """Launch the bundled LHM with a hidden window.

    Detached + no-console so it survives parent shutdown cleanly and
    doesn't flash a window.  Returns ``None`` when the bundled exe
    isn't shipped or ``Popen`` raises.
    """
    exe = _lhm_exe_path()
    if exe is None:
        log.debug("LHM exe not found in expected locations")
        return None

    creationflags = 0
    startupinfo = None
    if sys.platform == "win32":
        # CREATE_NO_WINDOW (0x08000000) — no console window
        # DETACHED_PROCESS (0x00000008) — independent of TRCC's console
        creationflags = 0x08000000
        # SW_HIDE — belt-and-suspenders for the WinForms main window.
        startupinfo = subprocess.STARTUPINFO()  # pyright: ignore[reportAttributeAccessIssue]
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # pyright: ignore[reportAttributeAccessIssue]
        startupinfo.wShowWindow = 0  # SW_HIDE

    try:
        return subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Failed to spawn LibreHardwareMonitor: %s", e)
        return None


def _wait_for_wmi_namespace(
    *,
    probe: Callable[[], Any] = _probe_wmi_namespace,
    timeout_s: float = _WMI_READY_TIMEOUT_SEC,
    interval_s: float = _WMI_READY_INTERVAL_SEC,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Poll for the LHM namespace until it registers or we time out.

    Used after spawning the bundled exe: the WinForms process takes a
    couple of seconds to wire up its WMI publisher.  DI seams on every
    blocking call keep the polling loop testable.
    """
    deadline = clock() + timeout_s
    while clock() < deadline:
        handle = probe()
        if handle is not None:
            return handle
        sleep(interval_s)
    return None


class LhmSubprocess:
    """Owns the bundled LHM lifecycle: probe → spawn → wait → handle.

    Detection is namespace-first: if ``root\\LibreHardwareMonitor`` is
    already populated (manual install, autostart, sibling TRCC), we
    reuse it and never spawn.  Falls back to spawning the bundled exe
    only when the namespace is missing.  ``stop()`` terminates only
    what we ourselves spawned — reused processes are left running for
    whoever owns them.

    DI seams (``probe`` / ``spawn`` / ``wait``) keep the lifecycle
    fully testable from the Linux dev box.
    """

    __slots__ = (
        "_namespace_handle",
        "_owned_process",
        "_probe",
        "_spawn",
        "_unavailable",
        "_wait",
    )

    def __init__(
        self,
        *,
        probe: Callable[[], Any] = _probe_wmi_namespace,
        spawn: Callable[[], subprocess.Popen[bytes] | None] = _spawn_lhm,
        wait: Callable[[], Any] = _wait_for_wmi_namespace,
    ) -> None:
        self._probe = probe
        self._spawn = spawn
        self._wait = wait
        self._owned_process: subprocess.Popen[bytes] | None = None
        self._namespace_handle: Any = None
        # True once a spawn attempt found no bundled exe — stops us
        # re-attempting (and re-warning) on every poll.
        self._unavailable = False

    @property
    def namespace(self) -> Any:
        return self._namespace_handle

    def start(self) -> Any:
        """Return the WMI namespace handle, spawning LHM if needed.

        Idempotent AND spawn-safe — once a handle is cached, subsequent
        ``start()`` calls just return it; and we NEVER spawn a second LHM
        while one we already launched is still pending (the bug behind the
        "multiple LibreHardwareMonitor windows", #191).  Returns ``None``
        when LHM isn't running AND the bundled exe isn't available AND the
        namespace doesn't register after spawning.
        """
        if self._namespace_handle is not None:
            return self._namespace_handle

        existing = self._probe()
        if existing is not None:
            log.info("LibreHardwareMonitor already running; reusing WMI namespace")
            self._namespace_handle = existing
            return existing

        # We already launched LHM — its WMI namespace just hasn't registered
        # yet (or never will).  Wait on THAT process; never spawn a second
        # copy.  This is the #191 fix: without it, a slow/failed namespace
        # registration made every poll spawn another LibreHardwareMonitor.
        if self._owned_process is not None:
            self._namespace_handle = self._wait()
            return self._namespace_handle

        # A previous spawn found no bundled exe — don't retry it (or re-warn)
        # on every poll.  Cleared by stop() so a fresh session can try again.
        if self._unavailable:
            return None

        self._owned_process = self._spawn()
        if self._owned_process is None:
            log.warning(
                "LibreHardwareMonitor not running and bundled exe not "
                "found; LHM sensor source unavailable",
            )
            self._unavailable = True
            return None
        log.info("Spawned LibreHardwareMonitor (pid=%d)",
                 self._owned_process.pid)

        self._namespace_handle = self._wait()
        if self._namespace_handle is None:
            log.warning(
                "LibreHardwareMonitor WMI namespace did not register "
                "within %.0fs; LHM sensor source unavailable",
                _WMI_READY_TIMEOUT_SEC,
            )
        return self._namespace_handle

    def stop(self) -> None:
        """Terminate the LHM subprocess if WE spawned it; else no-op."""
        self._namespace_handle = None
        self._unavailable = False
        if self._owned_process is None:
            return
        try:
            self._owned_process.terminate()
            self._owned_process.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.debug("LHM terminate failed (%s); killing", e)
            try:
                self._owned_process.kill()
            except OSError:
                pass
        self._owned_process = None


# Module-level singleton.  Lazy-started by ``_default_handle_factory``
# below so importing this module costs nothing on platforms without LHM.
# ``atexit`` cleanup terminates only spawned processes — reused
# user-installed LHM stays alive.
_SHARED_LHM = LhmSubprocess()
atexit.register(_SHARED_LHM.stop)


_handle_local = threading.local()


def _default_handle_factory() -> Any:
    """Return a per-thread LHM WMI namespace handle, spawning LHM once.

    The spawn (launching ``LibreHardwareMonitor.exe``) stays process-global
    via the shared :class:`LhmSubprocess`.  The WMI handle, however, is
    COM-apartment-bound — a handle created on the main thread can't be read
    from the poll thread (issue #131) — so each thread gets its OWN
    ``wmi.WMI(namespace=...)``, cached thread-local for cheap repeat reads.

    Tests inject a custom ``handle_factory`` per source, or replace the
    entire ``_SHARED_LHM`` for end-to-end coverage.
    """
    if _SHARED_LHM.start() is None:
        return None
    handle = getattr(_handle_local, "lhm_ns", None)
    if handle is not None:
        return handle
    try:
        import wmi  # pyright: ignore[reportMissingImports]
        handle = wmi.WMI(namespace=_LHM_NAMESPACE)
    except Exception:
        log.debug("LHM per-thread WMI handle failed", exc_info=True)
        handle = None
    _handle_local.lhm_ns = handle
    return handle


# =========================================================================
# Helpers — find sensors of a given type on a Hardware row
# =========================================================================


def _sensors_for(ns: Any, hw_row: Any, sensor_type: str) -> list[Any]:
    """List sensors of ``sensor_type`` whose parent is ``hw_row``.

    LHM's WMI schema: ``Sensor(Parent=<hw.Identifier>)``.  Filtering by
    SensorType after the fact (LHM doesn't accept it as a WHERE clause).
    """
    try:
        return [s for s in ns.Sensor(Parent=hw_row.Identifier)
                if str(s.SensorType) == sensor_type]
    except Exception:
        log.debug("LHM sensor query failed for %s/%s",
                  hw_row.Identifier, sensor_type, exc_info=True)
        return []


def _max_value(sensors: list[Any]) -> float | None:
    """Return the maximum ``Value`` field across a list of LHM sensors."""
    values = [float(s.Value) for s in sensors if s.Value is not None]
    return max(values) if values else None


def _sum_value(sensors: list[Any]) -> float | None:
    values = [float(s.Value) for s in sensors if s.Value is not None]
    return sum(values) if values else None


def _named_value(sensors: list[Any], name_contains: str) -> float | None:
    """First sensor whose Name contains *name_contains* (case-insensitive)."""
    needle = name_contains.lower()
    for s in sensors:
        if s.Value is None:
            continue
        if needle in str(s.Name).lower():
            return float(s.Value)
    return None


# =========================================================================
# LhmCpu
# =========================================================================


class LhmCpu(CpuSource):
    """CPU readings via LHM.

    Each method query the WMI namespace for the CPU's sensors.  Returns
    ``None`` when LHM isn't running, or when LHM is running but the CPU
    sensor isn't populated yet (cold boot, before the first refresh).
    """

    def __init__(
        self,
        *,
        handle_factory: Callable[[], Any] = _default_handle_factory,
    ) -> None:
        # Defer the handle to first read so it is born on the READING
        # thread's apartment (the poll thread), not the construction
        # thread's.  Cache only the apartment-agnostic CPU identifier +
        # display name — strings travel across threads safely.
        self._handle_factory = handle_factory
        self._name: str = "LibreHardwareMonitor (CPU)"
        self._cpu_id: str | None = None
        self._row_cached = False

    def _ensure_cpu_row(self, ns: Any) -> None:
        """Find + cache the CPU Hardware row's Identifier + Name (once)."""
        if self._row_cached:
            return
        self._row_cached = True
        if ns is None:
            return
        try:
            for hw in ns.Hardware():
                if str(hw.HardwareType) == _HW_CPU:
                    self._cpu_id = str(hw.Identifier)
                    self._name = f"LHM: {hw.Name}"
                    return
        except Exception:
            log.debug("LHM cpu row enumeration failed", exc_info=True)

    @property
    def name(self) -> str:
        # Resolve the hardware name lazily on the asking thread — it's a
        # string, safe to cache cross-thread once found.
        self._ensure_cpu_row(self._handle_factory())
        return self._name

    def _cpu_row(self, ns: Any) -> Any | None:
        self._ensure_cpu_row(ns)
        if ns is None or self._cpu_id is None:
            return None
        try:
            rows = list(ns.Hardware(Identifier=self._cpu_id))
        except Exception:
            return None
        return rows[0] if rows else None

    def temp(self) -> float | None:
        """Hottest CPU core temperature in °C."""
        ns = self._handle_factory()
        if (row := self._cpu_row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_TEMP))

    def usage(self) -> float | None:
        """CPU total load 0-100 — LHM names this 'CPU Total'."""
        ns = self._handle_factory()
        if (row := self._cpu_row(ns)) is None:
            return None
        loads = _sensors_for(ns, row, _TYPE_LOAD)
        # Prefer the explicit "CPU Total" sensor; fall back to max across cores.
        return _named_value(loads, "total") or _max_value(loads)

    def freq(self) -> float | None:
        """Highest CPU clock in MHz."""
        ns = self._handle_factory()
        if (row := self._cpu_row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_CLOCK))

    def power(self) -> float | None:
        """Package power draw in W — LHM names this 'CPU Package'."""
        ns = self._handle_factory()
        if (row := self._cpu_row(ns)) is None:
            return None
        powers = _sensors_for(ns, row, _TYPE_POWER)
        return _named_value(powers, "package") or _max_value(powers)


# =========================================================================
# LhmGpu — one per LHM-detected GPU row
# =========================================================================


class LhmGpu(GpuSource):
    """GPU readings via LHM.

    Constructed per Hardware row that matches a ``Gpu*`` HardwareType.
    Multiple GPUs (e.g. iGPU + dGPU) get one instance each, keyed by
    LHM's Identifier so the aggregator can match by key.
    """

    def __init__(
        self,
        hardware_identifier: str,
        display_name: str,
        *,
        discrete: bool,
        handle_factory: Callable[[], Any] = _default_handle_factory,
    ) -> None:
        # Defer the handle to first read (born on the reading thread's
        # apartment).  Identity is the apartment-agnostic LHM identifier.
        self._handle_factory = handle_factory
        self._id = hardware_identifier
        self._display_name = display_name
        self._discrete = discrete

    @property
    def key(self) -> str:
        # LHM identifiers look like "/gpu-nvidia/0" — normalize to a
        # vendor key matching the rest of next/ ("nvidia:0").
        ident = self._id.lower().lstrip("/")
        if ident.startswith("gpu-nvidia"):
            return f"nvidia:{ident.rsplit('/', 1)[-1]}"
        if ident.startswith("gpu-amd"):
            return f"amd:{ident.rsplit('/', 1)[-1]}"
        if ident.startswith("gpu-intel"):
            return f"intel:{ident.rsplit('/', 1)[-1]}"
        return f"lhm:{ident}"

    @property
    def name(self) -> str:
        return self._display_name

    @property
    def is_discrete(self) -> bool:
        return self._discrete

    def _row(self, ns: Any) -> Any | None:
        if ns is None:
            return None
        try:
            rows = list(ns.Hardware(Identifier=self._id))
        except Exception:
            return None
        return rows[0] if rows else None

    def temp(self) -> float | None:
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        temps = _sensors_for(ns, row, _TYPE_TEMP)
        # Prefer "GPU Core"; fall back to max across all GPU temps.
        return _named_value(temps, "core") or _max_value(temps)

    def usage(self) -> float | None:
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        loads = _sensors_for(ns, row, _TYPE_LOAD)
        return _named_value(loads, "core") or _max_value(loads)

    def clock(self) -> float | None:
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        clocks = _sensors_for(ns, row, _TYPE_CLOCK)
        return _named_value(clocks, "core") or _max_value(clocks)

    def power(self) -> float | None:
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_POWER))

    def fan(self) -> float | None:
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_FAN))

    def vram_used(self) -> float | None:
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _named_value(
            _sensors_for(ns, row, _TYPE_SMALL_DATA), "used",
        )

    def vram_total(self) -> float | None:
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _named_value(
            _sensors_for(ns, row, _TYPE_SMALL_DATA), "total",
        )


# =========================================================================
# discover_lhm_gpus — one constructor per GPU row LHM reports
# =========================================================================


def discover_lhm_gpus(
    *,
    handle_factory: Callable[[], Any] = _default_handle_factory,
) -> list[LhmGpu]:
    """Enumerate GPUs that LHM is currently reporting on.

    Returns ``[]`` when LHM isn't running.  Each returned GpuSource is
    independently queryable; the chain wraps them with vendor-native
    sources (NVML, etc.) for higher-quality readings.
    """
    log.info("discover_lhm_gpus: called")
    ns = handle_factory()
    if ns is None:
        return []
    out: list[LhmGpu] = []
    try:
        for hw in ns.Hardware():
            ht = str(hw.HardwareType)
            if not ht.startswith(_HW_GPU_PREFIX):
                continue
            # iGPUs (typically GpuIntel) → non-discrete; dGPUs discrete.
            discrete = ht != "GpuIntel"
            out.append(LhmGpu(
                hardware_identifier=str(hw.Identifier),
                display_name=str(hw.Name),
                discrete=discrete,
                handle_factory=handle_factory,
            ))
    except Exception:
        log.debug("LHM GPU enumeration failed", exc_info=True)
    return out


# =========================================================================
# LhmDisk — one per LHM-detected Storage row (NVMe / SATA SSD / HDD)
# =========================================================================


class LhmDisk(DiskSource):
    """Storage temperature via LHM — one per ``Storage`` Hardware row.

    LHM's storage tree (``HardwareType == "Storage"``) carries a
    ``Temperature`` sensor per drive — the same WMI shape as the GPU
    temp, so this is :class:`LhmGpu`'s temperature path narrowed to the
    one reading the model's ``disk_temp`` slot needs.  Returns ``None``
    when LHM isn't running or the drive has no temp sensor yet.
    """

    def __init__(
        self,
        hardware_identifier: str,
        display_name: str,
        *,
        handle_factory: Callable[[], Any] = _default_handle_factory,
    ) -> None:
        # Defer the handle to first read (born on the reading thread's
        # apartment, #131); identity is the apartment-agnostic LHM identifier.
        self._handle_factory = handle_factory
        self._id = hardware_identifier
        self._display_name = display_name

    @property
    def key(self) -> str:
        # LHM storage identifiers look like "/nvme/0" or "/hdd/0" — flatten to
        # a stable per-drive key ("lhm:nvme:0").
        ident = self._id.lower().lstrip("/").replace("/", ":")
        return f"lhm:{ident}"

    @property
    def name(self) -> str:
        return self._display_name

    def _row(self, ns: Any) -> Any | None:
        if ns is None:
            return None
        try:
            rows = list(ns.Hardware(Identifier=self._id))
        except Exception:
            return None
        return rows[0] if rows else None

    def temp(self) -> float | None:
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_TEMP))


def discover_lhm_disks(
    *,
    handle_factory: Callable[[], Any] = _default_handle_factory,
) -> list[DiskSource]:
    """Enumerate storage devices LHM is currently reporting on.

    Returns ``[]`` when LHM isn't running.  The aggregator folds the hottest
    into ``disk:temp``; multiple drives each get their own source.
    """
    log.info("discover_lhm_disks: called")
    ns = handle_factory()
    if ns is None:
        return []
    out: list[DiskSource] = []
    try:
        for hw in ns.Hardware():
            if str(hw.HardwareType) != _HW_STORAGE:
                continue
            out.append(LhmDisk(
                hardware_identifier=str(hw.Identifier),
                display_name=str(hw.Name),
                handle_factory=handle_factory,
            ))
    except Exception:
        log.debug("LHM disk enumeration failed", exc_info=True)
    return out
