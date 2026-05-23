"""Tests for Windows USB device detector (mocked — runs on Linux)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

MODULE = 'trcc.legacy.adapters.device.windows.detector'


# ── VID/PID Parsing ───────────────────────────────────────────────────


class TestParseVidPid:

    def test_standard_format(self):
        from trcc.legacy.adapters.device.windows.detector import _parse_vid_pid
        vid, pid = _parse_vid_pid('USB\\VID_0416&PID_5020\\12345')
        assert vid == 0x0416
        assert pid == 0x5020

    def test_lowercase(self):
        from trcc.legacy.adapters.device.windows.detector import _parse_vid_pid
        vid, pid = _parse_vid_pid('USB\\VID_0416&PID_5020\\abc')
        assert vid == 0x0416

    def test_no_match(self):
        from trcc.legacy.adapters.device.windows.detector import _parse_vid_pid
        vid, pid = _parse_vid_pid('PCI\\VEN_8086&DEV_1234')
        assert vid is None
        assert pid is None

    def test_empty_string(self):
        from trcc.legacy.adapters.device.windows.detector import _parse_vid_pid
        vid, pid = _parse_vid_pid('')
        assert vid is None
        assert pid is None

    def test_partial_match(self):
        from trcc.legacy.adapters.device.windows.detector import _parse_vid_pid
        vid, pid = _parse_vid_pid('VID_041')  # Too short
        assert vid is None


# ── Device Matching ───────────────────────────────────────────────────


class TestMatchDevice:

    def test_unknown_vid_pid_returns_none(self):
        from trcc.legacy.adapters.device.windows.detector import _match_device
        pnp = MagicMock()
        pnp.DeviceID = 'USB\\VID_FFFF&PID_FFFF'
        result = _match_device(0xFFFF, 0xFFFF, pnp)
        assert result is None

    def test_known_scsi_device(self):
        """Known SCSI VID:PID should return DetectedDevice with protocol='scsi'."""
        from trcc.legacy.adapters.device.detector import KNOWN_DEVICES
        from trcc.legacy.adapters.device.windows.detector import _match_device

        if not KNOWN_DEVICES:
            pytest.skip("No SCSI devices in registry")

        vid, pid = next(iter(KNOWN_DEVICES))
        pnp = MagicMock()
        pnp.DeviceID = f'USB\\VID_{vid:04X}&PID_{pid:04X}'

        with patch(f'{MODULE}._find_physical_drive', return_value='\\\\.\\PhysicalDrive2'):
            result = _match_device(vid, pid, pnp)

        assert result is not None
        assert result.vid == vid
        assert result.pid == pid
        assert result.protocol == 'scsi'
        assert result.scsi_device == '\\\\.\\PhysicalDrive2'

    def test_known_hid_lcd_device(self):
        """Known HID LCD VID:PID should return DetectedDevice with protocol='hid'."""
        from trcc.legacy.adapters.device.detector import _HID_LCD_DEVICES
        from trcc.legacy.adapters.device.windows.detector import _match_device

        if not _HID_LCD_DEVICES:
            pytest.skip("No HID LCD devices in registry")

        vid, pid = next(iter(_HID_LCD_DEVICES))
        pnp = MagicMock()
        pnp.DeviceID = f'USB\\VID_{vid:04X}&PID_{pid:04X}'

        result = _match_device(vid, pid, pnp)
        assert result is not None
        assert result.protocol == 'hid'
        assert result.scsi_device is None

    def test_known_bulk_device(self):
        """Known Bulk VID:PID should return protocol='bulk'."""
        from trcc.legacy.adapters.device.detector import _BULK_DEVICES
        from trcc.legacy.adapters.device.windows.detector import _match_device

        if not _BULK_DEVICES:
            pytest.skip("No Bulk devices in registry")

        vid, pid = next(iter(_BULK_DEVICES))
        pnp = MagicMock()
        pnp.DeviceID = 'USB\\test'

        result = _match_device(vid, pid, pnp)
        assert result is not None
        assert result.protocol == 'bulk'
        assert result.device_type == 4

    def test_known_ly_device(self):
        """Known LY VID:PID should return protocol='ly'."""
        from trcc.legacy.adapters.device.detector import _LY_DEVICES
        from trcc.legacy.adapters.device.windows.detector import _match_device

        if not _LY_DEVICES:
            pytest.skip("No LY devices in registry")

        vid, pid = next(iter(_LY_DEVICES))
        pnp = MagicMock()
        pnp.DeviceID = 'USB\\test'

        result = _match_device(vid, pid, pnp)
        assert result is not None
        assert result.protocol == 'ly'
        assert result.device_type == 10

    def test_known_led_device(self):
        """Known LED VID:PID should return protocol='hid', implementation='hid_led'."""
        from trcc.legacy.adapters.device.detector import _LED_DEVICES
        from trcc.legacy.adapters.device.windows.detector import _match_device

        if not _LED_DEVICES:
            pytest.skip("No LED devices in registry")

        vid, pid = next(iter(_LED_DEVICES))
        pnp = MagicMock()
        pnp.DeviceID = 'USB\\test'

        result = _match_device(vid, pid, pnp)
        assert result is not None
        assert result.protocol == 'hid'
        assert result.implementation == 'hid_led'
        assert result.device_type == 0


# ── Detector.detect() ─────────────────────────────────────────────────


class TestWindowsDetector:

    def test_returns_empty_without_wmi(self):
        """On Linux (no wmi), returns empty list."""
        from trcc.legacy.adapters.device.windows.detector import WindowsDeviceDetector
        devices = WindowsDeviceDetector.detect()
        assert devices == []


# ── Physical Drive Finder ─────────────────────────────────────────────


class TestFindPhysicalDrive:

    def test_returns_none_without_wmi(self):
        """On Linux, returns None (wmi not available)."""
        from trcc.legacy.adapters.device.windows.detector import _find_physical_drive
        result = _find_physical_drive(0x0416, 0x5020)
        assert result is None

    def test_usbstor_tiny_disk_matches(self):
        """USBSTOR disk with 0 size matches — VID/PID confirmed, any vendor string works."""
        from trcc.legacy.adapters.device.windows.detector import _find_physical_drive

        lcd_disk = MagicMock()
        lcd_disk.PNPDeviceID = r'USBSTOR\DISK&VEN_USBLCD&PROD_USB_PRC_SYSTEM&REV_\7&AF300EF&0'
        lcd_disk.DeviceID = '\\\\.\\PhysicalDrive1'
        lcd_disk.Size = '0'

        qemu_disk = MagicMock()
        qemu_disk.PNPDeviceID = r'SCSI\DISK&VEN_QEMU&PROD_HARDDISK\4&35424867'
        qemu_disk.DeviceID = '\\\\.\\PhysicalDrive0'
        qemu_disk.Size = '64424509440'

        usb_rel = MagicMock()
        usb_rel.Dependent = r'Win32_PnPEntity.DeviceID="USB\VID_0402&PID_3922\6&1C4D2F9B&0&21"'

        mock_wmi_mod = MagicMock()
        mock_wmi_instance = MagicMock()
        mock_wmi_mod.WMI.return_value = mock_wmi_instance
        mock_wmi_instance.Win32_DiskDrive.return_value = [qemu_disk, lcd_disk]
        mock_wmi_instance.Win32_USBControllerDevice.return_value = [usb_rel]

        with patch.dict('sys.modules', {'wmi': mock_wmi_mod}):
            result = _find_physical_drive(0x0402, 0x3922)

        assert result == '\\\\.\\PhysicalDrive1'

    def test_xsail_firmware_tiny_disk_matches(self):
        """Xsail firmware vendor string — size-based match works regardless."""
        from trcc.legacy.adapters.device.windows.detector import _find_physical_drive

        lcd_disk = MagicMock()
        lcd_disk.PNPDeviceID = r'USBSTOR\DISK&VEN_XSAIL&PROD_USB_PRC_SYSTEM&REV_\7&AF300EF&0'
        lcd_disk.DeviceID = '\\\\.\\PhysicalDrive1'
        lcd_disk.Size = '0'

        usb_rel = MagicMock()
        usb_rel.Dependent = r'Win32_PnPEntity.DeviceID="USB\VID_0402&PID_3922\6&1C4D2F9B&0&21"'

        mock_wmi_mod = MagicMock()
        mock_wmi_instance = MagicMock()
        mock_wmi_mod.WMI.return_value = mock_wmi_instance
        mock_wmi_instance.Win32_DiskDrive.return_value = [lcd_disk]
        mock_wmi_instance.Win32_USBControllerDevice.return_value = [usb_rel]

        with patch.dict('sys.modules', {'wmi': mock_wmi_mod}):
            result = _find_physical_drive(0x0402, 0x3922)

        assert result == '\\\\.\\PhysicalDrive1'

    def test_flash_drive_ignored(self):
        """USB flash drive (large size) is not matched even if VID/PID confirmed."""
        from trcc.legacy.adapters.device.windows.detector import _find_physical_drive

        flash_drive = MagicMock()
        flash_drive.PNPDeviceID = r'USBSTOR\DISK&VEN_KINGSTON&PROD_DATATRAVELER&REV_1.0\1234'
        flash_drive.DeviceID = '\\\\.\\PhysicalDrive2'
        flash_drive.Size = '32212254720'  # 32 GB

        usb_rel = MagicMock()
        usb_rel.Dependent = r'Win32_PnPEntity.DeviceID="USB\VID_0402&PID_3922\6&1C4D2F9B&0&21"'

        mock_wmi_mod = MagicMock()
        mock_wmi_instance = MagicMock()
        mock_wmi_mod.WMI.return_value = mock_wmi_instance
        mock_wmi_instance.Win32_DiskDrive.return_value = [flash_drive]
        mock_wmi_instance.Win32_USBControllerDevice.return_value = [usb_rel]

        with patch.dict('sys.modules', {'wmi': mock_wmi_mod}):
            result = _find_physical_drive(0x0402, 0x3922)

        assert result is None

    def test_no_usb_device_returns_none(self):
        """VID/PID not in USB tree — no disk returned."""
        from trcc.legacy.adapters.device.windows.detector import _find_physical_drive

        mock_wmi_mod = MagicMock()
        mock_wmi_instance = MagicMock()
        mock_wmi_mod.WMI.return_value = mock_wmi_instance
        mock_wmi_instance.Win32_DiskDrive.return_value = []
        mock_wmi_instance.Win32_USBControllerDevice.return_value = []

        with patch.dict('sys.modules', {'wmi': mock_wmi_mod}):
            result = _find_physical_drive(0x0402, 0x3922)

        assert result is None

    def test_wmi_exception_returns_none(self):
        """WMI unavailable — returns None gracefully."""
        from trcc.legacy.adapters.device.windows.detector import _find_physical_drive

        mock_wmi_mod = MagicMock()
        mock_wmi_mod.WMI.side_effect = Exception("WMI unavailable")

        with patch.dict('sys.modules', {'wmi': mock_wmi_mod}):
            result = _find_physical_drive(0x0402, 0x3922)

        assert result is None
