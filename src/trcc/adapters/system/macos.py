"""MacOSPlatform — concrete Platform implementation for macOS.

macOS has no SG_IO / DeviceIoControl SCSI-passthrough equivalent:
IOUSBMassStorageClass claims mass-storage devices exclusively.  SCSI
CDBs are therefore framed as USB BOT (CBW/data/CSW) over libusb.  The
app needs to run with elevated privileges (root / entitled) to detach
the kernel driver; see `setup()`.
"""
from __future__ import annotations

import json
import logging
import os
import platform as _platform
import subprocess
from collections.abc import Callable
from pathlib import Path

import psutil  # pyright: ignore[reportMissingImports]
import usb.util

from ...core.models import DeviceInfo
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
from ..device.usb_bot_scsi import UsbBotScsiTransport
from . import PlatformFactory

log = logging.getLogger(__name__)


class MacOSPaths(Paths):
    """~/Library/Application Support style paths."""

    def __init__(self) -> None:
        home = Path.home()
        self._root = home / "Library" / "Application Support" / "trcc"
        self._user_content = home / "Library" / "Application Support" / "trcc-user"
        log.info("MacOSPaths: root=%s user_content=%s",
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
        return self._root / "Logs" / "trcc.log"


@PlatformFactory.register("darwin")
class MacOSPlatform(Platform):
    """macOS implementation of Platform — BOT-only SCSI via libusb."""

    def __init__(self) -> None:
        log.info("MacOSPlatform: initialising")
        self._paths = MacOSPaths()
        self._sensors: SensorEnumerator | None = None
        self._autostart: AutostartManager | None = None
        self._hotplug: HotplugMonitor | None = None

    def open_bulk(self, vid: int, pid: int,
                  serial: str | None = None) -> BulkTransport:
        log.info("open_bulk: %04x:%04x serial=%r", vid, pid, serial)
        return PyUsbBulkTransport(vid, pid, serial)

    def open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        """SCSI via USB BOT over libusb — macOS has no kernel SCSI passthrough."""
        log.info("open_scsi: %04x:%04x serial=%r", vid, pid, serial)
        bulk = PyUsbBulkTransport(vid, pid, serial)
        return UsbBotScsiTransport(bulk)

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
        """SMC temperature on top of the psutil / NVML baseline.

        Intel keys ship enabled by default; Apple Silicon keys are
        gated behind ``TRCC_NEXT_APPLE_SILICON_SMC=1`` until reporter-
        confirmed.
        """
        log.info("sensors: cached=%s", self._sensors is not None)
        if self._sensors is None:
            from ..sensors.macos import build_macos_sensors
            self._sensors = build_macos_sensors()
        return self._sensors

    def autostart(self) -> AutostartManager:
        log.info("autostart: cached=%s", self._autostart is not None)
        if self._autostart is None:
            from ._autostart import MacOSAutostart
            self._autostart = MacOSAutostart()
        return self._autostart

    def hotplug(self) -> HotplugMonitor:
        """Polling fallback — 1 s ``scan_devices`` diff.

        Native IOKit ``IOServiceAddMatchingNotification`` + CFRunLoop
        would require either ~250 lines of fragile ctypes or a 50 MB
        pyobjc dep; polling once a second hits the same UX (≤1 s
        attach/detach latency) at zero new cost.
        """
        log.info("hotplug: cached=%s", self._hotplug is not None)
        if self._hotplug is None:
            from ._hotplug import PollingHotplugMonitor
            self._hotplug = PollingHotplugMonitor(scan=self._scan_vid_pid_set)
        return self._hotplug

    def _scan_vid_pid_set(self) -> set[tuple[int, int]]:
        """Snapshot of currently-attached registry vid:pid combos.

        Named method (not a lambda) so PollingHotplugMonitor's scan
        callable survives across stack frames + shows up in tracebacks
        with a real name.
        """
        log.debug("_scan_vid_pid_set: called")
        return {(d.vid, d.pid) for d in self.scan_devices()}

    def setup(self, interactive: bool = True) -> int:
        """Diagnose codesign / quarantine / privileges, print fix steps.

        Read-only: macOS USB access requires either a signed bundle
        with a provisioning entitlement (Developer Program) or
        ``sudo``, neither of which a setup wizard can install for the
        user.
        """
        log.info("setup: interactive=%s", interactive)
        from ._macos_setup import install
        return install(dry_run=not interactive)

    def check_permissions(self) -> list[str]:
        log.info("check_permissions: called")
        if os.geteuid() != 0:
            return [
                "macOS requires root privileges to detach the mass-storage "
                "kernel driver — run with sudo or install as a signed app bundle.",
            ]
        return []

    def distro_name(self) -> str:
        log.info("distro_name: called")
        return "macOS"

    def install_method(self) -> str:
        log.info("install_method: called")
        import sys
        if getattr(sys, "frozen", False):
            return "pyinstaller"
        return "source"

    # ── Hardware probes (LED memory + disk widgets) ───────────────────

    def memory_info(self) -> list[dict[str, str]]:
        """DRAM probe via ``system_profiler SPMemoryDataType``.

        Apple Silicon reports unified memory as a single entry; Intel
        Macs report one entry per DIMM.  psutil fallback for totals
        when the profiler is unavailable (sandboxed contexts).
        """
        log.info("MacOSPlatform.memory_info: probing")
        slots = _macos_memory_info()
        log.info("MacOSPlatform.memory_info: %d slot(s)", len(slots))
        return slots

    def disk_info(self) -> list[dict[str, str]]:
        """Physical-disk probe via ``system_profiler SPStorageDataType``."""
        log.info("MacOSPlatform.disk_info: probing")
        disks = _macos_disk_info()
        log.info("MacOSPlatform.disk_info: %d disk(s)", len(disks))
        return disks


# =========================================================================
# macOS hardware-probe helpers — used by MacOSPlatform.memory_info/disk_info
# =========================================================================


# `Callable[[str], dict[str, object]]` — runs `system_profiler <type> -json`
# and returns parsed JSON, or an empty dict on any failure.  DI seam:
# tests inject canned dicts; production binds to ``_run_system_profiler``.
ProfilerRunner = Callable[[str], dict]


def _run_system_profiler(data_type: str) -> dict:
    """Run ``system_profiler <data_type> -json`` and return parsed JSON.

    Returns ``{}`` on any failure (missing binary, non-zero exit,
    malformed JSON, timeout).  Logged at DEBUG — the probe is best-
    effort and an empty result is normal on stripped/sandboxed Macs.
    """
    try:
        result = subprocess.run(
            ["system_profiler", data_type, "-json"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.debug("system_profiler %s failed", data_type, exc_info=True)
        return {}
    if result.returncode != 0:
        log.debug("system_profiler %s exited %d", data_type, result.returncode)
        return {}
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        log.debug("system_profiler %s returned malformed JSON", data_type)
        return {}


def _is_apple_silicon() -> bool:
    log.debug("_is_apple_silicon: called")
    return _platform.machine() == "arm64"


def _macos_memory_info(runner: ProfilerRunner = _run_system_profiler) -> list[dict[str, str]]:
    """Parse ``SPMemoryDataType`` into one dict per DIMM slot.

    Apple Silicon: top-level items already describe one unified-memory
    block.  Intel: outer items wrap ``_items`` per DIMM.  Empty list
    when the profiler returned nothing — caller falls back to psutil.
    """
    log.debug("_macos_memory_info: called")
    slots: list[dict[str, str]] = []
    data = runner("SPMemoryDataType")
    items = data.get("SPMemoryDataType", []) if isinstance(data, dict) else []

    for item in items:
        if not isinstance(item, dict):
            continue
        dimms = item.get("_items") or [item]
        for dimm in dimms:
            if not isinstance(dimm, dict):
                continue
            slot: dict[str, str] = {
                "manufacturer": str(dimm.get("dimm_manufacturer", "Apple")),
                "part_number": str(dimm.get("dimm_part_number", "")),
                "type": str(dimm.get("dimm_type", "")),
                "speed": str(dimm.get("dimm_speed", "")),
                "size": str(dimm.get("dimm_size", "")),
                "form_factor": str(dimm.get("dimm_form_factor", "")),
                "locator": str(dimm.get("_name", "")),
            }
            if slot["size"]:
                slots.append(slot)

    if not slots:
        mem = psutil.virtual_memory()
        unified = _is_apple_silicon()
        slots.append({
            "manufacturer": "Apple",
            "part_number": "Unknown",
            "type": "Unified" if unified else "Unknown",
            "speed": "Unknown",
            "size": f"{mem.total // (1024 ** 3)} GB",
            "form_factor": "Unified" if unified else "Unknown",
            "locator": "Total",
        })

    return slots


def _macos_disk_info(runner: ProfilerRunner = _run_system_profiler) -> list[dict[str, str]]:
    """Parse ``SPStorageDataType`` into one dict per physical disk.

    SSD vs HDD classification reads ``physical_drive.medium_type``;
    rotational → HDD, anything else → SSD (modern Macs are SSD-only,
    so SSD is the safe default).
    """
    log.debug("_macos_disk_info: called")
    disks: list[dict[str, str]] = []
    data = runner("SPStorageDataType")
    items = data.get("SPStorageDataType", []) if isinstance(data, dict) else []

    for item in items:
        if not isinstance(item, dict):
            continue
        physical = item.get("physical_drive")
        physical = physical if isinstance(physical, dict) else {}
        info: dict[str, str] = {
            "name": str(item.get("bsd_name", "")),
            "model": str(physical.get("device_name", "")),
            "size": str(item.get("size_in_bytes", "")),
            "health": str(item.get("smart_status", "Unknown")),
        }
        if info["size"]:
            try:
                b = int(info["size"])
                if b >= 1024 ** 4:
                    info["size"] = f"{b / (1024 ** 4):.1f} TB"
                elif b >= 1024 ** 3:
                    info["size"] = f"{b / (1024 ** 3):.0f} GB"
            except (ValueError, TypeError):
                pass
        medium = str(physical.get("medium_type", "")).lower()
        if "rotational" in medium:
            info["type"] = "HDD"
        else:
            info["type"] = "SSD"
        if info["name"] or info["model"]:
            disks.append(info)

    return disks
