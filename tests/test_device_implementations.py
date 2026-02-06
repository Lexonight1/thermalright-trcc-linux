"""Tests for device_implementations – LCD device abstraction and registry."""

import struct
import unittest

from trcc.device_implementations import (
    AliCorpLCDV1,
    GenericLCD,
    IMPLEMENTATIONS,
    LCDDeviceImplementation,
    ThermalrightLCDV1,
    get_implementation,
    list_implementations,
)


class TestRGBToBytes(unittest.TestCase):
    """RGB565 big-endian conversion."""

    def setUp(self):
        self.impl = GenericLCD()

    def test_white(self):
        result = self.impl.rgb_to_bytes(255, 255, 255)
        # RGB565: R=0x1F, G=0x3F, B=0x1F → 0xFFFF
        self.assertEqual(result, struct.pack('>H', 0xFFFF))

    def test_black(self):
        result = self.impl.rgb_to_bytes(0, 0, 0)
        self.assertEqual(result, struct.pack('>H', 0x0000))

    def test_pure_red(self):
        result = self.impl.rgb_to_bytes(255, 0, 0)
        # R=0xF8<<8 = 0xF800
        self.assertEqual(result, struct.pack('>H', 0xF800))

    def test_pure_green(self):
        result = self.impl.rgb_to_bytes(0, 255, 0)
        # G=0xFC<<3 = 0x07E0
        self.assertEqual(result, struct.pack('>H', 0x07E0))

    def test_pure_blue(self):
        result = self.impl.rgb_to_bytes(0, 0, 255)
        # B=255>>3 = 0x1F
        self.assertEqual(result, struct.pack('>H', 0x001F))

    def test_output_is_two_bytes(self):
        result = self.impl.rgb_to_bytes(128, 64, 32)
        self.assertEqual(len(result), 2)


class TestResolution(unittest.TestCase):
    """Resolution defaults and manual setting."""

    def test_default_320x320(self):
        impl = GenericLCD()
        self.assertEqual(impl.resolution, (320, 320))

    def test_set_resolution(self):
        impl = GenericLCD()
        impl.set_resolution(480, 480)
        self.assertEqual(impl.resolution, (480, 480))
        self.assertTrue(impl._resolution_detected)

    def test_set_resolution_marks_detected(self):
        impl = GenericLCD()
        self.assertFalse(impl._resolution_detected)
        impl.set_resolution(640, 480)
        self.assertTrue(impl._resolution_detected)


class TestCommands(unittest.TestCase):
    """Default command tuples."""

    def setUp(self):
        self.impl = GenericLCD()

    def test_poll_command(self):
        cmd, size = self.impl.get_poll_command()
        self.assertEqual(cmd, 0xF5)
        self.assertEqual(size, 0xE100)

    def test_init_command(self):
        cmd, size = self.impl.get_init_command()
        self.assertEqual(cmd, 0x1F5)
        self.assertEqual(size, 0xE100)

    def test_frame_chunks_count(self):
        chunks = self.impl.get_frame_chunks()
        self.assertEqual(len(chunks), 4)

    def test_frame_chunks_total_size(self):
        """Total frame data = sum of chunk sizes."""
        total = sum(size for _, size in self.impl.get_frame_chunks())
        # 3 * 0x10000 + 0x2000 = 196608 + 8192 = 204800 = 320*320*2
        self.assertEqual(total, 320 * 320 * 2)

    def test_no_init_per_frame(self):
        self.assertFalse(self.impl.needs_init_per_frame())

    def test_zero_delays(self):
        self.assertEqual(self.impl.get_init_delay(), 0.0)
        self.assertEqual(self.impl.get_frame_delay(), 0.0)


class TestRegistry(unittest.TestCase):
    """Implementation registry and lookup."""

    def test_get_thermalright(self):
        impl = get_implementation('thermalright_lcd_v1')
        self.assertIsInstance(impl, ThermalrightLCDV1)

    def test_get_ali_corp(self):
        impl = get_implementation('ali_corp_lcd_v1')
        self.assertIsInstance(impl, AliCorpLCDV1)

    def test_get_generic(self):
        impl = get_implementation('generic')
        self.assertIsInstance(impl, GenericLCD)

    def test_unknown_falls_back_to_generic(self):
        impl = get_implementation('nonexistent_device')
        self.assertIsInstance(impl, GenericLCD)

    def test_all_implementations_are_lcd(self):
        for name, cls in IMPLEMENTATIONS.items():
            inst = cls()
            self.assertIsInstance(inst, LCDDeviceImplementation)

    def test_list_implementations(self):
        result = list_implementations()
        self.assertEqual(len(result), len(IMPLEMENTATIONS))
        names = {item['name'] for item in result}
        self.assertEqual(names, set(IMPLEMENTATIONS.keys()))


class TestConcreteDevices(unittest.TestCase):
    """Concrete device names and inheritance."""

    def test_thermalright_name(self):
        self.assertIn('Thermalright', ThermalrightLCDV1().name)

    def test_ali_corp_name(self):
        self.assertIn('ALi Corp', AliCorpLCDV1().name)

    def test_generic_name(self):
        self.assertEqual(GenericLCD().name, 'Generic LCD')

    def test_pixel_format(self):
        for cls in [ThermalrightLCDV1, AliCorpLCDV1, GenericLCD]:
            self.assertEqual(cls().pixel_format, 'RGB565')


if __name__ == '__main__':
    unittest.main()
