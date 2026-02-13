"""Tests for lcd_driver – unified LCD driver with SCSI communication."""

import binascii
import struct
import tempfile
import unittest
from typing import cast
from unittest.mock import MagicMock, patch

from trcc.driver_lcd import LCDDriver


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
    """Test _build_header and _crc32 (delegated to scsi_device)."""

    def test_crc32(self):
        from trcc.device_scsi import _crc32
        data = b'\x01\x00\x00\x00' + b'\x00' * 8 + b'\x00\x02\x00\x00'
        expected = binascii.crc32(data) & 0xFFFFFFFF
        self.assertEqual(_crc32(data), expected)

    def test_build_header_length(self):
        from trcc.device_scsi import _build_header
        header = _build_header(0x01, 512)
        self.assertEqual(len(header), 20)

    def test_build_header_structure(self):
        from trcc.device_scsi import _build_header
        header = _build_header(0x42, 1024)

        cmd = struct.unpack_from('<I', header, 0)[0]
        size = struct.unpack_from('<I', header, 12)[0]
        crc = struct.unpack_from('<I', header, 16)[0]

        self.assertEqual(cmd, 0x42)
        self.assertEqual(size, 1024)
        # Verify CRC matches first 16 bytes
        self.assertEqual(crc, binascii.crc32(header[:16]) & 0xFFFFFFFF)


# ── Init paths ───────────────────────────────────────────────────────────────

class TestLCDDriverInit(unittest.TestCase):

    @patch('trcc.driver_lcd.get_implementation')
    @patch('trcc.driver_lcd.detect_devices')
    def test_init_with_path_finds_device(self, mock_detect, mock_get_impl):
        dev = _mock_device(scsi='/dev/sg1')
        mock_detect.return_value = [dev]
        impl = _mock_implementation()
        mock_get_impl.return_value = impl

        driver = LCDDriver(device_path='/dev/sg1')
        self.assertEqual(driver.device_path, '/dev/sg1')
        self.assertEqual(driver.device_info, dev)

    @patch('trcc.driver_lcd.get_implementation')
    @patch('trcc.driver_lcd.detect_devices', return_value=[])
    def test_init_with_path_falls_back_to_generic(self, mock_detect, mock_get_impl):
        impl = _mock_implementation()
        mock_get_impl.return_value = impl

        driver = LCDDriver(device_path='/dev/sg5')
        self.assertEqual(driver.device_path, '/dev/sg5')
        self.assertIsNone(driver.device_info)  # No matching device found

    @patch('trcc.driver_lcd.get_implementation')
    @patch('trcc.driver_lcd.detect_devices')
    def test_init_by_vid_pid(self, mock_detect, mock_get_impl):
        dev = _mock_device(vid=0x3633, pid=0x0002, scsi='/dev/sg0')
        mock_detect.return_value = [dev]
        mock_get_impl.return_value = _mock_implementation()

        driver = LCDDriver(vid=0x3633, pid=0x0002)
        self.assertEqual(driver.device_path, '/dev/sg0')

    @patch('trcc.driver_lcd.get_implementation')
    @patch('trcc.driver_lcd.detect_devices', return_value=[])
    def test_init_by_vid_pid_not_found_raises(self, mock_detect, mock_get_impl):
        with self.assertRaises(RuntimeError):
            LCDDriver(vid=0xDEAD, pid=0xBEEF)

    @patch('trcc.driver_lcd.get_implementation')
    @patch('trcc.driver_lcd.get_default_device')
    def test_init_auto_detect(self, mock_default, mock_get_impl):
        dev = _mock_device()
        mock_default.return_value = dev
        mock_get_impl.return_value = _mock_implementation()

        driver = LCDDriver()
        self.assertEqual(driver.device_info, dev)

    @patch('trcc.driver_lcd.get_default_device', return_value=None)
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

    @patch('trcc.driver_lcd._scsi_write', return_value=True)
    def test_send_frame_pads_short_data(self, mock_write):
        driver = self._make_driver()
        assert driver.implementation is not None
        impl = cast(MagicMock, driver.implementation)
        impl.get_frame_chunks.return_value = [(0x10, 100)]
        driver.send_frame(b'\x00' * 50)
        # Should pad to 100 bytes — _scsi_write(dev, header, data)
        args = mock_write.call_args
        sent_data = args[0][2]  # 3rd positional arg is data
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
    """Test _scsi_read and _scsi_write (module-level functions in scsi_device)."""

    @patch('trcc.device_scsi.require_sg_raw')
    @patch('trcc.device_scsi.subprocess.run')
    def test_scsi_read_success(self, mock_run, _):
        from trcc.device_scsi import _scsi_read
        mock_run.return_value = MagicMock(returncode=0, stdout=b'\xDE\xAD')
        result = _scsi_read('/dev/sg0', b'\x01\x02', 256)
        self.assertEqual(result, b'\xDE\xAD')
        mock_run.assert_called_once()

    @patch('trcc.device_scsi.require_sg_raw')
    @patch('trcc.device_scsi.subprocess.run')
    def test_scsi_read_failure(self, mock_run, _):
        from trcc.device_scsi import _scsi_read
        mock_run.return_value = MagicMock(returncode=1, stdout=b'')
        result = _scsi_read('/dev/sg0', b'\x01', 128)
        self.assertEqual(result, b'')

    @patch('trcc.device_scsi.os.unlink')
    @patch('trcc.device_scsi.require_sg_raw')
    @patch('trcc.device_scsi.subprocess.run')
    def test_scsi_write_success(self, mock_run, _, __):
        from trcc.device_scsi import _build_header, _scsi_write
        mock_run.return_value = MagicMock(returncode=0)
        header = _build_header(0x101F5, 100)
        result = _scsi_write('/dev/sg0', header, b'\x00' * 100)
        self.assertTrue(result)

    @patch('trcc.device_scsi.os.unlink')
    @patch('trcc.device_scsi.require_sg_raw')
    @patch('trcc.device_scsi.subprocess.run')
    def test_scsi_write_failure(self, mock_run, _, __):
        from trcc.device_scsi import _build_header, _scsi_write
        mock_run.return_value = MagicMock(returncode=1)
        header = _build_header(0x101F5, 100)
        result = _scsi_write('/dev/sg0', header, b'\x00' * 100)
        self.assertFalse(result)


# ── init_device ──────────────────────────────────────────────────────────────

class TestLCDDriverInitDevice(unittest.TestCase):

    def _make_driver(self):
        driver = LCDDriver.__new__(LCDDriver)
        driver.device_info = _mock_device()
        driver.device_path = '/dev/sg0'
        driver.implementation = _mock_implementation()
        driver.initialized = False
        return driver

    @patch('trcc.driver_lcd._scsi_write', return_value=True)
    @patch('trcc.driver_lcd._scsi_read', return_value=b'')
    def test_init_device_calls_poll_then_init(self, mock_read, mock_write):
        driver = self._make_driver()
        driver.init_device()
        mock_read.assert_called_once()
        mock_write.assert_called_once()
        self.assertTrue(driver.initialized)

    @patch('trcc.driver_lcd._scsi_write', return_value=True)
    @patch('trcc.driver_lcd._scsi_read', return_value=b'')
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
