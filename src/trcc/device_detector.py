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
"""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from .paths import find_scsi_devices as _find_sg_entries


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


# Known LCD devices (SCSI/USB Mass Storage)
# These appear as SCSI Generic devices (/dev/sgX) on Linux
# NOTE: On Linux we can't access HID pm/sub bytes to identify exact model,
# so we map VID:PID directly to the most common device for that hardware.
# Users can override model via config if needed.
KNOWN_DEVICES = {
    (0x87CD, 0x70DB): {
        "vendor": "Thermalright",
        "product": "LCD Display (USBLCD)",
        "model": "CZTV",
        "button_image": "A1CZTV",
        "implementation": "thermalright_lcd_v1"
    },
    (0x0416, 0x5406): {
        "vendor": "Winbond",
        "product": "LCD Display (USBLCD)",
        "model": "CZTV",
        "button_image": "A1CZTV",
        "implementation": "ali_corp_lcd_v1"
    },
    # USB 0402:3922 - FROZEN WARFRAME series (SE/PRO/Ultra, confirmed on PRO 360 Black)
    (0x0402, 0x3922): {
        "vendor": "ALi Corp",
        "product": "FROZEN WARFRAME",
        "model": "FROZEN_WARFRAME",
        "button_image": "A1FROZEN_WARFRAME",
        "implementation": "ali_corp_lcd_v1"
    },
}

# HID LCD devices — auto-detected when plugged in.
# From UCDevice.cs (TRCC 2.0.3 decompiled — decimal PIDs confirmed).
_HID_LCD_DEVICES = {
    # device2: UsbHidDevice(1046, 21250) = 0x0416:0x5302, DA/DB/DC/DD handshake, 512-byte chunks
    (0x0416, 0x5302): {
        "vendor": "Winbond",
        "product": "USBDISPLAY (HID)",
        "model": "CZTV",
        "button_image": "A1CZTV",
        "implementation": "hid_type2",
        "protocol": "hid",
        "device_type": 2,
    },
    # device3: UsbHidDevice(1048, 21251) = 0x0418:0x5303, 64-byte packets
    (0x0418, 0x5303): {
        "vendor": "ALi Corp",
        "product": "LCD Display (HID)",
        "model": "CZTV",
        "button_image": "A1CZTV",
        "implementation": "hid_type3",
        "protocol": "hid",
        "device_type": 3,
    },
    # device4: UsbHidDevice(1048, 21252) = 0x0418:0x5304
    (0x0418, 0x5304): {
        "vendor": "ALi Corp",
        "product": "LCD Display (HID)",
        "model": "CZTV",
        "button_image": "A1CZTV",
        "implementation": "hid_type3",
        "protocol": "hid",
        "device_type": 3,
    },
}

# LED HID devices (RGB controllers — FormLED in Windows, not FormCZTV)
# These use 64-byte HID reports for LED color control, not LCD image display.
# device1: UsbHidDevice(1046, 32769) = 0x0416:0x8001, 64-byte packets
_LED_DEVICES = {
    (0x0416, 0x8001): {
        "vendor": "Winbond",
        "product": "LED Controller (HID)",
        "model": "AX120_DIGITAL",
        "button_image": "A1AX120_DIGITAL",
        "implementation": "hid_led",
        "protocol": "hid",
        "device_type": 1,
    },
}

# Backward-compat alias (tests and setup-udev reference this)
KNOWN_LED_DEVICES = _LED_DEVICES

# Legacy flag — kept for backward compat but no longer checked.
# HID devices are now auto-detected when plugged in.
_hid_testing_enabled = False


def enable_hid_testing():
    """No-op, kept for backward compatibility. HID devices are now auto-detected."""
    global _hid_testing_enabled
    _hid_testing_enabled = True


def _get_all_devices():
    """Return device lookup dict (SCSI + HID LCD + LED)."""
    all_devices = dict(KNOWN_DEVICES)
    all_devices.update(_HID_LCD_DEVICES)
    all_devices.update(_LED_DEVICES)
    return all_devices


def run_command(cmd: List[str]) -> str:
    """Run command and return output"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def find_usb_devices() -> List[DetectedDevice]:
    """Find all USB LCD devices using lsusb"""
    devices = []
    output = run_command(['lsusb'])

    if not output:
        return devices

    # Parse lsusb output
    # Format: Bus 002 Device 003: ID 87cd:70db Thermalright LCD Display
    pattern = r'Bus (\d+) Device (\d+): ID ([0-9a-f]{4}):([0-9a-f]{4})\s+(.*)'

    for line in output.split('\n'):
        match = re.search(pattern, line, re.IGNORECASE)
        if not match:
            continue

        bus, device, vid_str, pid_str, description = match.groups()
        vid = int(vid_str, 16)
        pid = int(pid_str, 16)

        # Check if this is a known LCD or LED device
        all_devices = _get_all_devices()
        if (vid, pid) not in all_devices:
            continue

        device_info = all_devices[(vid, pid)]

        # Get USB path
        usb_path = f"{int(bus)}-{device}"

        devices.append(DetectedDevice(
            vid=vid,
            pid=pid,
            vendor_name=device_info["vendor"],
            product_name=device_info["product"],
            usb_path=usb_path,
            implementation=device_info["implementation"],
            model=device_info.get("model", "CZTV"),
            button_image=device_info.get("button_image", "A1CZTV"),
            protocol=device_info.get("protocol", "scsi"),
            device_type=device_info.get("device_type", 1),
        ))

    return devices


def find_scsi_device_by_usb_path(usb_path: str) -> Optional[str]:
    """Find SCSI device corresponding to USB path"""
    # Method 1: Scan sysfs directly for sg devices with USBLCD vendor
    # This is most reliable as it doesn't depend on lsscsi output format
    for sg_name in _find_sg_entries():
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

    # Method 2: Use lsscsi -g to get sg device mapping
    output = run_command(['lsscsi', '-g'])
    if output:
        for line in output.split('\n'):
            if 'USBLCD' in line:
                # lsscsi -g shows both block device and sg device
                # Format: [H:C:T:L]  type  vendor   model  /dev/sdX  /dev/sgY
                match = re.search(r'/dev/sg\d+', line)
                if match:
                    return match.group(0)

    # Method 3: Use lsscsi -t and look for usb transport
    output = run_command(['lsscsi', '-t'])
    if output:
        for line in output.split('\n'):
            if 'USBLCD' in line or 'usb' in line.lower():
                match = re.search(r'/dev/sg\d+', line)
                if match:
                    return match.group(0)

    # Method 4: Plain lsscsi
    output = run_command(['lsscsi'])
    if output:
        for line in output.split('\n'):
            if 'USBLCD' in line:
                match = re.search(r'/dev/sg\d+', line)
                if match:
                    return match.group(0)

    return None


def find_scsi_usblcd_devices() -> List[DetectedDevice]:
    """Find USBLCD devices directly via sysfs (pure Python, no external commands)"""
    devices = []

    # Check all sg devices via sysfs (dynamic scan, no hardcoded limit)
    for sg_name in _find_sg_entries():
        sg_path = f"/dev/{sg_name}"
        sysfs_base = f"/sys/class/scsi_generic/{sg_name}/device"

        if not os.path.exists(sysfs_base):
            continue

        # Read vendor and model from sysfs
        try:
            with open(f"{sysfs_base}/vendor", 'r') as f:
                vendor = f.read().strip()
            with open(f"{sysfs_base}/model", 'r') as f:
                model = f.read().strip()

            if 'USBLCD' in vendor:
                # Try to get VID:PID from USB sysfs to look up specific model
                dev_model = "CZTV"
                dev_button = "A1CZTV"

                # Check if we can find the USB device info via sysfs
                try:
                    # Navigate up from scsi_generic to find usb device
                    device_path = os.path.realpath(sysfs_base)
                    # Look for idVendor/idProduct in parent directories
                    for _ in range(10):
                        device_path = os.path.dirname(device_path)
                        vid_path = os.path.join(device_path, "idVendor")
                        pid_path = os.path.join(device_path, "idProduct")
                        if os.path.exists(vid_path) and os.path.exists(pid_path):
                            with open(vid_path) as vf:
                                usb_vid = int(vf.read().strip(), 16)
                            with open(pid_path) as pf:
                                usb_pid = int(pf.read().strip(), 16)
                            # Look up in KNOWN_DEVICES
                            if (usb_vid, usb_pid) in KNOWN_DEVICES:
                                dev_info = KNOWN_DEVICES[(usb_vid, usb_pid)]
                                dev_model = dev_info.get("model", "CZTV")
                                dev_button = dev_info.get("button_image", "A1CZTV")
                            break
                except (IOError, OSError, ValueError):
                    pass

                devices.append(DetectedDevice(
                    vid=0x87CD,  # Assume Thermalright (will be overridden if USB info found)
                    pid=0x70DB,
                    vendor_name="Thermalright",
                    product_name=f"LCD Display ({model})",
                    usb_path="unknown",
                    scsi_device=sg_path,
                    implementation="thermalright_lcd_v1",
                    model=dev_model,
                    button_image=dev_button
                ))
        except (IOError, OSError):
            continue

    return devices


def detect_devices() -> List[DetectedDevice]:
    """Detect all USB LCD devices and their SCSI mappings"""
    devices = find_usb_devices()

    for device in devices:
        scsi_dev = find_scsi_device_by_usb_path(device.usb_path)
        device.scsi_device = scsi_dev

    # If we found USB devices but none have SCSI mappings, try sysfs fallback
    if devices and not any(d.scsi_device for d in devices):
        # Try to find sg device via sysfs and assign to first device
        scsi_devices = find_scsi_usblcd_devices()
        if scsi_devices and scsi_devices[0].scsi_device:
            devices[0].scsi_device = scsi_devices[0].scsi_device

    # Fallback: scan SCSI devices directly for USBLCD if no USB devices found
    if not devices:
        devices = find_scsi_usblcd_devices()

    return devices


def get_default_device() -> Optional[DetectedDevice]:
    """Get the first available LCD device"""
    devices = detect_devices()

    if not devices:
        return None

    # Prefer Thermalright device, then any other
    for device in devices:
        if device.vid == 0x87CD:
            return device

    return devices[0]


def get_device_path() -> Optional[str]:
    """Get SCSI device path for LCD (convenience function)"""
    device = get_default_device()
    return device.scsi_device if device else None


def usb_reset_device(usb_path: str) -> bool:
    """Soft reset USB device by unbinding/rebinding (simulates unplug/replug)"""
    try:
        import time

        # Find the device's bus and port
        busnum_path = f"/sys/bus/usb/devices/{usb_path}/busnum"
        devnum_path = f"/sys/bus/usb/devices/{usb_path}/devnum"

        if not os.path.exists(busnum_path):
            return False

        with open(busnum_path) as f:
            _bus = f.read().strip()
        with open(devnum_path) as f:
            _dev = f.read().strip()

        # Use usbreset or authorized mechanism
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
                print(f"[✓] USB device {usb_path} reset successfully")
                return True
            except PermissionError:
                print("[!] Permission denied for USB reset (need root)")

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
                print(f"[✓] USB device {usb_path} reset via unbind/bind")
                return True
            except Exception as e:
                print(f"[!] Failed to reset via unbind/bind: {e}")

        return False
    except Exception as e:
        print(f"[!] USB reset failed: {e}")
        return False


def check_device_health(device_path: str) -> bool:
    """Check if device is responding properly (not in bad binary mode)"""
    try:
        import subprocess

        # Try a simple inquiry command
        result = subprocess.run(
            ['sg_inq', device_path],
            capture_output=True,
            text=True,
            timeout=3
        )

        # Check for error indicators
        if result.returncode != 0:
            return False

        # Check for bad state indicators in output
        output_lower = result.stdout.lower() + result.stderr.lower()
        bad_states = ['error', 'failed', 'not ready', 'medium not present',
                     'i/o error', 'device not responding']

        for state in bad_states:
            if state in output_lower:
                return False

        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return False


def print_device_info(device: DetectedDevice):
    """Pretty print device information"""
    print(f"Device: {device.vendor_name} {device.product_name}")
    print(f"  USB VID:PID: {device.vid:04X}:{device.pid:04X}")
    print(f"  USB Path: {device.usb_path}")
    print(f"  Protocol: {device.protocol.upper()} (type {device.device_type})")
    if device.protocol == "scsi":
        print(f"  SCSI Device: {device.scsi_device or 'Not found'}")
    print(f"  Model: {device.model}")
    print(f"  Button Image: {device.button_image}")
    print(f"  Implementation: {device.implementation}")


def main():
    """CLI interface"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Detect Thermalright LCD USB devices'
    )
    parser.add_argument(
        '--path-only',
        action='store_true',
        help='Only output device path (for scripts)'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Show all detected devices'
    )
    args = parser.parse_args()

    if args.all:
        devices = detect_devices()
        if not devices:
            print("No LCD devices found")
            return 1

        print(f"Found {len(devices)} device(s):\n")
        for i, device in enumerate(devices, 1):
            print(f"Device {i}:")
            print_device_info(device)
            print()
        return 0

    # Get default device
    device = get_default_device()

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

    print_device_info(device)
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
