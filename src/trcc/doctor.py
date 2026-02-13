"""Dependency health check for TRCC Linux.

Usage: trcc doctor
"""

from __future__ import annotations

import ctypes.util
import os
import platform
import shutil
import sys

# ── Distro → package manager mapping ────────────────────────────────────────

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
    'cachyos': 'pacman', 'garuda': 'pacman',
    # others
    'opensuse-tumbleweed': 'zypper', 'opensuse-leap': 'zypper', 'sles': 'zypper',
    'void': 'xbps', 'alpine': 'apk', 'gentoo': 'emerge',
}

# Fallback: ID_LIKE family → package manager
_FAMILY_TO_PM: dict[str, str] = {
    'fedora': 'dnf', 'rhel': 'dnf',
    'debian': 'apt', 'ubuntu': 'apt',
    'arch': 'pacman',
    'suse': 'zypper',
}

# ── Package names per package manager ────────────────────────────────────────

_INSTALL_MAP: dict[str, dict[str, str]] = {
    'sg_raw': {
        'dnf': 'sg3_utils', 'apt': 'sg3-utils', 'pacman': 'sg3_utils',
        'zypper': 'sg3_utils', 'xbps': 'sg3_utils', 'apk': 'sg3_utils',
        'emerge': 'sg3_utils',
    },
    '7z': {
        'dnf': 'p7zip p7zip-plugins', 'apt': 'p7zip-full', 'pacman': 'p7zip',
        'zypper': 'p7zip-full', 'xbps': 'p7zip', 'apk': '7zip',
        'emerge': 'p7zip',
    },
    'ffmpeg': {
        'dnf': 'ffmpeg', 'apt': 'ffmpeg', 'pacman': 'ffmpeg',
        'zypper': 'ffmpeg', 'xbps': 'ffmpeg', 'apk': 'ffmpeg',
        'emerge': 'ffmpeg',
    },
    'libusb': {
        'dnf': 'libusb1', 'apt': 'libusb-1.0-0', 'pacman': 'libusb',
        'zypper': 'libusb-1_0-0', 'xbps': 'libusb', 'apk': 'libusb',
        'emerge': 'dev-libs/libusb',
    },
}

# sudo prefix per package manager
_INSTALL_CMD: dict[str, str] = {
    'dnf': 'sudo dnf install', 'apt': 'sudo apt install',
    'pacman': 'sudo pacman -S', 'zypper': 'sudo zypper install',
    'xbps': 'sudo xbps-install', 'apk': 'sudo apk add',
    'emerge': 'sudo emerge',
}


# ── Distro detection ────────────────────────────────────────────────────────

def _read_os_release() -> dict[str, str]:
    """Read /etc/os-release into a dict."""
    # Python 3.10+ API
    _os_release = getattr(platform, 'freedesktop_os_release', None)
    if _os_release is not None:
        try:
            return _os_release()
        except OSError:
            pass
    # Fallback for Python < 3.10 or missing file
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
    """Detect the system package manager from os-release."""
    info = _read_os_release()
    distro_id = info.get('ID', '').lower()

    # Exact match
    if pm := _DISTRO_TO_PM.get(distro_id):
        return pm

    # ID_LIKE fallback (space-separated list of parent distros)
    for like in info.get('ID_LIKE', '').lower().split():
        if pm := _FAMILY_TO_PM.get(like):
            return pm

    return None


def _install_hint(dep: str, pm: str | None) -> str:
    """Build 'sudo dnf install pkg' string, or generic fallback."""
    if pm and dep in _INSTALL_MAP and pm in _INSTALL_MAP[dep]:
        cmd = _INSTALL_CMD.get(pm, f'sudo {pm} install')
        return f"{cmd} {_INSTALL_MAP[dep][pm]}"
    if dep in _INSTALL_MAP:
        # Show all distros as fallback
        lines = [f"  {_INSTALL_CMD.get(m, m)} {pkg}"
                 for m, pkg in _INSTALL_MAP[dep].items()]
        return "install one of:\n" + "\n".join(lines)
    return f"install {dep}"


# ── Check helpers ────────────────────────────────────────────────────────────

def get_module_version(import_name: str) -> str | None:
    """Get version string for a Python module, or None if not installed.

    Handles PyQt6 (version in QtCore), hidapi (tuple version), and
    standard __version__ / version attributes.
    """
    try:
        mod = __import__(import_name)
        ver = getattr(mod, '__version__', getattr(mod, 'version', ''))
        if isinstance(ver, tuple):
            ver = '.'.join(str(x) for x in ver)
        # PyQt6 stores version in PyQt6.QtCore.PYQT_VERSION_STR
        if not ver and import_name == 'PyQt6':
            try:
                from PyQt6.QtCore import PYQT_VERSION_STR
                ver = PYQT_VERSION_STR
            except ImportError:
                pass
        return str(ver) if ver else ''
    except ImportError:
        return None


_OK = "\033[32m[OK]\033[0m"
_MISS = "\033[31m[MISSING]\033[0m"
_OPT = "\033[33m[--]\033[0m"


def _check_python_module(
    label: str, import_name: str, required: bool, pm: str | None,
) -> bool:
    """Try importing a Python module. Print status. Return True if OK."""
    ver = get_module_version(import_name)
    if ver is not None:
        ver_str = f" {ver}" if ver else ""
        print(f"  {_OK}  {label}{ver_str}")
        return True
    if required:
        print(f"  {_MISS}  {label} — pip install {label.lower()}")
        return False
    print(f"  {_OPT}  {label} not installed (optional)")
    return True  # optional — not a failure


def _check_binary(
    name: str, required: bool, pm: str | None, note: str = '',
) -> bool:
    """Check if a CLI binary is on PATH. Return True if OK."""
    if shutil.which(name):
        print(f"  {_OK}  {name}")
        return True
    suffix = f" ({note})" if note else ""
    hint = _install_hint(name, pm)
    if required:
        print(f"  {_MISS}  {name} — {hint}{suffix}")
        return False
    print(f"  {_OPT}  {name} not found — {hint}{suffix}")
    return True  # optional


def _check_library(
    label: str, so_name: str, required: bool, pm: str | None,
    dep_key: str = '',
) -> bool:
    """Check if a shared library is loadable. Return True if OK."""
    if ctypes.util.find_library(so_name):
        print(f"  {_OK}  {label}")
        return True
    hint = _install_hint(dep_key or label, pm)
    if required:
        print(f"  {_MISS}  {label} — {hint}")
        return False
    print(f"  {_OPT}  {label} not found — {hint}")
    return True


def _check_udev_rules() -> bool:
    """Check if TRCC udev rules are installed."""
    path = '/etc/udev/rules.d/99-trcc-lcd.rules'
    if os.path.isfile(path):
        print(f"  {_OK}  udev rules ({path})")
        return True
    print(f"  {_MISS}  udev rules — run: trcc setup-udev")
    return False


# ── Main entry point ─────────────────────────────────────────────────────────

def run_doctor() -> int:
    """Run dependency health check. Returns 0 if all required deps pass."""
    pm = _detect_pkg_manager()
    distro = _read_os_release().get('PRETTY_NAME', 'Unknown')
    all_ok = True

    print(f"\n  TRCC Doctor — {distro}\n")

    # Python version
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 9):
        print(f"  {_OK}  Python {ver}")
    else:
        print(f"  {_MISS}  Python {ver} (need >= 3.9)")
        all_ok = False

    # Python modules (required)
    print()
    for label, imp in [
        ('PyQt6', 'PyQt6'),
        ('Pillow', 'PIL'),
        ('numpy', 'numpy'),
        ('psutil', 'psutil'),
        ('pyusb', 'usb.core'),
    ]:
        if not _check_python_module(label, imp, required=True, pm=pm):
            all_ok = False

    # Python modules (optional)
    _check_python_module('hidapi', 'hid', required=False, pm=pm)

    # System libraries
    print()
    if not _check_library('libusb-1.0', 'usb-1.0', required=True, pm=pm,
                          dep_key='libusb'):
        all_ok = False

    # System binaries
    print()
    if not _check_binary('sg_raw', required=True, pm=pm,
                         note='SCSI LCD devices'):
        all_ok = False
    if not _check_binary('7z', required=True, pm=pm,
                         note='theme extraction'):
        all_ok = False
    _check_binary('ffmpeg', required=False, pm=pm, note='video playback')

    # udev rules
    print()
    if not _check_udev_rules():
        all_ok = False

    # Summary
    print()
    if all_ok:
        print("  All required dependencies OK.\n")
        return 0
    print("  Some required dependencies are missing.\n")
    return 1
