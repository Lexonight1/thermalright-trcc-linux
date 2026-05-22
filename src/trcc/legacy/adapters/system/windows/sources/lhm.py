"""LibreHardwareMonitor sensor source (bundled, MPL-2.0).

Reads CPU/GPU/fan/voltage sensors via the ``root\\LibreHardwareMonitor``
WMI namespace, populated by ``LibreHardwareMonitor.exe`` running in the
background.  The C# Thermalright app does the equivalent for HWINFO
(closed-source freeware); we use LHM because it ships under MPL-2.0
and we can bundle and distribute it.

Design:
- **Idempotent.**  If LHM is already running (user-installed autostart
  or another TRCC process), reuse it.  Don't spawn a duplicate.
- **Window-suppressed.**  Bundled config is pre-seeded with
  start-minimized + minimize-to-tray.  Spawn also passes
  ``CREATE_NO_WINDOW`` — belt and suspenders for the WinForms host.
- **Owned vs. found.**  Track whether *we* spawned the process.  On
  stop, only terminate processes we own; never kill a user's own LHM.
- **Graceful degradation.**  If spawn fails (AV quarantine, missing exe,
  permission), log and ``probe()`` returns ``False``.  The enumerator
  falls through to lower-priority sources (MSAcpi) so we always have
  *something*.

WinRing0 caveat:  LHM bundles WinRing0 as the kernel driver that reads
MSRs.  Microsoft Defender increasingly flags WinRing0
(CVE-2020-14979, unpatched).  If LHM can't start because Defender
quarantined it, this source goes dark and MSAcpi tier takes over.
That's the *point* of the chain.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trcc.legacy.adapters.system.windows.sources._base import WindowsSensorSource
from trcc.legacy.core.models import SensorInfo

if TYPE_CHECKING:
    from trcc.legacy.adapters.system.windows.enumerator import WindowsSensorEnumerator

log = logging.getLogger(__name__)


# ── LHM WMI metadata ────────────────────────────────────────────────────

_LHM_PROCESS_NAME = "LibreHardwareMonitor.exe"
_LHM_NAMESPACE = "root\\LibreHardwareMonitor"
_WMI_READY_TIMEOUT_SEC = 30.0
_WMI_READY_INTERVAL_SEC = 0.2

# LHM SensorType → (category, unit).  Mirrors LibreHardwareMonitorLib's
# SensorType enum names.  Sensor types we don't recognise are skipped.
_LHM_TYPE_MAP: dict[str, tuple[str, str]] = {
    'Temperature': ('temperature', '°C'),
    'Fan':         ('fan', 'RPM'),
    'Clock':       ('clock', 'MHz'),
    'Load':        ('usage', '%'),
    'Power':       ('power', 'W'),
    'Voltage':     ('voltage', 'V'),
    'SmallData':   ('memory', 'MB'),
    'Data':        ('memory', 'GB'),
    'Throughput':  ('throughput', 'B/s'),
}


# =========================================================================
# LHM subprocess lifecycle — owned by LHMSource
# =========================================================================

class _LHMSubprocess:
    """Owns the bundled LHM process and the WMI namespace handle.

    One instance per ``LHMSource``.  Tracks the spawned ``Popen`` (or
    ``None`` if LHM was already running and we reused it).  ``stop()``
    terminates only what we own.
    """

    __slots__ = ("_namespace_handle", "_owned_process")

    def __init__(self) -> None:
        self._owned_process: subprocess.Popen[bytes] | None = None
        self._namespace_handle: Any = None

    @property
    def namespace(self) -> Any:
        """Cached WMI handle to ``root\\LibreHardwareMonitor`` or ``None``."""
        return self._namespace_handle

    def start(self) -> Any:
        """Ensure LHM is running and the WMI namespace is queryable.

        Detection is namespace-first: if ``root\\LibreHardwareMonitor``
        already returns Hardware rows, LHM is alive somewhere (manually
        installed, autostart, another TRCC process) — reuse it.  Falls
        back to spawning the bundled exe only if the namespace is
        absent.  Process detection via psutil is unreliable across user
        sessions and ACL boundaries; WMI is the actual contract.
        """
        if self._namespace_handle is not None:
            return self._namespace_handle

        existing = _probe_wmi_namespace()
        if existing is not None:
            log.info("LibreHardwareMonitor already running; reusing WMI namespace")
            self._namespace_handle = existing
            return existing

        self._owned_process = _spawn_lhm()
        if self._owned_process is None:
            log.warning("LibreHardwareMonitor not running and bundled exe not "
                        "found; this tier unavailable. Install LHM manually "
                        "or rely on the installer.")
            return None
        log.info("Spawned LibreHardwareMonitor (pid=%d)",
                 self._owned_process.pid)

        self._namespace_handle = _wait_for_wmi_namespace()
        if self._namespace_handle is None:
            log.warning("LibreHardwareMonitor WMI namespace did not register "
                        "within %.0fs; this tier unavailable",
                        _WMI_READY_TIMEOUT_SEC)
        return self._namespace_handle

    def stop(self) -> None:
        """Terminate the LHM subprocess if we spawned it."""
        self._namespace_handle = None
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


def _lhm_exe_path() -> Path | None:
    """Locate the bundled LibreHardwareMonitor.exe.

    Looks in ``<exe-dir>/lhm/`` (PyInstaller dist layout).  Returns
    ``None`` if the binary isn't present — graceful degradation rather
    than a hard error.
    """
    candidates = [
        Path(sys.executable).parent / "lhm" / _LHM_PROCESS_NAME,
        # Dev mode: assume someone dropped LHM here for testing.
        Path.cwd() / "lhm" / _LHM_PROCESS_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _probe_wmi_namespace() -> Any:
    """Single-attempt WMI namespace probe — handle on hit, ``None`` on miss."""
    from trcc.legacy.adapters.system._windows_wmi import wmi_handle
    try:
        handle = wmi_handle(namespace=_LHM_NAMESPACE)
        if list(handle.Hardware()):
            return handle
    except Exception as e:
        log.debug("LHM WMI namespace not available: %s", e)
    return None


def _spawn_lhm() -> subprocess.Popen[bytes] | None:
    """Launch the bundled LHM with a hidden window."""
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
        # SW_HIDE (0) — belt-and-suspenders for the WinForms main window.
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


def _wait_for_wmi_namespace() -> Any:
    """Poll ``root\\LibreHardwareMonitor`` until it registers or we time out."""
    from trcc.legacy.adapters.system._windows_wmi import wmi_handle

    deadline = time.monotonic() + _WMI_READY_TIMEOUT_SEC
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            handle = wmi_handle(namespace=_LHM_NAMESPACE)
            if list(handle.Hardware()):
                return handle
        except Exception as e:
            last_err = e
        time.sleep(_WMI_READY_INTERVAL_SEC)

    if last_err is not None:
        log.debug("WMI namespace probe last error: %s", last_err)
    return None


# =========================================================================
# LHMSource — the registered strategy
# =========================================================================

@WindowsSensorSource.register('lhm')
class LHMSource(WindowsSensorSource):
    """LHM strategy: bundled subprocess + WMI namespace polling."""

    priority = 20  # Between HWiNFO (10) and MSAcpi (30).
    name = "LibreHardwareMonitor"
    provides_gpu = True  # LHM covers NVIDIA/AMD/Intel GPUs with metrics.

    __slots__ = ("_gpu_seen", "_lhm")

    def __init__(self, subprocess: _LHMSubprocess | None = None) -> None:
        # DI seam — production creates the real subprocess lifecycle owner;
        # tests inject a stub with a pre-populated namespace handle.
        self._lhm = subprocess if subprocess is not None else _LHMSubprocess()
        self._gpu_seen = False

    # ── Strategy hooks ────────────────────────────────────────────────────

    def probe(self) -> bool:
        return self._lhm.start() is not None

    def contribute(self, enum: WindowsSensorEnumerator) -> None:
        self._gpu_seen = False
        for hw_key, hw_row in self._walk_nodes():
            if 'Gpu' in str(hw_row.HardwareType):
                self._gpu_seen = True
            self._register_node_sensors(enum, hw_key, hw_row)
        log.info("LHM discovery: %d sensors (GPU covered: %s)",
                 len(enum._sensors), self._gpu_seen)
        enum._register_poll(self.poll)

    def poll(self, enum: WindowsSensorEnumerator,
             readings: dict[str, float]) -> None:
        ns = self._lhm.namespace
        if ns is None:
            return
        try:
            for hw_key, hw_row in self._walk_nodes():
                self._read_node(readings, hw_key, hw_row)
        except Exception:
            log.debug("LHM poll failed", exc_info=True)

    def stop(self) -> None:
        self._lhm.stop()

    # ── GPU enumeration hook (called by enumerator.get_gpu_list) ──────────

    def gpu_list(self) -> list[tuple[str, str]]:
        """Return ``[(key, display_name), ...]`` for every LHM-detected GPU."""
        out: list[tuple[str, str]] = []
        try:
            for hw_key, hw_row in self._walk_nodes():
                if 'Gpu' in str(hw_row.HardwareType):
                    out.append((f'lhm:{hw_key}', str(hw_row.Name)))
        except Exception:
            log.debug("LHM GPU enumeration failed", exc_info=True)
        return out

    @property
    def gpu_seen(self) -> bool:
        """Did the most recent ``contribute()`` find any LHM GPU rows?"""
        return self._gpu_seen

    # ── Internal walkers (single chokepoint) ──────────────────────────────

    @staticmethod
    def _node_key(hw_row: Any) -> str:
        """Normalise a Hardware row's Name into a sensor-ID-safe key.

        Identical formula to v9.5.x so existing user theme configs continue
        to match across the pythonnet→WMI→strategy-chain migrations.
        """
        return str(hw_row.Name).lower().replace(' ', '_')[:20]

    def _walk_nodes(self):
        """Yield ``(hw_key, hw_row)`` for every Hardware row from WMI."""
        ns = self._lhm.namespace
        if ns is None:
            return
        for hw_row in ns.Hardware():
            yield self._node_key(hw_row), hw_row

    def _register_node_sensors(
        self,
        enum: WindowsSensorEnumerator,
        hw_key: str,
        hw_row: Any,
    ) -> None:
        hw_name = str(hw_row.Name)
        ns = self._lhm.namespace
        if ns is None:
            return
        for sensor in ns.Sensor(Parent=hw_row.Identifier):
            s_type = str(sensor.SensorType)
            s_name = str(sensor.Name)
            if not (mapping := _LHM_TYPE_MAP.get(s_type)):
                continue
            category, unit = mapping
            sid = f'lhm:{hw_key}:{s_name.lower().replace(" ", "_")}'
            enum._sensors.append(
                SensorInfo(sid, f'{hw_name} {s_name}', category, unit, 'lhm'),
            )

    def _read_node(
        self,
        readings: dict[str, float],
        hw_key: str,
        hw_row: Any,
    ) -> None:
        ns = self._lhm.namespace
        if ns is None:
            return
        for sensor in ns.Sensor(Parent=hw_row.Identifier):
            val = sensor.Value
            if val is None:
                continue
            s_name = str(sensor.Name).lower().replace(' ', '_')
            readings[f'lhm:{hw_key}:{s_name}'] = float(val)


__all__ = ['LHMSource']
