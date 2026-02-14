"""Tests for trcc.services — core hexagon (pure Python, no Qt)."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from trcc.services.device import DeviceService
from trcc.services.image import ImageService
from trcc.services.media import MediaService
from trcc.services.overlay import OverlayService
from trcc.services.theme import ThemeData, ThemeService

# =============================================================================
# ImageService
# =============================================================================


class TestImageServiceRgb565(unittest.TestCase):
    """Test RGB565 conversion."""

    def test_pure_red(self):
        """Pure red pixel → R=31, G=0, B=0 → 0xF800."""
        img = Image.new('RGB', (1, 1), (255, 0, 0))
        data = ImageService.to_rgb565(img, '>')
        self.assertEqual(len(data), 2)
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0xF800)

    def test_pure_green(self):
        """Pure green → R=0, G=63, B=0 → 0x07E0."""
        img = Image.new('RGB', (1, 1), (0, 255, 0))
        data = ImageService.to_rgb565(img, '>')
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0x07E0)

    def test_pure_blue(self):
        """Pure blue → R=0, G=0, B=31 → 0x001F."""
        img = Image.new('RGB', (1, 1), (0, 0, 255))
        data = ImageService.to_rgb565(img, '>')
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0x001F)

    def test_white(self):
        """White → all bits set → 0xFFFF."""
        img = Image.new('RGB', (1, 1), (255, 255, 255))
        data = ImageService.to_rgb565(img, '>')
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0xFFFF)

    def test_black(self):
        """Black → 0x0000."""
        img = Image.new('RGB', (1, 1), (0, 0, 0))
        data = ImageService.to_rgb565(img, '>')
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0x0000)

    def test_size_matches_pixel_count(self):
        """Output size = width * height * 2 bytes."""
        img = Image.new('RGB', (10, 20))
        data = ImageService.to_rgb565(img)
        self.assertEqual(len(data), 10 * 20 * 2)

    def test_little_endian(self):
        """Little-endian byte order swaps bytes."""
        img = Image.new('RGB', (1, 1), (255, 0, 0))
        be = ImageService.to_rgb565(img, '>')
        le = ImageService.to_rgb565(img, '<')
        self.assertEqual(be[0], le[1])
        self.assertEqual(be[1], le[0])

    def test_rgba_input(self):
        """RGBA images are converted to RGB before processing."""
        img = Image.new('RGBA', (2, 2), (255, 0, 0, 128))
        data = ImageService.to_rgb565(img)
        self.assertEqual(len(data), 2 * 2 * 2)


class TestImageServiceRotation(unittest.TestCase):
    """Test image rotation."""

    def test_no_rotation(self):
        img = Image.new('RGB', (4, 4), (255, 0, 0))
        result = ImageService.apply_rotation(img, 0)
        self.assertEqual(result.size, (4, 4))

    def test_90_rotation(self):
        """90° rotation transposes dimensions on non-square."""
        img = Image.new('RGB', (4, 8))
        result = ImageService.apply_rotation(img, 90)
        self.assertEqual(result.size, (8, 4))

    def test_180_rotation(self):
        img = Image.new('RGB', (4, 8))
        result = ImageService.apply_rotation(img, 180)
        self.assertEqual(result.size, (4, 8))

    def test_270_rotation(self):
        img = Image.new('RGB', (4, 8))
        result = ImageService.apply_rotation(img, 270)
        self.assertEqual(result.size, (8, 4))


class TestImageServiceBrightness(unittest.TestCase):
    """Test brightness adjustment."""

    def test_100_percent_unchanged(self):
        img = Image.new('RGB', (2, 2), (200, 100, 50))
        result = ImageService.apply_brightness(img, 100)
        self.assertIs(result, img)  # Same object, no processing

    def test_50_percent_darker(self):
        img = Image.new('RGB', (1, 1), (200, 100, 50))
        result = ImageService.apply_brightness(img, 50)
        px = result.getpixel((0, 0))
        self.assertLess(px[0], 200)
        self.assertGreater(px[0], 0)

    def test_0_percent_black(self):
        img = Image.new('RGB', (1, 1), (200, 100, 50))
        result = ImageService.apply_brightness(img, 0)
        px = result.getpixel((0, 0))
        self.assertEqual(px, (0, 0, 0))


class TestImageServiceByteOrder(unittest.TestCase):
    """Test byte order determination."""

    def test_320x320_scsi_big_endian(self):
        self.assertEqual(ImageService.byte_order_for('scsi', (320, 320)), '>')

    def test_480x480_scsi_little_endian(self):
        self.assertEqual(ImageService.byte_order_for('scsi', (480, 480)), '<')

    def test_hid_always_big_endian(self):
        self.assertEqual(ImageService.byte_order_for('hid', (480, 480)), '>')

    def test_240x240_scsi_little_endian(self):
        self.assertEqual(ImageService.byte_order_for('scsi', (240, 240)), '<')


class TestImageServiceResize(unittest.TestCase):
    """Test image resize."""

    def test_resize(self):
        img = Image.new('RGB', (100, 100))
        result = ImageService.resize(img, 50, 50)
        self.assertEqual(result.size, (50, 50))


class TestImageServiceToJpeg(unittest.TestCase):
    """Test JPEG encoding (C# CompressionImage pattern)."""

    def test_returns_valid_jpeg(self):
        img = Image.new('RGB', (100, 100), (255, 0, 0))
        data = ImageService.to_jpeg(img)
        self.assertTrue(data[:2] == b'\xff\xd8')  # JPEG SOI marker

    def test_quality_reduces_until_under_max(self):
        """Large images should reduce quality until under max_size."""
        img = Image.new('RGB', (480, 480), (128, 64, 200))
        data = ImageService.to_jpeg(img, quality=95, max_size=450_000)
        self.assertLess(len(data), 450_000)

    def test_tiny_max_size_still_returns_data(self):
        """Even with very small max_size, fallback to quality=5."""
        img = Image.new('RGB', (100, 100), (255, 128, 0))
        data = ImageService.to_jpeg(img, quality=95, max_size=1)
        self.assertTrue(len(data) > 0)
        self.assertTrue(data[:2] == b'\xff\xd8')

    def test_rgba_input_converted(self):
        """RGBA images should be converted to RGB before encoding."""
        img = Image.new('RGBA', (50, 50), (255, 0, 0, 128))
        data = ImageService.to_jpeg(img)
        self.assertTrue(data[:2] == b'\xff\xd8')

    def test_default_quality_95(self):
        """Default quality produces smaller output than raw RGB565."""
        img = Image.new('RGB', (320, 320), (100, 150, 200))
        jpeg = ImageService.to_jpeg(img)
        rgb565 = ImageService.to_rgb565(img)
        self.assertLess(len(jpeg), len(rgb565))


class TestDeviceServiceSendPilBulk(unittest.TestCase):
    """Test that send_pil routes bulk devices through JPEG encoding."""

    def test_bulk_sends_jpeg(self):
        """Bulk protocol → ImageService.to_jpeg() path."""
        from trcc.core.models import DeviceInfo
        svc = DeviceService()
        dev = DeviceInfo(name='bulk', path='bulk:87ad:70db', protocol='bulk')
        svc.select(dev)

        with patch.object(svc, 'send_rgb565', return_value=True) as mock_send:
            img = Image.new('RGB', (480, 480), (255, 0, 0))
            result = svc.send_pil(img, 480, 480)

        self.assertTrue(result)
        call_data = mock_send.call_args[0][0]
        self.assertTrue(call_data[:2] == b'\xff\xd8')  # JPEG data

    def test_scsi_sends_rgb565(self):
        """SCSI protocol → ImageService.to_rgb565() path (not JPEG)."""
        from trcc.core.models import DeviceInfo
        svc = DeviceService()
        dev = DeviceInfo(name='scsi', path='/dev/sg0', protocol='scsi',
                         resolution=(320, 320))
        svc.select(dev)

        with patch.object(svc, 'send_rgb565', return_value=True) as mock_send:
            img = Image.new('RGB', (320, 320), (255, 0, 0))
            result = svc.send_pil(img, 320, 320)

        self.assertTrue(result)
        call_data = mock_send.call_args[0][0]
        # RGB565: 320*320*2 = 204800 bytes, not JPEG
        self.assertEqual(len(call_data), 320 * 320 * 2)


# =============================================================================
# DeviceService
# =============================================================================


class TestDeviceService(unittest.TestCase):
    """Test device detection and selection."""

    def test_initial_state(self):
        svc = DeviceService()
        self.assertIsNone(svc.selected)
        self.assertEqual(svc.devices, [])
        self.assertFalse(svc.is_busy)

    @patch('trcc.services.device.DeviceService.detect')
    def test_detect_returns_list(self, mock_detect):
        mock_detect.return_value = []
        svc = DeviceService()
        result = svc.detect()
        self.assertIsInstance(result, list)

    def test_select(self):
        from trcc.core.models import DeviceInfo
        svc = DeviceService()
        dev = DeviceInfo(name='test', path='/dev/sg0')
        svc.select(dev)
        self.assertEqual(svc.selected, dev)

    def test_is_busy_default_false(self):
        svc = DeviceService()
        self.assertFalse(svc.is_busy)


# =============================================================================
# MediaService
# =============================================================================


class TestMediaService(unittest.TestCase):
    """Test media playback service."""

    def test_initial_state(self):
        svc = MediaService()
        self.assertFalse(svc.is_playing)
        self.assertFalse(svc.has_frames)
        self.assertIsNone(svc.source_path)
        self.assertEqual(svc.progress, 0.0)

    def test_set_target_size(self):
        svc = MediaService()
        svc.set_target_size(480, 480)
        self.assertEqual(svc._target_size, (480, 480))

    def test_stop_on_fresh(self):
        """stop() on fresh service doesn't raise."""
        svc = MediaService()
        svc.stop()
        self.assertFalse(svc.is_playing)

    def test_toggle_without_player(self):
        """toggle() without loaded media doesn't crash."""
        svc = MediaService()
        svc.toggle()
        self.assertFalse(svc.is_playing)

    def test_tick_when_not_playing(self):
        svc = MediaService()
        frame, should_send, progress = svc.tick()
        self.assertIsNone(frame)
        self.assertFalse(should_send)
        self.assertIsNone(progress)


# =============================================================================
# OverlayService
# =============================================================================


class TestOverlayService(unittest.TestCase):
    """Test overlay rendering service."""

    def test_initial_state(self):
        svc = OverlayService()
        self.assertFalse(svc.enabled)
        self.assertIsNone(svc.background)
        self.assertIsNone(svc.get_dc_data())

    def test_enable_disable(self):
        svc = OverlayService()
        svc.enabled = True
        self.assertTrue(svc.enabled)
        svc.enabled = False
        self.assertFalse(svc.enabled)

    def test_set_background(self):
        svc = OverlayService()
        img = Image.new('RGB', (320, 320))
        svc.set_background(img)
        self.assertIs(svc.background, img)

    def test_set_resolution(self):
        svc = OverlayService(320, 320)
        svc.set_resolution(480, 480)
        self.assertEqual(svc.width, 480)
        self.assertEqual(svc.height, 480)

    def test_render_no_config_returns_background(self):
        """With no config/mask set, render returns background as-is (fast path)."""
        svc = OverlayService()
        img = Image.new('RGB', (320, 320), (255, 0, 0))
        svc.set_background(img)
        result = svc.render()
        self.assertIs(result, img)

    def test_dc_data_round_trip(self):
        svc = OverlayService()
        data = {'display_options': {'ui_mode': 1}}
        svc.set_dc_data(data)
        self.assertEqual(svc.get_dc_data(), data)
        svc.clear_dc_data()
        self.assertIsNone(svc.get_dc_data())


# =============================================================================
# ThemeService
# =============================================================================


class TestThemeService(unittest.TestCase):
    """Test theme discovery and loading."""

    def test_categories(self):
        self.assertIn('all', ThemeService.CATEGORIES)
        self.assertIn('a', ThemeService.CATEGORIES)

    def test_discover_local_empty_dir(self, ):
        """Empty/missing directory returns empty list."""
        result = ThemeService.discover_local(Path('/nonexistent'))
        self.assertEqual(result, [])

    def test_discover_cloud_empty_dir(self):
        result = ThemeService.discover_cloud(Path('/nonexistent'))
        self.assertEqual(result, [])

    def test_passes_filter_all(self):
        from trcc.core.models import ThemeInfo
        theme = ThemeInfo(name='test')
        self.assertTrue(ThemeService._passes_filter(theme, 'all'))

    def test_passes_filter_user(self):
        from trcc.core.models import ThemeInfo
        theme = ThemeInfo(name='Custom_foo')
        self.assertTrue(ThemeService._passes_filter(theme, 'user'))

    def test_passes_filter_default_excludes_custom(self):
        from trcc.core.models import ThemeInfo
        theme = ThemeInfo(name='Custom_foo')
        self.assertFalse(ThemeService._passes_filter(theme, 'default'))


class TestThemeData(unittest.TestCase):
    """Test ThemeData dataclass."""

    def test_defaults(self):
        data = ThemeData()
        self.assertIsNone(data.background)
        self.assertIsNone(data.animation_path)
        self.assertFalse(data.is_animated)
        self.assertIsNone(data.mask)
        self.assertIsNone(data.mask_position)


# =============================================================================
# Services __init__ exports
# =============================================================================


class TestServicesInit(unittest.TestCase):
    """Test services package exports."""

    def test_all_exports(self):
        from trcc import services
        self.assertTrue(hasattr(services, 'ImageService'))
        self.assertTrue(hasattr(services, 'DeviceService'))
        self.assertTrue(hasattr(services, 'DisplayService'))
        self.assertTrue(hasattr(services, 'MediaService'))
        self.assertTrue(hasattr(services, 'OverlayService'))
        self.assertTrue(hasattr(services, 'ThemeService'))

    def test_theme_data_in_models(self):
        """ThemeData is a DTO — lives in models, not services."""
        from trcc.core.models import ThemeData
        self.assertIsNotNone(ThemeData)
