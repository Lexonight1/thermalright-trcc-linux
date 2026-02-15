"""Tests for LCDDeviceConfig (core/models.py) and related service methods."""

import struct
import unittest
from unittest.mock import patch

from trcc.core.models import IMPL_NAMES, LCDDeviceConfig
from trcc.services.image import ImageService


class TestRGBToBytes(unittest.TestCase):
    """RGB565 big-endian conversion via ImageService."""

    def test_white(self):
        result = ImageService.rgb_to_bytes(255, 255, 255, '>')
        self.assertEqual(result, struct.pack('>H', 0xFFFF))

    def test_black(self):
        result = ImageService.rgb_to_bytes(0, 0, 0, '>')
        self.assertEqual(result, struct.pack('>H', 0x0000))

    def test_pure_red(self):
        result = ImageService.rgb_to_bytes(255, 0, 0, '>')
        self.assertEqual(result, struct.pack('>H', 0xF800))

    def test_pure_green(self):
        result = ImageService.rgb_to_bytes(0, 255, 0, '>')
        self.assertEqual(result, struct.pack('>H', 0x07E0))

    def test_pure_blue(self):
        result = ImageService.rgb_to_bytes(0, 0, 255, '>')
        self.assertEqual(result, struct.pack('>H', 0x001F))

    def test_output_is_two_bytes(self):
        result = ImageService.rgb_to_bytes(128, 64, 32, '>')
        self.assertEqual(len(result), 2)


class TestResolution(unittest.TestCase):
    """Resolution defaults and manual setting on LCDDeviceConfig."""

    def test_default_320x320(self):
        cfg = LCDDeviceConfig()
        self.assertEqual(cfg.resolution, (320, 320))

    def test_set_resolution(self):
        cfg = LCDDeviceConfig()
        cfg.width = 480
        cfg.height = 480
        cfg.resolution_detected = True
        self.assertEqual(cfg.resolution, (480, 480))
        self.assertTrue(cfg.resolution_detected)

    def test_resolution_not_detected_by_default(self):
        cfg = LCDDeviceConfig()
        self.assertFalse(cfg.resolution_detected)


class TestCommands(unittest.TestCase):
    """Default command tuples on LCDDeviceConfig."""

    def setUp(self):
        self.cfg = LCDDeviceConfig()

    def test_poll_command(self):
        cmd, size = self.cfg.poll_command
        self.assertEqual(cmd, 0xF5)
        self.assertEqual(size, 0xE100)

    def test_init_command(self):
        cmd, size = self.cfg.init_command
        self.assertEqual(cmd, 0x1F5)
        self.assertEqual(size, 0xE100)

    def test_frame_chunks_count(self):
        from trcc.device_scsi import ScsiDevice
        chunks = ScsiDevice._get_frame_chunks(self.cfg.width, self.cfg.height)
        self.assertEqual(len(chunks), 4)

    def test_frame_chunks_total_size(self):
        """Total frame data = sum of chunk sizes."""
        from trcc.device_scsi import ScsiDevice
        total = sum(size for _, size in ScsiDevice._get_frame_chunks(320, 320))
        # 3 * 0x10000 + 0x2000 = 196608 + 8192 = 204800 = 320*320*2
        self.assertEqual(total, 320 * 320 * 2)

    def test_no_init_per_frame(self):
        self.assertFalse(self.cfg.init_per_frame)

    def test_zero_delays(self):
        self.assertEqual(self.cfg.init_delay, 0.0)
        self.assertEqual(self.cfg.frame_delay, 0.0)


class TestRegistry(unittest.TestCase):
    """LCDDeviceConfig.from_key() and IMPL_NAMES registry."""

    def test_get_thermalright(self):
        cfg = LCDDeviceConfig.from_key('thermalright_lcd_v1')
        self.assertIsInstance(cfg, LCDDeviceConfig)
        self.assertIn('Thermalright', cfg.name)

    def test_get_ali_corp(self):
        cfg = LCDDeviceConfig.from_key('ali_corp_lcd_v1')
        self.assertIsInstance(cfg, LCDDeviceConfig)
        self.assertIn('ALi Corp', cfg.name)

    def test_get_generic(self):
        cfg = LCDDeviceConfig.from_key('generic')
        self.assertIsInstance(cfg, LCDDeviceConfig)
        self.assertEqual(cfg.name, 'Generic LCD')

    def test_unknown_falls_back_to_generic(self):
        cfg = LCDDeviceConfig.from_key('nonexistent_device')
        self.assertEqual(cfg.name, 'Generic LCD')

    def test_all_implementations_are_lcd_config(self):
        for name in IMPL_NAMES:
            cfg = LCDDeviceConfig.from_key(name)
            self.assertIsInstance(cfg, LCDDeviceConfig)

    def test_list_all(self):
        result = LCDDeviceConfig.list_all()
        self.assertEqual(len(result), len(IMPL_NAMES))
        names = {item['name'] for item in result}
        self.assertEqual(names, set(IMPL_NAMES.keys()))


class TestConcreteDevices(unittest.TestCase):
    """Concrete device names."""

    def test_thermalright_name(self):
        self.assertIn('Thermalright', LCDDeviceConfig.from_key('thermalright_lcd_v1').name)

    def test_ali_corp_name(self):
        self.assertIn('ALi Corp', LCDDeviceConfig.from_key('ali_corp_lcd_v1').name)

    def test_generic_name(self):
        self.assertEqual(LCDDeviceConfig.from_key('generic').name, 'Generic LCD')

    def test_pixel_format(self):
        for key in IMPL_NAMES:
            self.assertEqual(LCDDeviceConfig.from_key(key).pixel_format, 'RGB565')


class TestDetectResolution(unittest.TestCase):
    """Resolution auto-detection via DeviceService (SCSI poll byte[0] → fbl_to_resolution)."""

    def test_detect_success_480x480(self):
        """Poll response byte[0]=72 → FBL 72 → 480x480."""
        from trcc.services.device import DeviceService
        cfg = LCDDeviceConfig()
        poll_response = bytes([72]) + b'\x00' * 0xE0FF
        with patch('trcc.device_scsi.ScsiDevice._scsi_read', return_value=poll_response):
            result = DeviceService.detect_lcd_resolution(cfg, '/dev/sg0')
        self.assertTrue(result)
        self.assertEqual(cfg.width, 480)
        self.assertEqual(cfg.height, 480)
        self.assertEqual(cfg.fbl, 72)
        self.assertTrue(cfg.resolution_detected)

    def test_detect_success_320x320(self):
        """Poll response byte[0]=100 → FBL 100 → 320x320."""
        from trcc.services.device import DeviceService
        cfg = LCDDeviceConfig()
        poll_response = bytes([100]) + b'\x00' * 0xE0FF
        with patch('trcc.device_scsi.ScsiDevice._scsi_read', return_value=poll_response):
            result = DeviceService.detect_lcd_resolution(cfg, '/dev/sg0')
        self.assertTrue(result)
        self.assertEqual(cfg.width, 320)
        self.assertEqual(cfg.height, 320)
        self.assertEqual(cfg.fbl, 100)

    def test_detect_success_240x240(self):
        """Poll response byte[0]=36 → FBL 36 → 240x240."""
        from trcc.services.device import DeviceService
        cfg = LCDDeviceConfig()
        poll_response = bytes([36]) + b'\x00' * 0xE0FF
        with patch('trcc.device_scsi.ScsiDevice._scsi_read', return_value=poll_response):
            result = DeviceService.detect_lcd_resolution(cfg, '/dev/sg0')
        self.assertTrue(result)
        self.assertEqual(cfg.width, 240)
        self.assertEqual(cfg.height, 240)

    def test_detect_empty_response(self):
        """Empty poll response → returns False."""
        from trcc.services.device import DeviceService
        cfg = LCDDeviceConfig()
        with patch('trcc.device_scsi.ScsiDevice._scsi_read', return_value=b''):
            result = DeviceService.detect_lcd_resolution(cfg, '/dev/sg0')
        self.assertFalse(result)

    def test_detect_scsi_error(self):
        """SCSI read exception → returns False."""
        from trcc.services.device import DeviceService
        cfg = LCDDeviceConfig()
        with patch('trcc.device_scsi.ScsiDevice._scsi_read', side_effect=OSError("sg_raw fail")):
            result = DeviceService.detect_lcd_resolution(cfg, '/dev/sg0')
        self.assertFalse(result)

    def test_fbl_defaults_to_none(self):
        cfg = LCDDeviceConfig()
        self.assertIsNone(cfg.fbl)


class TestDetectResolutionEdge(unittest.TestCase):

    def test_detect_verbose_success(self):
        """Verbose mode logs resolution on success."""
        from trcc.services.device import DeviceService
        cfg = LCDDeviceConfig()
        poll_response = bytes([72]) + b'\x00' * 0xE0FF
        with patch('trcc.device_scsi.ScsiDevice._scsi_read', return_value=poll_response):
            result = DeviceService.detect_lcd_resolution(cfg, '/dev/sg0', verbose=True)
        self.assertTrue(result)
        self.assertEqual(cfg.width, 480)
        self.assertEqual(cfg.height, 480)

    def test_detect_verbose_failure(self):
        """Verbose mode logs warning on failure."""
        from trcc.services.device import DeviceService
        cfg = LCDDeviceConfig()
        with patch('trcc.device_scsi.ScsiDevice._scsi_read', side_effect=OSError("fail")):
            result = DeviceService.detect_lcd_resolution(cfg, '/dev/sg0', verbose=True)
        self.assertFalse(result)
        self.assertEqual(cfg.width, 320)  # default unchanged


if __name__ == '__main__':
    unittest.main()
