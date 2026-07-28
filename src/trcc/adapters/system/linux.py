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
import errno
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._imc_timings import ImcTimings

from ...core.errors import TransportError
from ...core.models import UsbPowerState
from ...core.ports import (
    AutostartManager,
    HotplugMonitor,
    Paths,
    ScsiTransport,
    SensorEnumerator,
)
from ..sensors.aggregator import build_linux_sensors
from ..sensors.gpu_detect import (
    detect_gpu_vendors,
    install_matching_gpu_extras,
)
from . import PlatformFactory
from ._base import BaseOS, BasePaths
from ._selinux import install as install_selinux_policy
from ._udev import install as install_udev_rules

log = logging.getLogger(__name__)


# =========================================================================
# LinuxPaths — XDG-style locations
# =========================================================================


class LinuxPaths(BasePaths):
    """XDG + HOME locations for user data."""

    def __init__(self) -> None:
        home = Path.home()
        self._root = home / ".trcc"
        self._user_content = home / ".trcc-user"
        log.info("LinuxPaths: root=%s user_content=%s",
                 self._root, self._user_content)


# =========================================================================
# Autostart — XDG .desktop manager moved to ._autostart
# =========================================================================
#
# ``XdgDesktopAutostart`` (writes ~/.config/autostart/trcc.desktop) now
# lives in ``._autostart`` so ``BSDPlatform`` can share the same XDG
# mechanism (legacy ran the identical code on both).  See
# ``LinuxPlatform.autostart()`` below.


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
    log.debug("_resolve_scsi_path: %04x:%04x", vid, pid)
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
    log.debug("_walk_sysfs_for_vid_pid: start=%s", start)
    path = Path(start).resolve()
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
            if e.errno == errno.EACCES:
                # A resolved-but-unreadable node almost always means setup was
                # never run: the /dev/sd* block fallback is root-only (the udev
                # 0666 rule only covers scsi_generic), and even /dev/sg* is
                # root-only until the setup rule lands.  Name the remediation
                # instead of a bare EACCES.  (#217)
                is_block = self._path.startswith("/dev/sd")
                detail = ("this is a root-only block node (the sg kernel module "
                          "isn't loaded) — " if is_block else "")
                log.warning(
                    "LinuxScsiTransport: permission denied on %s — %srun "
                    "`trcc system setup` then reboot to load sg and grant "
                    "0666 access without sudo (#217)", self._path, detail,
                )
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
        import fcntl  # Linux-only stdlib — lazy so linux.py imports on Windows (#166)
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
        import fcntl  # Linux-only stdlib — lazy so linux.py imports on Windows (#166)
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

# logical tool → distro package name (consumed by software_install_hint).
_LINUX_INSTALL_PKGS: dict[str, str] = {
    "ffmpeg": "ffmpeg",
    "7z": "p7zip",
    "python": "python3",
    "pynvml": "python3-pynvml",
}

# Tools whose package name DIFFERS per distro.  One name for all of Linux is a
# lie: advising "python3-pynvml" on Arch names a package that does not exist,
# so the NVIDIA user is told to run a command that fails (#207).
#
# This is also why the reader is an optdepend/Recommends and must stay one:
# Arch's python-nvidia-ml-py depends on nvidia-utils (~938 MB) and Debian's
# python3-pynvml pulls libnvidia-ml1 from *contrib* — hard-depending inflicts
# the NVIDIA driver stack on every AMD/Intel owner (#216) and breaks installs
# where contrib is not enabled.  The reader is optional; the ADVICE is what
# has to be right.
_LINUX_PKG_BY_MANAGER: dict[str, dict[str, str]] = {
    "pynvml": {
        "pacman": "python-nvidia-ml-py",
        "apt": "python3-pynvml",
        "dnf": "python3-pynvml",
        "zypper": "python3-pynvml",
    },
}


@PlatformFactory.register("linux")
class LinuxPlatform(BaseOS):
    """Linux implementation — same OS contract; only internals below differ.

    USB access via pyusb (libusb).  Udev rules installed by setup() give
    non-root users access to the devices listed in the product registry.
    """

    # ── Per-OS internals (the add-a-new-OS interface) ────────────────────

    def _make_paths(self) -> Paths:
        return LinuxPaths()

    def _build_sensors(self) -> SensorEnumerator:
        return build_linux_sensors()

    def _build_autostart(self) -> AutostartManager:
        # XDG .desktop — ~/.config/autostart/trcc.desktop.
        from ._autostart import XdgDesktopAutostart
        return XdgDesktopAutostart()

    def _build_hotplug(self) -> HotplugMonitor:
        from ._hotplug import LinuxHotplugMonitor
        return LinuxHotplugMonitor()

    # ── Transport factories ──────────────────────────────────────────

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

    # ── Setup / permissions ──────────────────────────────────────────

    def setup(self, interactive: bool = True) -> int:
        """Run one-time Linux setup.

        Three things happen here and none can silently no-op:
          1. udev rules — write /etc/udev/rules.d/99-trcc-lcd.rules for
             every device in the registry + modprobe quirks + sg autoload.
             Requires root; re-execs via sudo when not already root.
          2. GPU Python extras — detect GPU vendors via PCI sysfs and
             pip install matching libs (e.g., nvidia-ml-py for NVIDIA).
          3. SELinux policy — on enforcing systems, build + load the
             ``trcc_usb`` module so the bulk/SCSI USB ioctls aren't blocked.
             No-op off SELinux.  (RPM installs already load it in %post.)

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
            install_selinux_policy(dry_run=True)
            return 0

        rc_udev = install_udev_rules(dry_run=False)
        vendors = detect_gpu_vendors()
        log.info("Detected GPU vendors: %s", sorted(vendors) or "none")
        # GPU sensor extras are a BONUS — device access (udev) is the real job.
        # A pip hiccup here (e.g. an offline box) must NOT make setup report
        # total failure, which scared users into thinking nothing worked (#161).
        rc_gpu = install_matching_gpu_extras(vendors, dry_run=False)
        if rc_gpu:
            log.warning("GPU sensor extras install returned %d — device setup "
                        "is unaffected; GPU readings stay unavailable until the "
                        "reader installs", rc_gpu)
        rc_selinux = install_selinux_policy(dry_run=False)
        return rc_udev or rc_selinux

    def check_permissions(self) -> list[str]:
        """Return user-facing warnings if udev rules are missing, etc."""
        log.info("LinuxPlatform.check_permissions: probing")
        warnings: list[str] = []
        if not Path("/etc/udev/rules.d/99-trcc-lcd.rules").exists():
            warnings.append(
                "udev rules not installed — device access may require root. "
                "Run 'python -m trcc system setup' to install them."
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

    def usb_power_state(self, vid: int, pid: int) -> UsbPowerState | None:
        """Read the kernel's runtime-PM view of this device from sysfs.

        Read-only.  Setting the policy is the udev rules' job (``_udev.py``);
        this reports what the kernel currently thinks so a timed-out handshake
        can be told apart from a SUSPENDED panel (#150).

        ``supports_remote_wakeup`` comes from the config descriptor's
        ``bmAttributes`` bit 5.  It is the discriminator that explains why two
        panels behave differently: a device that cannot wake the host is never
        autosuspended, whatever ``power/control`` says — measured on the dev
        panel (0402:3922, bmAttributes 0x80 → bit 5 clear → never suspends
        even at control=auto).
        """
        log.debug("usb_power_state: %04x:%04x", vid, pid)
        base = Path("/sys/bus/usb/devices")
        if not base.is_dir():
            return None
        for dev in base.iterdir():
            try:
                if (int((dev / "idVendor").read_text().strip(), 16) != vid
                        or int((dev / "idProduct").read_text().strip(), 16) != pid):
                    continue
            except (OSError, ValueError):
                continue

            def _read(node: Path, name: str, default: str = "") -> str:
                try:
                    return (node / name).read_text().strip()
                except OSError:
                    return default

            def _int(node: Path, name: str) -> int:
                raw = _read(node, name)
                return int(raw) if raw.isdigit() else 0

            # bmAttributes is per-configuration; bit 5 = remote wakeup.
            wakeup = False
            raw_attrs = _read(dev, "bmAttributes")
            try:
                wakeup = bool(int(raw_attrs, 16) & 0x20) if raw_attrs else False
            except ValueError:
                wakeup = False

            state = UsbPowerState(
                control=_read(dev, "power/control"),
                runtime_status=_read(dev, "power/runtime_status"),
                autosuspend_delay_ms=_int(dev, "power/autosuspend_delay_ms"),
                suspended_time_ms=_int(dev, "power/runtime_suspended_time"),
                supports_remote_wakeup=wakeup,
            )
            log.info("usb_power_state %04x:%04x → control=%s status=%s "
                     "remote_wakeup=%s delay=%dms",
                     vid, pid, state.control, state.runtime_status,
                     state.supports_remote_wakeup, state.autosuspend_delay_ms)
            return state
        log.debug("usb_power_state: %04x:%04x not present on the bus", vid, pid)
        return None

    # ── Per-OS diagnostic hints (distro package manager) ──────────────

    def software_install_hint(self, tool: str) -> str:
        """Distro-package-manager install line for ``tool``.

        Reuses the existing pkg-manager detection (also used by the Linux
        setup commands) — maps the logical tool to its distro package name.
        """
        log.debug("LinuxPlatform.software_install_hint: tool=%s", tool)
        from ..diagnostics.health import detect_package_manager, package_install_hint
        by_manager = _LINUX_PKG_BY_MANAGER.get(tool)
        if by_manager is not None:
            pm = detect_package_manager() or ""
            pkg = by_manager.get(pm) or _LINUX_INSTALL_PKGS.get(tool, tool)
        else:
            pkg = _LINUX_INSTALL_PKGS.get(tool, tool)
        log.info("software_install_hint: tool=%s → package=%s", tool, pkg)
        return package_install_hint(pkg)

    def permission_denied_hint(self) -> str:
        log.debug("LinuxPlatform.permission_denied_hint: called")
        return "run 'trcc system setup' to install udev rules"

    def no_devices_hint(self) -> str:
        log.debug("LinuxPlatform.no_devices_hint: called")
        return (
            "Run `trcc system setup` to install the udev rules "
            "(/etc/udev/rules.d/99-trcc.rules), then replug the device."
        )

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

# Privileged MCHBAR reader (installed by the distro packages).  Intel family-6
# Alder Lake (0x97/0x9A) + Raptor Lake (0xB7/0xBA/0xBF) share the register map.
_IMC_HELPER = '/usr/bin/trcc-imc'
_ADL_RPL_MODELS = frozenset({0x97, 0x9A, 0xB7, 0xBA, 0xBF})


def _privileged_cmd(binary: str, args: list[str]) -> list[str]:
    """Build a command, wrapping in pkexec when polkit policy is installed."""
    log.debug("_privileged_cmd: binary=%s args=%s", binary, args)
    import shutil
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        return [binary, *args]
    full_path = shutil.which(binary)
    if full_path and Path(_POLKIT_POLICY).is_file() and shutil.which('pkexec'):
        return ['pkexec', full_path, *args]
    return [binary, *args]


def _enrich_with_spd_timings(slots: list[dict[str, str]]) -> None:
    """Add DDR5 SPD timings (tcas..trfc) to every slot, in place.

    dmidecode does not expose CAS timings; the DDR5 SPD EEPROM does.  Matched
    DIMMs share timings, so one rootless SPD read covers the whole channel.
    Failure (non-DDR5, no spd5118, permissions) leaves slots untouched → "NC".
    """
    if not slots:
        return
    from .spd import read_spd_timings
    t = read_spd_timings()
    if t is None:
        log.info("_enrich_with_spd_timings: no DDR5 SPD timings available")
        return
    fields = {'tcas': t.tcas, 'trcd': t.trcd, 'trp': t.trp,
              'tras': t.tras, 'trc': t.trc, 'trfc': t.trfc}
    log.info("_enrich_with_spd_timings: %s", fields)
    for slot in slots:
        for key, val in fields.items():
            slot[key] = str(val)


# Sentinel so a failed/unsupported live read is cached too (timings are static
# per boot; PlatformFactory builds a fresh LinuxPlatform each call, so the cache
# lives at module scope, not on the instance).
_UNREAD: object = object()
_live_imc_cache: object = _UNREAD


def _cpu_is_adl_rpl() -> bool:
    """Rootless: True iff /proc/cpuinfo is an Intel family-6 Alder/Raptor Lake."""
    family = model = None
    try:
        with Path("/proc/cpuinfo").open() as f:
            for line in f:
                if line.startswith("vendor_id") and "GenuineIntel" not in line:
                    return False
                if line.startswith("cpu family"):
                    family = int(line.split(":")[1])
                elif line.startswith("model") and "name" not in line:
                    model = int(line.split(":")[1])
                if line == "\n":          # end of the first processor block
                    break
    except (OSError, ValueError):
        return False
    return family == 6 and model in _ADL_RPL_MODELS


def _read_live_imc_timings() -> ImcTimings | None:
    """Live IMC timings via the privileged ``trcc-imc`` helper, cached for life.

    Silent by design: only spawns the helper when the polkit policy + helper are
    installed (packaged installs) so it never prompts for a password.  Any
    failure (unsupported CPU, no policy, bad output) caches ``None`` so we never
    re-spawn on the next ``memory_info()`` call.
    """
    global _live_imc_cache
    if _live_imc_cache is not _UNREAD:
        return _live_imc_cache  # type: ignore[return-value]
    _live_imc_cache = None      # cache the negative result up front

    if os.environ.get("TRCC_DISABLE_LIVE_IMC"):
        return None
    if not _cpu_is_adl_rpl() or not Path(_IMC_HELPER).is_file():
        return None

    import shutil
    import subprocess
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd = [_IMC_HELPER]
    elif Path(_POLKIT_POLICY).is_file() and shutil.which("pkexec"):
        cmd = ["pkexec", _IMC_HELPER]
    else:
        return None             # no silent privilege path available

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("_read_live_imc_timings: helper failed: %s", type(e).__name__)
        return None
    if result.returncode != 0:
        log.info("_read_live_imc_timings: helper exit=%d", result.returncode)
        return None

    regs: dict[str, int] = {}
    for token in result.stdout.split():
        key, _, val = token.partition("=")
        if key in ("tc_pre", "odt", "refresh", "bios_ddr"):
            try:
                regs[key] = int(val, 16)
            except ValueError:
                log.warning("_read_live_imc_timings: bad token %r", token)
                return None
    if len(regs) != 4:
        log.warning("_read_live_imc_timings: incomplete helper output")
        return None

    from ._imc_timings import decode_adl
    timings = decode_adl(regs["tc_pre"], regs["odt"],
                         regs["refresh"], regs["bios_ddr"])
    _live_imc_cache = timings
    return timings


def _enrich_with_live_imc_timings(slots: list[dict[str, str]]) -> None:
    """Override the SPD timings with the live IMC values, in place.

    Overrides tcas/trcd/trp/tras/trc; the SPD ``trfc`` (tRFC1) is kept — the live
    register is tRFC2 (2x refresh), a different parameter, so overwriting it
    would silently mislabel the panel.  No live read → SPD values stand.
    """
    if not slots:
        return
    t = _read_live_imc_timings()
    if t is None:
        return
    fields = {'tcas': t.tcas, 'trcd': t.trcd, 'trp': t.trp,
              'tras': t.tras, 'trc': t.trc}
    log.info("_enrich_with_live_imc_timings: %s", fields)
    for slot in slots:
        for key, val in fields.items():
            slot[key] = str(val)


def _linux_memory_info() -> list[dict[str, str]]:
    """Get DRAM slot info via dmidecode; falls back to psutil for totals."""
    log.debug("_linux_memory_info: called")
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

    _enrich_with_spd_timings(slots)
    _enrich_with_live_imc_timings(slots)

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
    log.debug("_linux_disk_info: called")
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
    log.debug("_smart_health: dev=%s", dev_name)
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
