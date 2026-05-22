"""FreeBSD USB device detection utilities.

Device detection on FreeBSD uses the cross-platform DeviceDetector
(pyusb-based) via BSDPlatform.detect_devices(). SCSI devices are
addressed by VID:PID through pyusb — no /dev/pass* mapping needed.

This module provides diagnostic utilities only.

Requires: pkg install py-pyusb (libusb is in base FreeBSD)
"""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)


def get_usb_list() -> list[str]:
    """Get USB device list via usbconfig (for diagnostics).

    Returns raw output lines from `usbconfig list`.
    """
    try:
        result = subprocess.run(
            ['usbconfig', 'list'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("usbconfig list failed: %s", e)
    return []
