"""macOS USB device detection via pyusb + system_profiler.

On macOS, pyusb with libusb backend works the same as Linux for USB
enumeration. Falls back to system_profiler JSON for additional metadata.

Requires: brew install libusb
"""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from trcc.legacy.core.models import DetectedDevice

log = logging.getLogger(__name__)


class MacOSDeviceDetector:
    """Detect Thermalright USB devices on macOS."""

    @staticmethod
    def detect() -> list[DetectedDevice]:
        """Scan for Thermalright USB devices.

        Strategy:
            1. Use pyusb (same as Linux) for VID:PID enumeration
            2. Filter by known device registries
            3. For SCSI devices: no /dev/sgN — pyusb bulk transfers used directly

        Returns:
            List of DetectedDevice, same contract as Linux DeviceDetector.detect()
        """
        devices: list[DetectedDevice] = []

        try:
            from .. import _pyusb_find
        except ImportError:
            log.error("pyusb not installed — pip install pyusb")
            return []

        # Lazy import to share registries with Linux detector
        from trcc.legacy.adapters.device.detector import (
            _BULK_DEVICES,
            _HID_LCD_DEVICES,
            _LED_DEVICES,
            _LY_DEVICES,
            KNOWN_DEVICES,
        )

        all_registries = [
            (KNOWN_DEVICES, 'scsi', 2),
            (_HID_LCD_DEVICES, 'hid', None),
            (_BULK_DEVICES, 'bulk', 4),
            (_LY_DEVICES, 'ly', 10),
            (_LED_DEVICES, 'hid', 0),
        ]

        for registry, protocol, device_type in all_registries:
            for (vid, pid), entry in registry.items():
                # find_all=True so two same-VID/PID coolers both surface (issue #128).
                for usb_dev in (_pyusb_find.find(find_all=True, idVendor=vid, idProduct=pid) or ()):
                    dev: Any = usb_dev
                    impl = 'hid_led' if registry is _LED_DEVICES else entry.implementation
                    dt = device_type if device_type is not None else getattr(entry, 'device_type', 2)
                    usb_path = f'usb:{dev.bus}:{dev.address}'

                    devices.append(DetectedDevice(
                        vid=vid, pid=pid,
                        vendor_name=entry.vendor, product_name=entry.product,
                        usb_path=usb_path,
                        scsi_device=None,  # macOS: no sg device, pyusb direct
                        implementation=impl,
                        model=getattr(entry, 'model', ''),
                        button_image=getattr(entry, 'button_image', ''),
                        protocol=protocol,
                        device_type=dt,
                    ))

        log.info("macOS detector found %d device(s)", len(devices))
        return devices


def get_usb_tree() -> list[dict]:
    """Get USB device tree via system_profiler (for diagnostics).

    Returns parsed JSON from `system_profiler SPUSBDataType -json`.
    """
    try:
        result = subprocess.run(
            ['system_profiler', 'SPUSBDataType', '-json'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get('SPUSBDataType', [])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
        log.debug("system_profiler USB query failed: %s", e)
    return []
