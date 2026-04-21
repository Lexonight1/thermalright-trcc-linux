"""BSDPlatform — concrete Platform for FreeBSD / OpenBSD.

Like macOS, BSD lacks a kernel SCSI-passthrough interface the user can
drive without the block-device claim.  We detach the `umass` driver and
frame SCSI CDBs as USB BOT over libusb.  Root required.
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


class BSDPaths(Paths):
    """XDG-style paths on BSD (falls back to HOME)."""

    def __init__(self) -> None:
        home = Path(os.path.expanduser("~"))
        self._root = home / ".trcc"
        self._user_content = home / ".trcc-user"

    def config_dir(self) -> Path:
        return self._root

    def data_dir(self) -> Path:
        return self._root / "data"

    def user_content_dir(self) -> Path:
        return self._user_content

    def log_file(self) -> Path:
        return self._root / "trcc.log"


class _NoopAutostart(AutostartManager):
    def is_enabled(self) -> bool:
        return False

    def enable(self) -> None:
        pass

    def disable(self) -> None:
        pass

    def refresh(self) -> None:
        pass


class BSDPlatform(Platform):
    """FreeBSD / OpenBSD implementation of Platform — BOT-only SCSI."""

    def __init__(self) -> None:
        self._paths = BSDPaths()
        self._sensors: Optional[SensorEnumerator] = None
        self._autostart: Optional[AutostartManager] = None

    def open_bulk(self, vid: int, pid: int,
                  serial: Optional[str] = None) -> BulkTransport:
        return PyUsbBulkTransport(vid, pid, serial)

    def open_scsi(self, vid: int, pid: int,
                  serial: Optional[str] = None) -> ScsiTransport:
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
        # Baseline (psutil + nvml) until BsdSysctl sensor source lands.
        if self._sensors is None:
            self._sensors = BaselineSensors()
        return self._sensors

    def autostart(self) -> AutostartManager:
        if self._autostart is None:
            self._autostart = _NoopAutostart()
        return self._autostart

    def setup(self, interactive: bool = True) -> int:
        log.warning("BSDPlatform.setup: devd rules wizard not yet wired")
        return 0

    def check_permissions(self) -> List[str]:
        if os.geteuid() != 0:
            return [
                "BSD requires root to detach the umass kernel driver — "
                "run with doas/sudo or adjust devd permissions.",
            ]
        return []

    def distro_name(self) -> str:
        import sys
        return "FreeBSD" if "freebsd" in sys.platform else "BSD"

    def install_method(self) -> str:
        import sys
        if getattr(sys, "frozen", False):
            return "pyinstaller"
        return "source"
