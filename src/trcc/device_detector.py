#!/usr/bin/env python3
"""
USB LCD/LED Device Detector
Finds Thermalright LCD and LED devices and maps them to SCSI or HID devices.

Supported devices (SCSI — stable):
- Thermalright: VID=0x87CD, PID=0x70DB
- Winbond:      VID=0x0416, PID=0x5406
- ALi Corp:     VID=0x0402, PID=0x3922

Supported devices (HID LCD — auto-detected when plugged in):
- Winbond:      VID=0x0416, PID=0x5302  (Type 2)
- ALi Corp:     VID=0x0418, PID=0x5303  (Type 3)
- ALi Corp:     VID=0x0418, PID=0x5304  (Type 3)

Supported devices (HID LED — RGB controllers, auto-detected when plugged in):
- Winbond:      VID=0x0416, PID=0x8001  (64-byte reports)

Supported devices (Raw USB bulk — bInterfaceClass=255, Vendor Specific):
- ChiZhu Tech:  VID=0x87AD, PID=0x70DB  (GrandVision/Mjolnir Vision, USBLCDNew protocol)
"""

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from .data_repository import SysUtils

log = logging.getLogger(__name__)


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class DeviceEntry:
    """Registry entry describing a known USB device's capabilities."""
    vendor: str
    product: str
    implementation: str
    model: str = "CZTV"
    button_image: str = "A1CZTV"
    protocol: str = "scsi"
    device_type: int = 1  # 1=SCSI, 2=HID Type 2, 3=HID Type 3, 4=Raw USB Bulk


@dataclass
class DetectedDevice:
    """Detected USB/SCSI device"""
    vid: int  # Vendor ID
    pid: int  # Product ID
    vendor_name: str
    product_name: str
    usb_path: str  # e.g., "2-1.4"
    scsi_device: Optional[str] = None  # e.g., "/dev/sg0"
    implementation: str = "generic"  # Device-specific implementation
    model: str = "CZTV"  # Device model for button image lookup
    button_image: str = "A1CZTV"  # Button image prefix (without .png)
    protocol: str = "scsi"  # "scsi" or "hid"
    device_type: int = 1  # 1=SCSI, 2=HID Type 2 ("H"), 3=HID Type 3 ("ALi")


# =========================================================================
# Device registries
# =========================================================================

# Known LCD devices (SCSI/USB Mass Storage)
KNOWN_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x87CD, 0x70DB): DeviceEntry(
        vendor="Thermalright", product="LCD Display",
        implementation="thermalright_lcd_v1",
    ),
    # NOTE: 87AD:70DB (GrandVision) moved to _BULK_DEVICES — it's raw USB bulk, not SCSI.
    (0x0416, 0x5406): DeviceEntry(
        vendor="Winbond", product="LCD Display",
        implementation="ali_corp_lcd_v1",
    ),
    # USB 0402:3922 - FROZEN WARFRAME series (SE/PRO/Ultra, confirmed on PRO 360 Black)
    (0x0402, 0x3922): DeviceEntry(
        vendor="ALi Corp", product="FROZEN WARFRAME",
        model="FROZEN_WARFRAME", button_image="A1FROZEN_WARFRAME",
        implementation="ali_corp_lcd_v1",
    ),
}

# HID LCD devices — auto-detected when plugged in.
_HID_LCD_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x0416, 0x5302): DeviceEntry(
        vendor="Winbond", product="USBDISPLAY",
        implementation="hid_type2", protocol="hid", device_type=2,
    ),
    (0x0418, 0x5303): DeviceEntry(
        vendor="ALi Corp", product="LCD Display",
        implementation="hid_type3", protocol="hid", device_type=3,
    ),
    (0x0418, 0x5304): DeviceEntry(
        vendor="ALi Corp", product="LCD Display",
        implementation="hid_type3", protocol="hid", device_type=3,
    ),
}

# LED HID devices (RGB controllers)
_LED_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x0416, 0x8001): DeviceEntry(
        vendor="Winbond", product="LED Controller",
        model="LED_DIGITAL", implementation="hid_led",
        protocol="hid", device_type=1,
    ),
}

# Raw USB bulk devices (bInterfaceClass=255, Vendor Specific)
_BULK_DEVICES: dict[tuple[int, int], DeviceEntry] = {
    (0x87AD, 0x70DB): DeviceEntry(
        vendor="ChiZhu Tech", product="GrandVision 360 AIO",
        model="GRAND_VISION", implementation="bulk_usblcdnew",
        protocol="bulk", device_type=4,
    ),
}

# Backward-compat aliases
KNOWN_LED_DEVICES = _LED_DEVICES
KNOWN_BULK_DEVICES = _BULK_DEVICES

# Legacy flag — kept for backward compat but no longer checked.
_hid_testing_enabled = False


def enable_hid_testing():
    """No-op, kept for backward compatibility. HID devices are now auto-detected."""
    global _hid_testing_enabled
    _hid_testing_enabled = True


# =========================================================================
# DeviceDetector — all detection and utility logic
# =========================================================================

class DeviceDetector:
    """USB LCD/LED device detection and management."""

    @staticmethod
    def _get_all_registries() -> dict[tuple[int, int], DeviceEntry]:
        """Return combined device lookup dict (SCSI + HID LCD + LED + Bulk)."""
        all_devices = dict(KNOWN_DEVICES)
        all_devices.update(_HID_LCD_DEVICES)
        all_devices.update(_LED_DEVICES)
        all_devices.update(_BULK_DEVICES)
        return all_devices

    @staticmethod
    def run_command(cmd: List[str]) -> str:
        """Run command and return output."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    # ------------------------------------------------------------------
    # USB scanning
    # ------------------------------------------------------------------

    @staticmethod
    def find_usb_devices() -> List[DetectedDevice]:
        """Find all USB LCD devices using lsusb."""
        devices = []
        log.debug("Scanning USB devices via lsusb...")
        output = DeviceDetector.run_command(['lsusb'])

        if not output:
            log.debug("lsusb returned no output")
            return devices

        pattern = r'Bus (\d+) Device (\d+): ID ([0-9a-f]{4}):([0-9a-f]{4})\s+(.*)'

        for line in output.split('\n'):
            match = re.search(pattern, line, re.IGNORECASE)
            if not match:
                continue

            bus, device, vid_str, pid_str, _description = match.groups()
            vid = int(vid_str, 16)
            pid = int(pid_str, 16)

            all_devices = DeviceDetector._get_all_registries()
            if (vid, pid) not in all_devices:
                continue

            device_info = all_devices[(vid, pid)]
            usb_path = f"{int(bus)}-{device}"

            log.debug("Found known device: %04X:%04X %s (%s)",
                      vid, pid, device_info.vendor, device_info.protocol)
            devices.append(DetectedDevice(
                vid=vid, pid=pid,
                vendor_name=device_info.vendor,
                product_name=device_info.product,
                usb_path=usb_path,
                implementation=device_info.implementation,
                model=device_info.model,
                button_image=device_info.button_image,
                protocol=device_info.protocol,
                device_type=device_info.device_type,
            ))

        log.debug("USB scan found %d known device(s)", len(devices))
        return devices

    # ------------------------------------------------------------------
    # SCSI mapping
    # ------------------------------------------------------------------

    @staticmethod
    def find_scsi_device_by_usb_path(usb_path: str) -> Optional[str]:
        """Find SCSI device corresponding to USB path."""
        # Method 1: Scan sysfs directly for sg devices with USBLCD vendor
        for sg_name in SysUtils.find_scsi_devices():
            sysfs_base = f"/sys/class/scsi_generic/{sg_name}/device"
            if not os.path.exists(sysfs_base):
                continue
            try:
                with open(f"{sysfs_base}/vendor", 'r') as f:
                    vendor = f.read().strip()
                if 'USBLCD' in vendor:
                    return f"/dev/{sg_name}"
            except (IOError, OSError):
                continue

        # Method 2: Use lsscsi -g
        output = DeviceDetector.run_command(['lsscsi', '-g'])
        if output:
            for line in output.split('\n'):
                if 'USBLCD' in line:
                    match = re.search(r'/dev/sg\d+', line)
                    if match:
                        return match.group(0)

        # Method 3: Use lsscsi -t
        output = DeviceDetector.run_command(['lsscsi', '-t'])
        if output:
            for line in output.split('\n'):
                if 'USBLCD' in line or 'usb' in line.lower():
                    match = re.search(r'/dev/sg\d+', line)
                    if match:
                        return match.group(0)

        # Method 4: Plain lsscsi
        output = DeviceDetector.run_command(['lsscsi'])
        if output:
            for line in output.split('\n'):
                if 'USBLCD' in line:
                    match = re.search(r'/dev/sg\d+', line)
                    if match:
                        return match.group(0)

        return None

    @staticmethod
    def find_scsi_usblcd_devices() -> List[DetectedDevice]:
        """Find USBLCD devices directly via sysfs (pure Python, no external commands)."""
        devices = []

        for sg_name in SysUtils.find_scsi_devices():
            sg_path = f"/dev/{sg_name}"
            sysfs_base = f"/sys/class/scsi_generic/{sg_name}/device"

            if not os.path.exists(sysfs_base):
                continue

            try:
                with open(f"{sysfs_base}/vendor", 'r') as f:
                    vendor = f.read().strip()
                with open(f"{sysfs_base}/model", 'r') as f:
                    model = f.read().strip()

                if 'USBLCD' in vendor:
                    dev_vid = 0x87CD  # Fallback
                    dev_pid = 0x70DB
                    dev_model = "CZTV"
                    dev_button = "A1CZTV"

                    try:
                        device_path = os.path.realpath(sysfs_base)
                        for _ in range(10):
                            device_path = os.path.dirname(device_path)
                            vid_path = os.path.join(device_path, "idVendor")
                            pid_path = os.path.join(device_path, "idProduct")
                            if os.path.exists(vid_path) and os.path.exists(pid_path):
                                with open(vid_path) as vf:
                                    dev_vid = int(vf.read().strip(), 16)
                                with open(pid_path) as pf:
                                    dev_pid = int(pf.read().strip(), 16)
                                if (dev_vid, dev_pid) in KNOWN_DEVICES:
                                    dev_info = KNOWN_DEVICES[(dev_vid, dev_pid)]
                                    dev_model = dev_info.model
                                    dev_button = dev_info.button_image
                                break
                    except (IOError, OSError, ValueError):
                        pass

                    devices.append(DetectedDevice(
                        vid=dev_vid, pid=dev_pid,
                        vendor_name="Thermalright",
                        product_name=f"LCD Display ({model})",
                        usb_path="unknown",
                        scsi_device=sg_path,
                        implementation="thermalright_lcd_v1",
                        model=dev_model,
                        button_image=dev_button,
                    ))
            except (IOError, OSError):
                continue

        return devices

    # ------------------------------------------------------------------
    # Main detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect() -> List[DetectedDevice]:
        """Detect all USB LCD devices and their SCSI mappings."""
        log.debug("Starting device detection...")
        devices = DeviceDetector.find_usb_devices()

        for device in devices:
            scsi_dev = DeviceDetector.find_scsi_device_by_usb_path(device.usb_path)
            device.scsi_device = scsi_dev
            if scsi_dev:
                log.debug("Mapped %04X:%04X → %s", device.vid, device.pid, scsi_dev)

        # If we found USB devices but none have SCSI mappings, try sysfs fallback
        if devices and not any(d.scsi_device for d in devices):
            log.debug("No SCSI mappings found, trying sysfs fallback...")
            scsi_devices = DeviceDetector.find_scsi_usblcd_devices()
            if scsi_devices and scsi_devices[0].scsi_device:
                devices[0].scsi_device = scsi_devices[0].scsi_device

        # Fallback: scan SCSI devices directly for USBLCD if no USB devices found
        if not devices:
            log.debug("No USB devices found, scanning SCSI directly...")
            devices = DeviceDetector.find_scsi_usblcd_devices()

        log.info(
            "Detected %d device(s): %s", len(devices),
            ", ".join(f"{d.vendor_name} {d.product_name} [{d.protocol}]"
                      for d in devices) or "none",
        )
        return devices

    # ------------------------------------------------------------------
    # Convenience / utility
    # ------------------------------------------------------------------

    @staticmethod
    def check_udev_rules(device: DetectedDevice) -> bool:
        """Check if udev rules file contains the VID:PID for *device*."""
        vid_hex = f"{device.vid:04x}"
        try:
            with open("/etc/udev/rules.d/99-trcc-lcd.rules") as f:
                return vid_hex in f.read()
        except (IOError, OSError):
            return False

    @staticmethod
    def get_default() -> Optional[DetectedDevice]:
        """Get the first available LCD device."""
        devices = DeviceDetector.detect()
        if not devices:
            return None
        for device in devices:
            if device.vid == 0x87CD:
                return device
        return devices[0]

    @staticmethod
    def get_device_path() -> Optional[str]:
        """Get SCSI device path for LCD (convenience function)."""
        device = DeviceDetector.get_default()
        return device.scsi_device if device else None

    @staticmethod
    def usb_reset(usb_path: str) -> bool:
        """Soft reset USB device by unbinding/rebinding (simulates unplug/replug)."""
        try:
            import time

            busnum_path = f"/sys/bus/usb/devices/{usb_path}/busnum"
            devnum_path = f"/sys/bus/usb/devices/{usb_path}/devnum"

            if not os.path.exists(busnum_path):
                return False

            with open(busnum_path) as f:
                _bus = f.read().strip()
            with open(devnum_path) as f:
                _dev = f.read().strip()

            # Method 1: Try authorized=0/1 (safest)
            auth_path = f"/sys/bus/usb/devices/{usb_path}/authorized"
            if os.path.exists(auth_path):
                try:
                    with open(auth_path, 'w') as f:
                        f.write('0')
                    time.sleep(0.5)
                    with open(auth_path, 'w') as f:
                        f.write('1')
                    time.sleep(1)
                    log.info("USB device %s reset successfully", usb_path)
                    return True
                except PermissionError:
                    log.warning("Permission denied for USB reset (need root)")

            # Method 2: Try unbind/bind (requires root)
            driver_path = f"/sys/bus/usb/devices/{usb_path}/driver"
            if os.path.exists(driver_path):
                try:
                    unbind_path = os.path.join(os.readlink(driver_path), 'unbind')
                    bind_path = os.path.join(os.readlink(driver_path), 'bind')

                    with open(unbind_path, 'w') as f:
                        f.write(usb_path)
                    time.sleep(0.5)
                    with open(bind_path, 'w') as f:
                        f.write(usb_path)
                    time.sleep(1)
                    log.info("USB device %s reset via unbind/bind", usb_path)
                    return True
                except Exception as e:
                    log.warning("Failed to reset via unbind/bind: %s", e)

            return False
        except Exception as e:
            log.warning("USB reset failed: %s", e)
            return False

    @staticmethod
    def check_health(device_path: str) -> bool:
        """Check if device is responding properly (not in bad binary mode)."""
        try:
            result = subprocess.run(
                ['sg_inq', device_path],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode != 0:
                return False

            output_lower = result.stdout.lower() + result.stderr.lower()
            bad_states = [
                'error', 'failed', 'not ready', 'medium not present',
                'i/o error', 'device not responding',
            ]
            return not any(state in output_lower for state in bad_states)
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            return False

    @staticmethod
    def print_info(device: DetectedDevice) -> None:
        """Pretty print device information."""
        print(f"Device: {device.vendor_name} {device.product_name}")
        print(f"  USB VID:PID: {device.vid:04X}:{device.pid:04X}")
        print(f"  USB Path: {device.usb_path}")
        print(f"  Protocol: {device.protocol.upper()} (type {device.device_type})")
        if device.protocol == "scsi":
            print(f"  SCSI Device: {device.scsi_device or 'Not found'}")
        print(f"  Model: {device.model}")
        print(f"  Button Image: {device.button_image}")
        print(f"  Implementation: {device.implementation}")


# =========================================================================
# CLI entry point
# =========================================================================

def main():
    """CLI interface"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Detect Thermalright LCD USB devices'
    )
    parser.add_argument(
        '--path-only', action='store_true',
        help='Only output device path (for scripts)',
    )
    parser.add_argument(
        '--all', action='store_true',
        help='Show all detected devices',
    )
    args = parser.parse_args()

    if args.all:
        devices = DeviceDetector.detect()
        if not devices:
            print("No LCD devices found")
            return 1

        print(f"Found {len(devices)} device(s):\n")
        for i, device in enumerate(devices, 1):
            print(f"Device {i}:")
            DeviceDetector.print_info(device)
            print()
        return 0

    device = DeviceDetector.get_default()

    if not device:
        if not args.path_only:
            print("No LCD device found")
        return 1

    if args.path_only:
        if device.scsi_device:
            print(device.scsi_device)
            return 0
        else:
            return 1

    DeviceDetector.print_info(device)
    return 0


# =========================================================================
# Backward-compat aliases
# =========================================================================

_get_all_devices = DeviceDetector._get_all_registries
run_command = DeviceDetector.run_command
find_usb_devices = DeviceDetector.find_usb_devices
find_scsi_device_by_usb_path = DeviceDetector.find_scsi_device_by_usb_path
find_scsi_usblcd_devices = DeviceDetector.find_scsi_usblcd_devices
detect_devices = DeviceDetector.detect
check_udev_rules = DeviceDetector.check_udev_rules
get_default_device = DeviceDetector.get_default
get_device_path = DeviceDetector.get_device_path
usb_reset_device = DeviceDetector.usb_reset
check_device_health = DeviceDetector.check_health
print_device_info = DeviceDetector.print_info


if __name__ == '__main__':
    import sys
    sys.exit(main())
