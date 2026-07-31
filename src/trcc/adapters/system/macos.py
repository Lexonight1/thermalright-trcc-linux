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

from ...core.ports import (
    AutostartManager,
    HotplugMonitor,
    Paths,
    ScsiTransport,
    SensorEnumerator,
)
from ..device.transport import PyUsbBulkTransport
from ..device.usb_bot_scsi import UsbBotScsiTransport
from ._base import BaseOS, BasePaths

log = logging.getLogger(__name__)


class MacOSPaths(BasePaths):
    """~/Library/Application Support style paths."""

    def __init__(self) -> None:
        home = Path.home()
        self._root = home / "Library" / "Application Support" / "trcc"
        self._user_content = home / "Library" / "Application Support" / "trcc-user"
        log.info("MacOSPaths: root=%s user_content=%s",
                 self._root, self._user_content)

    def log_file(self) -> Path:
        # macOS convention: logs live under ~/Library/.../trcc/Logs/.
        path = self._root / "Logs" / "trcc.log"
        log.debug("MacOSPaths.log_file → %s", path)
        return path


# tool → Homebrew one-liner (consumed by software_install_hint).
_MAC_INSTALL_HINTS: dict[str, str] = {
    "ffmpeg": "brew install ffmpeg",
    "7z": "brew install p7zip",
    "python": "brew install python@3.12",
    "pynvml": "pip install nvidia-ml-py",
}


class MacOSPlatform(BaseOS, key="darwin"):
    """macOS implementation — BOT-only SCSI via libusb.

    Same OS contract as every other platform; only the internals below differ.
    """

    _INSTALL_HINTS = _MAC_INSTALL_HINTS

    # ── Per-OS internals (the add-a-new-OS interface) ────────────────────

    def _make_paths(self) -> Paths:
        return MacOSPaths()

    def _build_sensors(self) -> SensorEnumerator:
        """SMC temperature on top of the psutil / NVML baseline.

        Intel keys ship enabled by default; Apple Silicon keys are gated
        behind ``TRCC_NEXT_APPLE_SILICON_SMC=1`` until reporter-confirmed.
        """
        from ..sensors.macos import build_macos_sensors
        return build_macos_sensors()

    def _build_autostart(self) -> AutostartManager:
        from ._autostart import MacOSAutostart
        return MacOSAutostart()

    def _build_hotplug(self) -> HotplugMonitor:
        """Polling fallback — 1 s ``scan_devices`` diff.

        Native IOKit ``IOServiceAddMatchingNotification`` + CFRunLoop would
        need ~250 lines of fragile ctypes or a 50 MB pyobjc dep; polling once
        a second hits the same UX (≤1 s attach/detach latency) at zero cost.
        """
        from ._hotplug import PollingHotplugMonitor
        return PollingHotplugMonitor(scan=self._scan_vid_pid_set)

    def _open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        """SCSI via USB BOT over libusb — macOS has no kernel SCSI passthrough."""
        log.info("open_scsi: %04x:%04x serial=%r", vid, pid, serial)
        bulk = PyUsbBulkTransport(vid, pid, serial)
        return UsbBotScsiTransport(bulk)

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

    def permission_denied_hint(self) -> str:
        log.debug("MacOSPlatform.permission_denied_hint: called")
        return ("try running with sudo, or check System Settings → "
                "Privacy & Security → Files and Folders")

    def no_devices_hint(self) -> str:
        log.debug("no_devices_hint: called")
        return (
            "Ensure the device is connected. macOS needs no driver for SCSI "
            "LCD panels; if it still isn't seen, check System Settings → "
            "Privacy & Security for a blocked USB prompt."
        )

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
