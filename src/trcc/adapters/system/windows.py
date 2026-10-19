r"""WindowsPlatform — concrete Platform implementation for Windows.

Owns every Windows-specific thing: `\\.\PhysicalDriveN` resolution via
WMI, SCSI passthrough via `DeviceIoControl`, APPDATA path resolution,
Win32-style autostart.

This file imports Windows-only modules (wmi, ctypes.windll) lazily
inside methods so the module itself loads cleanly on Linux during
static analysis and cross-OS tests.
"""
from __future__ import annotations

import ctypes
import logging
import os
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, NoReturn

from ...core.errors import PermissionError_, TransportError
from ...core.logs import per_frame
from ...core.models import (
    DisplayServer,
    DisplaySession,
    memory_form_factor,
    memory_type,
)
from ...core.ports import (
    AutostartManager,
    HotplugMonitor,
    Paths,
    ScsiTransport,
    SensorEnumerator,
)
from ..device.scsi_transport import BaseScsiTransport, CdbResult
from ..infra.named_pipe import NamedPipeChannel
from ..sensors._pawnio_cpu import PawnioCpu
from ..sensors.windows import build_windows_sensors
from ..visual import WglSurface, drawn_on
from ..visual.loopback import LoopbackSoundInput
from ._base import BaseOS, BasePaths
from ._windows_drives import find_physical_drive
from ._windows_wmi import wmi_handle

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


# =========================================================================
# WindowsPaths — APPDATA/LOCALAPPDATA
# =========================================================================


class WindowsPaths(BasePaths):
    """Windows user-data locations via APPDATA / LOCALAPPDATA."""

    def __init__(self) -> None:
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        self._root = Path(appdata) / "trcc"
        self._user_content = Path(local) / "trcc-user"
        log.info("WindowsPaths: root=%s user_content=%s",
                 self._root, self._user_content)


# =========================================================================
# DeviceIoControl — Windows SCSI passthrough
# =========================================================================

# Both directions, as USBLCD.exe (USBLCD.exe.c:16684): the buffered form
# (0x4D004) is for transfers under 16 KB, and the poll reads 57,600 bytes.
_IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014
_SCSI_IOCTL_DATA_OUT = 0
_SCSI_IOCTL_DATA_IN = 1
_SENSE_LENGTH = 32


class _SCSI_PASS_THROUGH_DIRECT(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_uint16),
        ("ScsiStatus", ctypes.c_uint8),
        ("PathId", ctypes.c_uint8),
        ("TargetId", ctypes.c_uint8),
        ("Lun", ctypes.c_uint8),
        ("CdbLength", ctypes.c_uint8),
        ("SenseInfoLength", ctypes.c_uint8),
        ("DataIn", ctypes.c_uint8),
        ("DataTransferLength", ctypes.c_uint32),
        ("TimeOutValue", ctypes.c_uint32),
        ("DataBuffer", ctypes.c_void_p),
        ("SenseInfoOffset", ctypes.c_uint32),
        ("Cdb", ctypes.c_uint8 * 16),
    ]


class _SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER(ctypes.Structure):
    _fields_ = [
        ("sptd", _SCSI_PASS_THROUGH_DIRECT),
        ("sense", ctypes.c_uint8 * _SENSE_LENGTH),
    ]


def _kernel32() -> Any:
    """Access kernel32 only when needed (Windows-only attribute).  Called on
    every SCSI command, so its line is per-frame."""
    frame_log.debug("_kernel32: called")
    return ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]


def _is_elevated() -> bool:
    """True if the process holds Administrator rights.

    Raw ``\\\\.\\PhysicalDriveN`` read/write (SCSI passthrough) and PawnIO
    need admin: an elevated process uses them directly, any other goes
    through TRCC's SYSTEM service.  No build asks for elevation; an
    administrator terminal is the one way in.  Returns ``True``
    (assume fine, emit no warning) if ``shell32`` can't be reached — we only
    warn when we are *sure* the user lacks elevation.
    """
    log.debug("_is_elevated")
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # pyright: ignore[reportAttributeAccessIssue]
    except (OSError, AttributeError):
        return True


def make_pawnio_cpu() -> PawnioCpu:
    """PawnIO as this process can reach it: directly when elevated, else
    through TRCC's SYSTEM service (the driver admits only administrators).
    The one place that decides -- the sensors and ``trcc doctor`` both ask."""
    if _is_elevated():
        log.info("make_pawnio_cpu: elevated -- PawnIO directly")
        return PawnioCpu()
    from ._service_client import open_service_module
    log.info("make_pawnio_cpu: not elevated -- PawnIO through TRCC's service")
    return PawnioCpu(open_module=open_service_module)


# Common CreateFileW failures when opening \\.\PhysicalDriveN for SCSI
# passthrough — translated in the log so a reporter doesn't have to look up
# the bare WinError number.
#: ``ERROR_ACCESS_DENIED``: the user's setup, not a panel that is not ready.
_ERROR_ACCESS_DENIED = 5

_WIN_OPEN_ERRORS: dict[int, str] = {
    2:  "ERROR_FILE_NOT_FOUND — the drive path is gone (replug / rescan)",
    5:  "ERROR_ACCESS_DENIED — raw drive access needs Administrator; "
        "run TRCC from an elevated terminal (Run as administrator)",
    32: "ERROR_SHARING_VIOLATION — another process holds the device open",
}

# GetLastError codes that mean the drive is gone -- no retry will reach it, so
# the base raises disconnect-class and the recovery threshold marks the panel
# lost (Linux: DID_NO_CONNECT).  ERROR_GEN_FAILURE (31) is NOT here: USBLCD.exe
# retries it (USBLCD.exe.c:16684-16695).
#
# Measured on the win11 VM (2026-10-18, dev/tools/win_vm/gone_probe.py: a USB
# disk pulled mid-stream): the command in flight times out (121, status GOOD --
# not gone, the poll returns it too), the next few get 1167, and every one
# after, until the handle closes, gets 55.  433 and 1617 never appeared; they
# stay because their meaning is "removed", so no retryable error is lost.
_GONE_ERRORS: dict[int, str] = {
    55:   "ERROR_DEV_NOT_EXIST",             # measured: after removal completes
    433:  "ERROR_NO_SUCH_DEVICE",
    1167: "ERROR_DEVICE_NOT_CONNECTED",      # measured: right after the pull
    1617: "ERROR_DEVICE_REMOVED",
}

#: How the panel answers its handshake poll on Windows: ERROR_SEM_TIMEOUT with
#: SCSI status GOOD -- and the reply delivered anyway.  Measured on 0402:3922
#: (win11 VM, 2026-10-18): the buffer, pre-filled with 0xAA, came back with all
#: 57,600 bytes written and byte 0 = 0x64, its FBL.  Linux sees the same poll as
#: host=7 with the data delivered (#301), and USBLCD.exe never checks the call
#: at all -- it reads the identity byte straight out of the buffer.
_ERROR_SEM_TIMEOUT = 121

#: The single-owner claim on a drive, like Linux's flock: a named SEMAPHORE of
#: count 1.  Not a mutex -- a mutex belongs to the thread that took it, and the
#: transport may close on another thread.  Windows deletes the object when the
#: last handle closes, so a process that dies frees the panel, as flock does.
#: Global\ spans sessions: the service runs in session 0, an elevated App in
#: the user's.
_CLAIM_PREFIX = "Global\\TRCC-scsi-"
_WAIT_OBJECT_0, _WAIT_TIMEOUT = 0x0, 0x102
#: Who may share the claim: SYSTEM (the service) and Administrators (an elevated
#: App), SEMAPHORE_ALL_ACCESS each.  Without it the object takes its creator's
#: default DACL, and an elevated App got ERROR_ACCESS_DENIED opening the
#: service's claim -- so it ran unguarded beside it (win11 VM, 2026-10-18).  A
#: non-admin App never opens a drive; it goes through the service.
_CLAIM_SDDL = "D:(A;;0x1F0003;;;SY)(A;;0x1F0003;;;BA)"


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", ctypes.c_uint32),
                ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", ctypes.c_int)]


def _advapi32() -> Any:
    """advapi32, for the claim's security descriptor (Windows-only)."""
    log.debug("_advapi32: called")
    return ctypes.windll.advapi32  # pyright: ignore[reportAttributeAccessIssue]


def _spt_result(ok: bool, err: int, scsi_status: int, transferred: int,
                data: bytes = b"") -> CdbResult:
    """What one DeviceIoControl pass-through did -- no policy here."""
    if ok and scsi_status == 0:
        return CdbResult(ok=True, data=data)     # the per-frame path: no log
    frame_log.debug("_spt_result: ok=%s err=%d status=%d transferred=%d",
                    ok, err, scsi_status, transferred)
    if not ok and err in _GONE_ERRORS:
        return CdbResult(ok=False, gone=True, detail=_GONE_ERRORS[err])
    if ok:
        return CdbResult(ok=False, detail=f"SCSI status {scsi_status}")
    return CdbResult(ok=False, detail=f"WinError {err} (transferred {transferred})")


class WindowsScsiTransport(BaseScsiTransport):
    r"""SCSI over ``\\.\PhysicalDriveN``: one DeviceIoControl per CDB.

    Both directions go through IOCTL_SCSI_PASS_THROUGH_DIRECT, as USBLCD.exe
    does for frames AND the poll (USBLCD.exe.c:16684) -- the buffered form is
    meant for transfers under 16 KB, and the poll reads 57,600 bytes.  Write
    buffers are cached by size and filled with one memmove, like Linux: they
    were rebuilt byte by byte, ~298 M instructions per 320x320 frame
    (measured 2026-10-18).  Logging, the failure policy and open/close are
    ``BaseScsiTransport``'s.
    """

    def __init__(self, device_path: str) -> None:
        self._handle: int | None = None
        # {data_len: (sptd_with_sense, data_buf)} -- one set per size class.
        self._bufs: dict[int, tuple[Any, Any]] = {}
        self._claim_handle: int | None = None
        log.debug("WindowsScsiTransport.__init__: %s", device_path)
        super().__init__(device_path)

    def _open_handle(self) -> bool:
        log.debug("WindowsScsiTransport._open_handle: %s", self._path)
        try:
            GENERIC_READ_WRITE = 0xC0000000
            FILE_SHARE_READ_WRITE = 0x3
            OPEN_EXISTING = 3
            handle = _kernel32().CreateFileW(
                self._path, GENERIC_READ_WRITE, FILE_SHARE_READ_WRITE,
                None, OPEN_EXISTING, 0, None,
            )
        except Exception:
            log.exception("Failed to open %s", self._path)
            return False
        if handle == -1:
            err = ctypes.GetLastError()  # pyright: ignore[reportAttributeAccessIssue]
            detail = _WIN_OPEN_ERRORS.get(
                err, "see Microsoft 'System Error Codes'")
            log.error("CreateFileW failed for %s — WinError %d: %s",
                      self._path, err, detail)
            if err == _ERROR_ACCESS_DENIED:
                # Raised, like every other transport: a plain False read as
                # "not answering yet", so the reconnect watcher retried a panel
                # only elevation can open, without saying so.
                raise PermissionError_(f"{self._path}: {detail}")
            return False
        self._handle = handle
        return True

    def _claim(self) -> bool:
        """Take the drive's single-owner claim, or refuse the open.

        Two owners interleave frames on this wire with no error anywhere --
        Linux's flock exists because that was measured.  An error that is not
        "someone holds it" degrades to running without the guard rather than
        failing a panel we could otherwise drive, as on Linux.
        """
        name = _CLAIM_PREFIX + self._path.rsplit("\\", 1)[-1].upper()
        k32 = _kernel32()
        descriptor = ctypes.c_void_p()
        if not _advapi32().ConvertStringSecurityDescriptorToSecurityDescriptorW(
                _CLAIM_SDDL, 1, ctypes.byref(descriptor), None):
            log.warning("WindowsScsiTransport: claim descriptor failed (WinError "
                        "%d) — continuing WITHOUT the single-owner guard",
                        ctypes.GetLastError())  # pyright: ignore[reportAttributeAccessIssue]
            return True
        attributes = _SECURITY_ATTRIBUTES(ctypes.sizeof(_SECURITY_ATTRIBUTES),
                                          descriptor, 0)
        handle = k32.CreateSemaphoreW(ctypes.byref(attributes), 1, 1, name)
        k32.LocalFree(descriptor)
        if not handle:
            log.warning("WindowsScsiTransport: claim %s unavailable (WinError %d) "
                        "— continuing WITHOUT the single-owner guard", name,
                        ctypes.GetLastError())  # pyright: ignore[reportAttributeAccessIssue]
            return True
        waited = k32.WaitForSingleObject(handle, 0)
        if waited == _WAIT_OBJECT_0:
            self._claim_handle = handle
            log.info("WindowsScsiTransport: claimed %s (%s)", self._path, name)
            return True
        k32.CloseHandle(handle)
        if waited == _WAIT_TIMEOUT:
            log.warning(
                "WindowsScsiTransport: %s is already claimed by another TRCC "
                "process (GUI, App or service) — refusing to open it.  Two "
                "owners interleave frames on this wire, so close the other one, "
                "or run `trcc kill` if an App holds it.", self._path)
            return False
        log.warning("WindowsScsiTransport: claim %s wait returned %#x — "
                    "continuing WITHOUT the single-owner guard", name, waited)
        return True

    def _release(self) -> None:
        """Give the claim back; the last handle closing deletes it."""
        handle, self._claim_handle = self._claim_handle, None
        log.debug("WindowsScsiTransport._release: %s", handle)
        if handle is not None:
            k32 = _kernel32()
            k32.ReleaseSemaphore(handle, 1, None)
            k32.CloseHandle(handle)

    def _close_handle(self) -> None:
        log.debug("WindowsScsiTransport._close_handle: %s", self._handle)
        handle, self._handle = self._handle, None
        self._bufs.clear()
        if handle is not None and not _kernel32().CloseHandle(handle):
            log.warning("CloseHandle %s failed: WinError %d", self._path,
                        ctypes.GetLastError())  # pyright: ignore[reportAttributeAccessIssue]

    def _buffers(self, length: int) -> tuple[Any, Any]:
        """The cached pass-through block and data buffer for *length* bytes."""
        bufs = self._bufs.get(length)
        if bufs is None:
            frame_log.debug("WindowsScsiTransport._buffers: new size %d", length)
            bufs = (_SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER(),
                    (ctypes.c_uint8 * max(1, length))())
            self._bufs[length] = bufs
        return bufs

    def _pass_through(self, cdb: bytes, length: int, data_in: bool,
                      timeout_ms: int) -> tuple[bool, int, Any, Any]:
        """One IOCTL_SCSI_PASS_THROUGH_DIRECT; the caller filled data-out."""
        sptdwb, data_buf = self._buffers(length)
        sptd = sptdwb.sptd
        sptd.Length = ctypes.sizeof(_SCSI_PASS_THROUGH_DIRECT)
        sptd.CdbLength = min(len(cdb), 16)
        sptd.SenseInfoLength = _SENSE_LENGTH
        sptd.DataIn = _SCSI_IOCTL_DATA_IN if data_in else _SCSI_IOCTL_DATA_OUT
        sptd.DataTransferLength = length
        sptd.TimeOutValue = max(1, timeout_ms // 1000)   # Windows wants seconds
        sptd.DataBuffer = ctypes.addressof(data_buf)
        sptd.SenseInfoOffset = ctypes.sizeof(_SCSI_PASS_THROUGH_DIRECT)
        ctypes.memmove(sptd.Cdb, bytes(cdb[:16]).ljust(16, b"\0"), 16)
        returned = ctypes.c_uint32(0)
        ok = bool(_kernel32().DeviceIoControl(
            self._handle, _IOCTL_SCSI_PASS_THROUGH_DIRECT,
            ctypes.byref(sptdwb), ctypes.sizeof(sptdwb),
            ctypes.byref(sptdwb), ctypes.sizeof(sptdwb),
            ctypes.byref(returned), None,
        ))
        err = 0 if ok else ctypes.GetLastError()  # pyright: ignore[reportAttributeAccessIssue]
        if not ok:
            log.debug("WindowsScsiTransport._pass_through: WinError %d", err)
        return ok, err, sptd, data_buf

    def _send(self, cdb: bytes, data: bytes, timeout_ms: int) -> CdbResult:
        """CDB + data-out: one memmove into the cached buffer, one ioctl."""
        _sptdwb, data_buf = self._buffers(len(data))
        if data:
            ctypes.memmove(data_buf, data, len(data))
        else:
            log.debug("WindowsScsiTransport._send: no data phase")
        ok, err, sptd, _ = self._pass_through(cdb, len(data), False, timeout_ms)
        return _spt_result(ok, err, sptd.ScsiStatus, sptd.DataTransferLength)

    def _read(self, cdb: bytes, length: int, timeout_ms: int) -> CdbResult:
        """CDB + data-in, DIRECT like USBLCD.exe.

        The buffer is zeroed first, so a reply can only be what this read
        delivered.  The poll's ERROR_SEM_TIMEOUT with status GOOD carries its
        reply (``_ERROR_SEM_TIMEOUT``), which is kept; nothing else that
        failed is.
        """
        log.debug("WindowsScsiTransport._read: length=%d", length)
        _sptdwb, data_buf = self._buffers(length)
        ctypes.memset(data_buf, 0, length)
        ok, err, sptd, _ = self._pass_through(cdb, length, True, timeout_ms)
        if ok:
            moved = min(sptd.DataTransferLength, length)
            return _spt_result(ok, err, sptd.ScsiStatus, moved,
                               bytes(data_buf[:moved]))
        reply = bytes(data_buf[:length])
        if err == _ERROR_SEM_TIMEOUT and sptd.ScsiStatus == 0 and any(reply):
            return CdbResult(ok=False, data=reply,
                             detail="WinError 121 with SCSI status GOOD")
        return _spt_result(ok, err, sptd.ScsiStatus, 0)


# =========================================================================
# Stubs — real impls land later
# =========================================================================


# =========================================================================
# COM apartment — per-thread WMI setup
# =========================================================================


class _ComApartment:
    """Open a COM apartment for the current thread (for off-main-thread WMI).

    A worker thread must ``CoInitialize`` before touching WMI, and COM
    objects are apartment-bound — a handle created in one thread's
    apartment can't be used from another.  Wrapping a worker thread's body
    in this (via ``WindowsPlatform.worker_thread_context``) gives it its
    own apartment so WMI sensor reads work off the main thread.

    Matches ``_hotplug.WindowsHotplugMonitor._watch_loop``: ``CoInitialize``
    on enter, no ``CoUninitialize`` — the daemon poll thread holds the
    apartment for its lifetime and releases it on process exit.
    ``pythoncom`` absent (non-Windows / missing dep) degrades to a no-op
    so the manager is import-safe everywhere.
    """

    def __enter__(self) -> None:
        try:
            import pythoncom  # type: ignore[import-not-found,import-untyped]
        except ImportError:
            log.debug("_ComApartment: pythoncom unavailable — no-op")
            return
        try:
            pythoncom.CoInitialize()
        except pythoncom.com_error as e:
            # Already initialised on this thread (the main thread after Qt,
            # or a second entry) -- the apartment is there, which is all we
            # need.  Same handling as ``_windows_wmi.wmi_handle``.
            log.debug("_ComApartment: COM already initialised here (%s)", e)

    def __exit__(self, *exc: object) -> None:
        log.debug("__exit__")
        return None


# =========================================================================
# WindowsPlatform
# =========================================================================

# tool → winget/pip one-liner (consumed by software_install_hint).
_WIN_INSTALL_HINTS: dict[str, str] = {
    "ffmpeg": "winget install Gyan.FFmpeg",
    "7z": "winget install 7zip.7zip",
    "python": "winget install Python.Python.3.12",
    "pynvml": "pip install nvidia-ml-py",
}


class WindowsPlatform(BaseOS, key="win32"):
    """Windows implementation — same OS contract; only internals below differ."""

    _INSTALL_HINTS = _WIN_INSTALL_HINTS

    #: Audio visuals: what is playing, by WASAPI loopback of the default
    #: output (``adapters/visual/loopback.py``), drawn on WGL behind a
    #: hidden window -- headless too.
    SOUND_INPUT = LoopbackSoundInput
    #: CPython has no AF_UNIX on Windows: the App is reached by a named pipe.
    IPC_CHANNEL = NamedPipeChannel
    VISUALS = drawn_on(WglSurface)

    # ── Per-OS internals (the add-a-new-OS interface) ────────────────────

    def _make_paths(self) -> Paths:
        log.debug("_make_paths")
        return WindowsPaths()

    def _build_sensors(self) -> SensorEnumerator:
        """Strategy chain: HWiNFO → LHM → MSAcpi → psutil/NVML baseline."""
        log.debug("_build_sensors")
        return build_windows_sensors(thread_context=self.worker_thread_context,
                                     pawnio=make_pawnio_cpu())

    def _build_autostart(self) -> AutostartManager:
        """The Run key, for the installer build and pip alike.

        Neither is elevated -- TRCC's service does what needed admin -- and
        the Run key starts an unelevated program at sign-in (measured: 9.7.3
        autostarted from it).  The command differs per build; the key does not.
        """
        from ._autostart import WindowsAutostart
        log.info("_build_autostart: Run key")
        return WindowsAutostart()

    def _build_hotplug(self) -> HotplugMonitor:
        log.debug("_build_hotplug")
        from ._hotplug import WindowsHotplugMonitor
        return WindowsHotplugMonitor()

    def _open_scsi(self, vid: int, pid: int, serial: str | None = None,
                  unit: str = "") -> ScsiTransport:
        r"""Windows SCSI passthrough over a ``\\.\PhysicalDriveN`` handle.

        *unit* picks which of two identical panels (#287): ``find_physical_drive``
        names each disk by the socket it is in, as the scan names the panel.
        """
        log.info("open_scsi: %04x:%04x serial=%r unit=%s",
                 vid, pid, serial, unit or "(only)")
        if not _is_elevated():
            # A drive admits only administrators: TRCC's SYSTEM service opens
            # it, and checks every command against what TRCC sends.
            from ._service_client import ServiceScsiTransport
            log.info("open_scsi: not elevated -- through TRCC's service")
            return ServiceScsiTransport(vid, pid, unit)
        path = find_physical_drive(vid, pid, unit)
        if path is None:
            raise TransportError(
                f"No PhysicalDrive found for {vid:04x}:{pid:04x}"
                f"{f' in {unit}' if unit else ''} — ensure the device is "
                "attached and visible as a USB mass-storage disk"
            )
        log.debug("WindowsPlatform.open_scsi: %04x:%04x → %s", vid, pid, path)
        return WindowsScsiTransport(path)

    def worker_thread_context(self) -> AbstractContextManager[None]:
        """Open a COM apartment for a worker thread so WMI sensor reads
        work off the main thread (overrides the Platform no-op default)."""
        log.debug("worker_thread_context: COM apartment")
        return _ComApartment()

    def setup(self, dry_run: bool = False) -> int:
        """Diagnose WinUSB driver state and print Zadig instructions.

        Read-only: Windows driver installation needs UAC + a signed
        driver package, which this script can't fake.  ``dry_run``
        is accepted for parity with other platforms but ignored —
        diagnostic is the same either way.
        """
        log.info("setup: dry_run=%s", dry_run)
        from ._winusb import install
        return install(dry_run=dry_run)

    def check_permissions(self) -> list[str]:
        """What stands between this process and the panels, in words.

        Not elevation: a process that is not administrator reaches SCSI
        panels and PawnIO through TRCC's service, so the one thing that can
        be missing is the service.  This said "Not running as administrator
        -- will fail" to every such process, which stopped being true when
        the service arrived and is every installed TRCC since.
        """
        if _is_elevated():
            log.info("check_permissions: elevated -- direct access")
            return []
        from ._service_client import NOT_RUNNING, connect_service
        try:
            connect_service(timeout=1.0).close()
        except OSError as e:
            log.warning("check_permissions: the service does not answer: %s", e)
            return [NOT_RUNNING]
        log.info("check_permissions: not elevated -- the service answers")
        return []

    def display_session(self) -> DisplaySession:
        """Native windowing, whatever a leftover session variable says."""
        log.info("%s.display_session: native", type(self).__name__)
        return DisplaySession(DisplayServer.NATIVE)

    def distro_name(self) -> str:
        log.info("distro_name: called")
        return "Windows"

    def permission_denied_hint(self) -> str:
        log.debug("WindowsPlatform.permission_denied_hint: called")
        # WinUSB lets ONE program hold a device, and libusb reports "held by
        # another program" as access denied -- so with autostart on, a running
        # TRCC window or tray makes every CLI command fail exactly like a
        # missing driver (#173).
        return ("install the WinUSB driver via 'trcc system setup', and make "
                "sure no other TRCC (window, tray or daemon) is running — "
                "WinUSB lets only one program use the device")

    def no_devices_hint(self) -> str:
        log.debug("no_devices_hint: called")
        return (
            "On Windows, non-SCSI devices (HID / Bulk / LY) need the WinUSB "
            "driver — install it with Zadig (https://zadig.akeo.ie/). "
            "SCSI LCD panels need no driver."
        )

    def hard_exit(self, code: int) -> NoReturn:
        """``TerminateProcess`` on this process: Windows' ``_exit``.

        ``os._exit`` here ends in ``ExitProcess``, which kills the other
        threads wherever they stand -- one may hold the GIL -- and then runs
        every DLL's detach code, Qt's included.  Qt's teardown reaches PySide
        for the Python-made QObjects still alive, and about one GUI exit in
        three crashed in ``SignalManager::retrieveMetaObject`` (0xc0000005 at
        pyside6.abi3.dll+0x1c91e, measured on the win11 VM, 2026-10-19).
        ``TerminateProcess`` runs no detach code, as ``_exit`` runs none on
        Linux.  ``os._exit`` stays the fallback if Windows refuses.
        """
        log.info("hard_exit: TerminateProcess(%d)", code)
        kernel32 = _kernel32()
        if not kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code):
            log.warning("hard_exit: TerminateProcess failed (WinError %d) "
                        "-- os._exit", kernel32.GetLastError())
        os._exit(code)

    def configure_stdout(self) -> None:
        """Rewrap stdout / stderr as UTF-8 with ``errors='replace'``.

        Windows consoles default to cp1252 — any non-ASCII codepoint
        (log emoji, Chinese / Japanese / Korean device names, °, ×)
        raises ``UnicodeEncodeError`` from the logging StreamHandler
        and crashes the worker.  Legacy parity at
        ``legacy/adapters/system/windows_platform.py:295``.

        Idempotent: callers may invoke this twice across CLI→GUI
        re-entry.  TextIOWrapper.reconfigure handles that natively;
        for the older buffer-wrapping path we no-op when the encoding
        is already UTF-8.
        """
        log.info("configure_stdout: called")
        import io
        import sys as _sys
        for stream_name in ("stdout", "stderr"):
            stream = getattr(_sys, stream_name, None)
            if stream is None:
                continue
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                try:
                    reconfigure(encoding="utf-8", errors="replace")
                except (OSError, ValueError) as e:
                    log.debug(
                        "configure_stdout: %s.reconfigure failed (%s) "
                        "— falling back to TextIOWrapper",
                        stream_name, e,
                    )
                else:
                    continue
            buf = getattr(stream, "buffer", None)
            if buf is None:
                continue
            current_enc = (getattr(stream, "encoding", "") or "").lower()
            if current_enc.replace("-", "") == "utf8":
                continue
            setattr(_sys, stream_name, io.TextIOWrapper(
                buf, encoding="utf-8", errors="replace",
            ))

    # ── Hardware probes (LED memory + disk widgets) ───────────────────

    def memory_info(self) -> list[dict[str, str]]:
        """DRAM slot probe via WMI Win32_PhysicalMemory."""
        log.info("memory_info: probing")
        return _windows_memory_info()

    def disk_info(self) -> list[dict[str, str]]:
        """Disk probe via WMI Win32_DiskDrive."""
        log.info("disk_info: probing")
        return _windows_disk_info()


# =========================================================================
# Windows hardware-probe helpers
# =========================================================================


def _format_size_bytes(value: int | str | None) -> str:
    """Human-format a byte count as ``N GB`` (best-effort).

    WMI returns int / str / None depending on the property and version
    — narrow before coercing rather than blanket try/except.
    """
    log.debug("_format_size_bytes: value=%r", value)
    if value is None or value == 0 or value == "0":
        return ""
    if isinstance(value, int):
        return f"{value // (1024 ** 3)} GB"
    if isinstance(value, str):
        try:
            return f"{int(value) // (1024 ** 3)} GB"
        except ValueError:
            return ""
    return ""


def _windows_memory_info() -> list[dict[str, str]]:
    """Win32_PhysicalMemory probe; psutil fallback for totals."""
    log.debug("_windows_memory_info: called")
    slots: list[dict[str, str]] = []
    try:
        w = wmi_handle()
    except ImportError:
        log.debug("wmi package missing — falling back to psutil total")
        try:
            import psutil
            total = psutil.virtual_memory().total
            slots.append({
                "manufacturer": "Unknown", "part_number": "Unknown",
                "type": "Unknown", "speed": "Unknown",
                "size": f"{total // (1024 ** 3)} GB",
                "form_factor": "Unknown", "locator": "Total",
            })
        except (OSError, ImportError, AttributeError) as e:
            log.debug("psutil fallback failed: %s", type(e).__name__)
        return slots
    try:
        for mem in w.Win32_PhysicalMemory():
            slot: dict[str, str] = {
                "manufacturer": (mem.Manufacturer or "").strip(),
                "part_number": (mem.PartNumber or "").strip(),
                "speed": str(mem.ConfiguredClockSpeed or mem.Speed or ""),
                "size": _format_size_bytes(mem.Capacity),
                "locator": mem.DeviceLocator or "",
                "rank": str(mem.Rank or ""),
                "data_width": str(mem.DataWidth or ""),
                "total_width": str(mem.TotalWidth or ""),
                "type": memory_type(mem.SMBIOSMemoryType),
                "form_factor": memory_form_factor(mem.FormFactor),
            }
            if slot["size"] and slot["size"] != "0 GB":
                slots.append(slot)
    except Exception as e:  # WMI surface is wide; log and fall through
        log.debug("WMI memory query failed: %s", type(e).__name__)
    return slots


def _disk_type(disk: Any) -> str:
    """SSD / HDD / Unknown from a ``Win32_DiskDrive`` row.

    WMI has no clean rotational flag, so fall back to the model and
    ``MediaType`` strings — the same heuristic legacy used.
    """
    log.debug("_disk_type: disk=%s", disk)
    model = (getattr(disk, "Model", "") or "").upper()
    media_type = (getattr(disk, "MediaType", "") or "").upper()
    if "SSD" in model or "NVME" in model or "SOLID" in media_type:
        return "SSD"
    if "HDD" in model or "FIXED" in media_type:
        return "HDD"
    return "Unknown"


def _disk_health(device_id: str | None) -> str:
    """SMART overall health via ``MSStorageDriver_FailurePredictStatus``.

    Lives in the ``root\\WMI`` namespace (distinct from the default
    ``root\\cimv2``).  Returns ``PASSED`` / ``FAILED`` / ``Unknown``.
    """
    if not device_id:
        return "Unknown"
    try:
        w = wmi_handle(namespace="root\\WMI")
        for status in w.MSStorageDriver_FailurePredictStatus():
            if status.Active:
                return "FAILED" if status.PredictFailure else "PASSED"
    except ImportError:
        log.debug("wmi package missing — no SMART health probe")
    except Exception as e:  # WMI/COM exceptions share no common base
        log.debug("MSStorageDriver_FailurePredictStatus failed: %s", type(e).__name__)
    return "Unknown"


def _windows_disk_info() -> list[dict[str, str]]:
    """Win32_DiskDrive probe — name, model, size, type, SMART health."""
    log.debug("_windows_disk_info: called")
    disks: list[dict[str, str]] = []
    try:
        w = wmi_handle()
    except ImportError:
        log.debug("wmi package missing — no disk info on Windows")
        return disks
    try:
        for disk in w.Win32_DiskDrive():
            disks.append({
                "name": disk.DeviceID or "",
                "model": (disk.Model or "").strip(),
                "size": _format_size_bytes(disk.Size),
                "type": _disk_type(disk),
                "health": _disk_health(disk.DeviceID),
            })
    except Exception as e:
        log.debug("WMI disk query failed: %s", type(e).__name__)
    return disks

