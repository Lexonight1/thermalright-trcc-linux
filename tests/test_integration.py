"""Integration tests — end-to-end pipelines with mocked hardware boundary.

These tests verify the full detect → load → send pipeline by mocking only
the hardware boundary (SCSI sg_raw, USB, filesystem device nodes) and letting
all layers above run for real.
"""

import os
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

_qapp = QApplication.instance() or QApplication([])

from tests.conftest import (  # noqa: E402
    get_pixel,
    make_test_surface,
    surface_size,
)
from tests.conftest import (  # noqa: E402
    save_test_png as _make_png,
)
from trcc.adapters.device.detector import DetectedDevice  # noqa: E402

# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_device(vid=0x87CD, pid=0x70DB, scsi="/dev/sg0", usb_path="2-1",
                 impl="thermalright_lcd_v1", protocol="scsi", model="CZTV"):
    """Create a DetectedDevice with sensible defaults."""
    return DetectedDevice(
        vid=vid, pid=pid,
        vendor_name="Thermalright", product_name="LCD Display",
        usb_path=usb_path, scsi_device=scsi,
        implementation=impl, model=model,
        button_image="A1CZTV", protocol=protocol, device_type=1,
    )

# ── Pipeline: CLI send command ──────────────────────────────────────────────

class TestCLISendPipeline(unittest.TestCase):
    """CLI send_image()/send_color() → DeviceService → DeviceProtocolFactory."""

    @staticmethod
    def _real_builder():
        from trcc.adapters.system.linux_platform import LinuxPlatform

        # conftest.py already wired the renderer at session scope — reuse it
        from trcc.adapters.system.linux_platform import LinuxPlatform as LinuxOs
        from trcc.core.builder import ControllerBuilder
        from trcc.services.image import ImageService
        return ControllerBuilder(LinuxPlatform(), LinuxOs()).with_renderer(ImageService.renderer())

    @pytest.mark.skip(
        reason="Phase 9: TrccApp(builder) composition removed; CLI now goes "
               "through trcc()._boot._cached. send_image()/send_color() "
               "signatures changed (no builder param). Test needs rewriting "
               "against the _boot composition root.")
    def test_cli_send_image(self):
        pass

    def test_cli_send_missing_file(self):
        """send_image with nonexistent file returns 1."""
        from trcc.ui.cli import send_image
        result = send_image("/nonexistent/image.png")
        self.assertEqual(result, 1)

    @pytest.mark.skip(
        reason="Phase 9: TrccApp(builder) composition removed; CLI now goes "
               "through trcc()._boot._cached. send_color() signature changed "
               "(no builder param). Test needs rewriting against _boot.")
    def test_cli_send_color(self):
        pass

    def test_cli_send_color_invalid_hex(self):
        """send_color with invalid hex returns 1."""
        from trcc.ui.cli import send_color
        result = send_color("xyz")
        self.assertEqual(result, 1)


# ── Pipeline: CLI resume ────────────────────────────────────────────────────

class TestCLIResumePipeline(unittest.TestCase):
    """CLI resume() → DeviceService.detect → load config → ImageService → send."""

    @pytest.mark.skip(
        reason="Phase 9: resume() rewritten for Trcc-native flow with "
               "10x discover retry returning ThemeResult — old "
               "TrccApp._instance.lcd.restore_last_theme dict shape gone.")
    def test_resume_with_saved_theme(self):
        pass

    @pytest.mark.skip(
        reason="Phase 3: resume() now goes through Trcc.discover (which "
               "iterates the Platform directly).  These tests mocked the "
               "deleted ControllerBuilder.build_detect_fn wrapper — they "
               "need rewriting against the new flow.")
    def test_resume_no_devices(self):
        pass

    @pytest.mark.skip(
        reason="Phase 3: see test_resume_no_devices.")
    def test_resume_no_saved_theme(self):
        pass


# ── Pipeline: CLI detect ────────────────────────────────────────────────────

class TestCLIDetectPipeline(unittest.TestCase):
    """CLI detect() exercises device detection end-to-end."""

    def _mock_setup(self):
        setup = MagicMock()
        setup.check_device_permissions.return_value = []
        setup.no_devices_hint.return_value = None
        return setup

    def test_detect_shows_device(self):
        """detect() with a device returns 0 and formats output."""
        from trcc.ui.cli import detect

        dev = _make_device()
        with patch("trcc.conf.Settings.get_selected_device", return_value="/dev/sg0"):
            result = detect(show_all=True, detect_fn=lambda: [dev],
                            os_platform=self._mock_setup())
        self.assertEqual(result, 0)

    def test_detect_no_devices(self):
        """detect() with no devices returns 1."""
        from trcc.ui.cli import detect
        result = detect(detect_fn=lambda: [], os_platform=self._mock_setup())
        self.assertEqual(result, 1)

    def test_detect_multiple_devices(self):
        """detect --all with multiple devices lists all."""
        from trcc.ui.cli import detect

        devs = [
            _make_device(scsi="/dev/sg0"),
            _make_device(vid=0x0416, pid=0x5406, scsi="/dev/sg1",
                         impl="winbond_lcd"),
        ]
        with patch("trcc.conf.Settings.get_selected_device", return_value="/dev/sg0"):
            result = detect(show_all=True, detect_fn=lambda: devs,
                            os_platform=self._mock_setup())
        self.assertEqual(result, 0)


# ── Pipeline: device_detector round-trip ────────────────────────────────────

class TestDeviceDetectorRoundTrip(unittest.TestCase):
    """Verify find_usb_devices → detect_devices → get_default_device chain."""

    @patch("trcc.adapters.device.linux.detector.linux_scsi_resolver")
    @patch("trcc.adapters.device._pyusb_find.find")
    def test_usb_to_scsi_mapping(self, mock_find, mock_scsi_resolver):
        """USB device found → SCSI path assigned via scsi_resolver → returned in detect()."""
        from trcc.adapters.device.detector import DeviceDetector

        mock_usb_dev = MagicMock()
        mock_find.side_effect = lambda **kw: (
            [mock_usb_dev]
            if (kw.get('idVendor') == 0x87CD and kw.get('idProduct') == 0x70DB)
            else []
        )
        mock_scsi_resolver.return_value = "/dev/sg0"

        devices = DeviceDetector.detect()
        thermalright = [d for d in devices if d.vid == 0x87CD and d.pid == 0x70DB]
        self.assertEqual(len(thermalright), 1)
        self.assertEqual(thermalright[0].scsi_device, "/dev/sg0")

    @patch("trcc.adapters.device.detector.DeviceDetector.detect")
    def test_get_default_prefers_thermalright(self, mock_detect):
        """get_default_device prefers Thermalright (VID 0x87CD)."""
        from trcc.adapters.device.detector import get_default_device

        devs = [
            _make_device(vid=0x0416, pid=0x5406, scsi="/dev/sg1",
                         usb_path="2-2", impl="winbond_lcd"),
            _make_device(vid=0x87CD, pid=0x70DB, scsi="/dev/sg0",
                         usb_path="2-1"),
        ]
        mock_detect.return_value = devs

        device = get_default_device()
        self.assertIsNotNone(device)
        self.assertEqual(device.vid, 0x87CD)


# ── Pipeline: RGB565 conversion consistency ─────────────────────────────────

class TestRGB565Consistency(unittest.TestCase):
    """Verify RGB565 conversion is consistent between LCDDriver and controllers."""

    def test_driver_rgb_matches_controller(self):
        """rgb_to_bytes matches ImageService.to_rgb565 for single pixels."""
        from trcc.core.encoding import rgb_to_bytes
        from trcc.services import ImageService
        for r, g, b in [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                         (128, 128, 128), (255, 255, 255), (0, 0, 0)]:
            surface = make_test_surface(1, 1, (r, g, b))
            controller_bytes = ImageService.to_rgb565(surface)

            impl_bytes = rgb_to_bytes(r, g, b, '>')

            # Both should produce the same 2 bytes for the same color
            self.assertEqual(controller_bytes, impl_bytes,
                             f"Mismatch for ({r},{g},{b}): "
                             f"controller={controller_bytes.hex()} vs impl={impl_bytes.hex()}")

    def test_rgb565_red_channel(self):
        """Red (255,0,0) → RGB565 big-endian: 0xF800."""
        from trcc.core.encoding import rgb_to_bytes
        pixel = rgb_to_bytes(255, 0, 0, '>')
        val = struct.unpack(">H", pixel)[0]
        self.assertEqual(val, 0xF800)

    def test_rgb565_green_channel(self):
        """Green (0,255,0) → RGB565 big-endian: 0x07E0."""
        from trcc.core.encoding import rgb_to_bytes
        pixel = rgb_to_bytes(0, 255, 0, '>')
        val = struct.unpack(">H", pixel)[0]
        self.assertEqual(val, 0x07E0)

    def test_rgb565_blue_channel(self):
        """Blue (0,0,255) → RGB565 big-endian: 0x001F."""
        from trcc.core.encoding import rgb_to_bytes
        pixel = rgb_to_bytes(0, 0, 255, '>')
        val = struct.unpack(">H", pixel)[0]
        self.assertEqual(val, 0x001F)


# ── Pipeline: theme load → overlay render → send ───────────────────────────

class TestThemeLoadRender(unittest.TestCase):
    """Theme directory → load image → apply overlay → convert RGB565."""

    def test_theme_dir_to_rgb565(self):
        """Load 00.png from theme dir, convert to RGB565, verify size."""
        from trcc.services import ImageService

        with tempfile.TemporaryDirectory() as td:
            bg_path = os.path.join(td, "00.png")
            _make_png(bg_path, 320, 320)

            surface = ImageService.open_and_resize(bg_path, 320, 320)
            self.assertEqual(surface_size(surface), (320, 320))

            frame = ImageService.to_rgb565(surface)
            self.assertEqual(len(frame), 320 * 320 * 2)

    def test_theme_with_mask_overlay(self):
        """Load background + mask → composite → convert to RGB565."""
        from trcc.services import ImageService

        with tempfile.TemporaryDirectory() as td:
            # Create background and mask files via Qt
            make_test_surface(320, 320, (255, 0, 0)).save(os.path.join(td, "00.png"), "PNG")
            make_test_surface(320, 320, (0, 0, 0, 128)).save(os.path.join(td, "01.png"), "PNG")

            # Open and composite via Qt renderer
            r = ImageService.renderer()
            bg = r.open_image(os.path.join(td, "00.png"))
            mask = r.open_image(os.path.join(td, "01.png"))
            mask_rgba = r.convert_to_rgba(mask)
            surface = r.composite(r.copy_surface(bg), mask_rgba, (0, 0))

            frame = ImageService.to_rgb565(surface)
            self.assertEqual(len(frame), 320 * 320 * 2)

    def test_image_resize_and_convert(self):
        """Oversized image gets resized to device resolution before RGB565."""
        from trcc.services import ImageService

        # Create 800x600 native surface
        big = make_test_surface(800, 600, (0, 128, 255))
        resized = ImageService.resize(big, 320, 320)
        frame = ImageService.to_rgb565(resized)
        self.assertEqual(len(frame), 320 * 320 * 2)


# ── Pipeline: brightness + rotation ─────────────────────────────────────────

class TestBrightnessRotation(unittest.TestCase):
    """Verify brightness and rotation transforms produce correct output."""

    def test_apply_rotation_90(self):
        """90° rotation swaps dimensions correctly."""
        from PySide6.QtGui import QColor

        from trcc.services import ImageService

        # Red background with a green marker at (0,0)
        surface = make_test_surface(320, 320, (255, 0, 0))
        surface.setPixelColor(0, 0, QColor(0, 255, 0))

        rotated = ImageService.apply_rotation(surface, 90)
        self.assertEqual(surface_size(rotated), (320, 320))

        # After 90° CW rotation, top-left green pixel moves
        # Verify it's no longer at (0,0)
        self.assertNotEqual(get_pixel(rotated, 0, 0)[:3], (0, 255, 0))

    def test_apply_rotation_0_noop(self):
        """0° rotation returns identical image."""
        from PySide6.QtGui import QColor

        from trcc.services import ImageService

        # Red background with a green marker at (5,5)
        surface = make_test_surface(320, 320, (255, 0, 0))
        surface.setPixelColor(5, 5, QColor(0, 255, 0))

        rotated = ImageService.apply_rotation(surface, 0)
        self.assertEqual(get_pixel(rotated, 5, 5)[:3], (0, 255, 0))

    def test_brightness_reduces_values(self):
        """50% brightness reduces pixel values via ImageService.apply_brightness."""
        from trcc.services import ImageService

        img = make_test_surface(10, 10, (200, 200, 200))
        result = ImageService.apply_brightness(img, 50)
        r, g, b = get_pixel(result, 5, 5)
        self.assertLess(r, 200)
        self.assertLess(g, 200)
        self.assertLess(b, 200)

    def test_brightness_100_percent_unchanged(self):
        """100% brightness leaves pixels unchanged via ImageService.apply_brightness."""
        from trcc.services import ImageService

        img = make_test_surface(10, 10, (200, 200, 200))
        result = ImageService.apply_brightness(img, 100)
        r, g, b = get_pixel(result, 5, 5)
        self.assertEqual((r, g, b), (200, 200, 200))


# ── Pipeline: SCSI header + CRC integrity ───────────────────────────────────

class TestSCSIHeaderIntegrity(unittest.TestCase):
    """Verify SCSI header building produces valid CRC-checked packets."""

    def test_header_format(self):
        """_build_header produces 20-byte header with valid CRC32."""
        import binascii

        from trcc.adapters.device.scsi import ScsiDevice

        header = ScsiDevice._build_header(0xF5, 0xE100)
        self.assertEqual(len(header), 20)

        # First 4 bytes = cmd (little-endian)
        cmd = struct.unpack("<I", header[:4])[0]
        self.assertEqual(cmd, 0xF5)

        # Bytes 12-16 = size (little-endian)
        size = struct.unpack("<I", header[12:16])[0]
        self.assertEqual(size, 0xE100)

        # Last 4 bytes = CRC32 of first 16
        expected_crc = binascii.crc32(header[:16]) & 0xFFFFFFFF
        actual_crc = struct.unpack("<I", header[16:20])[0]
        self.assertEqual(actual_crc, expected_crc)

    def test_frame_chunk_headers_unique(self):
        """Each frame chunk has a unique command with incrementing index."""
        from trcc.adapters.device.scsi import ScsiDevice

        chunks = ScsiDevice._get_frame_chunks(320, 320)
        cmds = [cmd for cmd, _ in chunks]

        # All commands should be unique
        self.assertEqual(len(cmds), len(set(cmds)))

        # Index is encoded in bits [27:24]
        for i, (cmd, _) in enumerate(chunks):
            idx = (cmd >> 24) & 0xF
            self.assertEqual(idx, i)


# ── Pipeline: device implementation registry ────────────────────────────────

class TestImplementationRegistry(unittest.TestCase):
    """Verify LCDDeviceConfig.from_key returns correct config for known names."""

    def test_known_implementations(self):
        """All registered implementation names resolve."""
        from trcc.core.models import LCDDeviceConfig

        for name in ["generic", "thermalright_lcd_v1"]:
            cfg = LCDDeviceConfig.from_key(name)
            self.assertIsNotNone(cfg)
            self.assertIsNotNone(cfg.name)
            self.assertEqual(cfg.pixel_format, "RGB565")

    def test_generic_fallback(self):
        """Unknown implementation name falls back to generic."""
        from trcc.core.models import LCDDeviceConfig
        cfg = LCDDeviceConfig.from_key("nonexistent_device_xyz")
        self.assertIn("generic", cfg.name.lower())


if __name__ == "__main__":
    unittest.main()
