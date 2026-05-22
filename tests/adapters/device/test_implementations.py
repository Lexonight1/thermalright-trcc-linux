"""Tests for LCDDeviceConfig (core/models.py) and related service methods."""

import struct
import unittest

from trcc.legacy.core.encoding import rgb_to_bytes
from trcc.legacy.core.models import IMPL_NAMES, LCDDeviceConfig


class TestRGBToBytes(unittest.TestCase):
    """RGB565 big-endian conversion."""

    def test_white(self):
        result = rgb_to_bytes(255, 255, 255, '>')
        self.assertEqual(result, struct.pack('>H', 0xFFFF))

    def test_black(self):
        result = rgb_to_bytes(0, 0, 0, '>')
        self.assertEqual(result, struct.pack('>H', 0x0000))

    def test_pure_red(self):
        result = rgb_to_bytes(255, 0, 0, '>')
        self.assertEqual(result, struct.pack('>H', 0xF800))

    def test_pure_green(self):
        result = rgb_to_bytes(0, 255, 0, '>')
        self.assertEqual(result, struct.pack('>H', 0x07E0))

    def test_pure_blue(self):
        result = rgb_to_bytes(0, 0, 255, '>')
        self.assertEqual(result, struct.pack('>H', 0x001F))

    def test_output_is_two_bytes(self):
        result = rgb_to_bytes(128, 64, 32, '>')
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
        from trcc.legacy.adapters.device.scsi import ScsiDevice
        chunks = ScsiDevice._get_frame_chunks(self.cfg.width, self.cfg.height)
        self.assertEqual(len(chunks), 4)

    def test_frame_chunks_total_size(self):
        """Total frame data = sum of chunk sizes."""
        from trcc.legacy.adapters.device.scsi import ScsiDevice
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




if __name__ == '__main__':
    unittest.main()
