"""GPU vendor detection + conditional install of matching Python extras.

Scans PCI for display-controller-class devices to figure out which GPU
vendor(s) are present, then `pip install`s the Python libs that only
make sense for those vendors.

Only NVIDIA has a pip-installable sensor lib (`nvidia-ml-py`).  AMD and
Intel GPUs read through kernel sysfs on Linux / WMI on Windows — no
extra install needed.  macOS Apple Silicon uses IOKit via ctypes.

Call `install_matching_gpu_extras()` from `Platform.setup()` — not on
every app launch.  Detection is read-only and always safe.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)


# PCI class 0x03 = display controller (covers VGA, 3D, other display)
_PCI_DISPLAY_CLASS = 0x03

_VENDOR_IDS = {
    0x10DE: "nvidia",
    0x1002: "amd",
    0x8086: "intel",
    0x106B: "apple",
}


def detect_gpu_vendors() -> set[str]:
    """Scan PCI sysfs for display controllers.  Returns {'nvidia', 'amd', 'intel', 'apple'} subset.

    Linux-only detection path via `/sys/bus/pci/devices`.  Returns an
    empty set on any OS without that path (macOS, Windows — those use
    their own detection via the platform's native API).
    """
    pci_base = Path("/sys/bus/pci/devices")
    if not pci_base.exists():
        return set()

    found: set[str] = set()
    for dev in pci_base.iterdir():
        try:
            klass_raw = (dev / "class").read_text().strip()
            class_hi = (int(klass_raw, 16) >> 16) & 0xFF
            if class_hi != _PCI_DISPLAY_CLASS:
                continue
            vendor_raw = (dev / "vendor").read_text().strip()
            vendor = int(vendor_raw, 16)
        except (OSError, ValueError):
            continue
        name = _VENDOR_IDS.get(vendor)
        if name is not None:
            found.add(name)
    log.debug("PCI scan found GPU vendors: %s", sorted(found) or "none")
    return found


# Map vendor → pip requirement spec.  Empty = no install needed.
_VENDOR_EXTRAS = {
    "nvidia": "nvidia-ml-py>=11.0.0",
    # "amd", "intel", "apple" — no pip install, sensors via OS-native paths
}


def install_matching_gpu_extras(vendors: set[str],
                                dry_run: bool = False) -> int:
    """pip-install the Python libs matching detected GPU vendors.

    Returns shell-style exit code (0 = success or nothing to do).
    Pass `dry_run=True` to log what would be installed without doing it.
    """
    needed = [spec for name, spec in _VENDOR_EXTRAS.items() if name in vendors]
    if not needed:
        log.info("No GPU-specific Python libs required for: %s", sorted(vendors))
        return 0
    cmd = [sys.executable, "-m", "pip", "install", "--user", *needed]
    log.info("Installing GPU sensor support: %s", needed)
    if dry_run:
        log.info("(dry-run) would run: %s", " ".join(cmd))
        return 0
    try:
        result = subprocess.run(cmd, check=False)
    except (OSError, subprocess.SubprocessError):
        log.exception("pip install failed")
        return 1
    return result.returncode
