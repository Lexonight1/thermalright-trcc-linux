"""Tests for scsi_device – SCSI frame chunking, header building, CRC."""

import binascii
import struct
import unittest
from unittest.mock import MagicMock, patch

from trcc.scsi_device import (
    _CHUNK_SIZE,
    _FRAME_CMD_BASE,
    _build_header,
    _crc32,
    _get_frame_chunks,
    _init_device,
    _initialized_devices,
    _scsi_read,
    _scsi_write,
    _send_frame,
    find_lcd_devices,
    send_image_to_device,
)


class TestCRC32(unittest.TestCase):
    """CRC32 against known values."""

    def test_empty(self):
        self.assertEqual(_crc32(b''), binascii.crc32(b'') & 0xFFFFFFFF)

    def test_known_value(self):
        self.assertEqual(_crc32(b'hello'), binascii.crc32(b'hello') & 0xFFFFFFFF)

    def test_unsigned(self):
        """Result is always unsigned 32-bit."""
        result = _crc32(b'\xff' * 100)
        self.assertGreaterEqual(result, 0)
        self.assertLess(result, 2**32)


class TestBuildHeader(unittest.TestCase):
    """20-byte SCSI command header: cmd(4) + zeros(8) + size(4) + crc32(4)."""

    def test_length(self):
        header = _build_header(0xF5, 0xE100)
        self.assertEqual(len(header), 20)

    def test_cmd_field(self):
        header = _build_header(0xF5, 0xE100)
        cmd = struct.unpack('<I', header[:4])[0]
        self.assertEqual(cmd, 0xF5)

    def test_zero_padding(self):
        header = _build_header(0xF5, 0xE100)
        self.assertEqual(header[4:12], b'\x00' * 8)

    def test_size_field(self):
        header = _build_header(0x1F5, 0xE100)
        size = struct.unpack('<I', header[12:16])[0]
        self.assertEqual(size, 0xE100)

    def test_crc_matches_payload(self):
        header = _build_header(0xF5, 0xE100)
        payload = header[:16]
        expected_crc = binascii.crc32(payload) & 0xFFFFFFFF
        actual_crc = struct.unpack('<I', header[16:20])[0]
        self.assertEqual(actual_crc, expected_crc)

    def test_different_cmds_different_headers(self):
        h1 = _build_header(0xF5, 0xE100)
        h2 = _build_header(0x1F5, 0xE100)
        self.assertNotEqual(h1, h2)


class TestGetFrameChunks(unittest.TestCase):
    """Frame chunk calculation for various resolutions."""

    def test_320x320_total_bytes(self):
        chunks = _get_frame_chunks(320, 320)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 320 * 320 * 2)  # 204,800

    def test_320x320_chunk_count(self):
        chunks = _get_frame_chunks(320, 320)
        self.assertEqual(len(chunks), 4)  # 3×64K + 8K

    def test_480x480_total_bytes(self):
        chunks = _get_frame_chunks(480, 480)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 480 * 480 * 2)  # 460,800

    def test_480x480_chunk_count(self):
        chunks = _get_frame_chunks(480, 480)
        self.assertEqual(len(chunks), 8)  # 7×64K + 2K

    def test_640x480_total_bytes(self):
        chunks = _get_frame_chunks(640, 480)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 640 * 480 * 2)  # 614,400

    def test_240x240_total_bytes(self):
        chunks = _get_frame_chunks(240, 240)
        total = sum(size for _, size in chunks)
        self.assertEqual(total, 240 * 240 * 2)  # 115,200

    def test_chunk_sizes_within_limit(self):
        """No chunk exceeds 64 KiB."""
        for w, h in [(320, 320), (480, 480), (640, 480)]:
            for _, size in _get_frame_chunks(w, h):
                self.assertLessEqual(size, _CHUNK_SIZE)

    def test_cmd_encodes_index(self):
        """Chunk index embedded in bits [27:24] above base command."""
        chunks = _get_frame_chunks(480, 480)
        for i, (cmd, _) in enumerate(chunks):
            expected = _FRAME_CMD_BASE | (i << 24)
            self.assertEqual(cmd, expected, f"Chunk {i}: {cmd:#x} != {expected:#x}")

    def test_last_chunk_may_be_smaller(self):
        chunks = _get_frame_chunks(320, 320)
        last_size = chunks[-1][1]
        self.assertEqual(last_size, 320 * 320 * 2 - 3 * _CHUNK_SIZE)  # 8192


# ── SCSI read/write ─────────────────────────────────────────────────────────

class TestScsiRead(unittest.TestCase):
    """Low-level SCSI READ via sg_raw."""

    @patch('trcc.scsi_device.subprocess.run')
    def test_success_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b'\xAA\xBB')
        result = _scsi_read('/dev/sg0', b'\x01\x02\x03', 256)
        self.assertEqual(result, b'\xAA\xBB')
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], 'sg_raw')
        self.assertIn('/dev/sg0', args)

    @patch('trcc.scsi_device.subprocess.run')
    def test_failure_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout=b'')
        result = _scsi_read('/dev/sg0', b'\x01', 128)
        self.assertEqual(result, b'')

    @patch('trcc.scsi_device.subprocess.run')
    def test_cdb_hex_encoding(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout=b'')
        _scsi_read('/dev/sg0', b'\xFF\x00\xAB', 100)
        args = mock_run.call_args[0][0]
        # CDB bytes should be hex-encoded in command
        self.assertIn('ff', args)
        self.assertIn('00', args)
        self.assertIn('ab', args)


class TestScsiWrite(unittest.TestCase):
    """Low-level SCSI WRITE via sg_raw with temp file."""

    @patch('trcc.scsi_device.os.unlink')
    @patch('trcc.scsi_device.subprocess.run')
    def test_success_returns_true(self, mock_run, mock_unlink):
        mock_run.return_value = MagicMock(returncode=0)
        header = _build_header(0x101F5, 0x10000)
        result = _scsi_write('/dev/sg0', header, b'\x00' * 100)
        self.assertTrue(result)

    @patch('trcc.scsi_device.os.unlink')
    @patch('trcc.scsi_device.subprocess.run')
    def test_failure_returns_false(self, mock_run, mock_unlink):
        mock_run.return_value = MagicMock(returncode=1)
        header = _build_header(0x101F5, 0x10000)
        result = _scsi_write('/dev/sg0', header, b'\x00' * 10)
        self.assertFalse(result)

    @patch('trcc.scsi_device.os.unlink')
    @patch('trcc.scsi_device.subprocess.run')
    def test_temp_file_cleaned_up(self, mock_run, mock_unlink):
        mock_run.return_value = MagicMock(returncode=0)
        header = _build_header(0x101F5, 100)
        _scsi_write('/dev/sg0', header, b'\x00' * 10)
        mock_unlink.assert_called_once()


# ── Init device ──────────────────────────────────────────────────────────────

class TestInitDevice(unittest.TestCase):

    @patch('trcc.scsi_device._scsi_write')
    @patch('trcc.scsi_device._scsi_read')
    def test_sends_poll_then_init(self, mock_read, mock_write):
        _init_device('/dev/sg0')
        mock_read.assert_called_once()
        mock_write.assert_called_once()
        # Poll read uses 0xE100 length
        read_args = mock_read.call_args
        self.assertEqual(read_args[0][2], 0xE100)
        # Init write sends 0xE100 bytes of zeros
        write_args = mock_write.call_args
        self.assertEqual(len(write_args[0][2]), 0xE100)


# ── Send frame ───────────────────────────────────────────────────────────────

class TestSendFrame(unittest.TestCase):

    @patch('trcc.scsi_device._scsi_write')
    def test_sends_all_chunks(self, mock_write):
        # 320x320 = 4 chunks
        data = b'\x00' * (320 * 320 * 2)
        _send_frame('/dev/sg0', data)
        self.assertEqual(mock_write.call_count, 4)

    @patch('trcc.scsi_device._scsi_write')
    def test_pads_short_data(self, mock_write):
        _send_frame('/dev/sg0', b'\x00' * 100)
        # Should still send all 4 chunks totaling 320*320*2 bytes
        total_sent = sum(len(c[0][2]) for c in mock_write.call_args_list)
        self.assertEqual(total_sent, 320 * 320 * 2)

    @patch('trcc.scsi_device._scsi_write')
    def test_custom_resolution(self, mock_write):
        data = b'\x00' * (480 * 480 * 2)
        _send_frame('/dev/sg0', data, 480, 480)
        self.assertEqual(mock_write.call_count, 8)  # 480x480 = 8 chunks


# ── find_lcd_devices ─────────────────────────────────────────────────────────

class TestFindLCDDevices(unittest.TestCase):

    @patch('trcc.lcd_driver.LCDDriver')
    @patch('trcc.device_detector.detect_devices')
    def test_returns_device_dicts(self, mock_detect, mock_driver_cls):
        dev = MagicMock()
        dev.scsi_device = '/dev/sg0'
        dev.vendor_name = 'Thermalright'
        dev.product_name = 'LCD'
        dev.model = 'USBLCD'
        dev.button_image = 'btn.png'
        dev.vid = 0x87CD
        dev.pid = 0x70DB
        dev.protocol = 'scsi'
        dev.device_type = 1
        mock_detect.return_value = [dev]

        mock_driver = MagicMock()
        mock_driver.implementation.resolution = (320, 320)
        mock_driver_cls.return_value = mock_driver

        devices = find_lcd_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]['name'], 'Thermalright LCD')
        self.assertEqual(devices[0]['path'], '/dev/sg0')
        self.assertEqual(devices[0]['resolution'], (320, 320))
        self.assertEqual(devices[0]['device_index'], 0)

    @patch('trcc.device_detector.detect_devices')
    def test_skips_devices_without_scsi(self, mock_detect):
        dev = MagicMock()
        dev.scsi_device = None
        dev.protocol = 'scsi'
        dev.device_type = 1
        mock_detect.return_value = [dev]
        self.assertEqual(find_lcd_devices(), [])

    @patch('trcc.lcd_driver.LCDDriver', side_effect=Exception('driver fail'))
    @patch('trcc.device_detector.detect_devices')
    def test_driver_error_uses_default_resolution(self, mock_detect, _):
        dev = MagicMock()
        dev.scsi_device = '/dev/sg0'
        dev.vendor_name = 'Test'
        dev.product_name = 'LCD'
        dev.model = 'X'
        dev.button_image = None
        dev.vid = 1
        dev.pid = 2
        dev.protocol = 'scsi'
        dev.device_type = 1
        mock_detect.return_value = [dev]

        devices = find_lcd_devices()
        self.assertEqual(devices[0]['resolution'], (320, 320))


# ── send_image_to_device ─────────────────────────────────────────────────────

class TestSendImageToDevice(unittest.TestCase):

    def setUp(self):
        _initialized_devices.clear()

    @patch('trcc.scsi_device._send_frame')
    @patch('trcc.scsi_device._init_device')
    def test_first_send_initializes(self, mock_init, mock_send):
        result = send_image_to_device('/dev/sg0', b'\x00' * 100, 320, 320)
        self.assertTrue(result)
        mock_init.assert_called_once_with('/dev/sg0')
        mock_send.assert_called_once()

    @patch('trcc.scsi_device._send_frame')
    @patch('trcc.scsi_device._init_device')
    def test_second_send_skips_init(self, mock_init, mock_send):
        send_image_to_device('/dev/sg0', b'\x00', 320, 320)
        send_image_to_device('/dev/sg0', b'\x00', 320, 320)
        mock_init.assert_called_once()  # Only once
        self.assertEqual(mock_send.call_count, 2)

    @patch('trcc.scsi_device._send_frame', side_effect=Exception('fail'))
    @patch('trcc.scsi_device._init_device')
    def test_error_returns_false_and_resets(self, mock_init, _):
        result = send_image_to_device('/dev/sg0', b'\x00', 320, 320)
        self.assertFalse(result)
        # Device should be removed from initialized set for re-init
        self.assertNotIn('/dev/sg0', _initialized_devices)

    @patch('trcc.scsi_device._send_frame')
    @patch('trcc.scsi_device._init_device', side_effect=Exception('init fail'))
    def test_init_error_returns_false(self, mock_init, _):
        result = send_image_to_device('/dev/sg0', b'\x00', 320, 320)
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
