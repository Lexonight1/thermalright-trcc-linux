"""MacOSPlatform — concrete Platform implementation for macOS.

macOS has no SG_IO / DeviceIoControl SCSI-passthrough equivalent:
IOUSBMassStorageClass claims mass-storage devices exclusively.  SCSI
CDBs are therefore framed as USB BOT (CBW/data/CSW) over libusb.  The
app needs to run with elevated privileges (root / entitled) to detach
the kernel driver; see `setup()`.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

import usb.core
import usb.util

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
from ..device.usb_bot_scsi import UsbBotScsiTransport
from ..sensors.aggregator import BaselineSensors

log = logging.getLogger(__name__)


class MacOSPaths(Paths):
    """~/Library/Application Support style paths."""

    def __init__(self) -> None:
        home = Path(os.path.expanduser("~"))
        self._root = home / "Library" / "Application Support" / "trcc"
        self._user_content = home / "Library" / "Application Support" / "trcc-user"

    def config_dir(self) -> Path:
        return self._root

    def data_dir(self) -> Path:
        return self._root / "data"

    def user_content_dir(self) -> Path:
        return self._user_content

    def log_file(self) -> Path:
        return self._root / "Logs" / "trcc.log"


class _NoopAutostart(AutostartManager):
    def is_enabled(self) -> bool:
        return False

    def enable(self) -> None:
        pass

    def disable(self) -> None:
        pass

    def refresh(self) -> None:
        pass


class MacOSPlatform(Platform):
    """macOS implementation of Platform — BOT-only SCSI via libusb."""

    def __init__(self) -> None:
        self._paths = MacOSPaths()
        self._sensors: Optional[SensorEnumerator] = None
        self._autostart: Optional[AutostartManager] = None

    def open_bulk(self, vid: int, pid: int,
                  serial: Optional[str] = None) -> BulkTransport:
        return PyUsbBulkTransport(vid, pid, serial)

    def open_scsi(self, vid: int, pid: int,
                  serial: Optional[str] = None) -> ScsiTransport:
        """SCSI via USB BOT over libusb — macOS has no kernel SCSI passthrough."""
        bulk = PyUsbBulkTransport(vid, pid, serial)
        return UsbBotScsiTransport(bulk)

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
        # Baseline (psutil + nvml) until MacOsSmc sensor source lands.
        if self._sensors is None:
            self._sensors = BaselineSensors()
        return self._sensors

    def autostart(self) -> AutostartManager:
        if self._autostart is None:
            self._autostart = _NoopAutostart()
        return self._autostart

    def setup(self, interactive: bool = True) -> int:
        log.warning("MacOSPlatform.setup: codesign + entitlements wizard not yet wired")
        return 0

    def check_permissions(self) -> List[str]:
        if os.geteuid() != 0:
            return [
                "macOS requires root privileges to detach the mass-storage "
                "kernel driver — run with sudo or install as a signed app bundle.",
            ]
        return []

    def distro_name(self) -> str:
        return "macOS"

    def install_method(self) -> str:
        import sys
        if getattr(sys, "frozen", False):
            return "pyinstaller"
        return "source"
