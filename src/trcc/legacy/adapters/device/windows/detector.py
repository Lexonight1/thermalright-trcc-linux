"""Windows USB device detection via SetupAPI / WMI.

Replaces Linux sysfs + lsusb scanning with Windows-native USB enumeration.
Returns the same DetectedDevice dataclass used by the rest of the system.

References:
    - C# TRCC uses System.Management (WMI) for USB enumeration
    - SetupAPI: SetupDiGetClassDevs + SetupDiEnumDeviceInterfaces
    - WMI: Win32_USBControllerDevice + Win32_PnPEntity
"""
from __future__ import annotations

import logging

from trcc.legacy.core.models import DetectedDevice

log = logging.getLogger(__name__)


class WindowsDeviceDetector:
    """Detect Thermalright USB devices on Windows."""

    @staticmethod
    def detect() -> list[DetectedDevice]:
        """Scan for Thermalright USB devices using WMI.

        Strategy:
            1. Query WMI Win32_USBControllerDevice for connected USB devices
            2. Filter by known VID:PID pairs (same registries as Linux detector)
            3. Map to DetectedDevice with protocol/implementation fields
            4. For SCSI devices: find matching PhysicalDrive via WMI

        Returns:
            List of DetectedDevice, same contract as Linux DeviceDetector.detect()
        """
        # Import here to avoid ImportError on Linux
        try:
            from trcc.legacy.adapters.system._windows_wmi import wmi_handle
        except ImportError:
            log.error("wmi package not installed — pip install wmi pywin32")
            return []

        devices: list[DetectedDevice] = []
        seen_paths: set[str] = set()
        try:
            w = wmi_handle()
            for usb in w.Win32_USBControllerDevice():
                dependent = usb.Dependent
                vid, pid = _parse_vid_pid(dependent.DeviceID)
                if vid is None or pid is None:
                    continue

                device = _match_device(vid, pid, dependent)
                if device and device.path not in seen_paths:
                    seen_paths.add(device.path)
                    devices.append(device)

        except Exception:
            log.exception("WMI USB enumeration failed")

        log.info("Windows detector found %d device(s)", len(devices))
        return devices


def _parse_vid_pid(device_id: str) -> tuple[int | None, int | None]:
    """Extract VID and PID from Windows device ID string.

    Format: USB\\VID_XXXX&PID_XXXX\\...
    """
    import re
    match = re.search(r'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})', device_id)
    if not match:
        return None, None
    return int(match.group(1), 16), int(match.group(2), 16)


def _match_device(
    vid: int, pid: int, pnp_entity: object,
) -> DetectedDevice | None:
    """Check if VID:PID matches a known Thermalright device.

    Uses the same device registries as the Linux detector
    (KNOWN_DEVICES, _HID_LCD_DEVICES, _LED_DEVICES, _BULK_DEVICES, _LY_DEVICES).
    """
    # Lazy import to share registries with Linux detector
    from trcc.legacy.adapters.device.detector import (
        _BULK_DEVICES,
        _HID_LCD_DEVICES,
        _LED_DEVICES,
        _LY_DEVICES,
        KNOWN_DEVICES,
    )

    device_id = getattr(pnp_entity, 'DeviceID', '')

    # Check SCSI devices
    if (vid, pid) in KNOWN_DEVICES:
        entry = KNOWN_DEVICES[(vid, pid)]
        drive_path = _find_physical_drive(vid, pid)
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=drive_path,
            implementation=entry.implementation,
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='scsi',
            device_type=2,
        )

    # Check HID LCD devices
    if (vid, pid) in _HID_LCD_DEVICES:
        entry = _HID_LCD_DEVICES[(vid, pid)]
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=None,
            implementation=entry.implementation,
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='hid',
            device_type=getattr(entry, 'device_type', 2),
        )

    # Check Bulk devices
    if (vid, pid) in _BULK_DEVICES:
        entry = _BULK_DEVICES[(vid, pid)]
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=None,
            implementation=entry.implementation,
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='bulk',
            device_type=4,
        )

    # Check LY devices
    if (vid, pid) in _LY_DEVICES:
        entry = _LY_DEVICES[(vid, pid)]
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=None,
            implementation=entry.implementation,
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='ly',
            device_type=10,
        )

    # Check LED devices
    if (vid, pid) in _LED_DEVICES:
        entry = _LED_DEVICES[(vid, pid)]
        return DetectedDevice(
            vid=vid, pid=pid,
            vendor_name=entry.vendor, product_name=entry.product,
            usb_path=device_id,
            scsi_device=None,
            implementation='hid_led',
            model=getattr(entry, 'model', ''),
            button_image=getattr(entry, 'button_image', ''),
            protocol='hid',
            device_type=0,
        )

    return None


def _find_physical_drive(vid: int, pid: int) -> str | None:
    """Find the Windows physical drive path for a USB SCSI device.

    Maps USB VID:PID → PhysicalDrive via WMI — VID/PID only, no vendor strings.

    Strategy:
    1. Confirm device VID:PID exists via Win32_USBControllerDevice.
    2. Find USBSTOR disk with size < 1 MB — LCD devices report zero real storage,
       unlike USB flash drives or external HDDs. Return the first match.
    """
    vid_tag = f'VID_{vid:04X}'
    pid_tag = f'PID_{pid:04X}'
    try:
        from trcc.legacy.adapters.system._windows_wmi import wmi_handle
        w = wmi_handle()

        # Step 1: confirm VID/PID is present in the USB device tree
        confirmed = False
        for rel in w.Win32_USBControllerDevice():
            dep = str(rel.Dependent or '').upper()
            if vid_tag in dep and pid_tag in dep:
                confirmed = True
                log.debug("VID/PID %04X:%04X confirmed in USB tree", vid, pid)
                break

        if not confirmed:
            log.debug("VID/PID %04X:%04X not found in USB controller devices", vid, pid)
            return None

        # Step 2: find the USBSTOR disk with tiny capacity — LCD devices
        # report 0 storage; flash drives and HDDs are always > 1 MB.
        for disk in w.Win32_DiskDrive():
            pnp = (disk.PNPDeviceID or '').upper()
            if not pnp.startswith('USBSTOR'):
                continue
            size = int(disk.Size or 0)
            log.debug("USBSTOR disk %s size=%d PNP=%s", disk.DeviceID, size, pnp[:60])
            if size < 1_000_000:
                log.debug("LCD device match — drive: %s (size=%d)", disk.DeviceID, size)
                return disk.DeviceID

    except Exception as e:
        log.debug("WMI lookup failed for %04X:%04X: %s", vid, pid, e)

    log.debug("Could not find physical drive for %04X:%04X", vid, pid)
    return None
