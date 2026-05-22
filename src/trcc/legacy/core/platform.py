"""Platform detection — routes to the correct adapter implementations.

Zero imports from adapters. Returns string identifiers that composition roots
use to select the right concrete classes.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)

LINUX = sys.platform.startswith('linux')
WINDOWS = sys.platform == 'win32'
MACOS = sys.platform == 'darwin'
BSD = 'bsd' in sys.platform

# Suppress console window when spawning subprocesses from a GUI app on Windows.
# On Linux/macOS this is 0 (no-op). Pass as creationflags= to subprocess.run().
SUBPROCESS_NO_WINDOW: int = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def platform_name() -> str:
    """Human-readable platform name."""
    if WINDOWS:
        return 'Windows'
    if MACOS:
        return 'macOS'
    if BSD:
        return 'BSD'
    return 'Linux'


def is_root() -> bool:
    """Check if running as root/admin (cross-platform)."""
    if LINUX:
        return os.geteuid() == 0
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0  # type: ignore[attr-defined]
    except Exception:
        return False


def detect_install_method() -> str:
    """Detect how trcc-linux was installed.

    Returns 'pyinstaller', 'pipx', 'pip', 'pacman', 'dnf', or 'apt'.
    """
    if getattr(sys, 'frozen', False):
        log.debug("install method: pyinstaller (frozen)")
        return 'pyinstaller'
    if 'pipx' in sys.prefix:
        log.debug("install method: pipx")
        return 'pipx'
    try:
        from importlib.metadata import distribution
        dist = distribution('trcc-linux')
        installer = (dist.read_text('INSTALLER') or '').strip()
        if installer == 'pip':
            log.debug("install method: pip (INSTALLER metadata)")
            return 'pip'
    except Exception:
        # No INSTALLER metadata — fall through to package-manager probe.
        log.debug("install method: INSTALLER metadata unavailable, "
                  "falling through to PM probe", exc_info=True)
    for mgr in ('pacman', 'dnf', 'apt'):
        if shutil.which(mgr):
            log.debug("install method: %s (package manager detected)", mgr)
            return mgr
    log.debug("install method: pip (fallback)")
    return 'pip'
