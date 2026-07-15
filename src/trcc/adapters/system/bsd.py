"""BSDPlatform — concrete Platform for FreeBSD / OpenBSD.

Like macOS, BSD lacks a kernel SCSI-passthrough interface the user can
drive without the block-device claim.  We detach the `umass` driver and
frame SCSI CDBs as USB BOT over libusb.  Root required.
"""
from __future__ import annotations

import logging
import os
import re
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


class BSDPaths(Paths):
    """XDG-style paths on BSD (falls back to HOME)."""

    def __init__(self) -> None:
        home = Path.home()
        self._root = home / ".trcc"
        self._user_content = home / ".trcc-user"
        log.info("BSDPaths: root=%s user_content=%s",
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


# tool → pkg one-liner (FreeBSD/OpenBSD/NetBSD; consumed by software_install_hint).
_BSD_INSTALL_HINTS: dict[str, str] = {
    "ffmpeg": "pkg install ffmpeg",
    "7z": "pkg install p7zip",
    "python": "pkg install python311",
    "pynvml": "pip install nvidia-ml-py",
}


@PlatformFactory.register("bsd")
class BSDPlatform(Platform):
    """FreeBSD / OpenBSD implementation of Platform — BOT-only SCSI."""

    def __init__(self) -> None:
        log.info("BSDPlatform: initialising")
        self._paths = BSDPaths()
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
        """sysctl CPU temp on top of the psutil/NVML baseline."""
        log.info("sensors: cached=%s", self._sensors is not None)
        if self._sensors is None:
            from ..sensors.bsd import build_bsd_sensors
            self._sensors = build_bsd_sensors()
        return self._sensors

    def autostart(self) -> AutostartManager:
        log.info("autostart: cached=%s", self._autostart is not None)
        if self._autostart is None:
            # XDG .desktop — the BSD desktops (GNOME/KDE/XFCE on FreeBSD et
            # al.) honour the same spec as Linux; legacy shared this code.
            from ._autostart import XdgDesktopAutostart
            self._autostart = XdgDesktopAutostart()
        return self._autostart

    def hotplug(self) -> HotplugMonitor:
        """devd seqpacket socket on FreeBSD; noop on OpenBSD/NetBSD."""
        log.info("hotplug: cached=%s", self._hotplug is not None)
        if self._hotplug is None:
            import platform as _platform

            if _platform.system() in ("FreeBSD", "DragonFly"):
                from ._hotplug import FreeBSDHotplugMonitor
                self._hotplug = FreeBSDHotplugMonitor()
            else:
                from ._hotplug import NoopHotplugMonitor
                self._hotplug = NoopHotplugMonitor(
                    reason=f"hotplug listener not implemented for {_platform.system()}",
                )
        return self._hotplug

    def setup(self, interactive: bool = True) -> int:
        """Install FreeBSD devd rules so non-root users can talk to the cooler.

        Mirrors LinuxPlatform.setup(): writes a config file under
        ``/usr/local/etc/devd/`` that chmod's the USB device node 0666
        on attach for every device in :data:`ALL_DEVICES`.  Re-execs via
        sudo/doas when called as a normal user.

        ``interactive=False`` is a dry run — prints what would be
        written, no system changes.

        OpenBSD has no devd; the installer logs a pointer to the right
        manual setup path and returns 0.
        """
        log.info("setup: interactive=%s", interactive)
        from ._devd import install
        return install(dry_run=not interactive)

    def check_permissions(self) -> list[str]:
        log.info("check_permissions: called")
        if os.geteuid() != 0:
            return [
                "BSD requires root to detach the umass kernel driver — "
                "run with doas/sudo or adjust devd permissions.",
            ]
        return []

    def distro_name(self) -> str:
        log.info("distro_name: called")
        import sys
        return "FreeBSD" if "freebsd" in sys.platform else "BSD"

    def install_method(self) -> str:
        """How this package got here — delegated to the one honest detector.

        Was a per-OS guess: this returned "source" for every pip install
        (and Linux returned "pip" whenever `trcc` was merely on PATH, so
        rpm/deb/venv/source checkouts all reported "pip"). The reading of
        `INSTALLER` metadata is OS-agnostic, so there is nothing per-OS to
        implement — see adapters/diagnostics/install.detect_installer.
        """
        from ..diagnostics.install import detect_installer
        method = detect_installer()
        log.info("install_method: %s", method)
        return method

    # ── Per-OS diagnostic hints (pkg) ─────────────────────────────────

    def software_install_hint(self, tool: str) -> str:
        log.debug("software_install_hint: tool=%s", tool)
        hint = _BSD_INSTALL_HINTS.get(tool)
        if hint is None:
            return f"Install {tool} and ensure it is on PATH"
        return f"{tool} not found — install it:\n  {hint}"

    def no_devices_hint(self) -> str:
        log.debug("no_devices_hint: called")
        return (
            "Ensure the device is connected and your user can reach it "
            "(check `usbconfig list` and the device node permissions under "
            "/dev/da*)."
        )

    # ── Hardware probes (LED memory + disk widgets) ───────────────────

    def memory_info(self) -> list[dict[str, str]]:
        """DRAM probe via sysctl ``hw.physmem`` + psutil total fallback.

        FreeBSD doesn't expose per-DIMM SPD data through sysctl, so
        this returns a single ``Total`` entry rather than per-DIMM
        slots (matching legacy behaviour).
        """
        log.info("BSDPlatform.memory_info: probing")
        slots = _bsd_memory_info()
        log.info("BSDPlatform.memory_info: %d slot(s)", len(slots))
        return slots

    def disk_info(self) -> list[dict[str, str]]:
        """Physical-disk probe via ``geom disk list`` (FreeBSD only)."""
        log.info("BSDPlatform.disk_info: probing")
        disks = _bsd_disk_info()
        log.info("BSDPlatform.disk_info: %d disk(s)", len(disks))
        return disks


# =========================================================================
# BSD hardware-probe helpers
# =========================================================================


SysctlRunner = Callable[[str], str | None]
GeomRunner = Callable[[], str]


def _run_sysctl_n(key: str) -> str | None:
    """``sysctl -n <key>`` → trimmed stdout, or None on failure."""
    log.debug("_run_sysctl_n: key=%s", key)
    try:
        result = subprocess.run(
            ["sysctl", "-n", key],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.debug("sysctl -n %s failed", key, exc_info=True)
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _run_geom_disk_list() -> str:
    """``geom disk list`` stdout, or empty string when unavailable."""
    log.debug("_run_geom_disk_list: called")
    try:
        result = subprocess.run(
            ["geom", "disk", "list"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        log.debug("geom disk list failed", exc_info=True)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _bsd_memory_info(
    runner: SysctlRunner = _run_sysctl_n,
) -> list[dict[str, str]]:
    """Single-entry memory descriptor — BSD has no per-DIMM probe."""
    log.debug("_bsd_memory_info: called")
    slots: list[dict[str, str]] = []
    physmem = runner("hw.physmem")
    if physmem is not None:
        try:
            total_bytes = int(physmem)
            total_gb = total_bytes / (1024 ** 3)
            slots.append({
                "manufacturer": "Unknown",
                "part_number": "",
                "type": "Unknown",
                "speed": "Unknown",
                "size": f"{total_gb:.0f} GB",
                "form_factor": "Unknown",
                "locator": "Total",
            })
        except (ValueError, TypeError):
            pass

    if not slots:
        mem = psutil.virtual_memory()
        slots.append({
            "manufacturer": "Unknown",
            "part_number": "",
            "type": "Unknown",
            "speed": "Unknown",
            "size": f"{mem.total // (1024 ** 3)} GB",
            "form_factor": "Unknown",
            "locator": "Total",
        })

    return slots


def _bsd_disk_info(
    runner: GeomRunner = _run_geom_disk_list,
) -> list[dict[str, str]]:
    """Parse ``geom disk list`` (FreeBSD/DragonFly).

    OpenBSD/NetBSD don't ship ``geom``; ``runner`` returns ``""`` and
    we yield an empty list — caller renders ``NC``.
    """
    log.debug("_bsd_disk_info: called")
    disks: list[dict[str, str]] = []
    output = runner()
    if not output:
        return disks

    current: dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("Geom name:"):
            if current.get("name"):
                disks.append(current)
            current = {"name": line.split(":", 1)[1].strip()}
        elif line.startswith("descr:"):
            current["model"] = line.split(":", 1)[1].strip()
        elif line.startswith("Mediasize:"):
            raw_val = line.split(":", 1)[1].strip()
            match = re.search(r"\(([^)]+)\)", raw_val)
            if match:
                current["size"] = match.group(1)
            else:
                parts = raw_val.split()
                if parts:
                    try:
                        b = int(parts[0])
                        if b >= 1024 ** 4:
                            current["size"] = f"{b / (1024 ** 4):.1f} TB"
                        elif b >= 1024 ** 3:
                            current["size"] = f"{b / (1024 ** 3):.0f} GB"
                    except (ValueError, TypeError):
                        current["size"] = raw_val
        elif line.startswith("rotationrate:"):
            rate = line.split(":", 1)[1].strip()
            current["type"] = "HDD" if rate != "0" else "SSD"

    if current.get("name"):
        disks.append(current)

    for d in disks:
        d.setdefault("type", "Unknown")
        d.setdefault("model", "")
        d.setdefault("size", "")
        d.setdefault("health", "Unknown")

    return disks
