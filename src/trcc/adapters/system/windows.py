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
from pathlib import Path
from typing import Any, List, Optional

import usb.core
import usb.util

from ...core.errors import TransportError
from ...core.models import DeviceInfo
from ...core.ports import (
    AutostartManager,
    BulkTransport,
    Paths,
    Platform,
    ScsiTransport,
    SensorEnumerator,
)
from ...core.registry import ALL_DEVICES
from ..device.transport import PyUsbBulkTransport
from ..sensors.aggregator import BaselineSensors

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

    def config_dir(self) -> Path:
        return self._root

    def data_dir(self) -> Path:
        return self._root / "data"

    def user_content_dir(self) -> Path:
        return self._user_content

    def log_file(self) -> Path:
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
    return ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue]


def _find_physical_drive(vid: int, pid: int) -> Optional[str]:
    """Map VID:PID → \\\\.\\PhysicalDriveN via WMI.

    LCD devices report tiny capacity (< 1MB) because they have no real
    storage, which distinguishes them from flash drives and HDDs.
    """
    vid_tag = f"VID_{vid:04X}"
    pid_tag = f"PID_{pid:04X}"
    try:
        import wmi  # pyright: ignore[reportMissingImports]
        w = wmi.WMI()
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


class WindowsScsiTransport(ScsiTransport):
    """SCSI passthrough via DeviceIoControl on Windows."""

    def __init__(self, device_path: str) -> None:
        self._path = device_path
        self._handle: Optional[int] = None

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def open(self) -> bool:
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
                log.error("CreateFileW failed for %s", self._path)
                return False
            self._handle = handle
            return True
        except Exception:
            log.exception("Failed to open %s", self._path)
            return False

    def close(self) -> None:
        if self._handle is not None:
            try:
                _kernel32().CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    def send_cdb(self, cdb: bytes, data: bytes,
                 timeout_ms: int = 5000) -> bool:
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


class _NoopAutostart(AutostartManager):
    def is_enabled(self) -> bool:
        return False

    def enable(self) -> None:
        pass

    def disable(self) -> None:
        pass

    def refresh(self) -> None:
        pass


# =========================================================================
# WindowsPlatform
# =========================================================================


class WindowsPlatform(Platform):
    """Windows implementation of Platform."""

    def __init__(self) -> None:
        self._paths = WindowsPaths()
        self._sensors: Optional[SensorEnumerator] = None
        self._autostart: Optional[AutostartManager] = None

    def open_bulk(self, vid: int, pid: int,
                  serial: Optional[str] = None) -> BulkTransport:
        return PyUsbBulkTransport(vid, pid, serial)

    def open_scsi(self, vid: int, pid: int,
                  serial: Optional[str] = None) -> ScsiTransport:
        path = _find_physical_drive(vid, pid)
        if path is None:
            raise TransportError(
                f"No PhysicalDrive found for {vid:04x}:{pid:04x} — "
                "ensure the device is attached and visible as a USB mass-storage disk"
            )
        log.debug("WindowsPlatform.open_scsi: %04x:%04x → %s", vid, pid, path)
        return WindowsScsiTransport(path)

    def scan_devices(self) -> List[DeviceInfo]:
        found: List[DeviceInfo] = []
        for (vid, pid) in ALL_DEVICES:
            for dev in (usb.core.find(find_all=True, idVendor=vid, idProduct=pid) or []):
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
        return self._paths

    def sensors(self) -> SensorEnumerator:
        # Baseline (psutil + nvml) until WindowsLhm sensor source lands.
        if self._sensors is None:
            self._sensors = BaselineSensors()
        return self._sensors

    def autostart(self) -> AutostartManager:
        if self._autostart is None:
            self._autostart = _NoopAutostart()
        return self._autostart

    def setup(self, interactive: bool = True) -> int:
        log.warning("WindowsPlatform.setup: WinUSB driver installation not yet wired")
        return 0

    def check_permissions(self) -> List[str]:
        return []

    def distro_name(self) -> str:
        return "Windows"

    def install_method(self) -> str:
        import sys
        if getattr(sys, "frozen", False):
            return "pyinstaller"
        return "source"

