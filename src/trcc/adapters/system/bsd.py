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


class BSDPaths(BasePaths):
    """XDG-style paths on BSD (falls back to HOME)."""

    def __init__(self) -> None:
        home = Path.home()
        self._root = home / ".trcc"
        self._user_content = home / ".trcc-user"
        log.info("BSDPaths: root=%s user_content=%s",
                 self._root, self._user_content)


# tool → pkg one-liner (FreeBSD/OpenBSD/NetBSD; consumed by software_install_hint).
_BSD_INSTALL_HINTS: dict[str, str] = {
    "ffmpeg": "pkg install ffmpeg",
    "7z": "pkg install p7zip",
    "python": "pkg install python311",
    "pynvml": "pip install nvidia-ml-py",
}


class BSDPlatform(BaseOS, key="bsd"):
    """FreeBSD / OpenBSD implementation — BOT-only SCSI.

    Same OS contract as every other platform; only the internals below differ.
    """

    _INSTALL_HINTS = _BSD_INSTALL_HINTS

    # ── Per-OS internals (the add-a-new-OS interface) ────────────────────

    def _make_paths(self) -> Paths:
        return BSDPaths()

    def _build_sensors(self) -> SensorEnumerator:
        """sysctl CPU temp on top of the psutil/NVML baseline."""
        from ..sensors.bsd import build_bsd_sensors
        return build_bsd_sensors()

    def _build_autostart(self) -> AutostartManager:
        # XDG .desktop — the BSD desktops (GNOME/KDE/XFCE on FreeBSD et al.)
        # honour the same spec as Linux; legacy shared this code.
        from ._autostart import XdgDesktopAutostart
        return XdgDesktopAutostart()

    def _build_hotplug(self) -> HotplugMonitor:
        """devd seqpacket socket on FreeBSD; noop on OpenBSD/NetBSD."""
        import platform as _platform
        if _platform.system() in ("FreeBSD", "DragonFly"):
            from ._hotplug import FreeBSDHotplugMonitor
            return FreeBSDHotplugMonitor()
        from ._hotplug import NoopHotplugMonitor
        return NoopHotplugMonitor(
            reason=f"hotplug listener not implemented for {_platform.system()}",
        )

    def _open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        log.info("open_scsi: %04x:%04x serial=%r", vid, pid, serial)
        bulk = PyUsbBulkTransport(vid, pid, serial)
        return UsbBotScsiTransport(bulk)

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

        Also registers the applications-menu entry, exactly as Linux does —
        the BSD desktops are the same XDG desktops, and a pip install
        leaves the same gap there (#231).
        """
        log.info("setup: interactive=%s", interactive)
        from ._desktop_entry import XdgDesktopEntry
        from ._devd import install
        rc = install(dry_run=not interactive)
        if interactive:
            # Convenience, never a reason to fail setup.
            XdgDesktopEntry().install()
        else:
            log.info("would install the desktop entry: %s",
                     XdgDesktopEntry().path)
        return rc

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
