"""LinuxPlatform — concrete Platform implementation for Linux.

This file owns every Linux-specific thing: sysfs walks, SG_IO ioctl,
XDG paths, udev-rule checks, autostart.  Other OSes have their own
sibling file.

Key pieces:
    LinuxPaths             — XDG + HOME resolution
    LinuxScsiTransport     — SCSI over /dev/sgN via SG_IO ioctl
    _resolve_scsi_path     — vid:pid → /dev/sg* via sysfs walk
    LinuxPlatform          — the Platform ABC wiring
"""
from __future__ import annotations

import ctypes
import fcntl
import logging
import os
from pathlib import Path

import usb.core
import usb.util

from ...core.errors import TransportError
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
from ..device.transport import PyUsbBulkTransport
from ..sensors.aggregator import build_linux_sensors
from ..sensors.gpu_detect import (
    detect_gpu_vendors,
    install_matching_gpu_extras,
)
from ._udev import install as install_udev_rules

log = logging.getLogger(__name__)


# =========================================================================
# LinuxPaths — XDG-style locations
# =========================================================================


class LinuxPaths(Paths):
    """XDG + HOME locations for user data."""

    def __init__(self) -> None:
        home = Path.home()
        self._root = home / ".trcc"
        self._user_content = home / ".trcc-user"
        log.info("LinuxPaths: root=%s user_content=%s",
                 self._root, self._user_content)

    def config_dir(self) -> Path:
        log.debug("LinuxPaths.config_dir → %s", self._root)
        return self._root

    def data_dir(self) -> Path:
        path = self._root / "data"
        log.debug("LinuxPaths.data_dir → %s", path)
        return path

    def user_content_dir(self) -> Path:
        log.debug("LinuxPaths.user_content_dir → %s", self._user_content)
        return self._user_content

    def log_file(self) -> Path:
        path = self._root / "trcc.log"
        log.debug("LinuxPaths.log_file → %s", path)
        return path


# =========================================================================
# LinuxAutostart — XDG Autostart (.desktop in ~/.config/autostart/)
# =========================================================================
#
# The XDG Autostart spec is supported by every major Linux desktop (GNOME,
# KDE, XFCE, Cinnamon, Budgie, MATE, LXQt).  A simple `.desktop` file in
# `$XDG_CONFIG_HOME/autostart/` (default `~/.config/autostart/`) makes the
# app launch on user login.  No root required — pure per-user opt-in.


_AUTOSTART_FILENAME = "trcc-next.desktop"

_AUTOSTART_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=TRCC (next)
GenericName=Thermalright Cooler Control
Comment=Auto-start TRCC GUI on login
Exec={exec_cmd}
Icon=trcc-linux
Terminal=false
Categories=System;Settings;
X-GNOME-Autostart-enabled=true
StartupNotify=false
"""


class LinuxAutostart(AutostartManager):
    """XDG Autostart adapter — writes/removes ~/.config/autostart/trcc-next.desktop."""

    def __init__(self) -> None:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        self._path = base / "autostart" / _AUTOSTART_FILENAME
        log.info("LinuxAutostart: desktop file path = %s", self._path)

    @property
    def path(self) -> Path:
        log.debug("LinuxAutostart.path → %s", self._path)
        return self._path

    def is_enabled(self) -> bool:
        enabled = self._path.is_file()
        log.debug("LinuxAutostart.is_enabled → %s (%s)", enabled, self._path)
        return enabled

    def enable(self) -> None:
        log.info("LinuxAutostart.enable: writing %s", self._path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(self._render(), encoding="utf-8")
        self._path.chmod(0o644)
        log.info("Autostart enabled: %s", self._path)

    def disable(self) -> None:
        log.info("LinuxAutostart.disable: removing %s", self._path)
        if self._path.exists():
            self._path.unlink()
            log.info("Autostart disabled: %s", self._path)
        else:
            log.info("LinuxAutostart.disable: %s did not exist", self._path)

    def refresh(self) -> None:
        """Re-render the .desktop file if it's present (picks up new Exec path)."""
        if self._path.exists():
            log.info("LinuxAutostart.refresh: re-rendering %s", self._path)
            self.enable()
        else:
            log.debug("LinuxAutostart.refresh: %s not present — nothing to refresh",
                      self._path)

    def _render(self) -> str:
        return _AUTOSTART_TEMPLATE.format(exec_cmd=self._exec_cmd())

    @staticmethod
    def _exec_cmd() -> str:
        """Build the launch command.

        Preference order:
          1. `trcc-next` console script if installed and on PATH
          2. `<sys.executable> -m trcc.next gui`

        The second form is robust across pipx / venv / system-python
        installs because sys.executable is always the right interpreter.
        """
        import shutil
        import sys as _sys

        if (resolved := shutil.which("trcc-next")):
            return f"{resolved} gui"
        return f"{_sys.executable} -m trcc.next gui"


# =========================================================================
# SG_IO — Linux kernel-native SCSI passthrough
# =========================================================================
#
# A single ioctl(SG_IO) bundles CDB + data phase + status, so a frame
# chunk costs one syscall on Linux (vs. 3 for userspace USB BOT).  The
# kernel also handles the mass-storage prelude (INQUIRY / TEST UNIT
# READY / Get Max LUN) that raw BOT skips — which is what stalls
# endpoints on vendor CDBs like 0xF5/0x1F5.

_SG_IO = 0x2285
_SG_DXFER_TO_DEV = -2
_SG_DXFER_FROM_DEV = -3
_SENSE_BUF_LEN = 32


class _SgIoHdr(ctypes.Structure):
    """Kernel sg_io_hdr_t — the ioctl argument for SG_IO."""

    _fields_ = [
        ('interface_id', ctypes.c_int),
        ('dxfer_direction', ctypes.c_int),
        ('cmd_len', ctypes.c_ubyte),
        ('mx_sb_len', ctypes.c_ubyte),
        ('iovec_count', ctypes.c_ushort),
        ('dxfer_len', ctypes.c_uint),
        ('dxferp', ctypes.c_void_p),
        ('cmdp', ctypes.c_void_p),
        ('sbp', ctypes.c_void_p),
        ('timeout', ctypes.c_uint),
        ('flags', ctypes.c_uint),
        ('pack_id', ctypes.c_int),
        ('usr_ptr', ctypes.c_void_p),
        ('status', ctypes.c_ubyte),
        ('masked_status', ctypes.c_ubyte),
        ('msg_status', ctypes.c_ubyte),
        ('sb_len_wr', ctypes.c_ubyte),
        ('host_status', ctypes.c_ushort),
        ('driver_status', ctypes.c_ushort),
        ('resid', ctypes.c_int),
        ('duration', ctypes.c_uint),
        ('info', ctypes.c_uint),
    ]


_SG_HDR_SIZE = ctypes.sizeof(_SgIoHdr)


def _resolve_scsi_path(vid: int, pid: int) -> str | None:
    """Walk sysfs to find /dev/sgN for a given VID:PID.

    Pass 1: /sys/class/scsi_generic/sgN  (kernel `sg` module loaded)
    Pass 2: /sys/block/sdN               (sg not loaded — block fallback)

    Returns the absolute /dev path, or None if no match.
    """
    for base, name_prefix in (("/sys/class/scsi_generic", "sg"),
                              ("/sys/block", "sd")):
        base_path = Path(base)
        if not base_path.exists():
            continue
        for entry in base_path.iterdir():
            if not entry.name.startswith(name_prefix):
                continue
            sysfs_device = entry / "device"
            if not sysfs_device.exists():
                continue
            found = _walk_sysfs_for_vid_pid(sysfs_device)
            if found == (vid, pid):
                if name_prefix == "sd":
                    log.info("sg module not loaded — using block device /dev/%s",
                             entry.name)
                return f"/dev/{entry.name}"
    return None


def _walk_sysfs_for_vid_pid(start: Path) -> tuple[int, int] | None:
    """Walk up sysfs parents until we find idVendor + idProduct files."""
    path = Path(os.path.realpath(start))
    for _ in range(10):
        path = path.parent
        vid_file = path / "idVendor"
        pid_file = path / "idProduct"
        if vid_file.exists() and pid_file.exists():
            try:
                return (int(vid_file.read_text().strip(), 16),
                        int(pid_file.read_text().strip(), 16))
            except (OSError, ValueError):
                return None
    return None


class LinuxScsiTransport(ScsiTransport):
    """SCSI transport over /dev/sgN using SG_IO ioctl.

    One ioctl per CDB — the kernel bundles CDB, data phase, and status.
    Buffer allocations are cached by data-length so the per-frame hot
    path does only memmoves + one ioctl (no Python-side allocation).
    """

    def __init__(self, device_path: str) -> None:
        self._path = device_path
        self._fd: int | None = None
        # Cache for send_cdb: {data_len: (cdb_buf, data_buf, sense_buf, hdr, ioctl_buf)}
        self._write_bufs: dict[int, tuple] = {}
        log.info("LinuxScsiTransport: bound to %s", device_path)

    @property
    def is_open(self) -> bool:
        opened = self._fd is not None
        log.debug("LinuxScsiTransport.is_open → %s (fd=%s)", opened, self._fd)
        return opened

    def open(self) -> bool:
        if self._fd is not None:
            log.debug("LinuxScsiTransport.open: %s already open (fd=%d)",
                      self._path, self._fd)
            return True
        try:
            self._fd = os.open(self._path, os.O_RDWR | os.O_NONBLOCK)
            log.info("LinuxScsiTransport: opened %s (fd=%d)", self._path, self._fd)
            return True
        except OSError as e:
            log.error("LinuxScsiTransport: open failed for %s: %s", self._path, e)
            return False

    def close(self) -> None:
        if self._fd is None:
            log.debug("LinuxScsiTransport.close: %s already closed", self._path)
            return
        log.info("LinuxScsiTransport: closing %s (fd=%d)", self._path, self._fd)
        try:
            os.close(self._fd)
        except OSError as e:
            log.warning("LinuxScsiTransport: os.close raised %s — continuing", e)
        self._fd = None
        self._write_bufs.clear()

    def send_cdb(self, cdb: bytes, data: bytes,
                 timeout_ms: int = 5000) -> bool:
        """SCSI CDB + data-out via single SG_IO ioctl.  True on status 0."""
        log.debug("LinuxScsiTransport.send_cdb: cdb_len=%d data_len=%d timeout=%dms",
                  len(cdb), len(data), timeout_ms)
        if self._fd is None:
            log.error("LinuxScsiTransport.send_cdb: %s not open", self._path)
            raise TransportError(f"LinuxScsiTransport {self._path} not open")

        bufs = self._write_bufs.get(len(data))
        if bufs is None:
            bufs = self._alloc_write_bufs(len(cdb), len(data))
            self._write_bufs[len(data)] = bufs
        cdb_buf, data_buf, _sense, hdr, ioctl_buf = bufs

        ctypes.memmove(cdb_buf, cdb, len(cdb))
        if data:
            ctypes.memmove(data_buf, data, len(data))
        hdr.timeout = timeout_ms
        ctypes.memmove(ioctl_buf, ctypes.addressof(hdr), _SG_HDR_SIZE)
        fcntl.ioctl(self._fd, _SG_IO, ioctl_buf)
        ctypes.memmove(ctypes.addressof(hdr), ioctl_buf, _SG_HDR_SIZE)

        if hdr.status != 0:
            log.warning("SG_IO send_cdb status=%d host=%d driver=%d",
                        hdr.status, hdr.host_status, hdr.driver_status)
            return False
        return True

    def read_cdb(self, cdb: bytes, length: int,
                 timeout_ms: int = 5000) -> bytes:
        """SCSI CDB + data-in via single SG_IO ioctl.  Empty bytes on error.

        Not cached — reads happen only at handshake/poll, not per frame.
        """
        log.debug("LinuxScsiTransport.read_cdb: cdb_len=%d length=%d timeout=%dms",
                  len(cdb), length, timeout_ms)
        if self._fd is None:
            log.error("LinuxScsiTransport.read_cdb: %s not open", self._path)
            raise TransportError(f"LinuxScsiTransport {self._path} not open")

        cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)
        data_buf = (ctypes.c_ubyte * length)()
        sense_buf = (ctypes.c_ubyte * _SENSE_BUF_LEN)()

        hdr = _SgIoHdr()
        hdr.interface_id = ord('S')
        hdr.dxfer_direction = _SG_DXFER_FROM_DEV
        hdr.cmd_len = len(cdb)
        hdr.mx_sb_len = _SENSE_BUF_LEN
        hdr.dxfer_len = length
        hdr.dxferp = ctypes.addressof(data_buf)
        hdr.cmdp = ctypes.addressof(cdb_buf)
        hdr.sbp = ctypes.addressof(sense_buf)
        hdr.timeout = timeout_ms

        ioctl_buf = ctypes.create_string_buffer(_SG_HDR_SIZE)
        ctypes.memmove(ioctl_buf, ctypes.addressof(hdr), _SG_HDR_SIZE)
        fcntl.ioctl(self._fd, _SG_IO, ioctl_buf)
        ctypes.memmove(ctypes.addressof(hdr), ioctl_buf, _SG_HDR_SIZE)

        if hdr.status != 0:
            log.warning("SG_IO read_cdb status=%d host=%d driver=%d",
                        hdr.status, hdr.host_status, hdr.driver_status)
            return b""
        actual = length - hdr.resid
        return bytes(data_buf[:actual])

    def _alloc_write_bufs(self, cdb_len: int, data_len: int) -> tuple:
        """Build a (cdb, data, sense, hdr, ioctl) buffer set for one size class."""
        cdb_buf = (ctypes.c_ubyte * cdb_len)()
        data_buf = (ctypes.c_ubyte * data_len)()
        sense_buf = (ctypes.c_ubyte * _SENSE_BUF_LEN)()
        hdr = _SgIoHdr()
        hdr.interface_id = ord('S')
        hdr.dxfer_direction = _SG_DXFER_TO_DEV
        hdr.cmd_len = cdb_len
        hdr.mx_sb_len = _SENSE_BUF_LEN
        hdr.dxfer_len = data_len
        hdr.dxferp = ctypes.addressof(data_buf)
        hdr.cmdp = ctypes.addressof(cdb_buf)
        hdr.sbp = ctypes.addressof(sense_buf)
        ioctl_buf = ctypes.create_string_buffer(_SG_HDR_SIZE)
        return (cdb_buf, data_buf, sense_buf, hdr, ioctl_buf)


# =========================================================================
# LinuxPlatform
# =========================================================================


class LinuxPlatform(Platform):
    """Linux implementation of Platform.

    USB access via pyusb (libusb).  Udev rules installed by setup() give
    non-root users access to the devices listed in the product registry.
    """

    def __init__(self) -> None:
        log.info("LinuxPlatform: initialising")
        self._paths = LinuxPaths()
        self._sensors: SensorEnumerator | None = None
        self._autostart: AutostartManager | None = None
        self._hotplug: HotplugMonitor | None = None

    # ── Transport factories ──────────────────────────────────────────

    def open_bulk(self, vid: int, pid: int,
                  serial: str | None = None) -> BulkTransport:
        """Return an unopened PyUsbBulkTransport for HID/BULK/LY/LED."""
        log.info("LinuxPlatform.open_bulk: %04x:%04x serial=%r", vid, pid, serial)
        return PyUsbBulkTransport(vid, pid, serial)

    def open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        """Return an unopened SG_IO-backed SCSI transport.

        Resolves vid:pid → /dev/sgN via sysfs before building the
        transport.  Raises TransportError if the device isn't present
        as a SCSI generic or sd block device.
        """
        log.info("LinuxPlatform.open_scsi: %04x:%04x serial=%r", vid, pid, serial)
        path = _resolve_scsi_path(vid, pid)
        if path is None:
            log.error("LinuxPlatform.open_scsi: no /dev/sg* node for %04x:%04x",
                      vid, pid)
            raise TransportError(
                f"No SCSI device node found for {vid:04x}:{pid:04x} — "
                "check that the device is attached and the scsi_generic "
                "kernel module is loaded"
            )
        log.info("LinuxPlatform.open_scsi: %04x:%04x → %s", vid, pid, path)
        return LinuxScsiTransport(path)

    def scan_devices(self) -> list[DeviceInfo]:
        """Walk ALL_DEVICES and return a DeviceInfo for each present VID/PID.

        No kernel-subsystem filtering — we ask pyusb whether the device
        physically enumerated and let the Device subclass handle any
        per-OS driver detach on connect().
        """
        log.info("LinuxPlatform.scan_devices: scanning %d known VID/PID pairs",
                 len(ALL_DEVICES))
        found: list[DeviceInfo] = []
        for (vid, pid) in ALL_DEVICES:
            for dev in (usb.core.find(find_all=True, idVendor=vid, idProduct=pid) or []):
                serial_idx = getattr(dev, 'iSerialNumber', 0)
                serial: str = ""
                try:
                    if serial_idx:
                        serial = usb.util.get_string(dev, serial_idx) or ""
                except Exception:
                    serial = ""
                found.append(DeviceInfo(vid=vid, pid=pid, serial=serial or None))
                log.info("  found %04x:%04x serial=%r", vid, pid, serial)
        log.info("LinuxPlatform.scan_devices: %d device(s) total", len(found))
        return found

    # ── Filesystem ────────────────────────────────────────────────────

    def paths(self) -> Paths:
        log.debug("LinuxPlatform.paths()")
        return self._paths

    # ── Sensors / Autostart (stubs; real impls later) ─────────────────

    def sensors(self) -> SensorEnumerator:
        if self._sensors is None:
            log.info("LinuxPlatform.sensors: building sensor enumerator")
            self._sensors = build_linux_sensors()
        else:
            log.debug("LinuxPlatform.sensors: returning cached enumerator")
        return self._sensors

    def autostart(self) -> AutostartManager:
        if self._autostart is None:
            log.info("LinuxPlatform.autostart: building LinuxAutostart")
            self._autostart = LinuxAutostart()
        else:
            log.debug("LinuxPlatform.autostart: returning cached manager")
        return self._autostart

    def hotplug(self) -> HotplugMonitor:
        if self._hotplug is None:
            from ._hotplug import LinuxHotplugMonitor
            log.info("LinuxPlatform.hotplug: building LinuxHotplugMonitor")
            self._hotplug = LinuxHotplugMonitor()
        else:
            log.debug("LinuxPlatform.hotplug: returning cached monitor")
        return self._hotplug

    # ── Setup / permissions ──────────────────────────────────────────

    def setup(self, interactive: bool = True) -> int:
        """Run one-time Linux setup.

        Two things happen here and neither can silently no-op:
          1. udev rules — write /etc/udev/rules.d/99-trcc-lcd.rules for
             every device in the registry + modprobe quirks + sg autoload.
             Requires root; re-execs via sudo when not already root.
          2. GPU Python extras — detect GPU vendors via PCI sysfs and
             pip install matching libs (e.g., nvidia-ml-py for NVIDIA).

        Non-interactive mode prints what would be done and returns 0
        without touching the system.
        """
        log.info("LinuxPlatform.setup: interactive=%s", interactive)
        if not interactive:
            log.info("=== dry run (pass interactive=True to apply) ===")
            install_udev_rules(dry_run=True)
            vendors = detect_gpu_vendors()
            log.info("Detected GPU vendors: %s", sorted(vendors) or "none")
            install_matching_gpu_extras(vendors, dry_run=True)
            return 0

        rc_udev = install_udev_rules(dry_run=False)
        vendors = detect_gpu_vendors()
        log.info("Detected GPU vendors: %s", sorted(vendors) or "none")
        rc_gpu = install_matching_gpu_extras(vendors, dry_run=False)
        return rc_udev or rc_gpu

    def check_permissions(self) -> list[str]:
        """Return user-facing warnings if udev rules are missing, etc."""
        log.info("LinuxPlatform.check_permissions: probing")
        warnings: list[str] = []
        if not Path("/etc/udev/rules.d/99-trcc-lcd.rules").exists():
            warnings.append(
                "udev rules not installed — device access may require root. "
                "Run 'python -m trcc.next system setup' to install them."
            )
        log.info("LinuxPlatform.check_permissions: %d warning(s)", len(warnings))
        return warnings

    # ── OS identity ───────────────────────────────────────────────────

    def distro_name(self) -> str:
        """Parse /etc/os-release for the pretty name."""
        path = Path("/etc/os-release")
        if not path.exists():
            log.info("LinuxPlatform.distro_name: /etc/os-release missing, "
                     "defaulting to 'Linux'")
            return "Linux"
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    name = line.split("=", 1)[1].strip().strip('"')
                    log.info("LinuxPlatform.distro_name → %s", name)
                    return name
        except Exception as e:
            log.warning("LinuxPlatform.distro_name: parse failed (%s) — "
                        "defaulting to 'Linux'", e)
        return "Linux"

    def install_method(self) -> str:
        """Rough heuristic: PyInstaller bundle > pip > source."""
        import sys
        if getattr(sys, 'frozen', False):
            log.info("LinuxPlatform.install_method → pyinstaller (frozen)")
            return "pyinstaller"
        try:
            import shutil
            if shutil.which("trcc"):
                log.info("LinuxPlatform.install_method → pip (trcc on PATH)")
                return "pip"
        except Exception as e:
            log.debug("LinuxPlatform.install_method: shutil.which raised %s", e)
        log.info("LinuxPlatform.install_method → source")
        return "source"

    # ── Hardware probes (LED memory + disk widgets) ───────────────────

    def memory_info(self) -> list[dict[str, str]]:
        """DRAM slot probe via dmidecode; psutil fallback for totals only."""
        log.info("LinuxPlatform.memory_info: probing")
        slots = _linux_memory_info()
        log.info("LinuxPlatform.memory_info: %d slot(s)", len(slots))
        return slots

    def disk_info(self) -> list[dict[str, str]]:
        """Disk probe via lsblk + smartctl health."""
        log.info("LinuxPlatform.disk_info: probing")
        disks = _linux_disk_info()
        log.info("LinuxPlatform.disk_info: %d disk(s)", len(disks))
        return disks


# =========================================================================
# Linux hardware-probe helpers — used by LinuxPlatform.memory_info/disk_info
# =========================================================================

_DMI_MEMORY_FIELDS: frozenset[str] = frozenset({
    'manufacturer', 'part_number', 'type', 'speed',
    'configured_memory_speed', 'size', 'locator', 'form_factor',
    'rank', 'data_width', 'total_width', 'configured_voltage',
    'minimum_voltage', 'maximum_voltage', 'memory_technology',
})

_POLKIT_POLICY = '/usr/share/polkit-1/actions/com.github.lexonight1.trcc.policy'


def _privileged_cmd(binary: str, args: list[str]) -> list[str]:
    """Build a command, wrapping in pkexec when polkit policy is installed."""
    import shutil
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        return [binary, *args]
    full_path = shutil.which(binary)
    if full_path and Path(_POLKIT_POLICY).is_file() and shutil.which('pkexec'):
        return ['pkexec', full_path, *args]
    return [binary, *args]


def _linux_memory_info() -> list[dict[str, str]]:
    """Get DRAM slot info via dmidecode; falls back to psutil for totals."""
    import subprocess
    slots: list[dict[str, str]] = []
    try:
        result = subprocess.run(
            _privileged_cmd('dmidecode', ['-t', 'memory']),
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            current: dict[str, str] = {}
            for raw_line in result.stdout.splitlines():
                line = raw_line.strip()
                if line.startswith('Memory Device'):
                    if current.get('size') and current['size'] != 'No Module Installed':
                        slots.append(current)
                    current = {}
                elif ':' in line:
                    key, _, val = line.partition(':')
                    val = val.strip()
                    key = key.strip().lower().replace(' ', '_')
                    if key in _DMI_MEMORY_FIELDS:
                        current[key] = val
            if current.get('size') and current['size'] != 'No Module Installed':
                slots.append(current)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("dmidecode -t memory failed: %s", type(e).__name__)

    if not slots:
        try:
            import psutil
            mem = psutil.virtual_memory()
            total_gb = f"{mem.total / (1024**3):.1f} GB"
            slots.append({'size': total_gb, 'type': 'Unknown',
                          'speed': 'Unknown', 'manufacturer': 'Unknown'})
        except (OSError, ImportError, AttributeError) as e:
            log.debug("psutil.virtual_memory failed: %s", type(e).__name__)
    return slots


def _linux_disk_info() -> list[dict[str, str]]:
    """Get disk info via lsblk -J + smartctl -H per disk."""
    import json
    import subprocess
    disks: list[dict[str, str]] = []
    try:
        result = subprocess.run(
            ['lsblk', '-J', '-o', 'NAME,MODEL,SIZE,TYPE,ROTA'],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            for dev in data.get('blockdevices', []):
                if dev.get('type') != 'disk' or not dev.get('model'):
                    continue
                disk_type = 'HDD' if dev.get('rota') else 'SSD'
                disk = {
                    'name': dev.get('name', ''),
                    'model': dev.get('model', 'Unknown').strip(),
                    'size': dev.get('size', 'Unknown'),
                    'type': disk_type,
                }
                if (health := _smart_health(dev['name'])):
                    disk['health'] = health
                disks.append(disk)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as e:
        log.debug("lsblk -J failed: %s", type(e).__name__)
    return disks


def _smart_health(dev_name: str) -> str | None:
    """SMART overall-health status via smartctl -H."""
    import subprocess
    try:
        result = subprocess.run(
            _privileged_cmd('smartctl', ['-H', f'/dev/{dev_name}']),
            capture_output=True, text=True, timeout=5, check=False,
        )
        for line in result.stdout.splitlines():
            if 'overall-health' in line.lower() or 'health status' in line.lower():
                if 'PASSED' in line:
                    return 'PASSED'
                if 'FAILED' in line:
                    return 'FAILED'
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("smartctl -H /dev/%s failed: %s", dev_name, type(e).__name__)
    return None
