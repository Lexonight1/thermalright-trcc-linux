"""LibreHardwareMonitor sensor sources — read live values from LHM's WMI.

When a ``LibreHardwareMonitor.exe`` the user runs publishes the sensor tree
to the ``root\\LibreHardwareMonitor`` WMI namespace, this module reads it
through the ``CpuSource`` / ``GpuSource`` / ``DiskSource`` ports.

TRCC no longer ships or launches LHM (#319): LHM 0.9.5 removed its WMI
provider, so only an LHM 0.9.4 or older still publishes here.  CPU
temperature and power come from PawnIO (``_pawnio_cpu``) instead.

Wire format ported from legacy ``src/trcc/adapters/system/windows/
sources/lhm.py``.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from ...core.logs import per_frame, recurring_failure
from ...core.ports import CpuSource, DiskSource, GpuSource

log = logging.getLogger(__name__)
#: Per-tick readers — their records must never be CONSTRUCTED at
#: default verbosity.  73 ns/call short-circuited, measured.
frame_log = per_frame(__name__)


_LHM_NAMESPACE = "root\\LibreHardwareMonitor"

# Sensor.SensorType strings emitted by LHM.  Stable across LHM versions.
_TYPE_TEMP = "Temperature"
_TYPE_LOAD = "Load"
_TYPE_CLOCK = "Clock"
_TYPE_POWER = "Power"
_TYPE_FAN = "Fan"                 # RPM
_TYPE_CONTROL = "Control"         # fan duty, %
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
        # Probed on EVERY LHM read while the namespace is missing (start()
        # re-probes so it picks the namespace up the moment it registers), so
        # a host without it logged a traceback a dozen times per sweep,
        # forever -- seen on a VM, 0x8004100E WBEM_E_INVALID_NAMESPACE.
        recurring_failure(log, "LHM namespace %s unavailable", _LHM_NAMESPACE)
        return None
    return ns


class _UserLhm:
    """Whether the user's own LibreHardwareMonitor publishes WMI -- asked
    once per process.

    TRCC no longer ships or launches LHM: 0.9.5 removed its WMI provider
    (".NET 10 doesn't support it", LHM #2143), so a bundled copy gave TRCC
    nothing while costing a background process.  An LHM 0.9.4 or older the
    user runs still publishes, and is read.  Asked once, as the old launch
    was settled once: probing a namespace that is not there costs a WMI
    round-trip a tick on every machine without one.
    """

    def __init__(self, probe: Callable[[], Any] = _probe_wmi_namespace) -> None:
        log.debug("_UserLhm.__init__")
        self._probe = probe
        self._lock = threading.Lock()
        self._available: bool | None = None

    def available(self) -> bool:
        """True when an LHM's namespace answered the one probe."""
        with self._lock:
            if self._available is None:
                self._available = self._probe() is not None
                log.info("_UserLhm: %s", "a running LHM publishes WMI"
                         if self._available else
                         "no LHM WMI namespace -- LHM sources stay empty")
            return self._available


_USER_LHM = _UserLhm()
_handle_local = threading.local()


def _default_handle_factory() -> Any:
    """A per-thread handle on the user's LHM namespace, or None without one.

    The WMI handle is COM-apartment-bound -- one made on the main thread
    cannot be read from the poll thread (#131) -- so each thread gets its
    OWN ``wmi.WMI(namespace=...)``, cached thread-local for cheap repeats.
    Tests inject a custom ``handle_factory`` per source.
    """
    if not _USER_LHM.available():
        return None
    handle = getattr(_handle_local, "lhm_ns", None)
    if handle is not None:
        return handle
    try:
        import wmi  # pyright: ignore[reportMissingImports]
        handle = wmi.WMI(namespace=_LHM_NAMESPACE)
    except Exception:
        recurring_failure(log, "LHM per-thread WMI handle failed")
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
        recurring_failure(log, "LHM sensor query failed for %s/%s",
                  hw_row.Identifier, sensor_type)
        return []


def _max_value(sensors: list[Any]) -> float | None:
    """Return the maximum ``Value`` field across a list of LHM sensors."""
    log.debug("_max_value: sensors=%s", sensors)
    values = [float(s.Value) for s in sensors if s.Value is not None]
    return max(values) if values else None


def _named_value(sensors: list[Any], name_contains: str) -> float | None:
    """First sensor whose Name contains *name_contains* (case-insensitive)."""
    log.debug("_named_value: sensors=%s name_contains=%s", sensors, name_contains)
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
        log.debug("__init__")
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
        frame_log.debug("name")
        self._ensure_cpu_row(self._handle_factory())
        return self._name

    def _cpu_row(self, ns: Any) -> Any | None:
        frame_log.debug("_cpu_row: ns=%s", ns)
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
        frame_log.debug("temp")
        ns = self._handle_factory()
        if (row := self._cpu_row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_TEMP))

    def usage(self) -> float | None:
        """CPU total load 0-100 — LHM names this 'CPU Total'."""
        frame_log.debug("usage")
        ns = self._handle_factory()
        if (row := self._cpu_row(ns)) is None:
            return None
        loads = _sensors_for(ns, row, _TYPE_LOAD)
        # Prefer the explicit "CPU Total" sensor; fall back to max across cores.
        return _named_value(loads, "total") or _max_value(loads)

    def freq(self) -> float | None:
        """Highest CPU clock in MHz."""
        frame_log.debug("freq")
        ns = self._handle_factory()
        if (row := self._cpu_row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_CLOCK))

    def power(self) -> float | None:
        """Package power draw in W — LHM names this 'CPU Package'."""
        frame_log.debug("power")
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
        log.debug("__init__: hardware_identifier=%s display_name=%s", hardware_identifier, display_name)
        self._handle_factory = handle_factory
        self._id = hardware_identifier
        self._display_name = display_name
        self._discrete = discrete

    @property
    def key(self) -> str:
        # LHM identifiers look like "/gpu-nvidia/0" — normalize to a
        # vendor key matching the rest of next/ ("nvidia:0").
        frame_log.debug("key")
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
        frame_log.debug("name")
        return self._display_name

    @property
    def is_discrete(self) -> bool:
        frame_log.debug("is_discrete")
        return self._discrete

    def _row(self, ns: Any) -> Any | None:
        log.debug("_row: ns=%s", ns)
        if ns is None:
            return None
        try:
            rows = list(ns.Hardware(Identifier=self._id))
        except Exception:
            return None
        return rows[0] if rows else None

    def temp(self) -> float | None:
        frame_log.debug("temp")
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        temps = _sensors_for(ns, row, _TYPE_TEMP)
        # Prefer "GPU Core"; fall back to max across all GPU temps.
        return _named_value(temps, "core") or _max_value(temps)

    def usage(self) -> float | None:
        frame_log.debug("usage")
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        loads = _sensors_for(ns, row, _TYPE_LOAD)
        return _named_value(loads, "core") or _max_value(loads)

    def clock(self) -> float | None:
        frame_log.debug("clock")
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        clocks = _sensors_for(ns, row, _TYPE_CLOCK)
        return _named_value(clocks, "core") or _max_value(clocks)

    def power(self) -> float | None:
        frame_log.debug("power")
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_POWER))

    def fan(self) -> float | None:
        frame_log.debug("fan")
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_CONTROL))

    def fan_rpm(self) -> float | None:
        frame_log.debug("fan_rpm")
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _max_value(_sensors_for(ns, row, _TYPE_FAN))

    def vram_used(self) -> float | None:
        frame_log.debug("vram_used")
        ns = self._handle_factory()
        if (row := self._row(ns)) is None:
            return None
        return _named_value(
            _sensors_for(ns, row, _TYPE_SMALL_DATA), "used",
        )

    def vram_total(self) -> float | None:
        frame_log.debug("vram_total")
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
        log.debug("__init__: hardware_identifier=%s display_name=%s", hardware_identifier, display_name)
        self._handle_factory = handle_factory
        self._id = hardware_identifier
        self._display_name = display_name

    @property
    def key(self) -> str:
        # LHM storage identifiers look like "/nvme/0" or "/hdd/0" — flatten to
        # a stable per-drive key ("lhm:nvme:0").
        frame_log.debug("key")
        ident = self._id.lower().lstrip("/").replace("/", ":")
        return f"lhm:{ident}"

    @property
    def name(self) -> str:
        frame_log.debug("name")
        return self._display_name

    def _row(self, ns: Any) -> Any | None:
        log.debug("_row: ns=%s", ns)
        if ns is None:
            return None
        try:
            rows = list(ns.Hardware(Identifier=self._id))
        except Exception:
            return None
        return rows[0] if rows else None

    def temp(self) -> float | None:
        frame_log.debug("temp")
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
