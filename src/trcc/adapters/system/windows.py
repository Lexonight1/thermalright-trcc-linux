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
from typing import Any

import usb.util

from ...core.errors import TransportError
from ...core.models import DeviceInfo, memory_form_factor, memory_type
from ...core.ports import (
    AutostartManager,
    BulkTransport,
    HotplugMonitor,
    Paths,
    Platform,
    ScsiTransport,
    SensorEnumerator,
)
from ...core.registry import ALL_DEVICES
from ..device._pyusb_find import find as usb_find
from ..device.transport import PyUsbBulkTransport
from ..sensors.windows import build_windows_sensors
from . import PlatformFactory
from ._windows_wmi import wmi_handle

log = logging.getLogger(__name__)


# =========================================================================
# WindowsPaths — APPDATA/LOCALAPPDATA
# =========================================================================


class WindowsPaths(Paths):
    """Windows user-data locations via APPDATA / LOCALAPPDATA."""

    def __init__(self) -> None:
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        self._root = Path(appdata) / "trcc"
        self._user_content = Path(local) / "trcc-user"
        log.info("WindowsPaths: root=%s user_content=%s",
                 self._root, self._user_content)

    def config_dir(self) -> Path:
        log.debug("config_dir: called")
        return self._root

    def data_dir(self) -> Path:
        log.debug("data_dir: called")
        return self._root / "data"

    def user_content_dir(self) -> Path:
        log.debug("user_content_dir: called")
        return self._user_content

    def log_file(self) -> Path:
        log.debug("log_file: called")
        return self._root / "trcc.log"


# =========================================================================
# DeviceIoControl — Windows SCSI passthrough
# =========================================================================

_IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014  # METHOD_OUT_DIRECT — DMA writes
_IOCTL_SCSI_PASS_THROUGH        = 0x4D004  # METHOD_BUFFERED   — reads
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


class _SCSI_PASS_THROUGH(ctypes.Structure):
    """Buffered variant — layout: [struct][sense][data]."""
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
        ("DataBufferOffset", ctypes.c_size_t),   # ULONG_PTR on x64
        ("SenseInfoOffset", ctypes.c_uint32),
        ("Cdb", ctypes.c_uint8 * 16),
    ]


def _kernel32() -> Any:
    """Access kernel32 only when needed (Windows-only attribute)."""
    log.debug("_kernel32: called")
    return ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]


def _find_physical_drive(vid: int, pid: int) -> str | None:
    """Map VID:PID → \\\\.\\PhysicalDriveN via WMI.

    LCD devices report tiny capacity (< 1MB) because they have no real
    storage, which distinguishes them from flash drives and HDDs.
    """
    log.info("_find_physical_drive: %04x:%04x", vid, pid)
    vid_tag = f"VID_{vid:04X}"
    pid_tag = f"PID_{pid:04X}"
    try:
        w = wmi_handle()
        for rel in w.Win32_USBControllerDevice():
            dep = str(rel.Dependent or "").upper()
            if vid_tag in dep and pid_tag in dep:
                break
        else:
            log.debug("VID/PID %04x:%04x not present in USB tree", vid, pid)
            return None
        for disk in w.Win32_DiskDrive():
            pnp = (disk.PNPDeviceID or "").upper()
            if not pnp.startswith("USBSTOR"):
                continue
            if int(disk.Size or 0) < 1_000_000:
                return disk.DeviceID
    except Exception:
        log.exception("WMI lookup failed for %04x:%04x", vid, pid)
    return None


# Common CreateFileW failures when opening \\.\PhysicalDriveN for SCSI
# passthrough — translated in the log so a reporter doesn't have to look up
# the bare WinError number.
_WIN_OPEN_ERRORS: dict[int, str] = {
    2:  "ERROR_FILE_NOT_FOUND — the drive path is gone (replug / rescan)",
    5:  "ERROR_ACCESS_DENIED — raw drive access needs Administrator; "
        "run TRCC from an elevated terminal (Run as administrator)",
    32: "ERROR_SHARING_VIOLATION — another process holds the device open",
}


class WindowsScsiTransport(ScsiTransport):
    """SCSI passthrough via DeviceIoControl on Windows."""

    def __init__(self, device_path: str) -> None:
        self._path = device_path
        self._handle: int | None = None
        log.info("WindowsScsiTransport: bound to %s", device_path)

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def open(self) -> bool:
        log.info("open: path=%s", self._path)
        if self._handle is not None:
            return True
        try:
            GENERIC_READ_WRITE = 0xC0000000
            FILE_SHARE_READ_WRITE = 0x3
            OPEN_EXISTING = 3
            handle = _kernel32().CreateFileW(
                self._path, GENERIC_READ_WRITE, FILE_SHARE_READ_WRITE,
                None, OPEN_EXISTING, 0, None,
            )
            if handle == -1:
                err = ctypes.GetLastError()  # pyright: ignore[reportAttributeAccessIssue]
                detail = _WIN_OPEN_ERRORS.get(
                    err, "see Microsoft 'System Error Codes'")
                log.error("CreateFileW failed for %s — WinError %d: %s",
                          self._path, err, detail)
                return False
            self._handle = handle
            return True
        except Exception:
            log.exception("Failed to open %s", self._path)
            return False

    def close(self) -> None:
        log.info("close: path=%s", self._path)
        if self._handle is not None:
            try:
                _kernel32().CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    def send_cdb(self, cdb: bytes, data: bytes,
                 timeout_ms: int = 5000) -> bool:
        log.debug("send_cdb: cdb_len=%d data_len=%d timeout=%dms",
                  len(cdb), len(data), timeout_ms)
        if self._handle is None:
            raise TransportError(f"WindowsScsiTransport {self._path} not open")

        data_buf = (ctypes.c_uint8 * len(data))(*data) if data else (ctypes.c_uint8 * 1)()
        sptdwb = _SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER()
        sptd = sptdwb.sptd
        sptd.Length = ctypes.sizeof(_SCSI_PASS_THROUGH_DIRECT)
        sptd.CdbLength = len(cdb)
        sptd.SenseInfoLength = _SENSE_LENGTH
        sptd.DataIn = _SCSI_IOCTL_DATA_OUT
        sptd.DataTransferLength = len(data)
        sptd.TimeOutValue = max(1, timeout_ms // 1000)   # Windows wants seconds
        sptd.DataBuffer = ctypes.addressof(data_buf)
        sptd.SenseInfoOffset = ctypes.sizeof(_SCSI_PASS_THROUGH_DIRECT)
        for i, b in enumerate(cdb[:16]):
            sptd.Cdb[i] = b

        returned = ctypes.c_uint32(0)
        ok = _kernel32().DeviceIoControl(
            self._handle,
            _IOCTL_SCSI_PASS_THROUGH_DIRECT,
            ctypes.byref(sptdwb), ctypes.sizeof(sptdwb),
            ctypes.byref(sptdwb), ctypes.sizeof(sptdwb),
            ctypes.byref(returned), None,
        )
        if not ok:
            log.error("DeviceIoControl send_cdb failed: error %d",
                      ctypes.GetLastError())  # pyright: ignore[reportAttributeAccessIssue]
            return False
        if sptd.ScsiStatus != 0:
            log.warning("SCSI status %d", sptd.ScsiStatus)
            return False
        return True

    def read_cdb(self, cdb: bytes, length: int,
                 timeout_ms: int = 5000) -> bytes:
        log.debug("read_cdb: cdb_len=%d length=%d timeout=%dms",
                  len(cdb), length, timeout_ms)
        if self._handle is None:
            raise TransportError(f"WindowsScsiTransport {self._path} not open")

        spt_size = ctypes.sizeof(_SCSI_PASS_THROUGH)
        sense_offset = spt_size
        data_offset = sense_offset + _SENSE_LENGTH
        total = data_offset + length
        buf = (ctypes.c_uint8 * total)()

        spt = _SCSI_PASS_THROUGH.from_buffer(buf)
        spt.Length = spt_size
        spt.CdbLength = len(cdb)
        spt.SenseInfoLength = _SENSE_LENGTH
        spt.DataIn = _SCSI_IOCTL_DATA_IN
        spt.DataTransferLength = length
        spt.TimeOutValue = max(1, timeout_ms // 1000)
        spt.SenseInfoOffset = sense_offset
        spt.DataBufferOffset = data_offset
        for i, b in enumerate(cdb[:16]):
            spt.Cdb[i] = b

        returned = ctypes.c_uint32(0)
        ok = _kernel32().DeviceIoControl(
            self._handle, _IOCTL_SCSI_PASS_THROUGH,
            buf, total, buf, total,
            ctypes.byref(returned), None,
        )
        if not ok:
            log.error("DeviceIoControl read_cdb failed: error %d",
                      ctypes.GetLastError())  # pyright: ignore[reportAttributeAccessIssue]
            return b""
        if spt.ScsiStatus != 0:
            log.warning("SCSI read status %d", spt.ScsiStatus)
            return b""
        return bytes(buf[data_offset:data_offset + length])


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
            pythoncom.CoInitialize()
        except ImportError:
            log.debug("_ComApartment: pythoncom unavailable — no-op")

    def __exit__(self, *exc: object) -> None:
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


@PlatformFactory.register("win32")
class WindowsPlatform(Platform):
    """Windows implementation of Platform."""

    def __init__(self) -> None:
        log.info("WindowsPlatform: initialising")
        self._paths = WindowsPaths()
        self._sensors: SensorEnumerator | None = None
        self._autostart: AutostartManager | None = None
        self._hotplug: HotplugMonitor | None = None

    def open_bulk(self, vid: int, pid: int,
                  serial: str | None = None) -> BulkTransport:
        log.info("open_bulk: %04x:%04x serial=%r", vid, pid, serial)
        return PyUsbBulkTransport(vid, pid, serial)

    def open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        log.info("open_scsi: %04x:%04x serial=%r", vid, pid, serial)
        path = _find_physical_drive(vid, pid)
        if path is None:
            raise TransportError(
                f"No PhysicalDrive found for {vid:04x}:{pid:04x} — "
                "ensure the device is attached and visible as a USB mass-storage disk"
            )
        log.debug("WindowsPlatform.open_scsi: %04x:%04x → %s", vid, pid, path)
        return WindowsScsiTransport(path)

    def scan_devices(self) -> list[DeviceInfo]:
        log.info("scan_devices: scanning %d known VID/PID pairs",
                 len(ALL_DEVICES))
        found: list[DeviceInfo] = []
        for (vid, pid) in ALL_DEVICES:
            for dev in (usb_find(find_all=True, idVendor=vid, idProduct=pid) or []):
                serial_idx = getattr(dev, "iSerialNumber", 0)
                serial = ""
                try:
                    if serial_idx:
                        serial = usb.util.get_string(dev, serial_idx) or ""
                except Exception:
                    serial = ""
                found.append(DeviceInfo(vid=vid, pid=pid, serial=serial or None))
        return found

    def paths(self) -> Paths:
        log.debug("paths: called")
        return self._paths

    def sensors(self) -> SensorEnumerator:
        """Strategy chain: HWiNFO → LHM → MSAcpi → psutil/NVML baseline."""
        log.info("sensors: cached=%s", self._sensors is not None)
        if self._sensors is None:
            self._sensors = build_windows_sensors(
                thread_context=self.worker_thread_context)
        return self._sensors

    def worker_thread_context(self) -> AbstractContextManager[None]:
        """Open a COM apartment for a worker thread so WMI sensor reads
        work off the main thread (overrides the Platform no-op default)."""
        log.debug("worker_thread_context: COM apartment")
        return _ComApartment()

    def autostart(self) -> AutostartManager:
        log.info("autostart: cached=%s", self._autostart is not None)
        if self._autostart is None:
            from ._autostart import WindowsAutostart
            self._autostart = WindowsAutostart()
        return self._autostart

    def hotplug(self) -> HotplugMonitor:
        log.info("hotplug: cached=%s", self._hotplug is not None)
        if self._hotplug is None:
            from ._hotplug import WindowsHotplugMonitor
            self._hotplug = WindowsHotplugMonitor()
        return self._hotplug

    def setup(self, interactive: bool = True) -> int:
        """Diagnose WinUSB driver state and print Zadig instructions.

        Read-only: Windows driver installation needs UAC + a signed
        driver package, which this script can't fake.  ``interactive``
        is accepted for parity with other platforms but ignored —
        diagnostic is the same either way.
        """
        log.info("setup: interactive=%s", interactive)
        from ._winusb import install
        return install(dry_run=not interactive)

    def check_permissions(self) -> list[str]:
        log.info("check_permissions: called")
        return []

    def distro_name(self) -> str:
        log.info("distro_name: called")
        return "Windows"

    def install_method(self) -> str:
        log.info("install_method: called")
        import sys
        if getattr(sys, "frozen", False):
            return "pyinstaller"
        return "source"

    # ── Per-OS diagnostic hints (winget / WinUSB) ─────────────────────

    def software_install_hint(self, tool: str) -> str:
        log.debug("software_install_hint: tool=%s", tool)
        hint = _WIN_INSTALL_HINTS.get(tool)
        if hint is None:
            return f"Install {tool} and ensure it is on PATH"
        return f"{tool} not found — install it:\n  {hint}"

    def no_devices_hint(self) -> str:
        log.debug("no_devices_hint: called")
        return (
            "On Windows, non-SCSI devices (HID / Bulk / LY) need the WinUSB "
            "driver — install it with Zadig (https://zadig.akeo.ie/). "
            "SCSI LCD panels need no driver."
        )

    # ── GUI behaviour ─────────────────────────────────────────────────

    def minimize_on_close(self) -> bool:
        """Windows: minimize to taskbar (legacy TRCC parity).

        Linux/macOS/BSD inherit the base False (hide-to-tray).
        """
        log.debug("minimize_on_close: called")
        return True

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

