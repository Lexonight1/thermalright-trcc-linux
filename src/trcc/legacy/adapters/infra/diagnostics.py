"""TRCC diagnostics — single module for logging, health checks, and device debug.

``trcc report`` (DebugReport) is the base. Every protocol debug helper
(_debug_scsi, _debug_hid_lcd, _debug_hid_led, _debug_bulk, _debug_ly) is
shared by both the compact report sections and the verbose interactive
commands (device_debug, led_debug_interactive).

Structure
---------
1. Logging       — TrccLoggingConfigurator, StandardLoggingConfigurator
2. Doctor data   — distro/PM maps, install hints, check helpers
3. Result types  — DepResult, GpuResult, UdevResult, SelinuxResult, …
4. Doctor checks — get_module_version, check_*, run_doctor
5. Protocol debug helpers — shared between report and interactive CLI
6. Interactive debug      — device_debug, led_debug_interactive
7. DebugReport            — collect(), __str__(), all section methods

Design note on broad-excepts:  this module is the diagnostic surface.
Almost every ``except Exception`` is intentional Pattern A — a section
that captures any failure and surfaces it to the user-visible report
(via ``print`` or ``sec.lines.append``).  Narrowing here would silently
hide section failures from the user running ``trcc report``.  Sites that
only ``pass`` silently are narrowed + logged in the Phase 11.6 sweep.
"""

from __future__ import annotations

import ctypes.util
import logging
import logging.handlers
import os
import platform
import re
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trcc.legacy.core.models.constants import TRACE_LEVEL

if TYPE_CHECKING:
    from trcc.legacy.core.ports import DoctorPlatformConfig, ReportPlatformConfig

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Logging
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_LOG_FILE = Path.home() / '.trcc' / 'trcc.log'

# TRACE — finer than DEBUG, reserved for per-frame / per-tick hot-path lines
# that would otherwise spam the file. Default file handler stays at DEBUG so
# these are silent; opt-in via ``-vv`` (verbosity ≥ 2).
logging.addLevelName(TRACE_LEVEL, 'TRACE')


class DeviceLogger(logging.Logger):
    """Logger subclass that tags every record with a device identifier.

    Device-owned services create child loggers with a device label
    (e.g. ``logging.getLogger('trcc.services.display.lcd:0')``).
    Setting ``logger.dev = 'lcd:0'`` flows into every log record
    via ``makeRecord``. Format string uses ``%(dev)s``.
    """

    def __init__(self, name: str, level: int = logging.NOTSET) -> None:
        super().__init__(name, level)
        self.dev = '-'

    def makeRecord(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = super().makeRecord(*args, **kwargs)
        record.dev = self.dev  # type: ignore[attr-defined]
        return record


# Register before any getLogger() calls in this module or importers.
logging.setLoggerClass(DeviceLogger)


class _DeviceDefaultFilter(logging.Filter):
    """Ensure ``%(dev)s`` is always present — covers loggers created
    before ``setLoggerClass`` (stdlib, third-party).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, 'dev'):
            record.dev = '-'  # type: ignore[attr-defined]
        return True


class TrccLoggingConfigurator(ABC):
    """Port: TRCC application logging configuration.

    Named TrccLoggingConfigurator to avoid confusion with Python's
    standard logging.config.BaseConfigurator.
    """

    @abstractmethod
    def configure(self, verbosity: int = 0) -> None:
        """Configure root logger with file + console handlers.

        Args:
            verbosity: 0 = WARNING on console, 1 = INFO, 2+ = DEBUG.
                       File handler is always DEBUG regardless.
        """


class StandardLoggingConfigurator(TrccLoggingConfigurator):
    """Configures file + console logging with a consistent format.

    Both handlers share the same format string. %(funcName)s is included
    so every log line names its calling method — refactoring automatically
    updates log output.
    """

    FORMAT = '%(asctime)s [%(levelname)s] [%(dev)s] %(name)s.%(funcName)s: %(message)s'
    DATE_FMT = '%Y-%m-%d %H:%M:%S'
    DATE_FMT_CONSOLE = '%H:%M:%S'

    def __init__(self, log_file: Path = _DEFAULT_LOG_FILE) -> None:
        self._log_file = log_file

    def configure(self, verbosity: int = 0) -> None:
        """Replace all root logger handlers with file + console.

        Verbosity → effective levels:
          - 0:  file DEBUG (no TRACE),     console WARNING
          - 1:  file DEBUG (no TRACE),     console INFO
          - 2+: file TRACE (with frames),  console DEBUG

        Per-frame / per-tick log lines emit at the TRACE level so the
        default file output (verbosity ≤ 1) stays free of frame spam.
        Pass ``-vv`` to enable per-frame diagnostics.
        """
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(TRACE_LEVEL)

        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        dev_filter = _DeviceDefaultFilter()

        # Append, not truncate — preserves history across restarts so a
        # freeze captured "in flight" can be diagnosed after the user
        # exits and relaunches the app. Rotates on size below.
        fh = logging.handlers.RotatingFileHandler(
            self._log_file, mode='a',
            maxBytes=10 * 1024 * 1024,  # 10 MiB
            backupCount=2,              # trcc.log + trcc.log.1 + trcc.log.2
        )
        fh.setLevel(TRACE_LEVEL if verbosity >= 2 else logging.DEBUG)
        fh.setFormatter(logging.Formatter(self.FORMAT, datefmt=self.DATE_FMT))
        fh.addFilter(dev_filter)
        root.addHandler(fh)

        console_level = (
            logging.DEBUG if verbosity >= 2
            else logging.INFO if verbosity == 1
            else logging.WARNING
        )
        ch = logging.StreamHandler()
        ch.setLevel(console_level)
        ch.setFormatter(logging.Formatter(self.FORMAT, datefmt=self.DATE_FMT_CONSOLE))
        ch.addFilter(dev_filter)
        root.addHandler(ch)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Doctor data — distro/PM maps, install hints
# ─────────────────────────────────────────────────────────────────────────────

_DISTRO_TO_PM: dict[str, str] = {
    # dnf
    'fedora': 'dnf', 'rhel': 'dnf', 'centos': 'dnf',
    'rocky': 'dnf', 'alma': 'dnf', 'nobara': 'dnf',
    # apt
    'ubuntu': 'apt', 'debian': 'apt', 'linuxmint': 'apt',
    'pop': 'apt', 'zorin': 'apt', 'elementary': 'apt',
    'neon': 'apt', 'raspbian': 'apt', 'kali': 'apt',
    # pacman
    'arch': 'pacman', 'manjaro': 'pacman', 'endeavouros': 'pacman',
    'cachyos': 'pacman', 'garuda': 'pacman', 'artix': 'pacman',
    'arcolinux': 'pacman', 'steamos': 'pacman',
    # immutable
    'bazzite': 'rpm-ostree',
    # zypper
    'opensuse-tumbleweed': 'zypper', 'opensuse-leap': 'zypper', 'sles': 'zypper',
    # others
    'void': 'xbps',
    'alpine': 'apk', 'postmarketos': 'apk',
    'gentoo': 'emerge', 'funtoo': 'emerge', 'calculate': 'emerge',
    'solus': 'eopkg', 'clear-linux-os': 'swupd',
}

_FAMILY_TO_PM: dict[str, str] = {
    'fedora': 'dnf', 'rhel': 'dnf',
    'debian': 'apt', 'ubuntu': 'apt',
    'arch': 'pacman',
    'suse': 'zypper',
}

_INSTALL_MAP: dict[str, dict[str, str]] = {
    'sg_raw': {
        'dnf': 'sg3_utils', 'apt': 'sg3-utils', 'pacman': 'sg3_utils',
        'zypper': 'sg3_utils', 'xbps': 'sg3_utils', 'apk': 'sg3_utils',
        'emerge': 'sg3_utils', 'eopkg': 'sg3_utils',
        'rpm-ostree': 'sg3_utils', 'swupd': 'devpkg-sg3_utils',
    },
    '7z': {
        'dnf': 'p7zip p7zip-plugins', 'apt': 'p7zip-full', 'pacman': 'p7zip',
        'zypper': 'p7zip-full', 'xbps': 'p7zip', 'apk': '7zip',
        'emerge': 'p7zip',
        'winget': '7zip.7zip', 'brew': 'p7zip', 'pkg': 'p7zip',
    },
    'ffmpeg': {
        'dnf': 'ffmpeg', 'apt': 'ffmpeg', 'pacman': 'ffmpeg',
        'zypper': 'ffmpeg', 'xbps': 'ffmpeg', 'apk': 'ffmpeg',
        'emerge': 'ffmpeg', 'eopkg': 'ffmpeg',
        'winget': 'Gyan.FFmpeg', 'brew': 'ffmpeg', 'pkg': 'ffmpeg',
    },
    'libusb': {
        'dnf': 'libusb1', 'apt': 'libusb-1.0-0', 'pacman': 'libusb',
        'zypper': 'libusb-1_0-0', 'xbps': 'libusb', 'apk': 'libusb',
        'emerge': 'dev-libs/libusb',
        'brew': 'libusb', 'pkg': 'libusb',
    },
    'libxcb-cursor': {
        'apt': 'libxcb-cursor0',
    },
    'checkmodule': {
        'dnf': 'checkpolicy', 'apt': 'checkpolicy', 'pacman': 'checkpolicy',
        'zypper': 'checkpolicy', 'rpm-ostree': 'checkpolicy',
    },
    'semodule_package': {
        'dnf': 'policycoreutils', 'apt': 'semodule-utils',
        'pacman': 'semodule-utils', 'zypper': 'policycoreutils',
        'rpm-ostree': 'policycoreutils',
    },
    'hidapi': {
        'apt': 'python3-hidapi',
        'dnf': 'python3-hid',
        'pacman': 'python-hid',
        'zypper': 'python3-hidapi',
    },
    'nvidia-ml-py': {
        # Distro names lag PyPI — pyproject pins `nvidia-ml-py>=11.0.0` but
        # Debian/Fedora repos still ship the legacy `pynvml`-prefixed alias
        # (same upstream code, exposes the `pynvml` module). Arch ships the
        # official current name. Reflected in packaging/arch/PKGBUILD +
        # release.yml Recommends/optdepend so doctor + packagers agree.
        'apt': 'python3-pynvml',
        'dnf': 'python3-pynvml',
        'pacman': 'python-nvidia-ml-py',
        'zypper': 'python3-nvidia-ml-py',
        'rpm-ostree': 'python3-pynvml',
    },
}

_INSTALL_CMD: dict[str, str] = {
    'dnf': 'sudo dnf install', 'apt': 'sudo apt install',
    'pacman': 'sudo pacman -S', 'zypper': 'sudo zypper install',
    'xbps': 'sudo xbps-install', 'apk': 'sudo apk add',
    'emerge': 'sudo emerge', 'eopkg': 'sudo eopkg install',
    'rpm-ostree': 'sudo rpm-ostree install', 'swupd': 'sudo swupd bundle-add',
    'winget': 'winget install', 'brew': 'brew install',
    'pkg': 'sudo pkg install',
}


def _read_os_release() -> dict[str, str]:
    """Read /etc/os-release into a dict."""
    if (_os_release := getattr(platform, 'freedesktop_os_release', None)) is not None:
        try:
            return _os_release()
        except OSError:
            pass
    result: dict[str, str] = {}
    for path in ('/etc/os-release', '/usr/lib/os-release'):
        if os.path.isfile(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if '=' in line:
                        k, _, v = line.partition('=')
                        result[k] = v.strip('"')
            break
    return result


def _detect_pkg_manager() -> str | None:
    """Detect the Linux package manager from os-release."""
    info = _read_os_release()
    distro_id = info.get('ID', '').lower()
    if pm := _DISTRO_TO_PM.get(distro_id):
        return pm
    for like in info.get('ID_LIKE', '').lower().split():
        if pm := _FAMILY_TO_PM.get(like):
            return pm
    return None


def _re_pkg_name(pattern: str, text: str) -> str | None:
    """Extract package name from first regex group, or None."""
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _parse_dnf(output: str) -> str | None:
    return _re_pkg_name(r'([\w][\w.+-]*)-\d', output.split('\n')[0])


def _parse_pacman(output: str) -> str | None:
    return output.split('\n')[0].split('/')[-1].strip() or None


def _parse_zypper(output: str) -> str | None:
    for line in output.strip().split('\n'):
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 2 and parts[1] and parts[1] != 'Name':
            return parts[1]
    return None


def _parse_xbps(output: str) -> str | None:
    return _re_pkg_name(r'[\]\s]+([\w][\w.+-]*)-\d', output.split('\n')[0])


def _provides_search(dep: str, pm: str) -> str | None:
    """Use PM's native 'provides' to find which package owns a file."""
    match pm:
        case 'dnf':
            cmd = ['dnf', 'provides', '--quiet', f'*/{dep}*']
            parse = _parse_dnf
        case 'pacman':
            cmd = ['pacman', '-Fq', dep]
            parse = _parse_pacman
        case 'zypper':
            cmd = ['zypper', '--non-interactive', 'search', '--provides', dep]
            parse = _parse_zypper
        case 'apk':
            cmd = ['apk', 'search', dep]
            parse = _parse_dnf  # Same format as dnf
        case 'xbps':
            cmd = ['xbps-query', '-Rs', dep]
            parse = _parse_xbps
        case _:
            return None

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return parse(r.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _install_hint(dep: str, pm: str | None) -> str:
    """Build install command string, or generic fallback."""
    if pm and dep in _INSTALL_MAP and pm in _INSTALL_MAP[dep]:
        cmd = _INSTALL_CMD.get(pm, f'sudo {pm} install')
        return f"{cmd} {_INSTALL_MAP[dep][pm]}"
    if pm:
        if (pkg := _provides_search(dep, pm)):
            cmd = _INSTALL_CMD.get(pm, f'sudo {pm} install')
            return f"{cmd} {pkg}"
    if dep in _INSTALL_MAP:
        lines = [f"  {_INSTALL_CMD.get(m, m)} {pkg}"
                 for m, pkg in _INSTALL_MAP[dep].items()]
        return "install one of:\n" + "\n".join(lines)
    return f"install {dep}"


def _enable_ansi_windows() -> None:
    """Enable ANSI escape codes on Windows (virtual terminal processing)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except (OSError, AttributeError):
        # Non-Windows or missing windll — caller falls back to ASCII output.
        pass


_OK = "\033[32m[OK]\033[0m"
_MISS = "\033[31m[MISSING]\033[0m"
_OPT = "\033[33m[--]\033[0m"


def _check_python_module(
    label: str, import_name: str, required: bool, pm: str | None,
) -> bool:
    if (ver := get_module_version(import_name)) is not None:
        ver_str = f" {ver}" if ver else ""
        print(f"  {_OK}  {label}{ver_str}")
        return True
    if required:
        print(f"  {_MISS}  {label} — pip install {label.lower()}")
        return False
    print(f"  {_OPT}  {label} not installed (optional)")
    return True


def _check_binary(
    name: str, required: bool, pm: str | None, note: str = '',
) -> bool:
    if shutil.which(name):
        print(f"  {_OK}  {name}")
        return True
    suffix = f" ({note})" if note else ""
    hint = _install_hint(name, pm)
    if required:
        print(f"  {_MISS}  {name} — {hint}{suffix}")
        return False
    print(f"  {_OPT}  {name} not found — {hint}{suffix}")
    return True


def _check_library(
    label: str, so_name: str, required: bool, pm: str | None,
    dep_key: str = '',
) -> bool:
    if ctypes.util.find_library(so_name):
        print(f"  {_OK}  {label}")
        return True
    hint = _install_hint(dep_key or label, pm)
    if required:
        print(f"  {_MISS}  {label} — {hint}")
        return False
    print(f"  {_OPT}  {label} not found — {hint}")
    return True


def _check_gpu_packages(pm: str | None = None) -> None:
    if not (results := check_gpu(pm)):
        print(f"  {_OPT}  No discrete GPU detected")
        return
    for g in results:
        if g.vendor == 'nvidia' and not g.package_installed:
            print(f"  {_OPT}  NVIDIA GPU detected — {g.install_cmd}"
                  " (enables GPU temp/usage/clock)")
        elif g.vendor == 'nvidia':
            ver = get_module_version('pynvml')
            print(f"  {_OK}  nvidia-ml-py{f' {ver}' if ver else ''} (NVIDIA GPU detected)")
        else:
            print(f"  {_OK}  {g.label}")


def _check_udev_rules() -> bool:
    result = check_udev()
    if not result.ok and result.missing_vids:
        print(f"  {_MISS}  udev rules outdated — missing VID(s): {', '.join(result.missing_vids)}")
        print("         run: trcc setup-udev")
    elif not result.ok:
        print(f"  {_MISS}  udev rules — run: trcc setup-udev")
    else:
        print(f"  {_OK}  udev rules (/etc/udev/rules.d/99-trcc-lcd.rules)")
    return result.ok


def _check_rapl_permissions() -> bool:
    result = check_rapl()
    if not result.applicable:
        return True
    if result.ok:
        print(f"  {_OK}  RAPL power sensors ({result.domain_count} domain(s))")
    else:
        print(f"  {_OPT}  RAPL power sensors not readable — run: trcc setup-udev")
    return result.ok


def _check_winusb_devices() -> None:
    """Check if connected devices need WinUSB driver (Windows only)."""
    try:
        import wmi as wmi_mod  # pyright: ignore[reportMissingImports]
    except ImportError:
        return

    from trcc.legacy.core.models import BULK_DEVICES, HID_LCD_DEVICES, LED_DEVICES, LY_DEVICES
    winusb_registries = {**BULK_DEVICES, **HID_LCD_DEVICES, **LED_DEVICES, **LY_DEVICES}

    try:
        w = wmi_mod.WMI()
        for usb in w.Win32_PnPEntity():
            dev_id = getattr(usb, 'DeviceID', '') or ''
            if 'VID_' not in dev_id:
                continue
            m = re.search(r'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})', dev_id)
            if not m:
                continue
            vid, pid = int(m.group(1), 16), int(m.group(2), 16)
            if (vid, pid) not in winusb_registries:
                continue
            entry = winusb_registries[(vid, pid)]
            status = getattr(usb, 'Status', 'Unknown')
            service = getattr(usb, 'Service', '') or ''
            if service.lower() == 'winusb':
                print(f"  {_OK}  {entry.product} ({vid:04X}:{pid:04X}) — WinUSB")
            else:
                print(f"  {_OPT}  {entry.product} ({vid:04X}:{pid:04X}) — "
                      f"needs WinUSB (status: {status})")
                print("         Install via Zadig: https://zadig.akeo.ie/")
                print("         Or run: trcc setup-winusb")
    except Exception as e:
        # WMI/COM exceptions don't share a Python base; section is best-effort.
        print(f"  WinUSB enumeration failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Structured result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DepResult:
    """Result of a single dependency check."""
    name: str
    ok: bool
    required: bool
    version: str = ''
    note: str = ''
    install_cmd: str = ''


@dataclass
class GpuResult:
    """Result of GPU vendor detection."""
    vendor: str
    label: str
    package_installed: bool
    install_cmd: str = ''


@dataclass
class UdevResult:
    """Result of udev rules check."""
    ok: bool
    message: str
    missing_vids: list[str] = field(default_factory=list)


@dataclass
class SetupInfo:
    """System info for setup wizard header."""
    distro: str
    pkg_manager: str | None
    python_version: str


@dataclass
class SelinuxResult:
    """Result of SELinux policy check."""
    ok: bool
    message: str
    enforcing: bool = False
    module_loaded: bool = False


@dataclass
class RaplResult:
    """Result of RAPL power sensor check."""
    ok: bool
    message: str
    applicable: bool = True
    domain_count: int = 0


@dataclass
class PolkitResult:
    """Result of polkit policy check."""
    ok: bool
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# 4. Doctor checks — public API
# ─────────────────────────────────────────────────────────────────────────────

def get_module_version(import_name: str) -> str | None:
    """Get version string for a Python module, or None if not installed."""
    try:
        mod = __import__(import_name)
        ver = getattr(mod, '__version__', getattr(mod, 'version', ''))
        if isinstance(ver, tuple):
            ver = '.'.join(str(x) for x in ver)
        if not ver and import_name == 'PySide6':
            try:
                import PySide6
                ver = PySide6.__version__
            except ImportError:
                pass
        return str(ver) if ver else ''
    except ImportError:
        return None


def get_setup_info(doctor_config: DoctorPlatformConfig | None = None) -> SetupInfo:
    """Get system info for setup wizard."""
    if doctor_config is None:
        from trcc.legacy.adapters.system import PlatformFactory
        doctor_config = PlatformFactory.current().doctor_config()
    v = sys.version_info
    return SetupInfo(
        distro=doctor_config.distro_name,
        pkg_manager=doctor_config.pkg_manager,
        python_version=f"{v.major}.{v.minor}.{v.micro}",
    )


def check_system_deps(
    pm: str | None = None,
    doctor_config: DoctorPlatformConfig | None = None,
) -> list[DepResult]:
    """Check all dependencies and return structured results."""
    if doctor_config is None:
        from trcc.legacy.adapters.system import PlatformFactory
        doctor_config = PlatformFactory.current().doctor_config()
    if pm is None:
        pm = doctor_config.pkg_manager
    results: list[DepResult] = []

    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    results.append(DepResult(
        name='Python', ok=v >= (3, 9), required=True, version=ver,
        note='' if v >= (3, 9) else 'need >= 3.9',
    ))

    for label, imp in [
        ('PySide6', 'PySide6'), ('numpy', 'numpy'),
        ('psutil', 'psutil'), ('pyusb', 'usb.core'),
    ]:
        mod_ver = get_module_version(imp)
        results.append(DepResult(
            name=label, ok=mod_ver is not None, required=True,
            version=mod_ver or '',
            install_cmd=f'pip install {label.lower()}',
        ))

    hid_ver = get_module_version('hid')
    hid_hint = _install_hint('hidapi', pm)
    results.append(DepResult(
        name='hidapi', ok=hid_ver is not None, required=False,
        version=hid_ver or '',
        install_cmd=hid_hint if not hid_hint.startswith('install ') else 'pip install hidapi',
    ))

    if doctor_config.check_libusb:
        libusb_ok = ctypes.util.find_library('usb-1.0') is not None
        results.append(DepResult(
            name='libusb-1.0', ok=libusb_ok, required=True,
            install_cmd=_install_hint('libusb', pm),
        ))

    _binaries: list[tuple[str, bool, str]] = [
        *doctor_config.extra_binaries,
        ('7z', True, 'theme extraction'),
        ('ffmpeg', False, 'video playback'),
    ]
    for name, required, note in _binaries:
        results.append(DepResult(
            name=name, ok=shutil.which(name) is not None,
            required=required, note=note,
            install_cmd=_install_hint(name, pm),
        ))

    if pm == 'apt':
        xcb_ok = ctypes.util.find_library('xcb-cursor') is not None
        results.append(DepResult(
            name='libxcb-cursor', ok=xcb_ok, required=True,
            note='PySide6 segfaults without it',
            install_cmd=_install_hint('libxcb-cursor', pm),
        ))

    return results


def check_gpu(pm: str | None = None) -> list[GpuResult]:
    """Detect GPU vendors and check matching packages.

    ``pm`` is the active package manager (apt/dnf/pacman/…). When passed,
    the NVIDIA install hint resolves to the right distro package
    (issue #145 — `pip install nvidia-ml-py` fails on PEP 668-managed
    Pythons like Ubuntu 24.04 / Mint 22). None → falls back to pip.
    """
    from pathlib import Path  # local so tests can patch pathlib.Path
    pci_base = Path('/sys/bus/pci/devices')
    if not pci_base.exists():
        return []

    vendors: set[str] = set()
    for dev_dir in pci_base.iterdir():
        class_path = dev_dir / 'class'
        vendor_path = dev_dir / 'vendor'
        if not class_path.exists() or not vendor_path.exists():
            continue
        try:
            pci_class = class_path.read_text().strip()
            if pci_class.startswith(('0x0300', '0x0302')):
                vendors.add(vendor_path.read_text().strip().removeprefix('0x'))
        except OSError:
            continue

    results: list[GpuResult] = []
    if '10de' in vendors:
        ver = get_module_version('pynvml')
        # Per-distro hint resolves to the right native package name —
        # apt → python3-pynvml, pacman → python-nvidia-ml-py, etc.
        # Falls back to pip when no package manager is detected.
        hint = _install_hint('nvidia-ml-py', pm) if pm else 'pip install nvidia-ml-py'
        results.append(GpuResult(
            vendor='nvidia', label='NVIDIA GPU',
            package_installed=ver is not None,
            install_cmd=hint,
        ))
    if '1002' in vendors:
        results.append(GpuResult(
            vendor='amd', label='AMD GPU (sensors via sysfs)',
            package_installed=True,
        ))
    if '8086' in vendors:
        results.append(GpuResult(
            vendor='intel', label='Intel GPU (sensors via sysfs)',
            package_installed=True,
        ))
    return results


def check_udev() -> UdevResult:
    """Check udev rules status (structured return)."""
    path = '/etc/udev/rules.d/99-trcc-lcd.rules'
    if not os.path.isfile(path):
        return UdevResult(ok=False, message='udev rules not installed')
    try:
        with open(path) as f:
            content = f.read()
        from trcc.legacy.adapters.device.detector import DeviceDetector
        all_devices = DeviceDetector._get_all_registries()
        all_vids = {f"{vid:04x}" for vid, _ in all_devices}
        missing = [vid for vid in sorted(all_vids) if vid not in content]
        if missing:
            return UdevResult(
                ok=False,
                message=f'udev rules outdated — missing VID(s): {", ".join(missing)}',
                missing_vids=missing,
            )
        return UdevResult(ok=True, message='udev rules installed')
    except (OSError, ImportError, AttributeError):
        # Defensive: file exists but unreadable / detector import failed.
        # Default to "installed" rather than alarm the user.
        return UdevResult(ok=True, message='udev rules installed')


def check_selinux() -> SelinuxResult:
    """Check if SELinux is enforcing and if USB device access is allowed."""
    try:
        r = subprocess.run(
            ['getenforce'], capture_output=True, text=True, timeout=5,
        )
        status = r.stdout.strip().lower()
    except FileNotFoundError:
        return SelinuxResult(ok=True, message='SELinux not installed')
    except (OSError, subprocess.SubprocessError):
        return SelinuxResult(ok=True, message='SELinux status unknown')

    if status != 'enforcing':
        return SelinuxResult(ok=True, message=f'SELinux {status} (no policy needed)')

    try:
        r = subprocess.run(
            ['semodule', '-l'], capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and 'trcc_usb' in r.stdout:
            return SelinuxResult(
                ok=True, message='SELinux enforcing — trcc_usb module loaded',
                enforcing=True, module_loaded=True,
            )
    except (FileNotFoundError, Exception):
        pass

    if _selinux_usb_access_allowed():
        return SelinuxResult(
            ok=True,
            message='SELinux enforcing — USB access permitted by policy',
            enforcing=True, module_loaded=True,
        )

    return SelinuxResult(
        ok=False, message='SELinux enforcing — USB policy not installed',
        enforcing=True, module_loaded=False,
    )


def _selinux_usb_access_allowed() -> bool:
    """Check if loaded SELinux policy allows USB device access via sesearch."""
    try:
        r = subprocess.run(
            ['sesearch', '--allow', '-s', 'unconfined_t',
             '-t', 'usb_device_t', '-c', 'chr_file'],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return False
        return all(perm in r.stdout for perm in ('ioctl', 'open', 'read', 'write'))
    except (FileNotFoundError, Exception):
        return False


def check_rapl() -> RaplResult:
    """Check if RAPL power sensors are readable by non-root users."""
    from pathlib import Path  # local so tests can patch pathlib.Path
    rapl_base = Path('/sys/class/powercap')
    if not rapl_base.exists():
        return RaplResult(ok=True, message='No powercap subsystem', applicable=False)

    energy_files = sorted(rapl_base.glob('intel-rapl:*/energy_uj'))
    if not energy_files:
        return RaplResult(ok=True, message='No RAPL domains found', applicable=False)

    unreadable = [f for f in energy_files if not os.access(str(f), os.R_OK)]
    if unreadable:
        return RaplResult(
            ok=False,
            message=f'RAPL power sensors not readable ({len(unreadable)} domain(s))',
            domain_count=len(energy_files),
        )
    return RaplResult(
        ok=True,
        message=f'RAPL power sensors readable ({len(energy_files)} domain(s))',
        domain_count=len(energy_files),
    )


def check_polkit() -> PolkitResult:
    """Check if TRCC polkit policy is installed."""
    policy_path = '/usr/share/polkit-1/actions/com.github.lexonight1.trcc.policy'
    if os.path.isfile(policy_path):
        return PolkitResult(ok=True, message='polkit policy installed')
    return PolkitResult(ok=False, message='polkit policy not installed (dmidecode needs sudo)')


def check_desktop_entry() -> bool:
    """Check if .desktop file is installed."""
    return (Path.home() / '.local' / 'share' / 'applications' / 'trcc-linux.desktop').exists()


def run_doctor(doctor_config: DoctorPlatformConfig | None = None) -> int:
    """Run dependency health check. Returns 0 if all required deps pass."""
    if doctor_config is None:
        from trcc.legacy.adapters.system import PlatformFactory
        doctor_config = PlatformFactory.current().doctor_config()

    if doctor_config.enable_ansi:
        _enable_ansi_windows()

    pm = doctor_config.pkg_manager
    all_ok = True

    print(f"\n  TRCC Doctor — {doctor_config.distro_name}\n")

    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 9):
        print(f"  {_OK}  Python {ver}")
    else:
        print(f"  {_MISS}  Python {ver} (need >= 3.9)")
        all_ok = False

    print()
    for label, imp in [
        ('PySide6', 'PySide6'), ('numpy', 'numpy'),
        ('psutil', 'psutil'), ('pyusb', 'usb.core'),
    ]:
        if not _check_python_module(label, imp, required=True, pm=pm):
            all_ok = False

    _check_python_module('hidapi', 'hid', required=False, pm=pm)

    if doctor_config.run_gpu_check:
        print()
        _check_gpu_packages(pm)

    if doctor_config.check_libusb:
        print()
        if not _check_library('libusb-1.0', 'usb-1.0', required=True, pm=pm,
                              dep_key='libusb'):
            all_ok = False
        if pm == 'apt':
            if not _check_library('libxcb-cursor', 'xcb-cursor', required=True, pm=pm,
                                  dep_key='libxcb-cursor'):
                all_ok = False

    print()
    for name, required, note in doctor_config.extra_binaries:
        if not _check_binary(name, required=required, pm=pm, note=note):
            all_ok = False
    if not _check_binary('7z', required=True, pm=pm, note='theme extraction'):
        all_ok = False
    _check_binary('ffmpeg', required=False, pm=pm, note='video playback')

    if doctor_config.run_udev_check:
        print()
        if not _check_udev_rules():
            all_ok = False

    if doctor_config.run_selinux_check:
        se = check_selinux()
        if se.enforcing:
            print()
            if se.ok:
                print(f"  {_OK}  {se.message}")
            else:
                print(f"  {_MISS}  {se.message}")
                print("         run: trcc setup-selinux")
                all_ok = False

    if doctor_config.run_rapl_check:
        print()
        _check_rapl_permissions()

    if doctor_config.run_polkit_check:
        print()
        pk = check_polkit()
        if pk.ok:
            print(f"  {_OK}  {pk.message}")
        else:
            print(f"  {_OPT}  {pk.message}")
            print("         run: trcc setup (or sudo trcc setup-polkit)")

    if doctor_config.run_winusb_check:
        print()
        _check_winusb_devices()

    print()
    if all_ok:
        print("  All required dependencies OK.\n")
        return 0
    print("  Some required dependencies are missing.\n")
    return 1


# ─────────────────────────────────────────────────────────────────────────────
# 5. Protocol debug helpers — shared by DebugReport sections + interactive CLI
# ─────────────────────────────────────────────────────────────────────────────

_KNOWN_VIDS = ("0416", "0418", "87cd", "87ad", "0402")
_UDEV_PATH = "/etc/udev/rules.d/99-trcc-lcd.rules"
_WIDTH = 60


@dataclass
class _Section:
    title: str
    lines: list[str] = field(default_factory=list)


def _hex_dump(data: bytes, max_bytes: int = 64) -> None:
    """Print a hex dump of data to stdout."""
    for row in range(0, min(len(data), max_bytes), 16):
        hex_str = ' '.join(f'{b:02x}' for b in data[row:row + 16])
        ascii_str = ''.join(
            chr(b) if 32 <= b < 127 else '.'
            for b in data[row:row + 16]
        )
        print(f"  {row:04x}: {hex_str:<48s} {ascii_str}")


def _ebusy_fallback(sec: _Section) -> None:
    """Write cached handshake data to sec when device is in use by the GUI."""
    from trcc.legacy.conf import load_last_handshake

    cached = load_last_handshake()
    if cached and cached.get("resolution"):
        res = cached["resolution"]
        pm = cached.get("model_id", "?")
        raw = cached.get("raw", "")
        sec.lines.append(
            f"    PM={pm}, resolution=({res[0]}, {res[1]}), "
            f"serial={cached.get('serial', '')}")
        if raw:
            sec.lines.append(f"    raw[0:64]={raw[:128]}")
        sec.lines.append("    (from cache — device in use by trcc gui)")
    else:
        sec.lines.append("    (device in use by trcc gui)")


def _send_test_frame(protocol: Any, resolution: tuple[int, int], fbl: int) -> None:
    """Send a solid red test frame and print transfer details."""
    from trcc.legacy.core.models import JPEG_MODE_FBLS

    w, h = resolution
    print(f"\n  Sending RED test frame ({w}x{h})...")
    try:
        from trcc.legacy.services.image import ImageService

        img = ImageService.solid_color(255, 0, 0, w, h)
        if fbl in JPEG_MODE_FBLS:
            data = ImageService.to_jpeg(img)
            print(f"    Encoding: JPEG ({len(data):,} bytes)")
        else:
            data = ImageService.to_rgb565(img, '<')
            print(f"    Encoding: RGB565 LE ({len(data):,} bytes)")

        packet = protocol._device.build_frame_packet(data, w, h)
        print(f"    Packet size: {len(packet):,} bytes")
        print(f"    Header: {packet[:20].hex()}")
        ok = protocol._device.send_frame(data)
        print(f"    Send result: {'OK' if ok else 'FAILED'}")
        print("    >>> Check your LCD — did it turn RED?")
    except Exception as e:
        print(f"    Test frame FAILED: {e}")


def _debug_scsi_interactive(dev: Any) -> None:
    """Interactive SCSI handshake diagnostic."""
    from trcc.legacy.adapters.device.factory import DeviceProtocolFactory
    from trcc.legacy.core.models import FBL_TO_RESOLUTION

    protocol = DeviceProtocolFactory.create_protocol(dev)
    try:
        result = protocol.handshake()
        if result is None:
            print("  Handshake returned None (poll failed)")
            return
        fbl = result.model_id
        known = "KNOWN" if fbl in FBL_TO_RESOLUTION else "UNKNOWN"
        res = result.resolution or (0, 0)
        print("  Handshake OK!")
        print(f"  FBL      = {fbl} (0x{fbl:02x})  [{known}]")
        print(f"  Resolution = {res[0]}x{res[1]}")
        print(f"  Path     = {dev.scsi_device}")
        if result.raw_response:
            print("\n  Raw handshake response (first 64 bytes):")
            _hex_dump(result.raw_response)
    finally:
        protocol.close()


def _debug_hid_lcd_interactive(dev: Any, test_frame: bool = False) -> None:
    """Interactive HID LCD handshake diagnostic."""
    from trcc.legacy.adapters.device.hid import HidHandshakeInfo, get_button_image
    from trcc.legacy.adapters.device.hid_protocol import HidProtocol
    from trcc.legacy.core.models import FBL_TO_RESOLUTION, JPEG_MODE_FBLS, fbl_to_resolution, pm_to_fbl

    protocol = HidProtocol(vid=dev.vid, pid=dev.pid, device_type=dev.device_type)
    info = protocol.handshake()

    if info is None:
        error = protocol.last_error
        print(f"  Handshake FAILED: {error}" if error
              else "  Handshake returned None (no response from device)")
        protocol.close()
        return

    assert isinstance(info, HidHandshakeInfo)
    pm = info.mode_byte_1
    sub = info.mode_byte_2
    fbl = info.fbl if info.fbl is not None else pm_to_fbl(pm, sub)
    resolution = info.resolution or fbl_to_resolution(fbl, pm)

    print("  Handshake OK!")
    print(f"  PM byte    = {pm} (0x{pm:02x})")
    print(f"  SUB byte   = {sub} (0x{sub:02x})")
    print(f"  FBL        = {fbl} (0x{fbl:02x})")
    print(f"  Serial     = {info.serial}")
    print(f"  Resolution = {resolution[0]}x{resolution[1]}")
    print(f"  Encoding   = {'JPEG' if fbl in JPEG_MODE_FBLS else 'RGB565'}")

    if (button := get_button_image(dev.vid, dev.pid, pm, sub)):
        print(f"  Button image = {button}")
    else:
        print(f"  Button image = unknown PM={pm} SUB={sub} (defaulting to CZTV)")

    known = "KNOWN" if fbl in FBL_TO_RESOLUTION else "UNKNOWN (not in mapping table)"
    print(f"  FBL {fbl} = {known}")

    if info.raw_response:
        print("\n  Raw handshake response (first 64 bytes):")
        _hex_dump(info.raw_response)

    if test_frame:
        _send_test_frame(protocol, resolution, fbl)

    protocol.close()


def _debug_hid_led_interactive(dev: Any) -> None:
    """Interactive HID LED handshake diagnostic."""
    from trcc.legacy.adapters.device.led import LedHandshakeInfo, PmRegistry
    from trcc.legacy.adapters.device.led_protocol import LedProtocol

    protocol = LedProtocol(vid=dev.vid, pid=dev.pid)
    info = protocol.handshake()

    if info is None:
        error = protocol.last_error
        print(f"  Handshake FAILED: {error}" if error
              else "  Handshake returned None (no response from device)")
        protocol.close()
        return

    assert isinstance(info, LedHandshakeInfo)
    print("  Handshake OK!")
    print(f"  PM byte    = {info.pm} (0x{info.pm:02x})")
    print(f"  Sub-type   = {info.sub_type} (0x{info.sub_type:02x})")
    print(f"  Model      = {info.model_name}")

    if (style := info.style):
        print(f"  Style ID   = {style.style_id}")
        print(f"  LED count  = {style.led_count}")
        print(f"  Segments   = {style.segment_count}")
        print(f"  Zones      = {style.zone_count}")

    if info.pm in PmRegistry:
        print(f"\n  Status: KNOWN device (PM {info.pm} in tables)")
    else:
        print(f"\n  Status: UNKNOWN PM byte ({info.pm})")
        print("  This device falls back to AX120 defaults.")
        print(f"  Please report PM {info.pm} in your GitHub issue.")

    if info.raw_response:
        print("\n  Raw handshake response (first 64 bytes):")
        _hex_dump(info.raw_response)

    protocol.close()


def _debug_bulk_interactive(dev: Any, test_frame: bool = False) -> None:
    """Interactive Bulk protocol handshake diagnostic."""
    from trcc.legacy.adapters.device.bulk_protocol import BulkProtocol

    protocol = BulkProtocol(vid=dev.vid, pid=dev.pid)
    try:
        result = protocol.handshake()
        if result is None:
            error = protocol.last_error
            print(f"  Handshake FAILED: {error}" if error
                  else "  Handshake returned None (no response from device)")
            return
        print("  Handshake OK!")
        print(f"  PM byte    = {result.pm_byte} (0x{result.pm_byte:02x})")
        print(f"  SUB byte   = {result.sub_byte} (0x{result.sub_byte:02x})")
        print(f"  FBL        = {result.model_id} (0x{result.model_id:02x})")
        res = result.resolution or (0, 0)
        print(f"  Resolution = {res[0]}x{res[1]}")
        if result.serial:
            print(f"  Serial     = {result.serial}")
        if result.raw_response:
            print("\n  Raw handshake response (first 64 bytes):")
            _hex_dump(result.raw_response)
        if test_frame and result.resolution:
            _send_test_frame(protocol, result.resolution, result.model_id)
    finally:
        protocol.close()


def _debug_ly_interactive(dev: Any, test_frame: bool = False) -> None:
    """Interactive LY protocol handshake diagnostic."""
    from trcc.legacy.adapters.device.ly_protocol import LyProtocol

    protocol = LyProtocol(vid=dev.vid, pid=dev.pid)
    try:
        result = protocol.handshake()
        if result is None:
            error = protocol.last_error
            print(f"  Handshake FAILED: {error}" if error
                  else "  Handshake returned None (no response from device)")
            return
        print("  Handshake OK!")
        print(f"  PM byte    = {result.pm_byte} (0x{result.pm_byte:02x})")
        print(f"  SUB byte   = {result.sub_byte} (0x{result.sub_byte:02x})")
        print(f"  FBL        = {result.model_id} (0x{result.model_id:02x})")
        res = result.resolution or (0, 0)
        print(f"  Resolution = {res[0]}x{res[1]}")
        if result.serial:
            print(f"  Serial     = {result.serial}")
        if result.raw_response:
            print("\n  Raw handshake response (first 64 bytes):")
            _hex_dump(result.raw_response)
        if test_frame and result.resolution:
            _send_test_frame(protocol, result.resolution, result.model_id)
    finally:
        protocol.close()


# ─────────────────────────────────────────────────────────────────────────────
# 6. Interactive debug commands (CLI entry points)
# ─────────────────────────────────────────────────────────────────────────────

def device_debug(
    detect_fn: Callable[[], list[Any]] | None = None,
    test_frame: bool = False,
) -> int:
    """Interactive handshake diagnostic for all connected devices.

    Routes each device to the appropriate protocol debug function.
    Output is designed to be pasted into GitHub issues.
    """
    try:
        if detect_fn is None:
            from trcc.legacy.core.ports import Platform
            _platform = Platform.for_current_os()
            def _local_detect() -> list[Any]:
                return list(_platform)
            detect_fn = _local_detect

        print("Device Debug — Handshake Diagnostic")
        print("=" * _WIDTH)

        if not (devices := detect_fn()):
            print("\nNo devices found.")
            print("Make sure the device is plugged in and try:")
            print("  trcc setup-udev   (then unplug/replug USB cable)")
            return 0

        for dev in devices:
            print(f"\nDevice: {dev.vendor_name} {dev.product_name}")
            print(f"  VID:PID        = {dev.vid:04x}:{dev.pid:04x}")
            print(f"  Protocol       = {dev.protocol.upper()}")
            print(f"  Implementation = {dev.implementation}")

            print("\n  Attempting handshake...")
            try:
                proto = dev.protocol
                if proto == 'scsi':
                    _debug_scsi_interactive(dev)
                elif proto == 'hid':
                    if dev.implementation == 'hid_led':
                        _debug_hid_led_interactive(dev)
                    else:
                        _debug_hid_lcd_interactive(dev, test_frame=test_frame)
                elif proto == 'bulk':
                    _debug_bulk_interactive(dev, test_frame=test_frame)
                elif proto == 'ly':
                    _debug_ly_interactive(dev, test_frame=test_frame)
                else:
                    print(f"  Unknown protocol: {proto}")
            except ImportError as e:
                # Only suggest a pip install when the missing module is a real
                # third-party USB backend. Anything else (a ``trcc.*`` module
                # failing to load) is an internal bug — print the traceback so
                # the reporter has something to file, not a phantom dep hint.
                if e.name in ('usb', 'usb.core', 'usb.util', 'hid'):
                    print(f"  Missing dependency: {e}")
                    print("  Install: pip install pyusb  (or pip install hidapi)")
                else:
                    print(f"  Internal import error: {e}")
                    import traceback
                    traceback.print_exc()
                    print("  Please report at "
                          "https://github.com/Lexonight1/thermalright-trcc-linux/issues")
            except Exception as e:
                print(f"  Handshake FAILED: {e}")
                import traceback
                traceback.print_exc()

        print(f"\n{'=' * _WIDTH}")
        print("Copy the output above and paste it in your GitHub issue.")
        return 0

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def led_debug_interactive(test_colors: bool = False) -> int:
    """Diagnose LED device — handshake, PM byte discovery, optional test colors."""
    try:
        import time

        from trcc.legacy.adapters.device.led import LED_PID, LED_VID, LedHandshakeInfo, PmRegistry
        from trcc.legacy.adapters.device.led_protocol import LedProtocol

        print("LED Device Diagnostic")
        print("=" * 50)
        print(f"  Target: VID=0x{LED_VID:04x} PID=0x{LED_PID:04x}")

        protocol = LedProtocol(vid=LED_VID, pid=LED_PID)
        info = protocol.handshake()

        if info is None:
            error = protocol.last_error
            print(f"\nHandshake failed: {error or 'no response'}")
            protocol.close()
            return 1

        assert isinstance(info, LedHandshakeInfo)
        print(f"\n  PM byte:    {info.pm}")
        print(f"  Sub-type:   {info.sub_type}")
        print(f"  Model:      {info.model_name}")
        style = info.style
        if style is None:
            print("  Style:      (unknown — handshake returned no style)")
            protocol.close()
            return 1
        print(f"  Style ID:   {style.style_id}")
        print(f"  LED count:  {style.led_count}")
        print(f"  Segments:   {style.segment_count}")
        print(f"  Zones:      {style.zone_count}")

        if info.pm in PmRegistry:
            print(f"\n  Status: KNOWN device (PM {info.pm} in tables)")
        else:
            print(f"\n  Status: UNKNOWN PM byte ({info.pm})")
            print("  This device falls back to AX120 defaults.")
            print(f"  Add PM {info.pm} to led_device.py _PM_REGISTRY.")

        if test_colors:
            print("\n  Sending test colors...")
            led_count = style.led_count
            for name, color in [("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)),
                                 ("BLUE", (0, 0, 255)), ("WHITE", (255, 255, 255))]:
                protocol.send_data([color] * led_count, brightness=100)
                print(f"    {name}")
                time.sleep(1.5)
            protocol.send_data(
                [(0, 0, 0)] * led_count, global_on=False, brightness=0)
            print("    OFF")

        protocol.close()
        print("\nDone.")
        return 0

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


# ─────────────────────────────────────────────────────────────────────────────
# 7. DebugReport — the base, collects all sections for trcc report
# ─────────────────────────────────────────────────────────────────────────────

class DebugReport:
    """Collects and formats system diagnostics for GitHub issues.

    ``report_config`` and ``detect_fn`` are optional — both are resolved
    lazily from ControllerBuilder when not provided, so tests can construct
    DebugReport() with no arguments.
    """

    def __init__(
        self,
        report_config: ReportPlatformConfig | None = None,
        detect_fn: Callable[[], list[Any]] | None = None,
    ) -> None:
        self._sections: list[_Section] = []
        self._detected_devices: list[Any] = []
        self._config = report_config
        self._detect_fn = detect_fn

    def _get_config(self) -> ReportPlatformConfig:
        if self._config is None:
            from trcc.legacy.adapters.system import PlatformFactory
            self._config = PlatformFactory.current().report_config()
        return self._config

    def _get_detect_fn(self) -> Callable[[], list[Any]]:
        if self._detect_fn is None:
            from trcc.legacy.core.ports import Platform
            _platform = Platform.for_current_os()
            def _detect() -> list[Any]:
                return list(_platform)
            self._detect_fn = _detect
        return self._detect_fn

    # ── Public API ───────────────────────────────────────────────────────────

    def collect(self) -> None:
        """Gather all diagnostic sections."""
        config = self._get_config()
        self._version()
        if config.collect_lsusb:
            self._lsusb()
        if config.collect_udev:
            self._udev_rules()
        if config.collect_selinux:
            self._selinux()
        if config.collect_rapl:
            self._rapl_permissions()
        self._dependencies()
        self._devices()
        if config.collect_device_permissions:
            self._device_permissions()
        self._handshakes()
        self._app_config()
        self._installed_themes()
        self._sensor_availability()
        self._last_cpu_baseline()
        self._recent_log()

    @property
    def sections(self) -> list[tuple[str, str]]:
        """Return collected sections as (title, body) pairs."""
        return [(s.title, "\n".join(s.lines)) for s in self._sections]

    def __str__(self) -> str:
        parts: list[str] = []
        for s in self._sections:
            parts.append(f"\n{'─' * _WIDTH}")
            parts.append(s.title)
            parts.append(f"{'─' * _WIDTH}")
            parts.extend(s.lines)
        parts.append(f"\n{'=' * _WIDTH}")
        return "\n".join(parts)

    # ── Section helpers ──────────────────────────────────────────────────────

    def _add(self, title: str) -> _Section:
        sec = _Section(title)
        self._sections.append(sec)
        return sec

    # ── OS + env sections ────────────────────────────────────────────────────

    def _version(self) -> None:
        from trcc.__version__ import __version__

        sec = self._add("Version")
        sec.lines.append(f"  trcc-linux:  {__version__}")
        sec.lines.append(f"  Python:      {platform.python_version()}")
        sec.lines.append(f"  Installed:   {self._install_method()}")
        sec.lines.append(f"  Distro:      {self._get_config().distro_name}")
        sec.lines.append(f"  OS:          {platform.platform()}")
        sec.lines.append(f"  Kernel:      {platform.release()}")

    @staticmethod
    def _install_method() -> str:
        try:
            from trcc.legacy.conf import Settings
            info = Settings.get_install_info()
            if info and info.get('method'):
                return info['method']
        except (OSError, KeyError, AttributeError, ImportError):
            # Settings or config.json unavailable — fall through to detect.
            pass
        try:
            from trcc.legacy.core.platform import detect_install_method
            return detect_install_method()
        except (ImportError, OSError, AttributeError):
            return "unknown"

    def _lsusb(self) -> None:
        sec = self._add("lsusb (filtered)")
        try:
            result = subprocess.run(
                ["lsusb"], capture_output=True, text=True, timeout=5,
            )
            matches = [
                line for line in result.stdout.splitlines()
                if any(vid in line.lower() for vid in _KNOWN_VIDS)
            ]
            if matches:
                for line in matches:
                    sec.lines.append(f"  {line}")
            else:
                sec.lines.append("  (no Thermalright devices found)")
        except Exception as e:
            sec.lines.append(f"  lsusb failed: {e}")

    def _udev_rules(self) -> None:
        sec = self._add(f"udev rules ({_UDEV_PATH})")
        try:
            with open(_UDEV_PATH) as f:
                for line in f:
                    stripped = line.rstrip()
                    if stripped and not stripped.startswith("#"):
                        sec.lines.append(f"  {stripped}")
            if not sec.lines:
                sec.lines.append("  (file exists but no active rules)")
        except FileNotFoundError:
            sec.lines.append("  NOT INSTALLED — run: trcc setup-udev")
        except PermissionError:
            sec.lines.append("  (permission denied reading udev rules)")
        except Exception as e:
            sec.lines.append(f"  Error: {e}")

    def _selinux(self) -> None:
        sec = self._add("SELinux")
        se = check_selinux()
        sec.lines.append(f"  {se.message}")
        if not se.ok:
            sec.lines.append("         run: trcc setup-selinux")

    def _rapl_permissions(self) -> None:
        sec = self._add("RAPL power sensors")
        rapl_base = Path("/sys/class/powercap")
        if not rapl_base.exists():
            sec.lines.append("  not available (no powercap subsystem)")
            return
        energy_files = sorted(rapl_base.glob("intel-rapl:*/energy_uj"))
        if not energy_files:
            sec.lines.append("  not available (no RAPL domains)")
            return
        for f in energy_files:
            domain = f.parent.name
            readable = os.access(str(f), os.R_OK)
            mode = oct(f.stat().st_mode)[-3:]
            status = "readable" if readable else "NO ACCESS"
            sec.lines.append(f"  {domain}: mode={mode}  {status}")

    def _dependencies(self) -> None:
        sec = self._add("Dependencies")
        for import_name, pkg_name in [
            ("PySide6", "PySide6"),
            ("usb.core", "pyusb"),
            ("hid", "hidapi"),
        ]:
            if (ver := get_module_version(import_name)) is not None:
                sec.lines.append(f"  {pkg_name}: {ver or '?'}")
            else:
                sec.lines.append(f"  {pkg_name}: not installed")

    def _devices(self) -> None:
        sec = self._add("Detected devices")
        try:
            devices = self._get_detect_fn()()
            self._detected_devices = devices
            if not devices:
                sec.lines.append("  (none)")
                return
            for i, dev in enumerate(devices, 1):
                proto = dev.protocol.upper()
                sec.lines.append(
                    f"  [{i}] {dev.vid:04x}:{dev.pid:04x}  "
                    f"{dev.product_name}  ({proto})  "
                    f"path={dev.scsi_device or dev.usb_path}"
                )
        except Exception as e:
            sec.lines.append(f"  detect failed: {e}")

    def _device_permissions(self) -> None:
        sec = self._add("Device permissions")
        # User group membership
        try:
            import grp
            user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
            relevant_groups = ("plugdev", "dialout", "disk", "usb")
            for group_name in relevant_groups:
                try:
                    grp_entry = grp.getgrnam(group_name)
                    member = user in grp_entry.gr_mem
                    sec.lines.append(f"  group {group_name}: {'member' if member else 'NOT member'}")
                except KeyError:
                    sec.lines.append(f"  group {group_name}: (does not exist on this system)")
        except Exception as e:
            sec.lines.append(f"  groups: error ({e})")
        # /dev/sg* device access
        sg_found = False
        for entry in sorted(os.listdir("/dev")):
            if entry.startswith("sg"):
                path = f"/dev/{entry}"
                try:
                    mode = oct(os.stat(path).st_mode)[-3:]
                    readable = os.access(path, os.R_OK | os.W_OK)
                    status = "OK" if readable else "NO ACCESS"
                    sec.lines.append(f"  {path}: mode={mode}  {status}")
                    sg_found = True
                except OSError as e:
                    sec.lines.append(f"  {path}: stat failed ({e})")
                    sg_found = True
        if not sg_found:
            sec.lines.append("  /dev/sg*: (none found)")

    # ── Protocol handshake sections ──────────────────────────────────────────

    def _handshakes(self) -> None:
        sec = self._add("Handshakes")
        try:
            scsi_devs = [d for d in self._detected_devices if d.protocol == "scsi"]
            hid_devs  = [d for d in self._detected_devices if d.protocol == "hid"]
            bulk_devs = [d for d in self._detected_devices if d.protocol == "bulk"]
            ly_devs   = [d for d in self._detected_devices if d.protocol == "ly"]
            led_devs  = [d for d in self._detected_devices if d.protocol == "led"]

            if not any([scsi_devs, hid_devs, bulk_devs, ly_devs, led_devs]):
                sec.lines.append("  (no devices to handshake)")
                return

            for dev in scsi_devs:
                sec.lines.append(f"\n  {dev.vid:04x}:{dev.pid:04x} — SCSI")
                try:
                    self._handshake_scsi(dev, sec)
                except Exception as e:
                    sec.lines.append(f"    FAILED: {e}")

            for dev in hid_devs:
                kind = f"HID-LCD (Type {dev.device_type})"
                sec.lines.append(f"\n  {dev.vid:04x}:{dev.pid:04x} — {kind}")
                try:
                    self._handshake_hid_lcd(dev, sec)
                except Exception as e:
                    sec.lines.append(f"    FAILED: {e}")

            for dev in led_devs:
                sec.lines.append(f"\n  {dev.vid:04x}:{dev.pid:04x} — LED")
                try:
                    self._handshake_led(dev, sec)
                except Exception as e:
                    sec.lines.append(f"    FAILED: {e}")

            for dev in bulk_devs:
                sec.lines.append(f"\n  {dev.vid:04x}:{dev.pid:04x} — Bulk")
                try:
                    self._handshake_bulk(dev, sec)
                except Exception as e:
                    sec.lines.append(f"    FAILED: {e}")

            for dev in ly_devs:
                sec.lines.append(f"\n  {dev.vid:04x}:{dev.pid:04x} — LY")
                try:
                    self._handshake_ly(dev, sec)
                except Exception as e:
                    sec.lines.append(f"    FAILED: {e}")

        except Exception as e:
            sec.lines.append(f"  Error: {e}")

    def _handshake_scsi(self, dev: Any, sec: _Section) -> None:
        from trcc.legacy.adapters.device.factory import DeviceProtocolFactory
        from trcc.legacy.core.models import FBL_PROFILES, FBL_TO_RESOLUTION

        protocol = DeviceProtocolFactory.create_protocol(dev)
        try:
            result = protocol.handshake()
            if result is None:
                sec.lines.append("    Result: None (poll failed)")
                return
            fbl = result.model_id
            known = "KNOWN" if fbl in FBL_TO_RESOLUTION else "UNKNOWN"
            res = result.resolution or (0, 0)
            profile = FBL_PROFILES.get(fbl)
            if profile:
                enc = "JPEG" if profile.jpeg else ("RGB565-BE" if profile.big_endian else "RGB565-LE")
                rot = " rotated" if profile.rotate else ""
                sec.lines.append(
                    f"    FBL={fbl} ({known}), resolution={res[0]}x{res[1]}, encoding={enc}{rot}")
            else:
                sec.lines.append(
                    f"    FBL={fbl} ({known}), resolution={res[0]}x{res[1]}")
            if result.raw_response:
                sec.lines.append(f"    raw[0:64]={result.raw_response[:64].hex()}")
        finally:
            protocol.close()

    def _handshake_hid_lcd(self, dev: Any, sec: _Section) -> None:
        from trcc.legacy.adapters.device.factory import (
            _ERRNO_EACCES,
            _ERRNO_EBUSY,
            _has_usb_errno,
        )
        from trcc.legacy.adapters.device.hid import HidHandshakeInfo
        from trcc.legacy.adapters.device.hid_protocol import HidProtocol
        from trcc.legacy.core.models import FBL_PROFILES, fbl_to_resolution, pm_to_fbl

        protocol = HidProtocol(vid=dev.vid, pid=dev.pid, device_type=dev.device_type)
        try:
            info = protocol.handshake()
            if info is None:
                error = protocol.last_error
                if error and _has_usb_errno(error, _ERRNO_EACCES):
                    sec.lines.append("    Permission denied — run 'trcc setup-udev'")
                elif error and _has_usb_errno(error, _ERRNO_EBUSY):
                    self._ebusy_fallback(sec)
                else:
                    sec.lines.append(f"    Result: None ({error or 'no response'})")
                return

            assert isinstance(info, HidHandshakeInfo)
            pm = info.mode_byte_1
            sub = info.mode_byte_2
            fbl = info.fbl if info.fbl is not None else pm_to_fbl(pm, sub)
            resolution = info.resolution or fbl_to_resolution(fbl, pm)
            profile = FBL_PROFILES.get(fbl)
            enc_str = ""
            if profile:
                enc = "JPEG" if profile.jpeg else ("RGB565-BE" if profile.big_endian else "RGB565-LE")
                rot = " rotated" if profile.rotate else ""
                enc_str = f", encoding={enc}{rot}"
            sec.lines.append(
                f"    PM={pm} (0x{pm:02x}), SUB={sub} (0x{sub:02x}), "
                f"FBL={fbl}, resolution={resolution[0]}x{resolution[1]}{enc_str}")
            if info.serial:
                sec.lines.append(f"    serial={info.serial}")
            if info.raw_response:
                sec.lines.append(f"    raw[0:64]={info.raw_response[:64].hex()}")
        finally:
            protocol.close()

    def _handshake_led(self, dev: Any, sec: _Section) -> None:
        from trcc.legacy.adapters.device.factory import (
            _ERRNO_EACCES,
            _ERRNO_EBUSY,
            _has_usb_errno,
        )
        from trcc.legacy.adapters.device.led import LedHandshakeInfo, PmRegistry
        from trcc.legacy.adapters.device.led_protocol import LedProtocol

        protocol = LedProtocol(vid=dev.vid, pid=dev.pid)
        try:
            info = protocol.handshake()
            if info is None:
                error = protocol.last_error
                if error and _has_usb_errno(error, _ERRNO_EACCES):
                    sec.lines.append("    Permission denied — run 'trcc setup-udev'")
                elif error and _has_usb_errno(error, _ERRNO_EBUSY):
                    self._ebusy_fallback(sec)
                else:
                    sec.lines.append(f"    Result: None ({error or 'no response'})")
                return

            assert isinstance(info, LedHandshakeInfo)
            known = "KNOWN" if info.pm in PmRegistry else "UNKNOWN"
            style_info = ""
            if info.style:
                style_info = (f", LEDs={info.style.led_count}, "
                              f"segments={info.style.segment_count}")
            sec.lines.append(
                f"    PM={info.pm} (0x{info.pm:02x}), SUB={info.sub_type}, "
                f"model={info.model_name}, {known}{style_info}")
            if info.raw_response:
                sec.lines.append(f"    raw[0:64]={info.raw_response[:64].hex()}")
        finally:
            protocol.close()

    def _handshake_bulk(self, dev: Any, sec: _Section) -> None:
        from trcc.legacy.adapters.device.bulk_protocol import BulkProtocol
        from trcc.legacy.adapters.device.factory import (
            _ERRNO_EACCES,
            _ERRNO_EBUSY,
            _has_usb_errno,
        )
        from trcc.legacy.core.models import FBL_PROFILES

        protocol = BulkProtocol(vid=dev.vid, pid=dev.pid)
        try:
            result = protocol.handshake()
            if result is None:
                error = protocol.last_error
                if error and _has_usb_errno(error, _ERRNO_EACCES):
                    sec.lines.append("    Permission denied — run 'trcc setup-udev'")
                elif error and _has_usb_errno(error, _ERRNO_EBUSY):
                    self._ebusy_fallback(sec)
                else:
                    sec.lines.append(f"    Result: None ({error or 'no response'})")
                return
            fbl = result.model_id
            profile = FBL_PROFILES.get(fbl)
            enc_str = ""
            if profile:
                enc = "JPEG" if profile.jpeg else ("RGB565-BE" if profile.big_endian else "RGB565-LE")
                rot = " rotated" if profile.rotate else ""
                enc_str = f", encoding={enc}{rot}"
            sec.lines.append(
                f"    PM={result.pm_byte}, SUB={result.sub_byte}, "
                f"FBL={fbl}, resolution={result.resolution}{enc_str}, "
                f"serial={result.serial}")
            if result.raw_response:
                sec.lines.append(f"    raw[0:64]={result.raw_response[:64].hex()}")
        finally:
            protocol.close()

    def _handshake_ly(self, dev: Any, sec: _Section) -> None:
        from trcc.legacy.adapters.device.factory import (
            _ERRNO_EACCES,
            _ERRNO_EBUSY,
            _has_usb_errno,
        )
        from trcc.legacy.adapters.device.ly_protocol import LyProtocol
        from trcc.legacy.core.models import FBL_PROFILES

        protocol = LyProtocol(vid=dev.vid, pid=dev.pid)
        try:
            result = protocol.handshake()
            if result is None:
                error = protocol.last_error
                if error and _has_usb_errno(error, _ERRNO_EACCES):
                    sec.lines.append("    Permission denied — run 'trcc setup-udev'")
                elif error and _has_usb_errno(error, _ERRNO_EBUSY):
                    self._ebusy_fallback(sec)
                else:
                    sec.lines.append(f"    Result: None ({error or 'no response'})")
                return
            fbl = result.model_id
            profile = FBL_PROFILES.get(fbl)
            enc_str = ""
            if profile:
                enc = "JPEG" if profile.jpeg else ("RGB565-BE" if profile.big_endian else "RGB565-LE")
                rot = " rotated" if profile.rotate else ""
                enc_str = f", encoding={enc}{rot}"
            sec.lines.append(
                f"    PM={result.pm_byte}, SUB={result.sub_byte}, "
                f"FBL={fbl}, resolution={result.resolution}{enc_str}, "
                f"serial={result.serial}")
            if result.raw_response:
                sec.lines.append(f"    raw[0:64]={result.raw_response[:64].hex()}")
        finally:
            protocol.close()

    @staticmethod
    def _ebusy_fallback(sec: _Section) -> None:
        """Write cached handshake to sec when device is in use by the GUI."""
        _ebusy_fallback(sec)

    # ── App state sections ───────────────────────────────────────────────────

    def _app_config(self) -> None:
        sec = self._add("Config")
        try:
            from trcc.legacy.conf import CONFIG_PATH, load_config

            app_config = load_config()
            if not app_config:
                sec.lines.append(f"  {CONFIG_PATH}: (empty or missing)")
                return
            sec.lines.append(f"  path: {CONFIG_PATH}")
            # Top-level scalar settings
            for key in ("resolution", "temp_unit", "lang", "selected_device",
                        "show_info_module", "installed_resolutions"):
                if key in app_config:
                    sec.lines.append(f"  {key}: {app_config[key]}")
            if "format_prefs" in app_config:
                fp = app_config["format_prefs"]
                sec.lines.append(f"  format_prefs: {fp}")
            if "install_info" in app_config:
                ii = app_config["install_info"]
                sec.lines.append(f"  install_info: method={ii.get('method','?')}, distro={ii.get('distro','?')}")
            # Per-device settings
            devices = app_config.get("devices", {})
            if devices:
                sec.lines.append(f"  devices ({len(devices)} configured):")
                for dev_key, dev_cfg in devices.items():
                    vid_pid = dev_cfg.get("vid_pid", "?")
                    parts = [f"vid_pid={vid_pid}"]
                    for field in ("brightness_level", "rotation", "split_mode", "theme_name", "theme_type", "fbl"):
                        if field in dev_cfg:
                            parts.append(f"{field}={dev_cfg[field]}")
                    sec.lines.append(f"    [{dev_key}] {', '.join(parts)}")
            else:
                sec.lines.append("  devices: (none configured)")
        except Exception as e:
            sec.lines.append(f"  Error: {e}")

    def _installed_themes(self) -> None:
        sec = self._add("Installed themes")
        try:
            from trcc.legacy.core.models import FBL_PROFILES
            from trcc.legacy.core.paths import USER_DATA_DIR, masks_dir_name, theme_dir_name

            # Collect unique resolutions from FBL_PROFILES
            seen: set[tuple[int, int]] = set()
            resolutions: list[tuple[int, int]] = []
            for p in FBL_PROFILES.values():
                if p.resolution not in seen:
                    seen.add(p.resolution)
                    resolutions.append(p.resolution)
            resolutions.sort()

            found_any = False
            for w, h in resolutions:
                theme_dir = os.path.join(USER_DATA_DIR, theme_dir_name(w, h))
                web_dir = os.path.join(USER_DATA_DIR, "web", masks_dir_name(w, h))
                has_themes = os.path.isdir(theme_dir)
                has_masks = os.path.isdir(web_dir)
                if not has_themes and not has_masks:
                    continue
                found_any = True
                theme_count = 0
                if has_themes:
                    try:
                        theme_count = sum(
                            1 for e in os.listdir(theme_dir)
                            if os.path.isdir(os.path.join(theme_dir, e))
                            and not e.startswith('.')
                        )
                    except OSError:
                        pass
                mask_count = 0
                if has_masks:
                    try:
                        mask_count = sum(
                            1 for e in os.listdir(web_dir)
                            if os.path.isdir(os.path.join(web_dir, e))
                            and not e.startswith('.')
                        )
                    except OSError:
                        pass
                parts = [f"{w}x{h}:"]
                if has_themes:
                    parts.append(f"themes={theme_count}")
                if has_masks:
                    parts.append(f"masks={mask_count}")
                sec.lines.append(f"  {' '.join(parts)}")

            if not found_any:
                sec.lines.append("  (no themes installed — download themes in the GUI)")
        except Exception as e:
            sec.lines.append(f"  Error: {e}")

    def _sensor_availability(self) -> None:
        sec = self._add("Sensor availability")
        try:
            # psutil — CPU%, memory, disk
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=0.05)
                sec.lines.append(f"  psutil: OK (cpu={cpu:.1f}%)")
                # Temperature sensors (Linux hwmon / macOS IOKit)
                if hasattr(psutil, 'sensors_temperatures'):
                    try:
                        if (temps := psutil.sensors_temperatures()):
                            sensors = list(temps.keys())[:4]
                            sec.lines.append(f"  psutil temps: {sensors}")
                        else:
                            sec.lines.append("  psutil temps: none (no hwmon sensors visible)")
                    except Exception as e:
                        sec.lines.append(f"  psutil temps: error ({e})")
                else:
                    sec.lines.append("  psutil temps: not supported on this platform")
            except ImportError:
                sec.lines.append("  psutil: MISSING — install psutil")

            # pynvml — NVIDIA GPU
            try:
                import pynvml  # type: ignore[import]
                pynvml.nvmlInit()
                count = pynvml.nvmlDeviceGetCount()
                sec.lines.append(f"  pynvml (NVIDIA): OK ({count} GPU(s))")
            except ImportError:
                sec.lines.append("  pynvml (NVIDIA): not installed (optional)")
            except Exception as e:
                sec.lines.append(f"  pynvml (NVIDIA): {e}")

            # /sys/class/hwmon (Linux)
            hwmon_path = Path("/sys/class/hwmon")
            if hwmon_path.exists():
                try:
                    hwmon_dirs = list(hwmon_path.iterdir())
                    names = []
                    for d in hwmon_dirs[:8]:
                        name_file = d / "name"
                        if name_file.exists():
                            names.append(name_file.read_text().strip())
                    sec.lines.append(f"  hwmon nodes: {len(hwmon_dirs)} ({', '.join(names[:6])})")
                except Exception as e:
                    sec.lines.append(f"  hwmon: error ({e})")
            else:
                sec.lines.append("  hwmon: not present (non-Linux)")
        except Exception as e:
            sec.lines.append(f"  Error: {e}")

    def _last_cpu_baseline(self) -> None:
        sec = self._add("CPU baseline (last theme cache)")
        log_path = Path.home() / ".trcc" / "trcc.log"
        if not log_path.exists():
            sec.lines.append("  (no log file — load a theme in the GUI first)")
            return
        try:
            lines = log_path.read_text(errors="replace").splitlines()
            for line in reversed(lines):
                if "trcc CPU" in line:
                    sec.lines.append(f"  {line.strip()}")
                    return
            sec.lines.append("  (not found — load a theme in the GUI first)")
        except Exception as e:
            sec.lines.append(f"  Error reading log: {e}")

    def _recent_log(self) -> None:
        sec = self._add("Recent log (last 50 lines)")
        log_path = Path.home() / ".trcc" / "trcc.log"
        if not log_path.exists():
            sec.lines.append("  (no log file)")
            return
        try:
            lines = log_path.read_text(errors="replace").splitlines()
            tail = lines[-50:] if len(lines) > 50 else lines
            if not tail:
                sec.lines.append("  (empty log)")
                return
            for line in tail:
                sec.lines.append(f"  {line}")
        except Exception as e:
            sec.lines.append(f"  Error reading log: {e}")
