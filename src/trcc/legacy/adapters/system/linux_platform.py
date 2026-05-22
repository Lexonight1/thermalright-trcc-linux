"""Linux Platform — single file, single class, all Linux logic.

Everything the app needs from the OS lives on LinuxPlatform.
No intermediate classes, no OS names leaking out.
Private helpers and a SensorEnumerator (lifecycle needs its own class)
are scoped to this file.
"""
from __future__ import annotations

import logging
import os
import shutil
import site
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

if TYPE_CHECKING:
    from trcc.legacy.core.models import UsbAddress

# Re-exported for back-compat: tests + callers import SensorEnumerator and
# `_ensure_nvml` from this module. The implementation lives in
# `linux_sensors` now; this file keeps the public path stable.
import re

from trcc.legacy.adapters.system._base import (
    PSUTIL_EXC,
    SUBPROCESS_EXC,
    _ensure_nvml,
)
from trcc.legacy.adapters.system._shared import (
    _confirm,
    _posix_acquire_instance_lock,
    _posix_raise_existing_instance,
    _posix_wire_ipc_raise,
    _print_summary,
)
from trcc.legacy.adapters.system.linux_sensors import SensorEnumerator
from trcc.legacy.core.paths import _TRCC_PKG
from trcc.legacy.core.platform import is_root
from trcc.legacy.core.ports import (
    AutostartManager,
    DoctorPlatformConfig,
    Metrics,
    Platform,
    ReportPlatformConfig,
)

if TYPE_CHECKING:
    from trcc.legacy.core.ports import SensorEnumerator as _SensorEnumeratorABC

# OSError = FileNotFoundError/PermissionError; SubprocessError = TimeoutExpired/CalledProcessError.
_SUBPROCESS_EXC: tuple[type[BaseException], ...] = (
    OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError,
)

# Subprocess tools probed once at LinuxPlatform construction.  Read paths
# consult ``platform._tools`` and skip absent tools without re-probing.
# Order doesn't matter — each tool feeds one or more fallback fields.
_PROBE_TOOLS: tuple[str, ...] = ('sensors', 'dmidecode', 'lshw', 'smartctl')


def _read_sysfs(path: str) -> str | None:
    """Safely read a sysfs/proc file, return stripped content or None."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None

__all__ = [
    'LinuxAutostartManager',
    'LinuxPlatform',
    'SensorEnumerator',
    '_ensure_nvml',
    'get_disk_info',
    'get_memory_info',
    'install_desktop',
    'setup_polkit',
    'setup_rapl_permissions',
    'setup_selinux',
    'setup_udev',
    'sudo_reexec',
]

log = logging.getLogger(__name__)


# =========================================================================
# Private constants
# =========================================================================

_AUTOSTART_DIR = Path.home() / '.config' / 'autostart'
_AUTOSTART_FILE = _AUTOSTART_DIR / 'trcc-linux.desktop'
_LEGACY_AUTOSTART = _AUTOSTART_DIR / 'trcc.desktop'


class LinuxAutostartManager(AutostartManager):
    """XDG autostart — used by tests and BSD platform."""

    def is_enabled(self) -> bool:
        return _AUTOSTART_FILE.exists()

    def enable(self) -> None:
        _AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        _AUTOSTART_FILE.write_text(self._desktop_entry())
        log.info("Autostart enabled: %s", _AUTOSTART_FILE)

    def disable(self) -> None:
        if _AUTOSTART_FILE.exists():
            _AUTOSTART_FILE.unlink()
        log.info("Autostart disabled")

    def refresh(self) -> None:
        if not _AUTOSTART_FILE.exists():
            return
        expected = self._desktop_entry()
        if _AUTOSTART_FILE.read_text() != expected:
            _AUTOSTART_FILE.write_text(expected)
            log.info("Autostart refreshed: %s", _AUTOSTART_FILE)

    def ensure(self) -> bool:
        if _LEGACY_AUTOSTART.exists():
            _LEGACY_AUTOSTART.unlink()
            log.info("Removed legacy autostart file: %s", _LEGACY_AUTOSTART)
        return super().ensure()

    def _desktop_entry(self) -> str:
        exec_path = self.get_exec()
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=TRCC Linux\n"
            "Comment=Thermalright LCD Control Center\n"
            f"Exec={exec_path} gui --resume\n"
            "Icon=trcc\n"
            "Terminal=false\n"
            "Categories=Utility;System;\n"
            "StartupWMClass=trcc-linux\n"
            "X-GNOME-Autostart-enabled=true\n"
        )


_DMI_MEMORY_FIELDS = {
    'manufacturer', 'part_number', 'type', 'speed',
    'configured_memory_speed', 'size', 'locator', 'form_factor',
    'rank', 'data_width', 'total_width', 'configured_voltage',
    'minimum_voltage', 'maximum_voltage', 'memory_technology',
}
_POLKIT_POLICY = '/usr/share/polkit-1/actions/com.github.lexonight1.trcc.policy'


# =========================================================================
# Private helper functions
# =========================================================================

def _real_user_home() -> Path:
    """Return the real (non-root) user's home directory."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        import pwd
        return Path(pwd.getpwnam(sudo_user).pw_dir)
    return Path.home()


def _privileged_cmd(binary: str, args: list[str]) -> list[str]:
    """Build command with pkexec elevation when polkit policy is installed."""
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        return [binary, *args]
    full_path = shutil.which(binary)
    if full_path and os.path.isfile(_POLKIT_POLICY) and shutil.which('pkexec'):
        return ['pkexec', full_path, *args]
    return [binary, *args]


def _autostart_desktop_entry() -> str:
    """Generate XDG .desktop autostart content."""
    exec_path = AutostartManager.get_exec()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=TRCC Linux\n"
        "Comment=Thermalright LCD Control Center\n"
        f"Exec={exec_path} gui --resume\n"
        "Icon=trcc\n"
        "Terminal=false\n"
        "Categories=Utility;System;\n"
        "StartupWMClass=trcc-linux\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _get_smart_health(dev_name: str) -> str | None:
    """Get SMART health status via smartctl."""
    try:
        result = subprocess.run(
            _privileged_cmd('smartctl', ['-H', f'/dev/{dev_name}']),
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if 'overall-health' in line.lower() or 'health status' in line.lower():
                if 'PASSED' in line:
                    return 'PASSED'
                if 'FAILED' in line:
                    return 'FAILED'
    except SUBPROCESS_EXC as e:
        log.debug("smartctl -H /dev/%s returned %s", dev_name, type(e).__name__)
    return None


# =========================================================================
# Module-level setup functions (importable for sudo dispatch)
# =========================================================================

_SUDO_DISPATCH: dict[str, str] = {
    "setup-udev": "from trcc.legacy.adapters.system.linux_platform import setup_udev; exit(setup_udev())",
    "setup-selinux": "from trcc.legacy.adapters.system.linux_platform import setup_selinux; exit(setup_selinux())",
    "setup-polkit": "from trcc.legacy.adapters.system.linux_platform import setup_polkit; exit(setup_polkit())",
}


def sudo_reexec(subcommand: str) -> int:
    """Re-exec a setup function as root via sudo with correct PYTHONPATH."""
    paths: list[str] = []
    paths.extend(site.getsitepackages())
    paths.append(site.getusersitepackages())
    trcc_pkg = str(Path(__file__).resolve().parents[3])
    paths.append(trcc_pkg)
    pythonpath = os.pathsep.join(paths)

    path_inject = f"import sys; sys.path[:0] = {paths!r}; "

    if (snippet := _SUDO_DISPATCH.get(subcommand)):
        cmd = ["sudo", sys.executable, "-c", path_inject + snippet]
    else:
        cmd = [
            "sudo", "env", f"PYTHONPATH={pythonpath}",
            sys.executable, "-m", "trcc.cli", subcommand,
        ]

    print("Root required — requesting sudo...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n  sudo re-exec failed (exit {result.returncode}).")
        print(f"  Try running directly:  sudo trcc {subcommand}")
        print(f"  Or with full path:     sudo {sys.executable} -m trcc.cli {subcommand}")
    return result.returncode


def setup_rapl_permissions() -> None:
    """Make Intel/AMD RAPL energy counters readable by non-root users."""
    rapl_base = Path("/sys/class/powercap")
    if not rapl_base.exists():
        return

    energy_files = sorted(rapl_base.glob("intel-rapl:*/energy_uj"))
    if not energy_files:
        return

    tmpfiles_path = "/etc/tmpfiles.d/trcc-rapl.conf"
    lines = [
        "# Thermalright TRCC — allow non-root CPU power reading (RAPL)",
        "# Auto-generated by trcc setup-udev",
    ]
    for energy_file in energy_files:
        lines.append(f"z {energy_file} 0444 root root -")
    tmpfiles_content = "\n".join(lines) + "\n"

    with open(tmpfiles_path, "w") as f:
        f.write(tmpfiles_content)
    print(f"Wrote {tmpfiles_path}")

    made_readable = 0
    for energy_file in energy_files:
        try:
            energy_file.chmod(0o444)
            made_readable += 1
        except OSError as e:
            print(f"  WARNING: could not chmod {energy_file}: {e}")
    if made_readable == len(energy_files):
        print(f"RAPL power sensors: {made_readable} domain(s) made readable")
    else:
        print(f"RAPL power sensors: {made_readable}/{len(energy_files)} domain(s) made readable"
              f" — {len(energy_files) - made_readable} failed (tmpfiles.d will fix on next boot)")

    restorecon = subprocess.run(
        ["which", "restorecon"], capture_output=True, text=True,
    )
    if restorecon.returncode == 0:
        subprocess.run(
            ["restorecon", tmpfiles_path], capture_output=True, check=False,
        )


def setup_udev(dry_run: bool = False) -> int:
    """Generate and install udev rules + USB storage quirks from KNOWN_DEVICES."""
    from trcc.legacy.core.models import ALL_DEVICES, PROTOCOL_TRAITS, SCSI_DEVICES

    rules_path = "/etc/udev/rules.d/99-trcc-lcd.rules"
    rules_lines = ["# Thermalright LCD/LED cooler devices — auto-generated by trcc setup-udev"]

    for (vid, pid), info in sorted(ALL_DEVICES.items()):
        traits = PROTOCOL_TRAITS.get(info.protocol, PROTOCOL_TRAITS['scsi'])
        rule_parts = [f'# {info.vendor} {info.product}']
        for subsystem in traits.udev_subsystems:
            attr = 'ATTRS' if subsystem in ('hidraw', 'scsi_generic') else 'ATTR'
            rule_parts.append(
                f'SUBSYSTEM=="{subsystem}", '
                f'{attr}{{idVendor}}=="{vid:04x}", '
                f'{attr}{{idProduct}}=="{pid:04x}", '
                f'MODE="0666"'
            )
        # Tune autosuspend, don't disable it.  Pinning autosuspend=-1
        # (v9.2.10's fix for #98 device-resets-every-30s) was the reason
        # the panel never slept on Linux — kernel had no permission to
        # suspend, so on app exit the firmware showed "USB communication
        # lost" until VBUS dropped (#143).
        # 10000ms = 5× our 2s metrics tick → no spurious suspend during
        # normal use; finite so kernel autosuspends ~10s after our
        # process exits — same end state as Windows selective suspend.
        rule_parts.append(
            f'ACTION=="add", SUBSYSTEM=="usb", '
            f'ATTR{{idVendor}}=="{vid:04x}", '
            f'ATTR{{idProduct}}=="{pid:04x}", '
            f'ATTR{{power/autosuspend_delay_ms}}="10000"'
        )
        rules_lines.append('\n'.join(rule_parts))

    rules_content = "\n\n".join(rules_lines) + "\n"

    quirk_entries = [f"{vid:04x}:{pid:04x}:u" for vid, pid in sorted(SCSI_DEVICES)]
    quirks_param = ",".join(quirk_entries)

    modprobe_path = "/etc/modprobe.d/trcc-lcd.conf"
    modprobe_content = (
        "# Thermalright LCD — force usb-storage bulk-only (bypass UAS)\n"
        "# Without this, devices are ignored and /dev/sgX is never created\n"
        "# Auto-generated by trcc setup-udev\n"
        f"options usb-storage quirks={quirks_param}\n"
    )

    if dry_run:
        print("=== udev rules ===")
        print(rules_content)
        print(f"# Would write to {rules_path}\n")
        print("=== usb-storage quirks ===")
        print(modprobe_content)
        print(f"# Would write to {modprobe_path}\n")
        print("=== sg module autoload ===")
        print("sg")
        print("# Would write to /etc/modules-load.d/trcc-sg.conf")
        return 0

    if not is_root():
        return sudo_reexec("setup-udev")

    with open(rules_path, "w") as f:
        f.write(rules_content)
    print(f"Wrote {rules_path}")

    with open(modprobe_path, "w") as f:
        f.write(modprobe_content)
    print(f"Wrote {modprobe_path}")

    quirks_sysfs = "/sys/module/usb_storage/parameters/quirks"
    if os.path.exists(quirks_sysfs):
        with open(quirks_sysfs, "w") as f:
            f.write(quirks_param)
        print(f"Applied quirks: {quirks_param}")

    modules_load_path = "/etc/modules-load.d/trcc-sg.conf"
    modules_load_content = (
        "# Thermalright LCD — ensure SCSI generic (/dev/sgX) is available\n"
        "# Without this, some distros only create /dev/sdX for USB mass storage\n"
        "# Auto-generated by trcc setup-udev\n"
        "sg\n"
    )
    with open(modules_load_path, "w") as f:
        f.write(modules_load_content)
    print(f"Wrote {modules_load_path}")

    subprocess.run(["modprobe", "sg"], check=False, capture_output=True)
    setup_rapl_permissions()
    subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
    subprocess.run(["udevadm", "trigger"], check=False)
    print("\nDone. Unplug and replug the USB cable (or reboot if it's not easily accessible).")
    return 0


def setup_selinux() -> int:
    """Install SELinux policy module allowing USB device access."""
    import tempfile

    if not is_root():
        return sudo_reexec("setup-selinux")

    try:
        r = subprocess.run(["getenforce"], capture_output=True, text=True, timeout=5)
        status = r.stdout.strip().lower()
    except FileNotFoundError:
        print("SELinux not installed — nothing to do.")
        return 0

    if status != 'enforcing':
        print(f"SELinux is {status} — no policy needed.")
        return 0

    try:
        r = subprocess.run(["semodule", "-l"], capture_output=True, text=True, timeout=10)
        if 'trcc_usb' in r.stdout:
            print("SELinux module trcc_usb already loaded.")
            return 0
    except FileNotFoundError:
        print("semodule not found — cannot manage SELinux policies.")
        return 1

    from trcc.legacy.adapters.infra.doctor import _detect_pkg_manager, _install_hint
    pm = _detect_pkg_manager()

    missing: list[str] = []
    for tool in ('checkmodule', 'semodule_package'):
        if not shutil.which(tool):
            missing.append(tool)
    if missing:
        for tool in missing:
            print(f"  {tool} not found — {_install_hint(tool, pm)}")
        return 1

    te_src = os.path.join(_TRCC_PKG, 'data', 'trcc_usb.te')
    if not os.path.isfile(te_src):
        print(f"SELinux policy source not found: {te_src}")
        return 1

    try:
        with tempfile.TemporaryDirectory() as tmp:
            te_path = os.path.join(tmp, 'trcc_usb.te')
            mod_path = os.path.join(tmp, 'trcc_usb.mod')
            pp_path = os.path.join(tmp, 'trcc_usb.pp')
            shutil.copy2(te_src, te_path)

            r = subprocess.run(
                ['checkmodule', '-M', '-m', '-o', mod_path, te_path],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"checkmodule failed: {r.stderr.strip()}")
                return 1

            r = subprocess.run(
                ['semodule_package', '-o', pp_path, '-m', mod_path],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"semodule_package failed: {r.stderr.strip()}")
                return 1

            r = subprocess.run(
                ['semodule', '-i', pp_path],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"semodule install failed: {r.stderr.strip()}")
                return 1

        print("Installed SELinux module trcc_usb (USB device access for TRCC).")
        return 0

    except Exception as e:
        log.exception("setup_selinux failed")
        print(f"Error installing SELinux policy: {e}")
        return 1


def install_desktop() -> int:
    """Install .desktop menu entry and icon for app launchers."""
    home = _real_user_home()
    app_dir = home / ".local" / "share" / "applications"

    pkg_root = Path(_TRCC_PKG)
    icon_pkg_dir = pkg_root / "assets" / "icons"
    desktop_src = pkg_root / "assets" / "trcc-linux.desktop"

    app_dir.mkdir(parents=True, exist_ok=True)
    desktop_dst = app_dir / "trcc-linux.desktop"
    if desktop_src.exists():
        shutil.copy2(desktop_src, desktop_dst)
    else:
        desktop_dst.write_text(
            "[Desktop Entry]\nName=TRCC Linux\n"
            "Comment=Thermalright LCD Control Center\nExec=trcc gui\n"
            "Icon=trcc\nTerminal=false\nType=Application\n"
            "Categories=Utility;System;\n"
            "Keywords=thermalright;lcd;cooler;aio;cpu;\n"
            "StartupWMClass=trcc-linux\n"
        )
    print(f"Installed {desktop_dst}")

    installed_icon = False
    for size in [256, 128, 64, 48, 32, 24, 16]:
        icon_src = icon_pkg_dir / f"trcc_{size}x{size}.png"
        if icon_src.exists():
            icon_dir = home / ".local" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
            icon_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon_src, icon_dir / "trcc.png")
            installed_icon = True

    if installed_icon:
        subprocess.run(
            ["gtk-update-icon-cache", str(home / ".local" / "share" / "icons" / "hicolor")],
            check=False, capture_output=True,
        )
    else:
        print("Warning: icons not found, menu entry will use a generic icon")

    print("\nTRCC should now appear in your application menu.")
    print("If it doesn't show up immediately, log out and back in.")
    return 0


def setup_polkit() -> int:
    """Install polkit policy for passwordless dmidecode/smartctl access."""
    if not is_root():
        return sudo_reexec("setup-polkit")

    pkg_root = Path(__file__).parent.parent.parent
    policy_src = pkg_root / "assets" / "com.github.lexonight1.trcc.policy"

    if not policy_src.exists():
        print(f"Policy file not found: {policy_src}")
        return 1

    policy_text = policy_src.read_text()
    for binary in ('dmidecode', 'smartctl'):
        found = shutil.which(binary)
        if found:
            real_path = os.path.realpath(found)
            policy_text = policy_text.replace(f'/usr/bin/{binary}', real_path)

    policy_dst = Path("/usr/share/polkit-1/actions/com.github.lexonight1.trcc.policy")
    policy_dst.parent.mkdir(parents=True, exist_ok=True)
    policy_dst.write_text(policy_text)

    invoking_user = os.environ.get('SUDO_USER', '')
    if invoking_user:
        rules_dst = Path("/etc/polkit-1/rules.d/50-trcc.rules")
        rules_dst.parent.mkdir(parents=True, exist_ok=True)
        rules_dst.write_text(
            '// TRCC Linux — passwordless dmidecode/smartctl for installing user\n'
            'polkit.addRule(function(action, subject) {\n'
            '    if ((action.id == "com.github.lexonight1.trcc.dmidecode" ||\n'
            '         action.id == "com.github.lexonight1.trcc.smartctl") &&\n'
            f'        subject.user == "{invoking_user}") {{\n'
            '        return polkit.Result.YES;\n'
            '    }\n'
            '});\n'
        )
        print(f"Installed {rules_dst} (user: {invoking_user})")

    restore_paths = [str(policy_dst)]
    if invoking_user:
        restore_paths.append(str(rules_dst))
    if shutil.which('restorecon'):
        subprocess.run(['restorecon', *restore_paths], check=False)
    print(f"Installed {policy_dst}")
    print(f"User '{invoking_user}' can now run dmidecode/smartctl without a password.")
    return 0


def get_memory_info() -> list[dict[str, str]]:
    """Get DRAM slot info via dmidecode. Falls back to psutil for totals."""
    log.debug("get_memory_info: querying dmidecode")
    slots: list[dict[str, str]] = []
    try:
        result = subprocess.run(
            _privileged_cmd('dmidecode', ['-t', 'memory']),
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            current: dict[str, str] = {}
            for line in result.stdout.splitlines():
                line = line.strip()
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
    except SUBPROCESS_EXC as e:
        log.debug("dmidecode -t memory returned %s", type(e).__name__)

    log.debug("get_memory_info: found %d populated slots", len(slots))
    if not slots:
        try:
            mem = psutil.virtual_memory()
            total_gb = f"{mem.total / (1024**3):.1f} GB"
            slots.append({'size': total_gb, 'type': 'Unknown',
                          'speed': 'Unknown', 'manufacturer': 'Unknown'})
        except PSUTIL_EXC as e:
            log.debug("psutil.virtual_memory returned %s", type(e).__name__)
    return slots


def get_disk_info() -> list[dict[str, str]]:
    """Get disk info via lsblk + smartctl."""
    log.debug("get_disk_info: querying lsblk")
    disks: list[dict[str, str]] = []
    try:
        import json as _json
        result = subprocess.run(
            ['lsblk', '-J', '-o', 'NAME,MODEL,SIZE,TYPE,ROTA'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = _json.loads(result.stdout)
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
                if (health := _get_smart_health(dev['name'])):
                    disk['health'] = health
                disks.append(disk)
    except SUBPROCESS_EXC as e:
        log.debug("lsblk -J returned %s", type(e).__name__)
    log.debug("get_disk_info: found %d disks", len(disks))
    return disks


# =========================================================================
# LinuxPlatform — THE one class
# =========================================================================

class LinuxMetrics(Metrics):
    """Linux HardwareMetrics — composes its sources in ``__init__``.

    Receives only what it needs via DI: an :class:`SensorEnumerator`
    (hwmon / psutil / nvidia / computed I/O chain) and a resolved
    ``fallback_cache`` the :class:`LinuxPlatform` populated once at
    first build.  No Platform back-ref; no subprocess work at this layer.
    """

    def __init__(self,
                 enumerator: _SensorEnumeratorABC,
                 fallback_cache: dict[str, float]) -> None:
        super().__init__()
        self._enumerator = enumerator
        # ``__init__`` IS the build — cheap-first ordering.
        self._read_datetime()                       # base ABC helper, instant
        self._read_sensors(enumerator)              # cached enumerator pass
        self._apply_fallback_cache(fallback_cache)  # platform-resolved gaps

    def _apply_fallback_cache(self, cache: dict[str, float]) -> None:
        """Copy cached subprocess-derived values into fields the enumerator missed."""
        from trcc.legacy.core.models.sensor import FLOAT_FIELDS
        m = self.record
        for attr, value in cache.items():
            if attr in m._populated or not hasattr(m, attr):
                continue
            v: float = value if attr in FLOAT_FIELDS else int(value)
            setattr(m, attr, v)
            m._populated.add(attr)


class LinuxPlatform(Platform):
    """Linux Platform — all OS logic inline, no intermediaries.

    Owns the subprocess-tool capability map and the fallback-value cache.
    Probes tool availability once via :func:`shutil.which` at construction.
    Runs each available subprocess exactly once at first :meth:`_make_metrics`
    call; thereafter every Metrics build reads from the resolved cache.
    """

    def __init__(self) -> None:
        super().__init__()
        self._autostart: LinuxAutostartManager | None = None
        # Capability probe — ONE pass at startup.  ``shutil.which`` returns
        # the full tool path or None.  Read paths gate on this; no
        # try-FileNotFoundError-fall-through at runtime.  Announcement
        # happens on first ``_make_metrics`` — logging isn't configured
        # yet at ``PlatformFactory.current()`` time.
        self._tools: dict[str, str | None] = {
            name: shutil.which(name) for name in _PROBE_TOOLS
        }
        # Resolved subprocess-fallback values, populated once at first
        # ``_make_metrics`` call and reused thereafter.  The lock guards
        # the lazy probe — daemon metrics-loop thread + main-thread
        # seed both race to be "first caller".
        self._fallback_cache: dict[str, float] = {}
        self._fallback_probed: bool = False
        self._probe_lock = threading.Lock()

    def _make_metrics(self) -> Metrics:
        """Build a Linux Metrics snapshot.  Subprocess fallbacks run once.

        Double-checked locking — the daemon metrics-loop thread and the
        main-thread boot seed both race to be the first caller.  Without
        the lock both would enter ``_probe_subprocess_fallbacks`` before
        either flips the flag, producing duplicate announcements.
        """
        if not self._fallback_probed:
            with self._probe_lock:
                if not self._fallback_probed:
                    self._probe_subprocess_fallbacks()
                    self._fallback_probed = True
        return LinuxMetrics(self.sensors, self._fallback_cache)

    # ── One-shot subprocess fallback probe ───────────────────────────

    def _probe_subprocess_fallbacks(self) -> None:
        """Run each subprocess fallback once; cache any yields.

        Each ``_read_*`` gates on tool presence and returns ``None``
        cleanly when absent — no exceptions thrown for normal absence,
        no log line emitted for "tool not installed".
        """
        available = sorted(k for k, v in self._tools.items() if v)
        absent = sorted(k for k, v in self._tools.items() if not v)
        log.info(
            "Linux metric sources — available: %s%s",
            ', '.join(available) if available else 'none',
            f"; absent: {', '.join(absent)}" if absent else '',
        )
        cache = self._fallback_cache
        if (v := self._read_lm_sensors_cpu_temp()) is not None:
            cache['cpu_temp'] = v
        if (v := self._read_proc_loadavg()) is not None:
            cache['cpu_percent'] = v
        if (v := self._read_proc_cpuinfo_freq()) is not None:
            cache['cpu_freq'] = v
        if (v := self._read_lm_sensors_mem_temp()) is not None:
            cache['mem_temp'] = v
        if (v := self._read_mem_clock_chain()) is not None:
            cache['mem_clock'] = v
        if (v := self._read_smartctl_disk_temp()) is not None:
            cache['disk_temp'] = v

    # ── Individual fallback reads — tool-gated, deterministic ────────

    def _read_lm_sensors_cpu_temp(self) -> float | None:
        if not (sensors := self._tools['sensors']):
            return None
        try:
            result = subprocess.run(
                [sensors, '-u'], capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split('\n'):
                if 'temp1_input' in line or 'Tctl' in line.lower():
                    if (match := re.search(r':\s*([0-9.]+)', line)):
                        return float(match.group(1))
        except _SUBPROCESS_EXC as e:
            log.debug("sensors -u returned %s", type(e).__name__)
        return None

    @staticmethod
    def _read_proc_loadavg() -> float | None:
        if (loadavg := _read_sysfs('/proc/loadavg')) is None:
            return None
        try:
            load = float(loadavg.split()[0])
            return min(100.0, load * 10)
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _read_proc_cpuinfo_freq() -> float | None:
        try:
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if 'cpu MHz' in line:
                        if (match := re.search(r':\s*([0-9.]+)', line)):
                            return float(match.group(1))
        except (OSError, ValueError):
            return None
        return None

    def _read_lm_sensors_mem_temp(self) -> float | None:
        if not (sensors := self._tools['sensors']):
            return None
        try:
            result = subprocess.run(
                [sensors, '-u'], capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            in_memory_section = False
            for line in result.stdout.split('\n'):
                line_lower = line.lower()
                if any(x in line_lower for x in ['ddr', 'dimm', 'memory']):
                    in_memory_section = True
                elif line and not line.startswith(' ') and ':' not in line:
                    in_memory_section = False
                if in_memory_section and 'temp' in line_lower and '_input' in line_lower:
                    if (match := re.search(r':\s*([0-9.]+)', line)):
                        return float(match.group(1))
        except _SUBPROCESS_EXC as e:
            log.debug("sensors -u returned %s", type(e).__name__)
        return None

    def _read_mem_clock_chain(self) -> float | None:
        """dmidecode → lshw → EDAC chain (each step gated on tool presence)."""
        if dmidecode := self._tools['dmidecode']:
            try:
                result = subprocess.run(
                    [dmidecode, '-t', 'memory'],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    for line in result.stdout.split('\n'):
                        if 'Configured Memory Speed' in line:
                            if (match := re.search(r'(\d+)\s*(?:MT/s|MHz)', line)):
                                return float(match.group(1))
                    for line in result.stdout.split('\n'):
                        if 'Speed:' in line and 'Unknown' not in line:
                            if (match := re.search(r'(\d+)\s*(?:MT/s|MHz)', line)):
                                return float(match.group(1))
            except _SUBPROCESS_EXC as e:
                log.debug("dmidecode -t memory returned %s", type(e).__name__)
        if lshw := self._tools['lshw']:
            try:
                result = subprocess.run(
                    [lshw, '-class', 'memory', '-short'],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    if (match := re.search(r'(\d+)\s*(?:MT/s|MHz)', result.stdout)):
                        return float(match.group(1))
            except _SUBPROCESS_EXC as e:
                log.debug("lshw -class memory returned %s", type(e).__name__)
        mc_path = "/sys/devices/system/edac/mc"
        if os.path.exists(mc_path):
            try:
                for mc in os.listdir(mc_path):
                    if (content := _read_sysfs(f"{mc_path}/{mc}/dimm_info")):
                        if (match := re.search(r'(\d+)\s*MHz', content)):
                            return float(match.group(1))
            except (OSError, ValueError) as e:
                log.debug("EDAC dimm_info returned %s", type(e).__name__)
        return None

    def _read_smartctl_disk_temp(self) -> float | None:
        if not (smartctl := self._tools['smartctl']):
            return None
        try:
            result = subprocess.run(
                [smartctl, '-A', '/dev/sda'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'Temperature' in line or 'Airflow_Temperature' in line:
                        for part in line.split():
                            if part.isdigit() and int(part) < 100:
                                return float(part)
        except _SUBPROCESS_EXC as e:
            log.debug("smartctl -A returned %s", type(e).__name__)
        return None

    def _get_autostart(self) -> LinuxAutostartManager:
        if self._autostart is None:
            self._autostart = LinuxAutostartManager()
        return self._autostart

    # ── Hardware discovery ────────────────────────────────────

    def detect_devices(self):
        from trcc.legacy.adapters.device.detector import DeviceDetector
        from trcc.legacy.adapters.device.linux.detector import linux_scsi_resolver
        return DeviceDetector.make_detect_fn(scsi_resolver=linux_scsi_resolver)()

    def _make_sensor_enumerator(self) -> SensorEnumerator:
        return SensorEnumerator()

    # ── Transport creation ────────────────────────────────────

    def create_scsi_transport(self, path: str,
                              vid: int = 0, pid: int = 0,
                              *, usb_address: UsbAddress | None = None) -> Any:
        # Kernel SG_IO binds by /dev/sgN — usb_address is irrelevant here.
        from trcc.legacy.adapters.device.linux.scsi import LinuxScsiTransport
        return LinuxScsiTransport(path)

    # ── Autostart (XDG .desktop) ──────────────────────────────

    def autostart_enable(self) -> None:
        self._get_autostart().enable()

    def autostart_disable(self) -> None:
        self._get_autostart().disable()

    def autostart_enabled(self) -> bool:
        return self._get_autostart().is_enabled()

    def acquire_instance_lock(self) -> object | None:
        return _posix_acquire_instance_lock(self.config_dir())

    def raise_existing_instance(self) -> None:
        _posix_raise_existing_instance(self.config_dir())

    def _screen_capture_format(self) -> str | None:
        return 'x11grab'

    def wire_ipc_raise(self, app: Any, window: Any) -> None:
        _posix_wire_ipc_raise(app, window)

    def subscribe_power(self, on_suspend, on_resume) -> None:
        """Linux suspend/resume via systemd-logind PrepareForSleep D-Bus signal.

        Connection failures (no D-Bus, no systemd, sandbox without access,
        PySide6 missing) are logged at debug — Linux without logind just
        doesn't get power events, same as before.

        ``QDBusConnection.connect()`` in PySide6 only binds to a Qt slot on
        a QObject — it does NOT accept a Python callable.  The bridge below
        is a tiny QObject whose ``@Slot(bool)`` method forwards into the
        Python callbacks; we keep a reference on ``self`` so the QObject
        isn't garbage-collected while the signal is still wired.  Bug
        shipped in v9.5.9 (passed a bare Python callable, crashed every
        ``trcc`` invocation on Linux).
        """
        try:
            from PySide6.QtCore import (  # pyright: ignore[reportMissingImports]
                SLOT,
                QObject,
                Slot,
            )
            from PySide6.QtDBus import (  # pyright: ignore[reportMissingImports]
                QDBusConnection,
            )
        except ImportError:
            log.debug("subscribe_power: PySide6.QtDBus unavailable")
            return

        bus = QDBusConnection.systemBus()
        if not bus.isConnected():
            log.debug("subscribe_power: system D-Bus not connected")
            return

        class _PrepareForSleepBridge(QObject):
            @Slot(bool)
            def handle(self, sleeping: bool) -> None:
                if sleeping:
                    log.info("System suspending — Trcc.on_suspend")
                    on_suspend()
                else:
                    log.info("System resuming — Trcc.on_resume")
                    on_resume()

        # Keep a reference on self so the QObject (and the wired signal)
        # outlives this method's stack frame.
        self._sleep_listener = _PrepareForSleepBridge()

        # Empirical: PySide6's QDBusConnection.connect signature declares
        # ``slot: bytes | bytearray | memoryview`` but accepts only the
        # ``str`` returned by ``SLOT(...)`` at runtime — the bytes form
        # raises "wrong argument values".  Verified on PySide6 6.10.3.
        slot_str: Any = SLOT('handle(bool)')
        try:
            ok = bus.connect(
                'org.freedesktop.login1',
                '/org/freedesktop/login1',
                'org.freedesktop.login1.Manager',
                'PrepareForSleep',
                'b',
                self._sleep_listener,
                slot_str,
            )
        except Exception as e:
            log.warning("subscribe_power: bus.connect raised: %s", e)
            return

        if ok:
            log.info("subscribe_power: PrepareForSleep listener active")
        else:
            log.debug("subscribe_power: bus.connect returned False")

    # ── Administration ────────────────────────────────────────

    def get_pkg_manager(self) -> str | None:
        from trcc.legacy.adapters.infra.doctor import _detect_pkg_manager
        return _detect_pkg_manager()

    def check_deps(self) -> list:
        from trcc.legacy.adapters.infra.doctor import check_system_deps
        return check_system_deps(self.get_pkg_manager())

    def install_rules(self) -> int:
        return setup_udev()

    def check_permissions(self, devices: list) -> list[str]:
        from trcc.legacy.adapters.device.detector import check_udev_rules
        from trcc.legacy.core.models import PROTOCOL_TRAITS
        warnings = []
        for dev in devices:
            if not check_udev_rules(dev):
                traits = PROTOCOL_TRAITS.get(dev.protocol, PROTOCOL_TRAITS['scsi'])
                msg = (f"Device {dev.vid:04x}:{dev.pid:04x} needs updated udev rules.\n"
                       "Run:  sudo trcc setup-udev")
                if traits.requires_reboot:
                    msg += "\nThen reboot for the USB storage quirk to take effect."
                warnings.append(msg)
                break
        return warnings

    def get_system_files(self) -> list[str]:
        return [
            "/etc/udev/rules.d/99-trcc-lcd.rules",
            "/etc/modprobe.d/trcc-lcd.conf",
            "/etc/modules-load.d/trcc-sg.conf",
            "/usr/share/polkit-1/actions/com.github.lexonight1.trcc.policy",
            "/etc/polkit-1/rules.d/50-trcc.rules",
        ]

    def needs_setup(self) -> bool:
        return not os.path.isfile('/etc/udev/rules.d/99-trcc-lcd.rules')

    def auto_setup(self) -> None:
        print("\n[TRCC] First run — device permissions need to be configured.")
        print("       This requires your password (sudo) to install udev rules.")
        print("       [Y]es — set up now (will prompt for sudo password)")
        print("       [N]o  — skip, run 'trcc setup' later\n")
        if not _confirm("Set up now?", auto_yes=False):
            print("       Skipped. Run 'trcc setup' when ready.\n")
            return
        setup_udev()
        from trcc.legacy.adapters.infra.diagnostics import check_selinux
        se = check_selinux()
        if se.enforcing and not se.ok:
            setup_selinux()
        print("[TRCC] Setup complete.\n")

    # ── Identity ──────────────────────────────────────────────

    def distro_name(self) -> str:
        from trcc.legacy.adapters.infra.doctor import _read_os_release
        return _read_os_release().get('PRETTY_NAME', 'Unknown Linux')

    def no_devices_hint(self) -> str | None:
        return None

    def doctor_config(self) -> DoctorPlatformConfig:
        return DoctorPlatformConfig(
            distro_name=self.distro_name(),
            pkg_manager=self.get_pkg_manager(),
            check_libusb=True,
            extra_binaries=[('sg_raw', True, 'SCSI LCD devices')],
            run_gpu_check=True,
            run_udev_check=True,
            run_selinux_check=True,
            run_rapl_check=True,
            run_polkit_check=True,
            run_winusb_check=False,
            enable_ansi=False,
        )

    def report_config(self) -> ReportPlatformConfig:
        return ReportPlatformConfig(
            distro_name=self.distro_name(),
            collect_lsusb=True,
            collect_udev=True,
            collect_selinux=True,
            collect_rapl=True,
            collect_device_permissions=True,
        )

    # ── Setup operations ──────────────────────────────────────

    def run_setup(self, auto_yes: bool = False) -> int:
        from trcc.legacy.adapters.infra.doctor import (
            check_desktop_entry,
            check_gpu,
            check_polkit,
            check_rapl,
            check_selinux,
            check_system_deps,
            check_udev,
        )

        pm = self.get_pkg_manager()
        print(f"\n  TRCC Setup — {self.distro_name()}\n")
        actions: list[str] = []

        # Step 1/6: System dependencies
        print("  Step 1/6: System dependencies")
        deps = check_system_deps(pm)
        missing_required: list[str] = []
        missing_optional: list[str] = []
        for dep in deps:
            if dep.ok:
                ver = f" {dep.version}" if dep.version else ""
                print(f"    [OK]  {dep.name}{ver}")
            elif dep.required:
                note = f" ({dep.note})" if dep.note else ""
                print(f"    [!!]  {dep.name} — MISSING{note}")
                missing_required.append(dep.install_cmd)
            else:
                note = f" ({dep.note})" if dep.note else ""
                print(f"    [--]  {dep.name} — not installed{note}")
                missing_optional.append(dep.install_cmd)

        import shlex
        import sys as _sys

        def _run_install(cmd: str) -> bool:
            if cmd.startswith('pip install '):
                pkg = cmd[len('pip install '):]
                actual = [_sys.executable, '-m', 'pip', 'install', pkg]
            else:
                actual = shlex.split(cmd)
            print(f"    -> {cmd}")
            return subprocess.run(actual).returncode == 0

        for cmd in missing_required:
            if _confirm(f"Install? -> {cmd}", auto_yes):
                if _run_install(cmd):
                    actions.append(f"Installed: {cmd}")
                else:
                    print("    [!!] Command failed")
        for cmd in missing_optional:
            if _confirm(f"Install? -> {cmd}", auto_yes):
                if _run_install(cmd):
                    actions.append(f"Installed: {cmd}")
        print()

        # Step 2/6: GPU detection
        print("  Step 2/6: GPU detection")
        if not (gpus := check_gpu()):
            print("    [--]  No discrete GPU detected")
        for gpu in gpus:
            if gpu.package_installed:
                print(f"    [OK]  {gpu.label}")
            else:
                print(f"    [--]  {gpu.label} — {gpu.install_cmd}")
                if _confirm(f"Install? -> {gpu.install_cmd}", auto_yes):
                    print(f"    -> {gpu.install_cmd}")
                    result = subprocess.run(
                        [sys.executable, "-m", "pip", "install", *gpu.install_cmd.split()[-1:]],
                    )
                    if result.returncode == 0:
                        actions.append(f"Installed: {gpu.install_cmd}")
                    else:
                        print(f"    [!!] pip failed (exit {result.returncode})")
        print()

        # Step 3/6: USB device permissions (udev + RAPL)
        print("  Step 3/6: USB device permissions")
        udev = check_udev()
        if udev.ok:
            print(f"    [OK]  {udev.message}")
        else:
            print(f"    [!!]  {udev.message}")
            if _confirm("Install udev rules? (requires sudo)", auto_yes):
                rc = setup_udev()
                if rc == 0:
                    actions.append("Installed udev rules")
                else:
                    print("    [!!] udev setup failed")

        rapl = check_rapl()
        if rapl.applicable:
            if rapl.ok:
                print(f"    [OK]  {rapl.message}")
            else:
                print(f"    [--]  {rapl.message}")
                if _confirm("Fix RAPL permissions? (requires sudo)", auto_yes):
                    if not is_root():
                        rc = sudo_reexec("setup-udev")
                    else:
                        setup_rapl_permissions()
                        rc = 0
                    if rc == 0:
                        actions.append("Fixed RAPL power sensor permissions")
        print()

        # Step 4/6: SELinux policy
        se = check_selinux()
        if se.enforcing:
            print("  Step 4/6: SELinux policy")
            if se.ok:
                print(f"    [OK]  {se.message}")
            else:
                print(f"    [!!]  {se.message}")
                if _confirm("Install SELinux USB policy? (requires sudo)", auto_yes):
                    rc = setup_selinux()
                    if rc == 0:
                        actions.append("Installed SELinux policy")
                    else:
                        print("    [!!] SELinux setup failed")
            print()

        # Step 5/6: Polkit policy
        print("  Step 5/6: Hardware info access")
        pk = check_polkit()
        if pk.ok:
            print(f"    [OK]  {pk.message}")
        else:
            print(f"    [--]  {pk.message}")
            if _confirm("Install polkit policy for hardware info? (requires sudo)", auto_yes):
                rc = setup_polkit()
                if rc == 0:
                    actions.append("Installed polkit policy")
                else:
                    print("    [!!] polkit setup failed")
        print()

        # Step 6/6: Desktop integration
        print("  Step 6/6: Desktop integration")
        if check_desktop_entry():
            print("    [OK]  Application menu entry installed")
        else:
            print("    [--]  No application menu entry")
            if _confirm("Install application menu entry?", auto_yes):
                rc = install_desktop()
                if rc == 0:
                    actions.append("Installed desktop entry")
        print()

        _print_summary(actions, "Run 'trcc gui' to launch, or find TRCC in your app menu.")
        return 0

    def install_desktop(self) -> int:
        return install_desktop()

    # ── Help text ─────────────────────────────────────────────

    def archive_tool_install_help(self) -> str:
        from trcc.legacy.adapters.infra.doctor import _install_hint
        pm = self.get_pkg_manager()
        if (hint := _install_hint('7z', pm)):
            return f"7z not found. Install:\n  {hint}"
        return (
            "7z not found. Install p7zip for your distro:\n"
            "  Fedora/RHEL:    sudo dnf install p7zip p7zip-plugins\n"
            "  Ubuntu/Debian:  sudo apt install p7zip-full\n"
            "  Arch:           sudo pacman -S p7zip"
        )

    def ffmpeg_install_help(self) -> str:
        from trcc.legacy.adapters.infra.doctor import _detect_pkg_manager, _install_hint
        pm = _detect_pkg_manager()
        hint = _install_hint('ffmpeg', pm)
        return f"ffmpeg not found. Install:\n  {hint}" if hint else "ffmpeg not found"

    # ── Hardware info ─────────────────────────────────────────

    def get_memory_info(self) -> list[dict[str, str]]:
        return get_memory_info()

    def get_disk_info(self) -> list[dict[str, str]]:
        return get_disk_info()
