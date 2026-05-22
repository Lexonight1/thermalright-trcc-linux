"""Cross-platform USB device detector.

Uses pyusb for enumeration — works on Linux, macOS, and FreeBSD without
any OS-specific code. SCSI path resolution is injected by the builder so
this module has zero OS awareness.

Windows uses WindowsDeviceDetector (WMI-based) and is routed by the builder.

Usage (via builder)
-------------------
    detect_fn = DeviceDetector.make_detect_fn(scsi_resolver)
    devices = detect_fn()
"""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Any

from trcc.legacy.core.models import (
    ALL_DEVICES,
    BULK_DEVICES,
    HID_LCD_DEVICES,
    LED_DEVICES,
    LY_DEVICES,
    SCSI_DEVICES,
    DetectedDevice,
    DeviceEntry,
)

log = logging.getLogger(__name__)

# Backward-compat aliases — importers that used the old private names keep working
KNOWN_DEVICES = SCSI_DEVICES
_HID_LCD_DEVICES = HID_LCD_DEVICES
_LED_DEVICES = LED_DEVICES
_BULK_DEVICES = BULK_DEVICES
_LY_DEVICES = LY_DEVICES
KNOWN_LED_DEVICES = LED_DEVICES
KNOWN_BULK_DEVICES = BULK_DEVICES

# Legacy flag — kept for backward compat, no longer checked.
_hid_testing_enabled = False


def enable_hid_testing() -> None:
    """No-op — HID devices are now auto-detected."""
    global _hid_testing_enabled
    _hid_testing_enabled = True


ScsiResolver = Callable[[int, int], str | None]

# Protocol comes from DeviceEntry.protocol — models.py is the single source of truth.


class DeviceDetector:
    """Cross-platform USB device detector — no OS knowledge.

    Builder injects the platform-specific SCSI resolver via make_detect_fn().
    """

    @staticmethod
    def make_detect_fn(
        scsi_resolver: ScsiResolver | None = None,
    ) -> Callable[[], list[DetectedDevice]]:
        """Return a detect() callable with the given SCSI resolver bound.

        Args:
            scsi_resolver: (vid, pid) -> /dev/sgN or /dev/passN or None.
                None means SCSI devices have no path (macOS uses pyusb direct).
        """
        def detect() -> list[DetectedDevice]:
            return DeviceDetector._detect(scsi_resolver)
        return detect

    @staticmethod
    def detect() -> list[DetectedDevice]:
        """Linux default — resolves SCSI paths via sysfs."""
        from trcc.legacy.adapters.device.linux.detector import linux_scsi_resolver
        return DeviceDetector._detect(linux_scsi_resolver)

    @staticmethod
    def _detect(scsi_resolver: ScsiResolver | None) -> list[DetectedDevice]:
        """Core detection via pyusb with injected SCSI resolver."""
        try:
            from . import _pyusb_find
        except ImportError:
            log.error("pyusb not installed — pip install pyusb")
            return []

        devices: list[DetectedDevice] = []
        for (vid, pid), entry in ALL_DEVICES.items():
            # find_all=True yields every match; `or ()` handles None when
            # no devices are present, so two same-VID/PID coolers each
            # produce their own DetectedDevice (issue #128).
            for usb_dev in (_pyusb_find.find(find_all=True, idVendor=vid, idProduct=pid) or ()):
                dev: Any = usb_dev
                usb_path = f'usb:{dev.bus}:{dev.address}'
                scsi_dev = (
                    scsi_resolver(vid, pid)
                    if scsi_resolver and entry.protocol == 'scsi'
                    else None
                )

                devices.append(DetectedDevice(
                    vid=vid, pid=pid,
                    vendor_name=entry.vendor, product_name=entry.product,
                    usb_path=usb_path,
                    scsi_device=scsi_dev,
                    implementation=entry.implementation,
                    model=entry.model,
                    button_image=entry.button_image,
                    protocol=entry.protocol,
                    device_type=entry.device_type,
                ))

        log.info(
            "Detected %d device(s): %s", len(devices),
            ", ".join(f"{d.vendor_name} {d.product_name} [{d.protocol}]"
                      for d in devices) or "none",
        )
        return devices

    # ------------------------------------------------------------------
    # Convenience / utility (kept for backward compat)
    # ------------------------------------------------------------------

    @staticmethod
    def _get_all_registries() -> dict[tuple[int, int], DeviceEntry]:
        return dict(ALL_DEVICES)

    @staticmethod
    def run_command(cmd: list[str]) -> str:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    @staticmethod
    def check_udev_rules(device: DetectedDevice) -> bool:
        vid_hex = f"{device.vid:04x}"
        try:
            with open("/etc/udev/rules.d/99-trcc-lcd.rules") as f:
                return vid_hex in f.read()
        except OSError:
            return False

    @staticmethod
    def get_default() -> DetectedDevice | None:
        if not (devices := DeviceDetector.detect()):
            return None
        for device in devices:
            if device.vid == 0x87CD:
                return device
        return devices[0]

    @staticmethod
    def get_device_path() -> str | None:
        device = DeviceDetector.get_default()
        return device.scsi_device if device else None

    @staticmethod
    def print_info(device: DetectedDevice) -> None:
        print(f"Device: {device.vendor_name} {device.product_name}")
        print(f"  USB VID:PID: {device.vid:04X}:{device.pid:04X}")
        print(f"  USB Path: {device.usb_path}")
        print(f"  Protocol: {device.protocol.upper()} (type {device.device_type})")
        if device.protocol == "scsi":
            print(f"  SCSI Device: {device.scsi_device or 'Not found'}")
        print(f"  Model: {device.model}")
        print(f"  Button Image: {device.button_image}")
        print(f"  Implementation: {device.implementation}")


# Aliases used by cli/, __init__.py, and tests.
_get_all_devices = DeviceDetector._get_all_registries
check_udev_rules = DeviceDetector.check_udev_rules
get_device_path = DeviceDetector.get_device_path


def get_default_device() -> DetectedDevice | None:
    return DeviceDetector.get_default()
