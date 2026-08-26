"""LinuxOS — concrete Platform implementation for Linux.

This file owns every Linux-specific thing: sysfs walks, SG_IO ioctl,
XDG paths, udev-rule checks, autostart.  Other OSes have their own
sibling file.

Key pieces:
    LinuxPaths             — XDG + HOME resolution
    LinuxScsiTransport     — SCSI over /dev/sgN via SG_IO ioctl
    _resolve_scsi_path     — vid:pid → /dev/sg* via sysfs walk
    LinuxOS          — the Platform ABC wiring
"""
from __future__ import annotations

import ctypes
import errno
import logging
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._imc_timings import ImcTimings

from ...core.errors import TransportError
from ...core.logs import per_frame
from ...core.models import UsbPowerState
from ...core.ports import (
    AutostartManager,
    HotplugMonitor,
    PackageManager,
    Paths,
    ScsiTransport,
    SensorEnumerator,
)
from ..sensors.aggregator import build_linux_sensors
from ..sensors.gpu_detect import (
    detect_gpu_vendors,
    install_matching_gpu_extras,
)
from ._base import BaseOS, BasePaths
from ._desktop_entry import XdgDesktopEntry
from ._selinux import install as install_selinux_policy
from ._udev import install as install_udev_rules

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


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
# ``LinuxOS.autostart()`` below.


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
        frame_log.debug("LinuxScsiTransport.is_open → %s (fd=%s)", opened, self._fd)
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
        frame_log.debug("LinuxScsiTransport.send_cdb: cdb_len=%d data_len=%d timeout=%dms",
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
# LinuxOS
# =========================================================================

# logical tool → distro package name (consumed by software_install_hint).
_LINUX_INSTALL_PKGS: dict[str, str] = {
    "ffmpeg": "ffmpeg",
    "7z": "p7zip",
    "python": "python3",
    "pynvml": "python3-pynvml",
}

@dataclass(frozen=True)
class LinuxFamily:
    """One Linux package-manager family: a record, not a subclass.

    These were eight classes -- AptLinux, DnfLinux, PacmanLinux and so on --
    each subclassing LinuxOS.  Measured before the change: **all eight defined
    zero methods.**  Every attribute was a package-manager fact.  A child whose
    entire content is data is not a subclass; it is a record that has not been
    given a type yet, and reading "DATA over the parent's methods" as a design
    rather than an alarm is how eight of them accumulated.

    A package manager is also not an operating system.  Windows and macOS never
    grew this because they have exactly one manager each, so there was nothing
    to subclass along -- which is the diagnosis: the axis was never an OS axis.

    ``pacman`` is a package manager.  ``LinuxOS`` is one class that *has* one.
    """

    #: Human label for logs and hints ("Fedora-family").
    name: str
    #: The binary probed to detect this family — and the detection order in
    #: :data:`_FAMILIES` is load-bearing, first hit wins.
    manager: str
    #: Install one-liner with a ``{pkg}`` slot.
    install_cmd: str
    #: Argv that upgrades trcc-linux, or empty where there is no single line.
    upgrade_cmd: tuple[str, ...] = ()
    #: tool -> this family's package name, where it differs from
    #: :data:`_LINUX_INSTALL_PKGS`.  A missing row means "unconfirmed", not
    #: "same as Debian" — guessing is what #207 was.
    packages: Mapping[str, str] = field(default_factory=dict)


#: Probe order, first match wins.  Preserved exactly from the class tuple it
#: replaced: it is NOT definition order, and a machine with two managers gets
#: the first listed.
_FAMILIES: tuple[LinuxFamily, ...] = (
    LinuxFamily(
        name="Fedora-family", manager="dnf",
        install_cmd="sudo dnf install {pkg}",
        upgrade_cmd=("sudo", "dnf", "upgrade", "-y", "trcc-linux"),
        # Advised by BINARY PATH, not package name.  Both shared names are
        # wrong here and both fail in a way that looks like success:
        #   p7zip  -> 7zip-standalone, ships ['7za'] and NO 7z
        #   ffmpeg -> not in stock Fedora at all (that is RPM Fusion)
        # A corrected NAME only trades one wrong answer for another, because
        # which package owns the binary depends on the user's repos:
        #   /usr/bin/ffmpeg  stock -> ffmpeg-free  ·  +RPM Fusion -> ffmpeg
        # On RHEL/Rocky/Alma both are EPEL-only (el9/el10_2/el10_3).
        packages={"pynvml": "python3-pynvml",
                  "7z": "/usr/bin/7z",
                  "ffmpeg": "/usr/bin/ffmpeg"},
    ),
    LinuxFamily(
        name="Debian-family", manager="apt",
        install_cmd="sudo apt install {pkg}",
        upgrade_cmd=("sudo", "apt", "upgrade", "-y", "trcc-linux"),
        packages={"pynvml": "python3-pynvml"},
    ),
    LinuxFamily(
        name="Arch-family", manager="pacman",
        install_cmd="sudo pacman -S {pkg}",
        upgrade_cmd=("sudo", "pacman", "-Syu", "--noconfirm", "trcc-linux"),
        # Arch names it differently, and advising Debian's name here is #207.
        packages={"pynvml": "python-nvidia-ml-py"},
    ),
    LinuxFamily(
        name="SUSE-family", manager="zypper",
        install_cmd="sudo zypper install {pkg}",
        upgrade_cmd=("sudo", "zypper", "update", "-y", "trcc-linux"),
        packages={"pynvml": "python3-pynvml"},
    ),
    LinuxFamily(
        name="Void", manager="xbps-install",
        install_cmd="sudo xbps-install {pkg}",
        upgrade_cmd=("sudo", "xbps-install", "-u", "trcc-linux"),
    ),
    LinuxFamily(
        name="Alpine", manager="apk",
        install_cmd="sudo apk add {pkg}",
        upgrade_cmd=("sudo", "apk", "upgrade", "trcc-linux"),
    ),
    LinuxFamily(
        name="NixOS", manager="nix-env",
        install_cmd="nix-env -iA nixpkgs.{pkg}",
        upgrade_cmd=(),            # flake-managed; there is no one upgrade line
    ),
)

#: RHEL / CentOS Stream / Rocky / AlmaLinux.  Same manager as Fedora, which is
#: why it cannot be told apart by the probe and needs /etc/os-release.
#:
#: Measured 2026-08-21 against AlmaLinux 9 + EPEL 9 metadata: BOTH binaries the
#: app looks for are EPEL-only there.  Neither /usr/bin/7z nor /usr/bin/ffmpeg
#: is in BaseOS or AppStream, so a hint that verifies clean against Fedora
#: finds nothing on a Rocky box that has not enabled EPEL.
_EL_FAMILY = LinuxFamily(
    name="EL-family", manager="dnf",
    install_cmd="sudo dnf install {pkg}",
    upgrade_cmd=("sudo", "dnf", "upgrade", "-y", "trcc-linux"),
    packages={"pynvml": "python3-pynvml",
              "7z": "/usr/bin/7z",
              "ffmpeg": "/usr/bin/ffmpeg"},
)

#: ``ID=`` values that mean Enterprise Linux, and the ``ID_LIKE`` token that
#: covers the rebuilds this list does not name.
_EL_IDS = frozenset({"rhel", "rocky", "almalinux", "centos", "ol", "oracle"})

#: The package whose presence decides whether the EL hints work as written.
_EPEL = "epel-release"


#: No recognised manager.  Empty rather than borrowed: an unresolved Linux must
#: say so, not answer as somebody else's distro.
_GENERIC_FAMILY = LinuxFamily(name="Linux", manager="", install_cmd="")


#: Tools whose distro package name is confirmed to differ per family, and the
#: honest answer when a family has not confirmed one.  Without this an
#: unconfirmed family silently inherits Debian's name — a command that looks
#: right and fails (#207).
_UNCONFIRMED_FALLBACK: dict[str, str] = {
    "pynvml": "pip install nvidia-ml-py",
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
#
# These now live on the family classes at the bottom of this file — one class
# per package manager, each owning its own command and names.  They used to be
# four parallel tables in three files, and one had already drifted short by two.

def _is_enterprise_linux(text: str | None = None) -> bool:
    """Does /etc/os-release describe RHEL or a rebuild of it?

    Fedora and EL both answer ``dnf``, so the binary probe cannot separate
    them -- and the difference matters, because the packages our hints name are
    EPEL-only on EL.  ``ID`` names the distro and ``ID_LIKE`` covers rebuilds
    this does not list by name.

    *text* is injectable because there is no EL box here to test on; the tests
    feed real os-release contents rather than mocking the outcome.
    """
    if text is None:
        try:
            text = Path("/etc/os-release").read_text(encoding="utf-8")
        except OSError:
            return False
    ident, like = "", ""
    for line in text.splitlines():
        if line.startswith("ID="):
            ident = line[3:].strip().strip('"')
        elif line.startswith("ID_LIKE="):
            like = line[8:].strip().strip('"')
    hit = ident in _EL_IDS or "rhel" in like.split()
    log.debug("_is_enterprise_linux: ID=%r ID_LIKE=%r -> %s", ident, like, hit)
    return hit


class LinuxOS(BaseOS, key="linux"):
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

    def _open_scsi(self, vid: int, pid: int,
                  serial: str | None = None) -> ScsiTransport:
        """Return an unopened SG_IO-backed SCSI transport.

        Resolves vid:pid → /dev/sgN via sysfs before building the
        transport.  Raises TransportError if the device isn't present
        as a SCSI generic or sd block device.
        """
        log.info("LinuxOS.open_scsi: %04x:%04x serial=%r", vid, pid, serial)
        path = _resolve_scsi_path(vid, pid)
        if path is None:
            log.error("LinuxOS.open_scsi: no /dev/sg* node for %04x:%04x",
                      vid, pid)
            raise TransportError(
                f"No SCSI device node found for {vid:04x}:{pid:04x} — "
                "check that the device is attached and the scsi_generic "
                "kernel module is loaded"
            )
        log.info("LinuxOS.open_scsi: %04x:%04x → %s", vid, pid, path)
        return LinuxScsiTransport(path)

    # ── Setup / permissions ──────────────────────────────────────────

    def setup(self, interactive: bool = True) -> int:
        """Run one-time Linux setup.

        Four things happen here and none can silently no-op:
          1. udev rules — write /etc/udev/rules.d/99-trcc-lcd.rules for
             every device in the registry + modprobe quirks + sg autoload.
             Requires root; re-execs via sudo when not already root.
          2. GPU Python extras — detect GPU vendors via PCI sysfs and
             pip install matching libs (e.g., nvidia-ml-py for NVIDIA).
          3. SELinux policy — on enforcing systems, build + load the
             ``trcc_usb`` module so the bulk/SCSI USB ioctls aren't blocked.
             No-op off SELinux.  (RPM installs already load it in %post.)
          4. Desktop entry — register the app in the applications menu.
             Our packages do this; a pip/pipx install does not, so without
             it the app installs, autostarts, and cannot be launched from
             the menu (#231).  Per-user, no root, skipped when a package
             already provides it.

        Non-interactive mode prints what would be done and returns 0
        without touching the system.
        """
        log.info("LinuxOS.setup: interactive=%s", interactive)
        if not interactive:
            log.info("=== dry run (pass interactive=True to apply) ===")
            install_udev_rules(dry_run=True)
            vendors = detect_gpu_vendors()
            log.info("Detected GPU vendors: %s", sorted(vendors) or "none")
            install_matching_gpu_extras(vendors, dry_run=True)
            install_selinux_policy(dry_run=True)
            log.info("would install the desktop entry: %s",
                     XdgDesktopEntry().path)
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
        # Desktop integration is a convenience, never a reason to report
        # failure — the device still works from the CLI without a menu icon.
        XdgDesktopEntry().install()
        return rc_udev or rc_selinux

    def check_permissions(self) -> list[str]:
        """Return user-facing warnings if udev rules are missing, etc."""
        log.info("LinuxOS.check_permissions: probing")
        warnings: list[str] = []
        if not Path("/etc/udev/rules.d/99-trcc-lcd.rules").exists():
            warnings.append(
                "udev rules not installed — device access may require root. "
                "Run 'python -m trcc system setup' to install them."
            )
        log.info("LinuxOS.check_permissions: %d warning(s)", len(warnings))
        return warnings

    # ── OS identity ───────────────────────────────────────────────────

    def distro_name(self) -> str:
        """Parse /etc/os-release for the pretty name."""
        path = Path("/etc/os-release")
        if not path.exists():
            log.info("LinuxOS.distro_name: /etc/os-release missing, "
                     "defaulting to 'Linux'")
            return "Linux"
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    name = line.split("=", 1)[1].strip().strip('"')
                    log.info("LinuxOS.distro_name → %s", name)
                    return name
        except Exception as e:
            log.warning("LinuxOS.distro_name: parse failed (%s) — "
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

    def __init__(self, family: LinuxFamily | None = None) -> None:
        """Detect the package-manager family once, or accept one.

        ``family`` is for tests and ``dev/tools/check_program_deps.py``, which
        must be able to ask what a Debian box would be told while running on
        Fedora.  Production passes nothing: ``current_platform()`` builds this
        with ``cls()`` and the probe runs.
        """
        super().__init__()
        self._family = family if family is not None else self._detect_family()
        log.info("LinuxOS: family=%s manager=%s",
                 self._family.name, self._family.manager or "(none)")

    @staticmethod
    def _detect_family() -> LinuxFamily:
        """First installed manager wins — the order in :data:`_FAMILIES`.

        ``sys.platform`` is "linux" on every distro, so the manager is the only
        thing that distinguishes them.  Hundreds of distros, a handful of
        managers.
        """
        for candidate in _FAMILIES:
            if shutil.which(candidate.manager):
                log.debug("LinuxOS._detect_family: found %s",
                          candidate.manager)
                if candidate.manager == "dnf" and _is_enterprise_linux():
                    log.info("LinuxOS._detect_family: dnf, but os-release says "
                             "Enterprise Linux — EL family")
                    return _EL_FAMILY
                return candidate
        log.warning("LinuxOS._detect_family: no known package manager (%s) — "
                    "install advice will be generic",
                    ", ".join(f.manager for f in _FAMILIES))
        return _GENERIC_FAMILY

    @property
    def family(self) -> LinuxFamily:
        """This machine's package-manager family."""
        log.debug("LinuxOS.family: %s", self._family.name)
        return self._family

    def package_command(self, pkg: str) -> str:
        """This family's install line for *pkg*, or an honest generic."""
        if not self._family.install_cmd:
            log.info("package_command: no manager on this host — generic advice")
            return f"Install {pkg} via your package manager"
        return self._family.install_cmd.format(pkg=pkg)

    def _build_packages(self) -> PackageManager:
        """Only the rpm families can be asked so far.

        Verified against dnf5 + rpm on Fedora 44.  apt/pacman/zypper/apk/xbps
        are absent rather than guessed: writing them from documentation is what
        put four wrong package names in the tables this replaces.
        """
        from ._packages import NoPackageManager, Rpm
        if self._family.manager == "dnf":
            log.info("%s._build_packages: rpm", type(self).__name__)
            return Rpm()
        log.info("%s._build_packages: %s not implemented yet — cannot be asked",
                 type(self).__name__, self._family.manager or "(none)")
        return NoPackageManager()

    def package_manager(self) -> str:
        """This family's manager — "" on a Linux we did not recognise."""
        log.debug("package_manager: %s", self._family.manager or "(none)")
        return self._family.manager

    def upgrade_command(self) -> tuple[str, ...]:
        """Argv that upgrades trcc-linux on this family, or empty if unknown."""
        log.debug("upgrade_command: %s", self._family.upgrade_cmd)
        return self._family.upgrade_cmd

    def software_install_hint(self, tool: str) -> str:
        """Install line for a logical tool, in this family's package manager.

        A tool whose name is known to differ per family, and which THIS family
        has not confirmed, gets the fallback rather than another family's name.
        Advising Debian's ``python3-pynvml`` on Arch is #207.
        """
        log.debug("software_install_hint: tool=%s manager=%s",
                  tool, self._family.manager or "(none)")
        pkg = self._family.packages.get(tool)
        if pkg is None and tool in _UNCONFIRMED_FALLBACK:
            log.info("software_install_hint: no confirmed %s package for %s",
                     tool, self._family.name)
            return _UNCONFIRMED_FALLBACK[tool]
        command = self.package_command(pkg or _LINUX_INSTALL_PKGS.get(tool, tool))
        return self._with_epel(command)

    def _with_epel(self, command: str) -> str:
        """Prepend enabling EPEL on an EL box that has not.

        Both binaries the app probes for are EPEL-only on RHEL/Rocky/Alma, so
        the command is correct there ONLY once EPEL is enabled.  Whether it is
        is a local question -- ``rpm -q epel-release`` -- so this adapts rather
        than warning: a user who already has EPEL sees the plain command, and a
        user who does not gets one line that works.

        One line, deliberately.  Every consumer renders fix_hint under a
        `hint: ` label on a single line, and an embedded newline breaks out of
        both the label and the indent (see tests/test_install_hint_shape.py).
        """
        if self._family is not _EL_FAMILY:
            return command
        try:
            if self.packages().installed(_EPEL):
                log.debug("_with_epel: EPEL already enabled")
                return command
        except Exception as e:                 # a hint must never raise
            log.warning("_with_epel: could not check %s (%s)", _EPEL, e)
            return command
        log.info("_with_epel: EL without EPEL — prefixing enablement")
        return f"sudo dnf install {_EPEL} && {command}"

    def permission_denied_hint(self) -> str:
        log.debug("LinuxOS.permission_denied_hint: called")
        return "run 'trcc system setup' to install udev rules"

    def no_devices_hint(self) -> str:
        log.debug("LinuxOS.no_devices_hint: called")
        from ._udev import RULES_PATH
        return (
            f"Run `trcc system setup` to install the udev rules "
            f"({RULES_PATH}), then replug the device."
        )

    # ── Hardware probes (LED memory + disk widgets) ───────────────────

    def memory_info(self) -> list[dict[str, str]]:
        """DRAM slot probe via dmidecode; psutil fallback for totals only."""
        log.info("LinuxOS.memory_info: probing")
        slots = _linux_memory_info()
        log.info("LinuxOS.memory_info: %d slot(s)", len(slots))
        return slots

    def disk_info(self) -> list[dict[str, str]]:
        """Disk probe via lsblk + smartctl health."""
        log.info("LinuxOS.disk_info: probing")
        disks = _linux_disk_info()
        log.info("LinuxOS.disk_info: %d disk(s)", len(disks))
        return disks


# =========================================================================
# Linux families — one class per package manager, not per distro
# =========================================================================
#
# Hundreds of distros, a handful of managers.  Ubuntu/Mint/Pop/Kali all answer
# "apt", so the manager is the axis; a class per distro would be hundreds of
# subclasses differing by one string.  These replace four parallel tables that
# lived in three files, one of which had already drifted short by two entries.


# The eight LinuxOS subclasses that used to live here -- AptLinux, DnfLinux,
# PacmanLinux, ZypperLinux, XbpsLinux, ApkLinux, NixLinux, GenericLinux -- are
# now rows in :data:`_FAMILIES` above.  Measured before removing them: all
# eight defined ZERO methods.  See :class:`LinuxFamily` for why that is the
# defect and not the design.



# =========================================================================
# Linux hardware-probe helpers — used by LinuxOS.memory_info/disk_info
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
# per boot; current_platform() builds a fresh LinuxOS each call, so the cache
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
