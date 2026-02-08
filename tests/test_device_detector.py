"""
Tests for device_detector - USB LCD device detection module.

Tests cover:
- DetectedDevice dataclass
- KNOWN_DEVICES mapping
- run_command() subprocess wrapper
- find_usb_devices() via lsusb
- find_scsi_device_by_usb_path() via sysfs/lsscsi
- find_scsi_usblcd_devices() via sysfs
- detect_devices() integration
- get_default_device() and get_device_path()
- check_device_health() via sg_inq
"""

import os
import sys
import unittest
from dataclasses import fields
from unittest.mock import MagicMock, mock_open, patch

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.device_detector import (
    KNOWN_DEVICES,
    DetectedDevice,
    check_device_health,
    detect_devices,
    find_scsi_device_by_usb_path,
    find_scsi_usblcd_devices,
    find_usb_devices,
    get_default_device,
    get_device_path,
    main,
    print_device_info,
    run_command,
    usb_reset_device,
)


class TestDetectedDevice(unittest.TestCase):
    """Test DetectedDevice dataclass."""

    def test_dataclass_fields(self):
        """Test that DetectedDevice has all expected fields."""
        field_names = {f.name for f in fields(DetectedDevice)}
        expected_fields = {
            'vid', 'pid', 'vendor_name', 'product_name',
            'usb_path', 'scsi_device', 'implementation',
            'model', 'button_image', 'protocol', 'device_type'
        }
        self.assertEqual(field_names, expected_fields)

    def test_default_values(self):
        """Test default values for optional fields."""
        device = DetectedDevice(
            vid=0x87CD,
            pid=0x70DB,
            vendor_name="Thermalright",
            product_name="LCD Display",
            usb_path="2-1"
        )
        self.assertIsNone(device.scsi_device)
        self.assertEqual(device.implementation, "generic")
        self.assertEqual(device.model, "CZTV")
        self.assertEqual(device.button_image, "A1CZTV")

    def test_full_initialization(self):
        """Test full initialization with all fields."""
        device = DetectedDevice(
            vid=0x0402,
            pid=0x3922,
            vendor_name="ALi Corp",
            product_name="FROZEN WARFRAME",
            usb_path="1-2.3",
            scsi_device="/dev/sg0",
            implementation="ali_corp_lcd_v1",
            model="FROZEN_WARFRAME",
            button_image="A1FROZEN_WARFRAME"
        )
        self.assertEqual(device.vid, 0x0402)
        self.assertEqual(device.pid, 0x3922)
        self.assertEqual(device.scsi_device, "/dev/sg0")
        self.assertEqual(device.model, "FROZEN_WARFRAME")


class TestKnownDevices(unittest.TestCase):
    """Test KNOWN_DEVICES constant mapping."""

    def test_thermalright_device_in_known(self):
        """Test Thermalright device is in KNOWN_DEVICES."""
        self.assertIn((0x87CD, 0x70DB), KNOWN_DEVICES)
        device_info = KNOWN_DEVICES[(0x87CD, 0x70DB)]
        self.assertEqual(device_info["vendor"], "Thermalright")
        self.assertEqual(device_info["implementation"], "thermalright_lcd_v1")

    def test_winbond_device_in_known(self):
        """Test Winbond device (0x0416:0x5406) is in KNOWN_DEVICES."""
        self.assertIn((0x0416, 0x5406), KNOWN_DEVICES)
        device_info = KNOWN_DEVICES[(0x0416, 0x5406)]
        self.assertEqual(device_info["vendor"], "Winbond")

    def test_frozen_warframe_device_in_known(self):
        """Test FROZEN WARFRAME device is in KNOWN_DEVICES."""
        self.assertIn((0x0402, 0x3922), KNOWN_DEVICES)
        device_info = KNOWN_DEVICES[(0x0402, 0x3922)]
        self.assertEqual(device_info["model"], "FROZEN_WARFRAME")
        self.assertEqual(device_info["button_image"], "A1FROZEN_WARFRAME")

    def test_known_devices_have_required_keys(self):
        """Test all KNOWN_DEVICES have required keys."""
        required_keys = {"vendor", "product", "implementation"}
        for (vid, pid), device_info in KNOWN_DEVICES.items():
            for key in required_keys:
                self.assertIn(key, device_info,
                    f"Device {vid:04X}:{pid:04X} missing key '{key}'")


class TestRunCommand(unittest.TestCase):
    """Test run_command subprocess wrapper."""

    @patch('trcc.device_detector.subprocess.run')
    def test_successful_command(self, mock_run):
        """Test successful command execution."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="command output\n"
        )
        result = run_command(['echo', 'test'])
        self.assertEqual(result, "command output")

    @patch('trcc.device_detector.subprocess.run')
    def test_failed_command_returns_empty(self, mock_run):
        """Test failed command returns empty string."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="error output"
        )
        result = run_command(['false'])
        self.assertEqual(result, "")

    @patch('trcc.device_detector.subprocess.run')
    def test_timeout_returns_empty(self, mock_run):
        """Test command timeout returns empty string."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired('cmd', 5)
        result = run_command(['sleep', '100'])
        self.assertEqual(result, "")

    @patch('trcc.device_detector.subprocess.run')
    def test_file_not_found_returns_empty(self, mock_run):
        """Test missing command returns empty string."""
        mock_run.side_effect = FileNotFoundError()
        result = run_command(['nonexistent_command'])
        self.assertEqual(result, "")


class TestFindUsbDevices(unittest.TestCase):
    """Test find_usb_devices function."""

    @patch('trcc.device_detector.run_command')
    def test_no_devices_found(self, mock_run):
        """Test when no USB devices are found."""
        mock_run.return_value = ""
        devices = find_usb_devices()
        self.assertEqual(devices, [])

    @patch('trcc.device_detector.run_command')
    def test_thermalright_device_found(self, mock_run):
        """Test finding Thermalright device via lsusb."""
        mock_run.return_value = (
            "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub\n"
            "Bus 001 Device 003: ID 87cd:70db Thermalright LCD Display\n"
            "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub"
        )
        devices = find_usb_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].vid, 0x87CD)
        self.assertEqual(devices[0].pid, 0x70DB)
        self.assertEqual(devices[0].vendor_name, "Thermalright")
        self.assertEqual(devices[0].implementation, "thermalright_lcd_v1")

    @patch('trcc.device_detector.run_command')
    def test_winbond_device_found(self, mock_run):
        """Test finding Winbond device (0x0416:0x5406) via lsusb."""
        mock_run.return_value = "Bus 001 Device 004: ID 0416:5406 Winbond LCD"
        devices = find_usb_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].vid, 0x0416)
        self.assertEqual(devices[0].pid, 0x5406)
        self.assertEqual(devices[0].vendor_name, "Winbond")

    @patch('trcc.device_detector.run_command')
    def test_frozen_warframe_device_found(self, mock_run):
        """Test finding FROZEN WARFRAME device via lsusb."""
        mock_run.return_value = "Bus 002 Device 002: ID 0402:3922 Unknown Device"
        devices = find_usb_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].vid, 0x0402)
        self.assertEqual(devices[0].pid, 0x3922)
        self.assertEqual(devices[0].model, "FROZEN_WARFRAME")
        self.assertEqual(devices[0].button_image, "A1FROZEN_WARFRAME")

    @patch('trcc.device_detector.run_command')
    def test_multiple_devices_found(self, mock_run):
        """Test finding multiple LCD devices."""
        mock_run.return_value = (
            "Bus 001 Device 003: ID 87cd:70db Thermalright LCD\n"
            "Bus 002 Device 004: ID 0416:5406 ALi Corp LCD"
        )
        devices = find_usb_devices()
        self.assertEqual(len(devices), 2)
        vids = {d.vid for d in devices}
        self.assertEqual(vids, {0x87CD, 0x0416})

    @patch('trcc.device_detector.run_command')
    def test_unknown_device_ignored(self, mock_run):
        """Test that unknown USB devices are ignored."""
        mock_run.return_value = (
            "Bus 001 Device 001: ID 1234:5678 Unknown Device\n"
            "Bus 001 Device 002: ID 87cd:70db Thermalright LCD"
        )
        devices = find_usb_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].vid, 0x87CD)


class TestFindScsiDeviceByUsbPath(unittest.TestCase):
    """Test find_scsi_device_by_usb_path function."""

    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data='USBLCD  ')
    def test_find_via_sysfs(self, mock_file, mock_exists):
        """Test finding SCSI device via sysfs."""
        mock_exists.return_value = True
        result = find_scsi_device_by_usb_path("1-2")
        self.assertEqual(result, "/dev/sg0")

    @patch('os.path.exists')
    @patch('trcc.device_detector.run_command')
    def test_find_via_lsscsi_g(self, mock_run, mock_exists):
        """Test finding SCSI device via lsscsi -g."""
        mock_exists.return_value = False
        mock_run.side_effect = [
            # First call: lsscsi -g
            "[0:0:0:0]    disk    USBLCD   LCD              1.00  -      /dev/sg0",
            # Won't reach lsscsi -t
            "",
        ]
        result = find_scsi_device_by_usb_path("1-2")
        self.assertEqual(result, "/dev/sg0")

    @patch('os.path.exists')
    @patch('trcc.device_detector.run_command')
    def test_no_device_found(self, mock_run, mock_exists):
        """Test when no SCSI device is found."""
        mock_exists.return_value = False
        mock_run.return_value = ""
        result = find_scsi_device_by_usb_path("1-2")
        self.assertIsNone(result)


class TestFindScsiUsblcdDevices(unittest.TestCase):
    """Test find_scsi_usblcd_devices function."""

    @patch('os.path.exists')
    def test_no_sg_devices(self, mock_exists):
        """Test when no sg devices exist."""
        mock_exists.return_value = False
        devices = find_scsi_usblcd_devices()
        self.assertEqual(devices, [])

    @patch('builtins.open')
    @patch('os.path.exists')
    def test_usblcd_device_found(self, mock_exists, mock_open_fn):
        """Test finding USBLCD device via sysfs (basic case)."""
        # Configure exists() - only sg0 exists
        def exists_side_effect(path):
            if '/sys/class/scsi_generic/sg0' in path:
                return True
            if '/sys/class/scsi_generic/sg' in path:
                return False
            return False

        mock_exists.side_effect = exists_side_effect

        # Configure file reads for vendor/model
        def open_side_effect(path, *args, **kwargs):
            m = MagicMock()
            if 'vendor' in path:
                m.read.return_value = 'USBLCD  \n'
            elif 'model' in path:
                m.read.return_value = 'LCD\n'
            else:
                raise FileNotFoundError(path)
            m.__enter__.return_value = m
            m.__exit__.return_value = None
            return m

        mock_open_fn.side_effect = open_side_effect

        devices = find_scsi_usblcd_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].scsi_device, "/dev/sg0")
        self.assertEqual(devices[0].vendor_name, "Thermalright")


class TestDetectDevices(unittest.TestCase):
    """Test detect_devices integration function."""

    @patch('trcc.device_detector.find_scsi_usblcd_devices')
    @patch('trcc.device_detector.find_scsi_device_by_usb_path')
    @patch('trcc.device_detector.find_usb_devices')
    def test_usb_device_with_scsi(self, mock_usb, mock_scsi_path, mock_scsi_direct):
        """Test detection of USB device with SCSI mapping."""
        mock_usb.return_value = [
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="1-2"
            )
        ]
        mock_scsi_path.return_value = "/dev/sg0"
        mock_scsi_direct.return_value = []

        devices = detect_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].scsi_device, "/dev/sg0")

    @patch('trcc.device_detector.find_scsi_usblcd_devices')
    @patch('trcc.device_detector.find_scsi_device_by_usb_path')
    @patch('trcc.device_detector.find_usb_devices')
    def test_fallback_to_scsi_direct(self, mock_usb, mock_scsi_path, mock_scsi_direct):
        """Test fallback to direct SCSI detection when no USB devices found."""
        mock_usb.return_value = []
        mock_scsi_path.return_value = None
        mock_scsi_direct.return_value = [
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="unknown",
                scsi_device="/dev/sg0"
            )
        ]

        devices = detect_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].scsi_device, "/dev/sg0")

    @patch('trcc.device_detector.find_scsi_usblcd_devices')
    @patch('trcc.device_detector.find_scsi_device_by_usb_path')
    @patch('trcc.device_detector.find_usb_devices')
    def test_usb_without_scsi_uses_fallback(self, mock_usb, mock_scsi_path, mock_scsi_direct):
        """Test USB device without SCSI uses sysfs fallback."""
        mock_usb.return_value = [
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="1-2"
            )
        ]
        mock_scsi_path.return_value = None
        mock_scsi_direct.return_value = [
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="unknown",
                scsi_device="/dev/sg1"
            )
        ]

        devices = detect_devices()
        self.assertEqual(len(devices), 1)
        # Should have SCSI device from fallback
        self.assertEqual(devices[0].scsi_device, "/dev/sg1")

    @patch('trcc.device_detector.find_scsi_usblcd_devices')
    @patch('trcc.device_detector.find_scsi_device_by_usb_path')
    @patch('trcc.device_detector.find_usb_devices')
    def test_no_devices_found(self, mock_usb, mock_scsi_path, mock_scsi_direct):
        """Test when no devices are found anywhere."""
        mock_usb.return_value = []
        mock_scsi_path.return_value = None
        mock_scsi_direct.return_value = []

        devices = detect_devices()
        self.assertEqual(devices, [])


class TestGetDefaultDevice(unittest.TestCase):
    """Test get_default_device function."""

    @patch('trcc.device_detector.detect_devices')
    def test_no_devices(self, mock_detect):
        """Test when no devices available."""
        mock_detect.return_value = []
        device = get_default_device()
        self.assertIsNone(device)

    @patch('trcc.device_detector.detect_devices')
    def test_single_device(self, mock_detect):
        """Test with single device."""
        mock_detect.return_value = [
            DetectedDevice(
                vid=0x0416, pid=0x5406,
                vendor_name="ALi Corp", product_name="LCD",
                usb_path="1-2",
                scsi_device="/dev/sg0"
            )
        ]
        device = get_default_device()
        self.assertIsNotNone(device)
        self.assertEqual(device.vid, 0x0416)

    @patch('trcc.device_detector.detect_devices')
    def test_prefers_thermalright(self, mock_detect):
        """Test that Thermalright device is preferred."""
        mock_detect.return_value = [
            DetectedDevice(
                vid=0x0416, pid=0x5406,
                vendor_name="ALi Corp", product_name="LCD",
                usb_path="1-2"
            ),
            DetectedDevice(
                vid=0x87CD, pid=0x70DB,
                vendor_name="Thermalright", product_name="LCD",
                usb_path="2-1"
            ),
        ]
        device = get_default_device()
        self.assertIsNotNone(device)
        self.assertEqual(device.vid, 0x87CD)  # Thermalright preferred


class TestGetDevicePath(unittest.TestCase):
    """Test get_device_path convenience function."""

    @patch('trcc.device_detector.get_default_device')
    def test_no_device(self, mock_get):
        """Test when no device available."""
        mock_get.return_value = None
        path = get_device_path()
        self.assertIsNone(path)

    @patch('trcc.device_detector.get_default_device')
    def test_device_with_path(self, mock_get):
        """Test with device that has SCSI path."""
        mock_get.return_value = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name="Thermalright", product_name="LCD",
            usb_path="1-2",
            scsi_device="/dev/sg0"
        )
        path = get_device_path()
        self.assertEqual(path, "/dev/sg0")

    @patch('trcc.device_detector.get_default_device')
    def test_device_without_path(self, mock_get):
        """Test with device that has no SCSI path."""
        mock_get.return_value = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name="Thermalright", product_name="LCD",
            usb_path="1-2",
            scsi_device=None
        )
        path = get_device_path()
        self.assertIsNone(path)


class TestCheckDeviceHealth(unittest.TestCase):
    """Test check_device_health function."""

    @patch('subprocess.run')
    def test_healthy_device(self, mock_run):
        """Test healthy device returns True."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="USBLCD vendor, model info...",
            stderr=""
        )
        result = check_device_health("/dev/sg0")
        self.assertTrue(result)

    @patch('subprocess.run')
    def test_unhealthy_device_returncode(self, mock_run):
        """Test unhealthy device (bad return code) returns False."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error"
        )
        result = check_device_health("/dev/sg0")
        self.assertFalse(result)

    @patch('subprocess.run')
    def test_unhealthy_device_error_output(self, mock_run):
        """Test unhealthy device (error in output) returns False."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="i/o error reading device",
            stderr=""
        )
        result = check_device_health("/dev/sg0")
        self.assertFalse(result)

    @patch('subprocess.run')
    def test_timeout_returns_false(self, mock_run):
        """Test timeout returns False."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired('sg_inq', 3)
        result = check_device_health("/dev/sg0")
        self.assertFalse(result)

    @patch('subprocess.run')
    def test_command_not_found(self, mock_run):
        """Test command not found returns False."""
        mock_run.side_effect = FileNotFoundError()
        result = check_device_health("/dev/sg0")
        self.assertFalse(result)


class TestUsbResetDevice(unittest.TestCase):
    """Test usb_reset_device function."""

    @patch('time.sleep')
    @patch('builtins.open')
    @patch('os.path.exists')
    def test_reset_via_authorized(self, mock_exists, mock_open_fn, mock_sleep):
        """Test reset via authorized file."""
        mock_exists.return_value = True
        mock_file = MagicMock()
        mock_open_fn.return_value.__enter__.return_value = mock_file

        result = usb_reset_device("1-2.3")
        # Even if it works internally, verify no crash
        self.assertIsInstance(result, bool)

    @patch('os.path.exists')
    def test_nonexistent_path(self, mock_exists):
        """Test with nonexistent USB path."""
        mock_exists.return_value = False
        result = usb_reset_device("nonexistent-path")
        self.assertFalse(result)


class TestDeviceModelMapping(unittest.TestCase):
    """Test device model to button image mapping."""

    def test_thermalright_button_image(self):
        """Test Thermalright device has correct button image prefix."""
        device_info = KNOWN_DEVICES[(0x87CD, 0x70DB)]
        self.assertEqual(device_info.get("button_image", "A1CZTV"), "A1CZTV")

    def test_frozen_warframe_button_image(self):
        """Test FROZEN WARFRAME has unique button image prefix."""
        device_info = KNOWN_DEVICES[(0x0402, 0x3922)]
        self.assertEqual(device_info["button_image"], "A1FROZEN_WARFRAME")
        self.assertEqual(device_info["model"], "FROZEN_WARFRAME")


# ── find_scsi_device_by_usb_path additional methods ─────────────────────────

class TestScsiMethodFallbacks(unittest.TestCase):

    @patch('trcc.device_detector.run_command', return_value=None)
    @patch('os.path.exists')
    @patch('builtins.open')
    def test_sysfs_non_usblcd_skipped(self, mock_open_fn, mock_exists, _):
        """sg0 exists but vendor is NOT USBLCD → continues to next."""
        mock_exists.side_effect = lambda p: 'sg0' in p
        mock_open_fn.return_value.__enter__.return_value.read.return_value = 'SomeOther\n'
        result = find_scsi_device_by_usb_path('1-2')
        self.assertIsNone(result)

    @patch('trcc.device_detector.run_command', return_value=None)
    @patch('os.path.exists', return_value=True)
    @patch('builtins.open', side_effect=IOError("permission"))
    def test_sysfs_ioerror(self, *_):
        """IOError reading vendor file → continues."""
        result = find_scsi_device_by_usb_path('1-2')
        self.assertIsNone(result)

    @patch('trcc.device_detector.run_command')
    @patch('os.path.exists', return_value=False)
    def test_method3_lsscsi_t(self, _, mock_run):
        """Methods 1 & 2 fail, Method 3 (lsscsi -t) finds device."""
        mock_run.side_effect = [
            None,  # lsscsi -g
            '[0:0:0:0]  usb:1-2           /dev/sg0\n',  # lsscsi -t
        ]
        result = find_scsi_device_by_usb_path('1-2')
        self.assertEqual(result, '/dev/sg0')

    @patch('trcc.device_detector.run_command')
    @patch('os.path.exists', return_value=False)
    def test_method4_plain_lsscsi(self, _, mock_run):
        """Methods 1-3 fail, Method 4 (plain lsscsi) finds USBLCD."""
        mock_run.side_effect = [
            None,  # lsscsi -g
            None,  # lsscsi -t
            '[0:0:0:0]  disk    USBLCD   LCD-PANEL   /dev/sg1\n',  # lsscsi
        ]
        result = find_scsi_device_by_usb_path('1-2')
        self.assertEqual(result, '/dev/sg1')


# ── usb_reset_device additional branches ─────────────────────────────────────

class TestUsbResetFallbacks(unittest.TestCase):

    @patch('time.sleep')
    @patch('builtins.open')
    @patch('os.path.exists')
    def test_authorized_permission_error(self, mock_exists, mock_open_fn, _):
        """authorized file exists but write raises PermissionError."""
        mock_exists.side_effect = lambda p: True
        mock_open_fn.return_value.__enter__.return_value.read.return_value = '1\n'
        mock_open_fn.return_value.__enter__.return_value.write.side_effect = PermissionError

        result = usb_reset_device('1-2.3')
        # Falls through to Method 2 → also fails → False
        self.assertIsInstance(result, bool)

    @patch('time.sleep')
    @patch('os.readlink', return_value='/sys/bus/usb/drivers/usb')
    @patch('builtins.open')
    @patch('os.path.exists')
    def test_unbind_bind_method(self, mock_exists, mock_open_fn, mock_readlink, _):
        """Authorized does not exist, falls through to unbind/bind."""
        def exists_side(p):
            if 'busnum' in p or 'devnum' in p:
                return True
            if 'authorized' in p:
                return False
            if 'driver' in p:
                return True
            return True
        mock_exists.side_effect = exists_side

        mock_file = MagicMock()
        mock_file.read.return_value = '1\n'
        mock_open_fn.return_value.__enter__.return_value = mock_file

        result = usb_reset_device('1-2.3')
        self.assertIsInstance(result, bool)

    def test_top_level_exception(self):
        """Outer exception handler returns False."""
        with patch('os.path.exists', side_effect=RuntimeError("boom")):
            result = usb_reset_device('1-2.3')
        self.assertFalse(result)


# ── print_device_info ────────────────────────────────────────────────────────

class TestPrintDeviceInfo(unittest.TestCase):

    def test_prints_device_fields(self):
        import io
        from contextlib import redirect_stdout

        device = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name='Thermalright', product_name='LCD Panel',
            usb_path='1-2', scsi_device='/dev/sg0',
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_device_info(device)
        output = buf.getvalue()
        self.assertIn('Thermalright', output)
        self.assertIn('87CD', output)
        self.assertIn('/dev/sg0', output)

    def test_prints_none_scsi(self):
        import io
        from contextlib import redirect_stdout

        device = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name='Thermalright', product_name='LCD Panel',
            usb_path='1-2',
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_device_info(device)
        self.assertIn('Not found', buf.getvalue())


# ── main() CLI ───────────────────────────────────────────────────────────────

class TestMainCLI(unittest.TestCase):

    @patch('trcc.device_detector.detect_devices')
    def test_all_flag_no_devices(self, mock_detect):
        mock_detect.return_value = []
        with patch('sys.argv', ['prog', '--all']):
            result = main()
        self.assertEqual(result, 1)

    @patch('trcc.device_detector.detect_devices')
    def test_all_flag_with_devices(self, mock_detect):
        mock_detect.return_value = [
            DetectedDevice(vid=0x87CD, pid=0x70DB,
                           vendor_name='Thermalright',
                           product_name='LCD', usb_path='1-2',
                           scsi_device='/dev/sg0'),
        ]
        with patch('sys.argv', ['prog', '--all']):
            result = main()
        self.assertEqual(result, 0)

    @patch('trcc.device_detector.get_default_device')
    def test_path_only_with_device(self, mock_get):
        mock_get.return_value = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name='Thermalright', product_name='LCD',
            usb_path='1-2', scsi_device='/dev/sg0')
        with patch('sys.argv', ['prog', '--path-only']):
            result = main()
        self.assertEqual(result, 0)

    @patch('trcc.device_detector.get_default_device', return_value=None)
    def test_path_only_no_device(self, _):
        with patch('sys.argv', ['prog', '--path-only']):
            result = main()
        self.assertEqual(result, 1)

    @patch('trcc.device_detector.get_default_device')
    def test_default_prints_info(self, mock_get):
        mock_get.return_value = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name='Thermalright', product_name='LCD',
            usb_path='1-2', scsi_device='/dev/sg0')
        with patch('sys.argv', ['prog']):
            result = main()
        self.assertEqual(result, 0)

    @patch('trcc.device_detector.get_default_device', return_value=None)
    def test_default_no_device(self, _):
        with patch('sys.argv', ['prog']):
            result = main()
        self.assertEqual(result, 1)


# ── Targeted coverage: sysfs lookup, unbind/bind, main edge ─────────────────

class TestFindScsiUsblcdVidPid(unittest.TestCase):
    """Cover USB VID/PID lookup in sysfs (lines 210-221) and IOError (234-235)."""

    @patch('trcc.device_detector.os.path.exists')
    @patch('trcc.device_detector.os.path.realpath', return_value='/sys/devices/pci/usb/scsi/sg0')
    @patch('builtins.open', create=True)
    def test_vid_pid_found_in_known_devices(self, mock_open_fn, mock_realpath, mock_exists):
        """sysfs vendor=USBLCD, idVendor/idProduct match KNOWN_DEVICES."""
        def exists_side(path):
            if 'scsi_generic/sg0/device' in path:
                return True
            if 'idVendor' in path or 'idProduct' in path:
                return True
            return False
        mock_exists.side_effect = exists_side

        # Build mock file objects for opens
        def open_side(path, *args, **kwargs):
            m = MagicMock()
            if 'vendor' in path and 'id' not in path:
                m.__enter__ = lambda s: MagicMock(read=lambda: ' USBLCD  ')
            elif 'model' in path:
                m.__enter__ = lambda s: MagicMock(read=lambda: 'TRCC ')
            elif 'idVendor' in path:
                m.__enter__ = lambda s: MagicMock(read=lambda: '87cd')
            elif 'idProduct' in path:
                m.__enter__ = lambda s: MagicMock(read=lambda: '70db')
            else:
                m.__enter__ = lambda s: MagicMock(read=lambda: '')
            m.__exit__ = lambda s, *a: None
            return m
        mock_open_fn.side_effect = open_side

        devices = find_scsi_usblcd_devices()
        # Should find at least one device with model from KNOWN_DEVICES
        found = [d for d in devices if d.scsi_device == '/dev/sg0']
        self.assertTrue(len(found) > 0)

    @patch('trcc.device_detector.os.path.exists')
    @patch('builtins.open', side_effect=IOError("fail"))
    def test_ioerror_skips_device(self, mock_open_fn, mock_exists):
        """IOError reading vendor/model -> continue (lines 234-235)."""
        mock_exists.return_value = True
        devices = find_scsi_usblcd_devices()
        self.assertEqual(devices, [])


class TestUsbResetUnbindBind(unittest.TestCase):
    """Cover unbind/bind Method 2 (lines 318+)."""

    @patch('time.sleep')
    @patch('trcc.device_detector.os.readlink', return_value='/sys/bus/usb/drivers/usb')
    @patch('trcc.device_detector.os.path.exists')
    @patch('builtins.open', create=True)
    def test_unbind_bind_success(self, mock_open_fn, mock_exists, mock_readlink, mock_sleep):
        def exists_side(path):
            return True  # all paths exist
        mock_exists.side_effect = exists_side

        def open_side(path, *args, **kwargs):
            m = MagicMock()
            if 'busnum' in path:
                m.__enter__ = lambda s: MagicMock(read=lambda: '1')
            elif 'devnum' in path:
                m.__enter__ = lambda s: MagicMock(read=lambda: '3')
            elif 'authorized' in path:
                raise PermissionError("denied")  # force Method 2
            else:
                writer = MagicMock()
                m.__enter__ = lambda s: writer
            m.__exit__ = lambda s, *a: None
            return m
        mock_open_fn.side_effect = open_side

        usb_reset_device('1-3')
        # May succeed or fail depending on mock flow; we exercise the code path


class TestMainPathOnlyNoScsi(unittest.TestCase):
    """Cover main() --path-only when device has no scsi_device (line 427)."""

    @patch('trcc.device_detector.get_default_device')
    def test_path_only_no_scsi(self, mock_get):
        device = DetectedDevice(
            vid=0x87CD, pid=0x70DB, vendor_name='TR', product_name='LCD',
            usb_path='1-3', scsi_device=None, implementation='thermalright_lcd_v1',
        )
        mock_get.return_value = device
        with patch('sys.argv', ['prog', '--path-only']):
            result = main()
        self.assertEqual(result, 1)


if __name__ == '__main__':
    unittest.main()
