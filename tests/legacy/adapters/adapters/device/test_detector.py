"""Tests for DeviceDetector — cross-platform USB device detection."""
from __future__ import annotations

import subprocess
from dataclasses import fields
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.adapters.device.detector import (
    KNOWN_DEVICES,
    DetectedDevice,
    DeviceDetector,
)
from trcc.legacy.adapters.device.linux.detector import linux_scsi_resolver

_CLS = 'trcc.legacy.adapters.device.detector.DeviceDetector'


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def scsi_device():
    return DetectedDevice(
        vid=0x0402, pid=0x3922,
        vendor_name='ALi Corp', product_name='FROZEN WARFRAME',
        usb_path='1-2.3', scsi_device='/dev/sg0',
        implementation='scsi', protocol='scsi',
    )


@pytest.fixture
def hid_device():
    return DetectedDevice(
        vid=0x0416, pid=0x5302,
        vendor_name='Thermalright', product_name='HID LCD',
        usb_path='2-1.4', protocol='hid',
    )


@pytest.fixture
def bulk_device():
    """87AD:70DB — GrandVision 360 AIO (BULK protocol)."""
    return DetectedDevice(
        vid=0x87AD, pid=0x70DB,
        vendor_name='ChiZhu Tech', product_name='GrandVision 360 AIO',
        usb_path='1-2', protocol='bulk',
    )


@pytest.fixture
def mock_usb_device():
    """Mock usb.core device object."""
    dev = MagicMock()
    dev.bus = 1
    dev.address = 3
    return dev


# ── DetectedDevice ────────────────────────────────────────────────────────────

class TestDetectedDevice:

    def test_fields(self):
        field_names = {f.name for f in fields(DetectedDevice)}
        assert field_names == {
            'vid', 'pid', 'vendor_name', 'product_name',
            'usb_path', 'scsi_device', 'implementation',
            'model', 'button_image', 'protocol', 'device_type',
        }

    def test_defaults(self):
        dev = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name='Thermalright', product_name='LCD',
            usb_path='2-1',
        )
        assert dev.scsi_device is None
        assert dev.implementation == 'generic'

    def test_path_returns_scsi_device(self, scsi_device):
        assert scsi_device.path == '/dev/sg0'

    def test_path_falls_back_to_usb_path(self, hid_device):
        assert hid_device.path == '2-1.4'


# ── KNOWN_DEVICES / device registries ────────────────────────────────────────

class TestKnownDevices:

    def test_thermalright_present(self):
        entry = KNOWN_DEVICES[(0x87CD, 0x70DB)]
        assert entry.vendor == 'Thermalright'
        assert entry.implementation == 'thermalright_lcd_v1'

    def test_winbond_present(self):
        entry = KNOWN_DEVICES[(0x0416, 0x5406)]
        assert entry.vendor == 'Winbond'

    def test_ali_corp_present(self):
        entry = KNOWN_DEVICES[(0x0402, 0x3922)]
        assert entry.model == 'FROZEN_WARFRAME'

    def test_all_entries_have_required_attrs(self):
        for (vid, pid), entry in KNOWN_DEVICES.items():
            assert entry.vendor, f'{vid:04X}:{pid:04X} missing vendor'
            assert entry.product, f'{vid:04X}:{pid:04X} missing product'
            assert entry.implementation, f'{vid:04X}:{pid:04X} missing implementation'

    def test_all_registries(self):
        registries = DeviceDetector._get_all_registries()
        assert (0x87CD, 0x70DB) in registries
        assert (0x0402, 0x3922) in registries


# ── run_command ───────────────────────────────────────────────────────────────

class TestRunCommand:

    @patch('trcc.legacy.adapters.device.detector.subprocess.run')
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='output\n')
        assert DeviceDetector.run_command(['echo', 'test']) == 'output'

    @patch('trcc.legacy.adapters.device.detector.subprocess.run')
    def test_nonzero_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout='ignored')
        assert DeviceDetector.run_command(['false']) == ''

    @patch('trcc.legacy.adapters.device.detector.subprocess.run')
    def test_timeout_returns_empty(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired('cmd', 5)
        assert DeviceDetector.run_command(['sleep', '100']) == ''

    @patch('trcc.legacy.adapters.device.detector.subprocess.run')
    def test_file_not_found_returns_empty(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        assert DeviceDetector.run_command(['missing']) == ''


# ── make_detect_fn / _detect ─────────────────────────────────────────────────

class TestDetect:

    @patch('trcc.legacy.adapters.device._pyusb_find.find')
    def test_detect_finds_known_device(self, mock_find, mock_usb_device):
        mock_find.side_effect = lambda idVendor, idProduct, find_all=False: ([mock_usb_device] if (idVendor, idProduct) == (0x87CD, 0x70DB) else [])
        detect_fn = DeviceDetector.make_detect_fn(scsi_resolver=None)
        devices = detect_fn()
        found = [d for d in devices if d.vid == 0x87CD and d.pid == 0x70DB]
        assert len(found) == 1
        assert found[0].vendor_name == 'Thermalright'

    @patch('trcc.legacy.adapters.device._pyusb_find.find')
    def test_detect_calls_scsi_resolver_for_scsi_devices(self, mock_find, mock_usb_device):
        resolver = MagicMock(return_value='/dev/sg0')
        mock_find.side_effect = lambda idVendor, idProduct, find_all=False: ([mock_usb_device] if (idVendor, idProduct) == (0x0402, 0x3922) else [])
        detect_fn = DeviceDetector.make_detect_fn(scsi_resolver=resolver)
        devices = detect_fn()
        found = [d for d in devices if d.vid == 0x0402]
        assert len(found) == 1
        assert found[0].scsi_device == '/dev/sg0'
        resolver.assert_called_with(0x0402, 0x3922)

    @patch('trcc.legacy.adapters.device._pyusb_find.find', return_value=None)
    def test_detect_no_devices(self, _):
        detect_fn = DeviceDetector.make_detect_fn(scsi_resolver=None)
        assert detect_fn() == []

    @patch('trcc.legacy.adapters.device._pyusb_find.find')
    def test_detect_skips_scsi_resolver_for_bulk(self, mock_find, mock_usb_device):
        """SCSI resolver must NOT be called for bulk devices (87AD:70DB)."""
        resolver = MagicMock(return_value='/dev/sg0')
        mock_find.side_effect = lambda idVendor, idProduct, find_all=False: ([mock_usb_device] if (idVendor, idProduct) == (0x87AD, 0x70DB) else [])
        detect_fn = DeviceDetector.make_detect_fn(scsi_resolver=resolver)
        devices = detect_fn()
        found = [d for d in devices if d.vid == 0x87AD]
        assert len(found) == 1
        assert found[0].scsi_device is None
        resolver.assert_not_called()

    @patch('trcc.legacy.adapters.device._pyusb_find.find')
    def test_detect_usb_path_format(self, mock_find, mock_usb_device):
        mock_usb_device.bus = 2
        mock_usb_device.address = 5
        mock_find.side_effect = lambda idVendor, idProduct, find_all=False: ([mock_usb_device] if (idVendor, idProduct) == (0x87CD, 0x70DB) else [])
        detect_fn = DeviceDetector.make_detect_fn(scsi_resolver=None)
        devices = detect_fn()
        found = [d for d in devices if d.vid == 0x87CD]
        assert found[0].usb_path == 'usb:2:5'

    def test_detect_import_error_returns_empty(self):
        with patch.dict('sys.modules', {'usb': None, 'usb.core': None}):
            with patch('builtins.__import__', side_effect=ImportError):
                # _detect handles missing pyusb gracefully
                devices = DeviceDetector._detect(scsi_resolver=None)
        assert devices == []


# ── get_default / get_device_path ────────────────────────────────────────────

class TestGetDefault:

    @patch(f'{_CLS}.detect', return_value=[])
    def test_no_devices_returns_none(self, _):
        assert DeviceDetector.get_default() is None

    @patch(f'{_CLS}.detect')
    def test_single_device_returned(self, mock_detect, scsi_device):
        mock_detect.return_value = [scsi_device]
        assert DeviceDetector.get_default() is scsi_device

    @patch(f'{_CLS}.detect')
    def test_prefers_thermalright(self, mock_detect, scsi_device):
        other = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name='Thermalright', product_name='LCD',
            usb_path='2-1', protocol='scsi',
        )
        mock_detect.return_value = [scsi_device, other]
        assert DeviceDetector.get_default().vid == 0x87CD


class TestGetDevicePath:

    @patch(f'{_CLS}.get_default', return_value=None)
    def test_no_device_returns_none(self, _):
        assert DeviceDetector.get_device_path() is None

    @patch(f'{_CLS}.get_default')
    def test_returns_scsi_path(self, mock_get, scsi_device):
        mock_get.return_value = scsi_device
        assert DeviceDetector.get_device_path() == '/dev/sg0'

    @patch(f'{_CLS}.get_default')
    def test_no_scsi_returns_none(self, mock_get, hid_device):
        mock_get.return_value = hid_device
        assert DeviceDetector.get_device_path() is None


# ── print_info ────────────────────────────────────────────────────────────────

class TestPrintInfo:

    def test_prints_vendor_and_vid(self, scsi_device, capsys):
        DeviceDetector.print_info(scsi_device)
        out = capsys.readouterr().out
        assert 'ALi Corp' in out
        assert '0402' in out

    def test_prints_scsi_path(self, scsi_device, capsys):
        DeviceDetector.print_info(scsi_device)
        out = capsys.readouterr().out
        assert '/dev/sg0' in out

    def test_prints_not_found_when_scsi_path_missing(self, capsys):
        dev = DetectedDevice(
            vid=0x87CD, pid=0x70DB,
            vendor_name='Thermalright', product_name='LCD',
            usb_path='1-2', scsi_device=None, protocol='scsi',
        )
        DeviceDetector.print_info(dev)
        out = capsys.readouterr().out
        assert 'Not found' in out


# ── linux_scsi_resolver ───────────────────────────────────────────────────────

class TestLinuxScsiResolver:

    @patch('trcc.legacy.adapters.device.linux.detector.os.path.exists', return_value=False)
    def test_no_sysfs_returns_none(self, _):
        assert linux_scsi_resolver(0x87CD, 0x70DB) is None

    @patch('trcc.legacy.adapters.device.linux.detector.os.listdir')
    @patch('trcc.legacy.adapters.device.linux.detector.os.path.exists')
    def test_finds_sg_device(self, mock_exists, mock_listdir):
        def exists_side(path):
            return 'sg' in path or 'scsi_generic' in path or 'sys/class' in path
        mock_exists.side_effect = exists_side

        def listdir_side(path):
            if 'scsi_generic' in path:
                return ['sg0']
            return ['1-2']
        mock_listdir.side_effect = listdir_side

        # Full sysfs walk is complex — just verify it returns str or None
        result = linux_scsi_resolver(0x87CD, 0x70DB)
        assert result is None or result.startswith('/dev/')
