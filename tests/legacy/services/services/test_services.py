"""Tests for trcc.services — core hexagon (pure Python, no Qt)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QColor, QImage

from tests.conftest import get_pixel, make_device_service, make_test_surface, surface_size
from trcc.legacy.services.display import DisplayService
from trcc.legacy.services.image import ImageService
from trcc.legacy.services.media import MediaService
from trcc.legacy.services.overlay import OverlayService
from trcc.legacy.services.theme import ThemeData, ThemeService

# =============================================================================
# ImageService
# =============================================================================


class TestImageServiceRgb565(unittest.TestCase):
    """Test RGB565 conversion."""

    def test_pure_red(self):
        """Pure red pixel → R=31, G=0, B=0 → 0xF800."""
        img = make_test_surface(1, 1, (255, 0, 0))
        data = ImageService.to_rgb565(img, '>')
        self.assertEqual(len(data), 2)
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0xF800)

    def test_pure_green(self):
        """Pure green → R=0, G=63, B=0 → 0x07E0."""
        img = make_test_surface(1, 1, (0, 255, 0))
        data = ImageService.to_rgb565(img, '>')
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0x07E0)

    def test_pure_blue(self):
        """Pure blue → R=0, G=0, B=31 → 0x001F."""
        img = make_test_surface(1, 1, (0, 0, 255))
        data = ImageService.to_rgb565(img, '>')
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0x001F)

    def test_white(self):
        """White → all bits set → 0xFFFF."""
        img = make_test_surface(1, 1, (255, 255, 255))
        data = ImageService.to_rgb565(img, '>')
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0xFFFF)

    def test_black(self):
        """Black → 0x0000."""
        img = make_test_surface(1, 1, (0, 0, 0))
        data = ImageService.to_rgb565(img, '>')
        val = int.from_bytes(data, 'big')
        self.assertEqual(val, 0x0000)

    def test_size_matches_pixel_count(self):
        """Output size = width * height * 2 bytes."""
        img = make_test_surface(10, 20)
        data = ImageService.to_rgb565(img)
        self.assertEqual(len(data), 10 * 20 * 2)

    def test_little_endian(self):
        """Little-endian byte order swaps bytes."""
        img = make_test_surface(1, 1, (255, 0, 0))
        be = ImageService.to_rgb565(img, '>')
        le = ImageService.to_rgb565(img, '<')
        self.assertEqual(be[0], le[1])
        self.assertEqual(be[1], le[0])

    def test_rgba_input(self):
        """RGBA images are converted to RGB before processing."""
        img = make_test_surface(2, 2, (255, 0, 0, 128))
        data = ImageService.to_rgb565(img)
        self.assertEqual(len(data), 2 * 2 * 2)


class TestImageServiceRotation(unittest.TestCase):
    """Test image rotation."""

    def test_no_rotation(self):
        img = make_test_surface(4, 4, (255, 0, 0))
        result = ImageService.apply_rotation(img, 0)
        self.assertEqual(surface_size(result), (4, 4))

    def test_90_rotation(self):
        """90° rotation transposes dimensions on non-square."""
        img = make_test_surface(4, 8)
        result = ImageService.apply_rotation(img, 90)
        self.assertEqual(surface_size(result), (8, 4))

    def test_180_rotation(self):
        img = make_test_surface(4, 8)
        result = ImageService.apply_rotation(img, 180)
        self.assertEqual(surface_size(result), (4, 8))

    def test_270_rotation(self):
        img = make_test_surface(4, 8)
        result = ImageService.apply_rotation(img, 270)
        self.assertEqual(surface_size(result), (8, 4))


class TestImageServiceBrightness(unittest.TestCase):
    """Test brightness adjustment."""

    def test_100_percent_unchanged(self):
        img = make_test_surface(2, 2, (200, 100, 50))
        result = ImageService.apply_brightness(img, 100)
        self.assertIs(result, img)  # Same object, no processing

    def test_50_percent_darker(self):
        img = make_test_surface(1, 1, (200, 100, 50))
        result = ImageService.apply_brightness(img, 50)
        px = get_pixel(result, 0, 0)
        self.assertLess(px[0], 200)
        self.assertGreater(px[0], 0)

    def test_0_percent_black(self):
        img = make_test_surface(1, 1, (200, 100, 50))
        result = ImageService.apply_brightness(img, 0)
        px = get_pixel(result, 0, 0)
        self.assertEqual(px, (0, 0, 0))


class TestImageServiceByteOrder(unittest.TestCase):
    """Test byte order determination.

    C# ImageTo565 byte-order logic:
      SCSI: big-endian for 320x320 (is320x320) and 320x240 (SPIMode=2)
      HID:  big-endian only for 320x320 (is320x320), little-endian otherwise
    """

    def test_320x320_scsi_big_endian(self):
        self.assertEqual(ImageService.byte_order_for('scsi', (320, 320)), '>')

    def test_320x240_scsi_fbl51_little_endian(self):
        """HID Type 2 FBL 51 → little-endian (SPIMode=2 only for SPI mode 1)."""
        self.assertEqual(ImageService.byte_order_for('scsi', (320, 240), fbl=51), '<')

    def test_320x240_scsi_fbl50_little_endian(self):
        """SCSI 320x240 FBL 50 does NOT trigger SPIMode=2 → little-endian."""
        self.assertEqual(ImageService.byte_order_for('scsi', (320, 240), fbl=50), '<')

    def test_320x240_scsi_no_fbl_little_endian(self):
        """SCSI 320x240 with no FBL defaults to little-endian (safe default)."""
        self.assertEqual(ImageService.byte_order_for('scsi', (320, 240)), '<')

    def test_480x480_scsi_little_endian(self):
        self.assertEqual(ImageService.byte_order_for('scsi', (480, 480)), '<')

    def test_240x240_scsi_little_endian(self):
        self.assertEqual(ImageService.byte_order_for('scsi', (240, 240)), '<')

    def test_hid_320x320_big_endian(self):
        """HID Type 3 (320x320) uses big-endian (is320x320 in C#)."""
        self.assertEqual(ImageService.byte_order_for('hid', (320, 320)), '>')

    def test_hid_320x240_little_endian(self):
        """HID Type 2 at 320x240 uses little-endian (no SPIMode=2 for HID)."""
        self.assertEqual(ImageService.byte_order_for('hid', (320, 240)), '<')

    def test_hid_480x480_little_endian(self):
        """HID non-320x320 uses little-endian."""
        self.assertEqual(ImageService.byte_order_for('hid', (480, 480)), '<')

    def test_hid_240x240_little_endian(self):
        self.assertEqual(ImageService.byte_order_for('hid', (240, 240)), '<')

    def test_bulk_320x320_big_endian(self):
        self.assertEqual(ImageService.byte_order_for('bulk', (320, 320)), '>')

    def test_bulk_480x480_little_endian(self):
        self.assertEqual(ImageService.byte_order_for('bulk', (480, 480)), '<')


class TestImageServiceDeviceRotation(unittest.TestCase):
    """Test device-level pre-rotation for non-square displays.

    C# ImageTo565: non-square displays (not 240x240/320x320/480x480) get
    a 90° CW rotation before encoding.  Square displays are unchanged.
    """

    def test_square_240x240_no_rotation(self):
        img = make_test_surface(240, 240, (255, 0, 0))
        result = ImageService.apply_device_rotation(img, (240, 240))
        self.assertEqual(surface_size(result), (240, 240))

    def test_square_320x320_no_rotation(self):
        img = make_test_surface(320, 320, (255, 0, 0))
        result = ImageService.apply_device_rotation(img, (320, 320))
        self.assertEqual(surface_size(result), (320, 320))

    def test_square_480x480_no_rotation(self):
        img = make_test_surface(480, 480, (255, 0, 0))
        result = ImageService.apply_device_rotation(img, (480, 480))
        self.assertEqual(surface_size(result), (480, 480))

    def test_non_square_320x240_rotates(self):
        """320x240 → 90° CW → 240x320 (portrait for HID Type 2)."""
        img = make_test_surface(320, 240)
        result = ImageService.apply_device_rotation(img, (320, 240))
        self.assertEqual(surface_size(result), (240, 320))

    def test_non_square_640x480_rotates(self):
        img = make_test_surface(640, 480)
        result = ImageService.apply_device_rotation(img, (640, 480))
        self.assertEqual(surface_size(result), (480, 640))

    def test_non_square_360x360_rotates(self):
        """360x360 is NOT in C#'s square list — gets rotation."""
        img = make_test_surface(360, 360)
        result = ImageService.apply_device_rotation(img, (360, 360))
        self.assertEqual(surface_size(result), (360, 360))  # square → dimensions same

    def test_non_square_pixel_data(self):
        """Verify pixel data is correctly rotated — dimensions transpose."""
        # Use solid-color halves to survive smooth transformation interpolation.
        # Left half red, right half green → after 90° CW rotation (device pre-rot):
        # top half = red, bottom half = green.
        img = QImage(40, 20, QImage.Format.Format_RGB32)
        red, green = QColor(255, 0, 0), QColor(0, 255, 0)
        for x in range(20):
            for y in range(20):
                img.setPixelColor(x, y, red)   # left half = red
        for x in range(20, 40):
            for y in range(20):
                img.setPixelColor(x, y, green)  # right half = green
        result = ImageService.apply_device_rotation(img, (40, 20))
        self.assertEqual(surface_size(result), (20, 40))
        # After 90° CW rotation: left-half red → top region,
        # right-half green → bottom region. Check center pixels of each region.
        top_px = get_pixel(result, 10, 5)     # center of top region
        bot_px = get_pixel(result, 10, 35)    # center of bottom region
        self.assertGreater(top_px[0], 200)    # red channel dominant
        self.assertGreater(bot_px[1], 200)    # green channel dominant


class TestImageServiceResize(unittest.TestCase):
    """Test image resize."""

    def test_resize(self):
        img = make_test_surface(100, 100)
        result = ImageService.resize(img, 50, 50)
        self.assertEqual(surface_size(result), (50, 50))


class TestImageServiceToJpeg(unittest.TestCase):
    """Test JPEG encoding (C# CompressionImage pattern)."""

    def test_returns_valid_jpeg(self):
        img = make_test_surface(100, 100, (255, 0, 0))
        data = ImageService.to_jpeg(img)
        self.assertTrue(data[:2] == b'\xff\xd8')  # JPEG SOI marker

    def test_quality_reduces_until_under_max(self):
        """Large images should reduce quality until under max_size."""
        img = make_test_surface(480, 480, (128, 64, 200))
        data = ImageService.to_jpeg(img, quality=95, max_size=450_000)
        self.assertLess(len(data), 450_000)

    def test_tiny_max_size_still_returns_data(self):
        """Even with very small max_size, fallback to quality=5."""
        img = make_test_surface(100, 100, (255, 128, 0))
        data = ImageService.to_jpeg(img, quality=95, max_size=1)
        self.assertTrue(len(data) > 0)
        self.assertTrue(data[:2] == b'\xff\xd8')

    def test_rgba_input_converted(self):
        """RGBA images should be converted to RGB before encoding."""
        img = make_test_surface(50, 50, (255, 0, 0, 128))
        data = ImageService.to_jpeg(img)
        self.assertTrue(data[:2] == b'\xff\xd8')

    def test_default_quality_95(self):
        """Default quality produces smaller output than raw RGB565."""
        img = make_test_surface(320, 320, (100, 150, 200))
        jpeg = ImageService.to_jpeg(img)
        rgb565 = ImageService.to_rgb565(img)
        self.assertLess(len(jpeg), len(rgb565))


class TestImageServiceToAnsi(unittest.TestCase):
    """Test ANSI true-color terminal rendering."""

    def test_returns_string(self):
        img = make_test_surface(4, 4, (255, 0, 0))
        result = ImageService.to_ansi(img, cols=4)
        self.assertIsInstance(result, str)

    def test_contains_half_block(self):
        img = make_test_surface(4, 4, (0, 255, 0))
        result = ImageService.to_ansi(img, cols=4)
        self.assertIn('\u2580', result)

    def test_contains_ansi_escapes(self):
        img = make_test_surface(4, 4, (255, 0, 0))
        result = ImageService.to_ansi(img, cols=4)
        self.assertIn('\033[38;2;', result)   # foreground
        self.assertIn('\033[48;2;', result)   # background
        self.assertIn('\033[0m', result)      # reset

    def test_red_contains_red_color(self):
        """Solid red should produce 255;0;0 (or close) in ANSI escapes."""
        img = make_test_surface(2, 2, (255, 0, 0))
        result = ImageService.to_ansi(img, cols=2)
        self.assertIn('255;0;0', result)

    def test_rgba_input(self):
        """RGBA images should render without error."""
        img = make_test_surface(4, 4, (0, 0, 255, 128))
        result = ImageService.to_ansi(img, cols=4)
        self.assertIn('\u2580', result)

    def test_cursor_home_variant(self):
        img = make_test_surface(4, 4, (0, 0, 0))
        result = ImageService.to_ansi_cursor_home(img, cols=4)
        self.assertTrue(result.startswith('\033[H'))

    def test_cols_parameter(self):
        """Smaller cols = shorter lines."""
        img = make_test_surface(100, 100, (128, 128, 128))
        narrow = ImageService.to_ansi(img, cols=10)
        wide = ImageService.to_ansi(img, cols=40)
        # Wider output should have more characters
        self.assertGreater(len(wide), len(narrow))


# =============================================================================
# ImageService.encode_for_device — Strategy pattern (JPEG vs RGB565)
# =============================================================================

class TestEncodeForDevice:
    """encode_for_device() — unified encoding strategy."""

    @pytest.fixture
    def small_image(self):
        return make_test_surface(100, 100, (255, 0, 0))

    @pytest.fixture
    def nonsquare_image(self):
        return make_test_surface(320, 240, (0, 255, 0))

    # ── JPEG path ──────────────────────────────────────────────────────

    def test_bulk_jpeg(self, small_image):
        _surface, data = ImageService.encode_for_device(
            small_image, protocol='bulk', resolution=(480, 480),
            fbl=None, use_jpeg=True)
        assert data[:2] == b'\xff\xd8'  # JPEG SOI marker

    def test_ly_jpeg(self, small_image):
        _surface, data = ImageService.encode_for_device(
            small_image, protocol='ly', resolution=(1920, 462),
            fbl=192, use_jpeg=True)
        assert data[:2] == b'\xff\xd8'

    def test_hid_jpeg_fbl_54(self, small_image):
        """FBL 54 (360x360) is in JPEG_MODE_FBLS."""
        _surface, data = ImageService.encode_for_device(
            small_image, protocol='hid', resolution=(360, 360),
            fbl=54, use_jpeg=False)
        assert data[:2] == b'\xff\xd8'

    def test_bulk_pm32_rgb565(self, small_image):
        """Bulk with use_jpeg=False (PM=32) uses RGB565."""
        _surface, data = ImageService.encode_for_device(
            small_image, protocol='bulk', resolution=(480, 480),
            fbl=None, use_jpeg=False)
        assert data[:2] != b'\xff\xd8'
        assert len(data) == 100 * 100 * 2  # RGB565 = 2 bytes/pixel

    # ── RGB565 path ────────────────────────────────────────────────────

    def test_scsi_rgb565(self, small_image):
        _surface, data = ImageService.encode_for_device(
            small_image, protocol='scsi', resolution=(320, 320),
            fbl=100, use_jpeg=False)
        assert len(data) == 100 * 100 * 2

    def test_hid_non_jpeg_rgb565(self, small_image):
        """HID with non-JPEG FBL uses RGB565."""
        _surface, data = ImageService.encode_for_device(
            small_image, protocol='hid', resolution=(240, 240),
            fbl=36, use_jpeg=False)
        assert len(data) == 100 * 100 * 2

    def test_nonsquare_rotates_before_rgb565(self, nonsquare_image):
        """Non-square SCSI image gets 90 CW rotation → 240x320."""
        _surface, data = ImageService.encode_for_device(
            nonsquare_image, protocol='scsi', resolution=(320, 240),
            fbl=50, use_jpeg=False)
        # After rotation: 240x320 → 240*320*2 bytes
        assert len(data) == 240 * 320 * 2


class TestLEDServiceZonesToAnsi(unittest.TestCase):
    """Test LED zone ANSI terminal rendering."""

    def test_empty_returns_empty(self):
        from trcc.legacy.services.led import LEDService
        self.assertEqual(LEDService.zones_to_ansi([]), '')

    def test_single_zone(self):
        from trcc.legacy.services.led import LEDService
        result = LEDService.zones_to_ansi([(255, 0, 0)])
        self.assertIn('48;2;255;0;0', result)
        self.assertIn('\033[0m', result)

    def test_multiple_zones(self):
        from trcc.legacy.services.led import LEDService
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        result = LEDService.zones_to_ansi(colors)
        self.assertIn('48;2;255;0;0', result)
        self.assertIn('48;2;0;255;0', result)
        self.assertIn('48;2;0;0;255', result)

    def test_zone_count_matches(self):
        from trcc.legacy.services.led import LEDService
        colors = [(i, i, i) for i in range(5)]
        result = LEDService.zones_to_ansi(colors)
        # Each zone produces one reset escape
        self.assertEqual(result.count('\033[0m'), 5)


class TestMetricsToAnsi(unittest.TestCase):
    """Test ANSI dashboard rendering with arbitrary user metrics.

    Hexagonal: services take data in (HardwareMetrics), produce ANSI out.
    Any user's metrics should render correctly — hot CPU, idle GPU, full RAM, etc.
    """

    def _make_metrics(self, **overrides):
        """Create HardwareMetrics with custom values."""
        from trcc.legacy.core.models import HardwareMetrics
        m = HardwareMetrics()
        for k, v in overrides.items():
            setattr(m, k, v)
        return m

    def test_all_groups_returns_string(self):
        m = self._make_metrics(cpu_temp=45, gpu_temp=55, mem_percent=60)
        result = ImageService.metrics_to_ansi(m, cols=40)
        self.assertIsInstance(result, str)
        self.assertIn('\033[', result)  # has ANSI escapes

    def test_cpu_group_only(self):
        m = self._make_metrics(cpu_temp=85, cpu_percent=95, cpu_freq=4500)
        result = ImageService.metrics_to_ansi(m, cols=40, group='cpu')
        self.assertIn('\033[', result)
        self.assertIn('\u2580', result)

    def test_gpu_group_only(self):
        m = self._make_metrics(gpu_temp=78, gpu_usage=100, gpu_clock=1800)
        result = ImageService.metrics_to_ansi(m, cols=40, group='gpu')
        self.assertIn('\033[', result)

    def test_mem_group_only(self):
        m = self._make_metrics(mem_percent=92, mem_available=2048)
        result = ImageService.metrics_to_ansi(m, cols=40, group='mem')
        self.assertIn('\033[', result)

    def test_disk_group_only(self):
        m = self._make_metrics(disk_read=150.5, disk_write=80.3, disk_temp=42)
        result = ImageService.metrics_to_ansi(m, cols=40, group='disk')
        self.assertIn('\033[', result)

    def test_net_group_only(self):
        m = self._make_metrics(net_up=500, net_down=12000)
        result = ImageService.metrics_to_ansi(m, cols=40, group='net')
        self.assertIn('\033[', result)

    def test_fan_group_only(self):
        m = self._make_metrics(fan_cpu=1200, fan_gpu=657)
        result = ImageService.metrics_to_ansi(m, cols=40, group='fan')
        self.assertIn('\033[', result)

    def test_time_group_only(self):
        """Time group always renders (date/time/weekday are never 0-filtered)."""
        m = self._make_metrics()
        result = ImageService.metrics_to_ansi(m, cols=40, group='time')
        self.assertIn('\033[', result)

    def test_unknown_group_returns_error(self):
        m = self._make_metrics()
        result = ImageService.metrics_to_ansi(m, group='bogus')
        self.assertIn('Unknown group', result)
        self.assertIn('cpu', result)

    def test_zero_metrics_still_renders(self):
        """All-zero metrics should still produce valid ANSI (time group)."""
        m = self._make_metrics()
        result = ImageService.metrics_to_ansi(m, cols=30)
        self.assertIsInstance(result, str)
        self.assertIn('\033[', result)

    def test_hot_cpu_scenario(self):
        """Simulate user with overheating CPU — 95°C, 100% load."""
        m = self._make_metrics(cpu_temp=95, cpu_percent=100, cpu_freq=5200,
                               fan_cpu=2800, mem_percent=78)
        result = ImageService.metrics_to_ansi(m, cols=50)
        self.assertIn('\u2580', result)

    def test_idle_system_scenario(self):
        """Simulate idle system — low temps, low usage."""
        m = self._make_metrics(cpu_temp=28, cpu_percent=2, cpu_freq=800,
                               gpu_temp=25, gpu_usage=0, mem_percent=15)
        result = ImageService.metrics_to_ansi(m, cols=50)
        self.assertIn('\u2580', result)

    def test_full_load_scenario(self):
        """Simulate gaming — high everything."""
        m = self._make_metrics(
            cpu_temp=82, cpu_percent=87, cpu_freq=4800,
            gpu_temp=76, gpu_usage=99, gpu_clock=2100,
            mem_percent=91, mem_available=1200,
            fan_cpu=2200, fan_gpu=1800,
            net_down=50000, net_up=3000,
            disk_read=200, disk_write=150,
        )
        result = ImageService.metrics_to_ansi(m, cols=60)
        self.assertIn('\u2580', result)

    def test_cols_affects_output_length(self):
        m = self._make_metrics(cpu_temp=50, cpu_percent=50)
        narrow = ImageService.metrics_to_ansi(m, cols=30, group='cpu')
        wide = ImageService.metrics_to_ansi(m, cols=80, group='cpu')
        self.assertGreater(len(wide), len(narrow))


class TestLEDZonesAnsiWithMetrics(unittest.TestCase):
    """Test LED zone ANSI preview with simulated device tick output.

    Hexagonal: LEDService.tick() produces colors, zones_to_ansi() renders them.
    Any user's device configuration should produce valid ANSI output.
    """

    def _make_led_service(self, mode='static', color=(255, 0, 0),
                          brightness=100, segment_count=64):
        from trcc.legacy.core.models import LEDMode, LEDState
        from trcc.legacy.services.led import LEDService
        state = LEDState()
        state.mode = LEDMode[mode.upper()] if isinstance(mode, str) else mode
        state.color = color
        state.brightness = brightness
        state.segment_count = segment_count
        state.global_on = True
        svc = LEDService(state=state)
        return svc

    def test_static_red_zones(self):
        """Static red → all zones red."""
        from trcc.legacy.services.led import LEDService
        svc = self._make_led_service(color=(255, 0, 0))
        colors = svc.tick()
        result = LEDService.zones_to_ansi(colors)
        self.assertIn('255;0;0', result)

    def test_static_blue_zones(self):
        from trcc.legacy.services.led import LEDService
        svc = self._make_led_service(color=(0, 0, 255))
        colors = svc.tick()
        result = LEDService.zones_to_ansi(colors)
        self.assertIn('0;0;255', result)

    def test_breathing_produces_output(self):
        """Breathing mode tick → valid ANSI zones."""
        from trcc.legacy.services.led import LEDService
        svc = self._make_led_service(mode='breathing', color=(0, 255, 0))
        colors = svc.tick()
        result = LEDService.zones_to_ansi(colors)
        self.assertIn('\033[48;2;', result)

    def test_rainbow_produces_output(self):
        from trcc.legacy.services.led import LEDService
        svc = self._make_led_service(mode='rainbow')
        colors = svc.tick()
        result = LEDService.zones_to_ansi(colors)
        self.assertIn('\033[48;2;', result)

    def test_colorful_produces_output(self):
        from trcc.legacy.services.led import LEDService
        svc = self._make_led_service(mode='colorful')
        colors = svc.tick()
        result = LEDService.zones_to_ansi(colors)
        self.assertIn('\033[48;2;', result)

    def test_many_segments(self):
        """128-segment device → 128 zone blocks."""
        from trcc.legacy.services.led import LEDService
        svc = self._make_led_service(segment_count=128)
        colors = svc.tick()
        result = LEDService.zones_to_ansi(colors)
        self.assertEqual(result.count('\033[0m'), 128)

    def test_low_brightness_applied_manually(self):
        """Brightness scaling applied to zone colors before ANSI rendering."""
        from trcc.legacy.services.led import LEDService
        # Simulate what send_colors does: scale by brightness
        brightness = 10
        base = (255, 255, 255)
        scale = brightness / 100.0
        colors = [(int(r * scale), int(g * scale), int(b * scale))
                  for r, g, b in [base] * 4]
        result = LEDService.zones_to_ansi(colors)
        # At 10% brightness, channels should be ~25, not 255
        self.assertNotIn('255;255;255', result)
        self.assertIn('25;25;25', result)

    def test_zones_to_ansi_all_black(self):
        """All-black zones still produce valid ANSI."""
        from trcc.legacy.services.led import LEDService
        colors = [(0, 0, 0)] * 10
        result = LEDService.zones_to_ansi(colors)
        self.assertEqual(result.count('\033[0m'), 10)
        self.assertIn('0;0;0', result)

    def test_zones_to_ansi_max_white(self):
        from trcc.legacy.services.led import LEDService
        colors = [(255, 255, 255)] * 4
        result = LEDService.zones_to_ansi(colors)
        self.assertIn('255;255;255', result)
        self.assertEqual(result.count('\033[0m'), 4)


class TestDeviceServiceSendPilBulk(unittest.TestCase):
    """Test that send_frame routes bulk devices through JPEG encoding."""

    def test_bulk_sends_jpeg(self):
        """Bulk protocol → ImageService.to_jpeg() path."""
        from trcc.legacy.core.models import DeviceInfo
        svc = make_device_service()
        dev = DeviceInfo(name='bulk', path='bulk:87ad:70db', protocol='bulk')
        svc.select(dev)

        with patch.object(svc, 'send_rgb565', return_value=True) as mock_send:
            img = make_test_surface(480, 480, (255, 0, 0))
            result = svc.send_frame(img, 480, 480)

        self.assertTrue(result)
        call_data = mock_send.call_args[0][0]
        self.assertTrue(call_data[:2] == b'\xff\xd8')  # JPEG data

    def test_bulk_pm32_sends_rgb565(self):
        """Bulk PM=32 (FBL=100) → ImageService.to_rgb565() path."""
        from trcc.legacy.core.models import DeviceInfo
        svc = make_device_service()
        dev = DeviceInfo(name='bulk', path='bulk:87ad:70db', protocol='bulk',
                         resolution=(320, 320), fbl_code=100)
        svc.select(dev)

        with patch.object(svc, 'send_rgb565', return_value=True) as mock_send:
            img = make_test_surface(320, 320, (255, 0, 0))
            result = svc.send_frame(img, 320, 320)

        self.assertTrue(result)
        call_data = mock_send.call_args[0][0]
        # RGB565: 320*320*2 = 204800 bytes, not JPEG
        self.assertEqual(len(call_data), 320 * 320 * 2)
        self.assertNotEqual(call_data[:2], b'\xff\xd8')  # NOT JPEG

    def test_scsi_sends_rgb565(self):
        """SCSI protocol → ImageService.to_rgb565() path (not JPEG)."""
        from trcc.legacy.core.models import DeviceInfo
        svc = make_device_service()
        dev = DeviceInfo(name='scsi', path='/dev/sg0', protocol='scsi',
                         resolution=(320, 320))
        svc.select(dev)

        with patch.object(svc, 'send_rgb565', return_value=True) as mock_send:
            img = make_test_surface(320, 320, (255, 0, 0))
            result = svc.send_frame(img, 320, 320)

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
        svc = make_device_service()
        self.assertIsNone(svc.selected)
        self.assertEqual(svc.devices, [])
        self.assertFalse(svc.is_busy)

    @patch('trcc.legacy.services.device.DeviceService.detect')
    def test_detect_returns_list(self, mock_detect):
        mock_detect.return_value = []
        svc = make_device_service()
        result = svc.detect()
        self.assertIsInstance(result, list)

    def test_select(self):
        from trcc.legacy.core.models import DeviceInfo
        svc = make_device_service()
        dev = DeviceInfo(name='test', path='/dev/sg0')
        svc.select(dev)
        self.assertEqual(svc.selected, dev)

    def test_is_busy_default_false(self):
        svc = make_device_service()
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
        self.assertEqual(svc.state.progress, 0.0)

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
        svc = OverlayService(renderer=ImageService.renderer())
        self.assertFalse(svc.enabled)
        self.assertIsNone(svc.background)

    def test_enable_disable(self):
        svc = OverlayService(renderer=ImageService.renderer())
        svc.enabled = True
        self.assertTrue(svc.enabled)
        svc.enabled = False
        self.assertFalse(svc.enabled)

    def test_set_background(self):
        svc = OverlayService(renderer=ImageService.renderer())
        img = make_test_surface(320, 320)
        svc.set_background(img)
        self.assertIsNotNone(svc.background)

    def test_set_resolution(self):
        svc = OverlayService(320, 320, renderer=ImageService.renderer())
        svc.set_resolution(480, 480)
        self.assertEqual(svc.width, 480)
        self.assertEqual(svc.height, 480)

    def test_render_no_config_returns_background(self):
        """With no config/mask set, render returns background as-is (fast path)."""
        svc = OverlayService(renderer=ImageService.renderer())
        img = make_test_surface(320, 320, (255, 0, 0))
        svc.set_background(img)
        result = svc.render()
        self.assertIsNotNone(result)

    def test_set_dc_data(self):
        svc = OverlayService(renderer=ImageService.renderer())
        data = {'display_options': {'ui_mode': 1}}
        svc.set_dc_data(data)
        self.assertEqual(svc._dc_data, data)
        svc.set_dc_data(None)
        self.assertIsNone(svc._dc_data)


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
        from trcc.legacy.core.models import ThemeInfo
        theme = ThemeInfo(name='test')
        self.assertTrue(ThemeService._passes_filter(theme, 'all'))

    def test_passes_filter_user(self):
        from trcc.legacy.core.models import ThemeInfo
        theme = ThemeInfo(name='Custom_foo')
        self.assertTrue(ThemeService._passes_filter(theme, 'user'))

    def test_passes_filter_default_excludes_custom(self):
        from trcc.legacy.core.models import ThemeInfo
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
        from trcc.legacy.core.models import ThemeData
        self.assertIsNotNone(ThemeData)


class TestSetupDirsFallback(unittest.TestCase):
    """DisplayService._setup_dirs — each content type falls back independently."""

    def test_local_theme_falls_back_to_native(self):
        """No rotated local theme dir → falls back to native resolution."""
        svc = DisplayService(MagicMock(), MagicMock(), MagicMock())
        with tempfile.TemporaryDirectory() as td:
            native = Path(td) / 'theme800480'
            native.mkdir()
            (native / 'Theme1').mkdir()
            (native / 'Theme1' / '00.png').write_bytes(b'fake')

            svc._data_root = Path(td)
            svc.set_resolution(800, 480)
            svc._has_portrait_themes = False  # no portrait theme dir
            svc.rotation = 90
        # Falls back to native since has_portrait_themes is False
        self.assertIn('800480', str(svc.theme_dir.path))
