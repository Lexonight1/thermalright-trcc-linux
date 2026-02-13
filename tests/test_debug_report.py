"""
Tests for DebugReport — diagnostic report collector.

Tests cover:
- Section collection (each section produces output)
- str() formatting (header/footer, separator lines)
- Graceful handling of missing tools/files/devices
- Individual section content (lsusb filter, udev, SELinux, deps, etc.)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.debug_report import _KNOWN_VIDS, DebugReport


class TestDebugReportStructure(unittest.TestCase):
    """Test overall report structure."""

    @patch("trcc.debug_report.subprocess.run")
    @patch("trcc.debug_report.DebugReport._devices")
    @patch("trcc.debug_report.DebugReport._device_permissions")
    @patch("trcc.debug_report.DebugReport._handshakes")
    @patch("trcc.debug_report.DebugReport._config")
    def test_collect_creates_sections(self, *mocks):
        rpt = DebugReport()
        rpt.collect()
        titles = [t for t, _ in rpt.sections]
        self.assertIn("Version", titles)
        self.assertIn("lsusb (filtered)", titles)

    @patch("trcc.debug_report.subprocess.run")
    @patch("trcc.debug_report.DebugReport._devices")
    @patch("trcc.debug_report.DebugReport._device_permissions")
    @patch("trcc.debug_report.DebugReport._handshakes")
    @patch("trcc.debug_report.DebugReport._config")
    def test_str_contains_footer(self, *mocks):
        rpt = DebugReport()
        rpt.collect()
        text = str(rpt)
        self.assertIn("github.com", text)
        self.assertIn("Copy everything above", text)

    def test_empty_report(self):
        """Uncollected report has no sections."""
        rpt = DebugReport()
        self.assertEqual(rpt.sections, [])
        self.assertIn("Copy everything above", str(rpt))


class TestVersionSection(unittest.TestCase):
    """Test version info collection."""

    def test_version_contains_trcc(self):
        rpt = DebugReport()
        rpt._version()
        _, body = rpt.sections[0]
        self.assertIn("trcc-linux:", body)
        self.assertIn("Python:", body)
        self.assertIn("OS:", body)
        self.assertIn("Kernel:", body)


class TestLsusbSection(unittest.TestCase):
    """Test lsusb filtering."""

    @patch("trcc.debug_report.subprocess.run")
    def test_filters_known_vids(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=(
                "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation\n"
                "Bus 001 Device 003: ID 0416:8001 Winbond LED\n"
                "Bus 002 Device 005: ID 87ad:70db ChiZhu GrandVision\n"
            )
        )
        rpt = DebugReport()
        rpt._lsusb()
        _, body = rpt.sections[0]
        self.assertIn("0416:8001", body)
        self.assertIn("87ad:70db", body)
        self.assertNotIn("1d6b:0002", body)

    @patch("trcc.debug_report.subprocess.run")
    def test_no_devices_found(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Bus 001 Device 001: ID 1d6b:0002 Linux Foundation\n"
        )
        rpt = DebugReport()
        rpt._lsusb()
        _, body = rpt.sections[0]
        self.assertIn("no Thermalright devices", body)

    @patch("trcc.debug_report.subprocess.run", side_effect=FileNotFoundError)
    def test_lsusb_not_installed(self, _):
        rpt = DebugReport()
        rpt._lsusb()
        _, body = rpt.sections[0]
        self.assertIn("failed", body)


class TestUdevSection(unittest.TestCase):
    """Test udev rules reading."""

    @patch("builtins.open", mock_open(read_data=(
        "# TRCC udev rules\n"
        'SUBSYSTEM=="usb", ATTR{idVendor}=="0416", MODE="0666"\n'
    )))
    def test_reads_rules(self):
        rpt = DebugReport()
        rpt._udev_rules()
        _, body = rpt.sections[0]
        self.assertIn("0416", body)
        # Comments should be skipped
        self.assertNotIn("# TRCC", body)

    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_not_installed(self, _):
        rpt = DebugReport()
        rpt._udev_rules()
        _, body = rpt.sections[0]
        self.assertIn("NOT INSTALLED", body)
        self.assertIn("setup-udev", body)

    @patch("builtins.open", side_effect=PermissionError)
    def test_permission_denied(self, _):
        rpt = DebugReport()
        rpt._udev_rules()
        _, body = rpt.sections[0]
        self.assertIn("permission denied", body)


class TestSelinuxSection(unittest.TestCase):
    """Test SELinux status."""

    @patch("trcc.debug_report.subprocess.run")
    def test_enforcing(self, mock_run):
        mock_run.return_value = MagicMock(stdout="Enforcing\n")
        rpt = DebugReport()
        rpt._selinux()
        _, body = rpt.sections[0]
        self.assertIn("Enforcing", body)

    @patch("trcc.debug_report.subprocess.run", side_effect=FileNotFoundError)
    def test_not_installed(self, _):
        rpt = DebugReport()
        rpt._selinux()
        _, body = rpt.sections[0]
        self.assertIn("not installed", body)


class TestDependenciesSection(unittest.TestCase):
    """Test dependency checking."""

    def test_lists_packages(self):
        rpt = DebugReport()
        rpt._dependencies()
        _, body = rpt.sections[0]
        # At minimum, pyusb or hidapi status should appear
        self.assertIn("pyusb:", body)
        self.assertIn("hidapi:", body)
        self.assertIn("PyQt6:", body)


class TestDevicesSection(unittest.TestCase):
    """Test device listing."""

    @patch("trcc.device_detector.detect_devices", return_value=[])
    def test_no_devices(self, _):
        rpt = DebugReport()
        rpt._devices()
        _, body = rpt.sections[0]
        self.assertIn("none", body)

    @patch("trcc.device_detector.detect_devices")
    def test_lists_devices(self, mock_detect):
        dev = MagicMock()
        dev.vid = 0x0416
        dev.pid = 0x8001
        dev.product_name = "LED Controller"
        dev.protocol = "hid"
        dev.scsi_device = None
        dev.usb_path = "2-1.4"
        mock_detect.return_value = [dev]

        rpt = DebugReport()
        rpt._devices()
        _, body = rpt.sections[0]
        self.assertIn("0416:8001", body)
        self.assertIn("LED Controller", body)
        self.assertIn("HID", body)


class TestDevicePermissionsSection(unittest.TestCase):
    """Test device permission checking."""

    @patch("os.listdir", return_value=["sg0", "sg1", "sda"])
    @patch("os.stat")
    @patch("os.access", return_value=True)
    def test_sg_devices(self, mock_access, mock_stat, _):
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o100666
        rpt = DebugReport()
        rpt._device_permissions()
        _, body = rpt.sections[0]
        self.assertIn("/dev/sg0", body)
        self.assertIn("/dev/sg1", body)
        self.assertIn("OK", body)

    @patch("os.listdir", return_value=["sda", "sdb"])
    def test_no_sg_devices(self, _):
        rpt = DebugReport()
        rpt._device_permissions()
        _, body = rpt.sections[0]
        self.assertIn("no /dev/sg*", body)


class TestConfigSection(unittest.TestCase):
    """Test config dump."""

    @patch("trcc.conf.load_config", return_value={
        "resolution": [320, 320],
        "temp_unit": 0,
        "format_prefs": {"time_format": 0},
        "devices": {"0:87cd_70db": {"rotation": 0}},
    })
    def test_shows_config(self, _):
        rpt = DebugReport()
        rpt._config()
        _, body = rpt.sections[0]
        self.assertIn("resolution", body)
        self.assertIn("[320, 320]", body)
        self.assertIn("devices: 1 configured", body)

    @patch("trcc.conf.load_config", return_value={})
    def test_empty_config(self, _):
        rpt = DebugReport()
        rpt._config()
        _, body = rpt.sections[0]
        self.assertIn("empty or missing", body)


class TestHandshakesSection(unittest.TestCase):
    """Test handshake collection."""

    @patch("trcc.device_detector.detect_devices", return_value=[])
    def test_no_hid_devices(self, _):
        rpt = DebugReport()
        rpt._handshakes()
        _, body = rpt.sections[0]
        self.assertIn("no HID/Bulk", body)


class TestKnownVids(unittest.TestCase):
    """Test VID list completeness."""

    def test_all_vids_present(self):
        self.assertIn("0416", _KNOWN_VIDS)
        self.assertIn("0418", _KNOWN_VIDS)
        self.assertIn("87cd", _KNOWN_VIDS)
        self.assertIn("87ad", _KNOWN_VIDS)
        self.assertIn("0402", _KNOWN_VIDS)


class TestFullCollect(unittest.TestCase):
    """Test full collect with everything mocked."""

    @patch("trcc.debug_report.subprocess.run")
    @patch("trcc.device_detector.detect_devices", return_value=[])
    @patch("trcc.conf.load_config", return_value={})
    @patch("os.listdir", return_value=[])
    @patch("builtins.open", side_effect=FileNotFoundError)
    def test_full_collect_no_crash(self, *mocks):
        rpt = DebugReport()
        rpt.collect()
        text = str(rpt)
        self.assertIn("Version", text)
        self.assertIn("lsusb", text)
        self.assertIn("udev", text)
        self.assertIn("SELinux", text)
        self.assertIn("Dependencies", text)
        self.assertIn("Detected devices", text)
        self.assertIn("Device permissions", text)
        self.assertIn("Handshakes", text)
        self.assertIn("Config", text)
        # 9 sections total
        self.assertEqual(len(rpt.sections), 9)


if __name__ == "__main__":
    unittest.main()
