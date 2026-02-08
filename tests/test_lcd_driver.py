"""Tests for lcd_driver – unified LCD driver with SCSI communication."""

import binascii
import struct
import tempfile
import unittest
from typing import cast
from unittest.mock import MagicMock, patch

from trcc.lcd_driver import LCDDriver


def _mock_device(vid=0x3633, pid=0x0002, scsi='/dev/sg0',
                 vendor='Thermalright', product='LCD', impl='generic'):
    """Build a mock DetectedDevice."""
    dev = MagicMock()
    dev.vid = vid
    dev.pid = pid
    dev.scsi_device = scsi
    dev.vendor_name = vendor
    dev.product_name = product
    dev.usb_path = '1-2'
    dev.implementation = impl
    return dev


def _mock_implementation(name='generic', resolution=(320, 320)):
    impl = MagicMock()
    impl.name = name
    impl.resolution = resolution
    impl.pixel_format = 'RGB565'
    impl.get_poll_command.return_value = (0x01, 512)
    impl.get_init_command.return_value = (0x02, 512)
    impl.get_frame_chunks.return_value = [(0x10, 204800)]
    impl.needs_init_per_frame.return_value = False
    impl.rgb_to_bytes.return_value = b'\x00\x00'
    impl.detect_resolution = MagicMock()
    return impl


# ── Header building + CRC ───────────────────────────────────────────────────

class TestLCDDriverHeaderCRC(unittest.TestCase):
    """Test _build_header and _crc32 without hardware."""

    def _make_driver(self):
        """Create driver with mocked detection."""
        with patch('trcc.lcd_driver.detect_devices', return_value=[]):
            with patch('trcc.lcd_driver.get_implementation', return_value=_mock_implementation()):
                driver = LCDDriver.__new__(LCDDriver)
                driver.device_info = None
                driver.device_path = '/dev/sg0'
                driver.implementation = _mock_implementation()
                driver.initialized = False
                return driver

    def test_crc32(self):
        driver = self._make_driver()
        data = b'\x01\x00\x00\x00' + b'\x00' * 8 + b'\x00\x02\x00\x00'
        expected = binascii.crc32(data) & 0xFFFFFFFF
        self.assertEqual(driver._crc32(data), expected)

    def test_build_header_length(self):
        driver = self._make_driver()
        header = driver._build_header(0x01, 512)
        self.assertEqual(len(header), 20)

    def test_build_header_structure(self):
        driver = self._make_driver()
        header = driver._build_header(0x42, 1024)

        cmd = struct.unpack_from('<I', header, 0)[0]
        size = struct.unpack_from('<I', header, 12)[0]
        crc = struct.unpack_from('<I', header, 16)[0]

        self.assertEqual(cmd, 0x42)
        self.assertEqual(size, 1024)
        # Verify CRC matches first 16 bytes
        self.assertEqual(crc, binascii.crc32(header[:16]) & 0xFFFFFFFF)


# ── Init paths ───────────────────────────────────────────────────────────────

class TestLCDDriverInit(unittest.TestCase):

    @patch('trcc.lcd_driver.get_implementation')
    @patch('trcc.lcd_driver.detect_devices')
    def test_init_with_path_finds_device(self, mock_detect, mock_get_impl):
        dev = _mock_device(scsi='/dev/sg1')
        mock_detect.return_value = [dev]
        impl = _mock_implementation()
        mock_get_impl.return_value = impl

        driver = LCDDriver(device_path='/dev/sg1')
        self.assertEqual(driver.device_path, '/dev/sg1')
        self.assertEqual(driver.device_info, dev)

    @patch('trcc.lcd_driver.get_implementation')
    @patch('trcc.lcd_driver.detect_devices', return_value=[])
    def test_init_with_path_falls_back_to_generic(self, mock_detect, mock_get_impl):
        impl = _mock_implementation()
        mock_get_impl.return_value = impl

        driver = LCDDriver(device_path='/dev/sg5')
        self.assertEqual(driver.device_path, '/dev/sg5')
        self.assertIsNone(driver.device_info)  # No matching device found

    @patch('trcc.lcd_driver.get_implementation')
    @patch('trcc.lcd_driver.detect_devices')
    def test_init_by_vid_pid(self, mock_detect, mock_get_impl):
        dev = _mock_device(vid=0x3633, pid=0x0002, scsi='/dev/sg0')
        mock_detect.return_value = [dev]
        mock_get_impl.return_value = _mock_implementation()

        driver = LCDDriver(vid=0x3633, pid=0x0002)
        self.assertEqual(driver.device_path, '/dev/sg0')

    @patch('trcc.lcd_driver.get_implementation')
    @patch('trcc.lcd_driver.detect_devices', return_value=[])
    def test_init_by_vid_pid_not_found_raises(self, mock_detect, mock_get_impl):
        with self.assertRaises(RuntimeError):
            LCDDriver(vid=0xDEAD, pid=0xBEEF)

    @patch('trcc.lcd_driver.get_implementation')
    @patch('trcc.lcd_driver.get_default_device')
    def test_init_auto_detect(self, mock_default, mock_get_impl):
        dev = _mock_device()
        mock_default.return_value = dev
        mock_get_impl.return_value = _mock_implementation()

        driver = LCDDriver()
        self.assertEqual(driver.device_info, dev)

    @patch('trcc.lcd_driver.get_default_device', return_value=None)
    def test_init_auto_detect_no_device(self, _):
        with self.assertRaises(RuntimeError):
            LCDDriver()


# ── Frame operations ─────────────────────────────────────────────────────────

class TestLCDDriverFrameOps(unittest.TestCase):

    def _make_driver(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_info = _mock_device()
        driver.device_path = '/dev/sg0'
        driver.implementation = _mock_implementation()
        driver.initialized = True
        return driver

    def test_create_solid_color(self):
        driver = self._make_driver()
        assert driver.implementation is not None
        impl = cast(MagicMock, driver.implementation)
        impl.rgb_to_bytes.return_value = b'\xFF\x00'
        data = driver.create_solid_color(255, 0, 0)
        # 320*320 pixels * 2 bytes each
        self.assertEqual(len(data), 320 * 320 * 2)
        self.assertEqual(data[:2], b'\xFF\x00')

    def test_create_solid_color_no_impl_raises(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.implementation = None
        with self.assertRaises(RuntimeError):
            driver.create_solid_color(0, 0, 0)

    @patch('trcc.lcd_driver.LCDDriver._scsi_write', return_value=True)
    def test_send_frame_pads_short_data(self, mock_write):
        driver = self._make_driver()
        assert driver.implementation is not None
        impl = cast(MagicMock, driver.implementation)
        impl.get_frame_chunks.return_value = [(0x10, 100)]
        driver.send_frame(b'\x00' * 50)
        # Should pad to 100 bytes
        args = mock_write.call_args
        sent_data = args[0][1]
        self.assertEqual(len(sent_data), 100)

    def test_send_frame_no_impl_raises(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.implementation = None
        driver.initialized = False
        with self.assertRaises(RuntimeError):
            driver.send_frame(b'\x00')


# ── get_info ─────────────────────────────────────────────────────────────────

class TestLCDDriverGetInfo(unittest.TestCase):

    def test_info_full(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_path = '/dev/sg0'
        driver.initialized = True
        driver.device_info = _mock_device()
        driver.implementation = _mock_implementation()

        info = driver.get_info()
        self.assertEqual(info['device_path'], '/dev/sg0')
        self.assertTrue(info['initialized'])
        self.assertIn('vendor', info)
        self.assertIn('resolution', info)

    def test_info_minimal(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_path = None
        driver.initialized = False
        driver.device_info = None
        driver.implementation = None

        info = driver.get_info()
        self.assertIsNone(info['device_path'])
        self.assertNotIn('vendor', info)


# ── SCSI read/write ──────────────────────────────────────────────────────────

class TestLCDDriverScsiIO(unittest.TestCase):
    """Test _scsi_read and _scsi_write methods."""

    def _make_driver(self, path='/dev/sg0'):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_info = _mock_device(scsi=path)
        driver.device_path = path
        driver.implementation = _mock_implementation()
        driver.initialized = False
        return driver

    @patch('trcc.lcd_driver.subprocess.run')
    def test_scsi_read_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b'\xDE\xAD')
        driver = self._make_driver()
        result = driver._scsi_read(b'\x01\x02', 256)
        self.assertEqual(result, b'\xDE\xAD')
        mock_run.assert_called_once()

    @patch('trcc.lcd_driver.subprocess.run')
    def test_scsi_read_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout=b'')
        driver = self._make_driver()
        result = driver._scsi_read(b'\x01', 128)
        self.assertEqual(result, b'')

    def test_scsi_read_no_path_raises(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_path = None
        with self.assertRaises(RuntimeError):
            driver._scsi_read(b'\x01', 128)

    @patch('trcc.lcd_driver.os.unlink')
    @patch('trcc.lcd_driver.subprocess.run')
    def test_scsi_write_success(self, mock_run, mock_unlink):
        mock_run.return_value = MagicMock(returncode=0)
        driver = self._make_driver()
        header = driver._build_header(0x101F5, 100)
        result = driver._scsi_write(header, b'\x00' * 100)
        self.assertTrue(result)

    @patch('trcc.lcd_driver.os.unlink')
    @patch('trcc.lcd_driver.subprocess.run')
    def test_scsi_write_failure(self, mock_run, mock_unlink):
        mock_run.return_value = MagicMock(returncode=1)
        driver = self._make_driver()
        header = driver._build_header(0x101F5, 100)
        result = driver._scsi_write(header, b'\x00' * 100)
        self.assertFalse(result)

    def test_scsi_write_no_path_raises(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_path = None
        with self.assertRaises(RuntimeError):
            driver._scsi_write(b'\x00' * 20, b'\x00')


# ── init_device ──────────────────────────────────────────────────────────────

class TestLCDDriverInitDevice(unittest.TestCase):

    def _make_driver(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_info = _mock_device()
        driver.device_path = '/dev/sg0'
        driver.implementation = _mock_implementation()
        driver.initialized = False
        return driver

    @patch.object(LCDDriver, '_scsi_write', return_value=True)
    @patch.object(LCDDriver, '_scsi_read', return_value=b'')
    def test_init_device_calls_poll_then_init(self, mock_read, mock_write):
        driver = self._make_driver()
        driver.init_device()
        mock_read.assert_called_once()
        mock_write.assert_called_once()
        self.assertTrue(driver.initialized)

    @patch.object(LCDDriver, '_scsi_write', return_value=True)
    @patch.object(LCDDriver, '_scsi_read', return_value=b'')
    def test_init_device_skips_if_already_initialized(self, mock_read, mock_write):
        driver = self._make_driver()
        driver.initialized = True
        driver.init_device()
        mock_read.assert_not_called()
        mock_write.assert_not_called()


# ── load_image ───────────────────────────────────────────────────────────────

class TestLCDDriverLoadImage(unittest.TestCase):

    def _make_driver(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_info = _mock_device()
        driver.device_path = '/dev/sg0'
        driver.implementation = _mock_implementation()
        driver.initialized = False
        return driver

    def test_load_image_converts_to_rgb565(self):
        driver = self._make_driver()
        impl = cast(MagicMock, driver.implementation)
        impl.rgb_to_bytes.return_value = b'\xFF\x00'

        # Create a small test image
        from PIL import Image
        img = Image.new('RGB', (10, 10), (255, 0, 0))
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
            img.save(f, 'PNG')
            tmp_path = f.name

        try:
            data = driver.load_image(tmp_path)
            # 320x320 resolution (from mock) * 2 bytes per pixel
            self.assertEqual(len(data), 320 * 320 * 2)
        finally:
            import os
            os.unlink(tmp_path)

    def test_load_image_no_impl_raises(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.implementation = None
        with self.assertRaises(RuntimeError):
            driver.load_image('/tmp/test.png')


if __name__ == '__main__':
    unittest.main()
