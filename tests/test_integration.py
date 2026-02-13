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

from trcc.device_detector import DetectedDevice

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


def _make_png(path, w=320, h=320):
    """Write a minimal valid PNG at *path*."""
    from PIL import Image
    img = Image.new("RGB", (w, h), color=(255, 0, 0))
    img.save(path, "PNG")


# ── Pipeline: detect → driver init → send frame ────────────────────────────

class TestDetectToSend(unittest.TestCase):
    """Full pipeline: detect device → create LCDDriver → send_frame."""

    @patch("trcc.data_repository.SysUtils.require_sg_raw")
    @patch("trcc.device_scsi.subprocess.run")
    @patch("trcc.device_lcd.detect_devices")
    @patch("trcc.device_lcd.DeviceService.detect_lcd_resolution", return_value=False)
    def test_detect_init_send(self, _, mock_detect, mock_run, mock_sg):
        """detect_devices → LCDDriver(path) → send_frame goes through all layers."""
        from trcc.device_lcd import LCDDriver
        from trcc.device_scsi import _get_frame_chunks

        dev = _make_device()
        mock_detect.return_value = [dev]
        mock_run.return_value = MagicMock(returncode=0, stdout=b"\x00" * 512)

        driver = LCDDriver(device_path="/dev/sg0")

        self.assertEqual(driver.device_path, "/dev/sg0")
        self.assertIsNotNone(driver.implementation)
        self.assertEqual(driver.implementation.resolution, (320, 320))

        # Build a real RGB565 frame
        frame = driver.create_solid_color(255, 0, 0)
        self.assertEqual(len(frame), 320 * 320 * 2)

        # send_frame: poll + init + N chunks
        driver.send_frame(frame)
        self.assertTrue(driver.initialized)

        # poll(read) + init(write) + frame chunks(write)
        # sg_raw calls: 1 read (poll) + 1 write (init) + N chunk writes
        chunks = _get_frame_chunks(320, 320)
        expected_calls = 1 + 1 + len(chunks)  # poll + init + chunks
        self.assertEqual(mock_run.call_count, expected_calls)

    @patch("trcc.data_repository.SysUtils.require_sg_raw")
    @patch("trcc.device_scsi.subprocess.run")
    @patch("trcc.device_lcd.detect_devices")
    @patch("trcc.device_lcd.DeviceService.detect_lcd_resolution", return_value=False)
    def test_send_image_pipeline(self, _, mock_detect, mock_run, mock_sg):
        """LCDDriver.load_image → send_frame end-to-end."""
        from trcc.device_lcd import LCDDriver

        dev = _make_device()
        mock_detect.return_value = [dev]
        mock_run.return_value = MagicMock(returncode=0, stdout=b"\x00" * 512)

        driver = LCDDriver(device_path="/dev/sg0")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            _make_png(f.name)
            try:
                frame = driver.load_image(f.name)
                self.assertEqual(len(frame), 320 * 320 * 2)
                driver.send_frame(frame)
                self.assertTrue(driver.initialized)
            finally:
                os.unlink(f.name)


# ── Pipeline: CLI send command ──────────────────────────────────────────────

class TestCLISendPipeline(unittest.TestCase):
    """CLI send_image()/send_color() → DeviceService → DeviceProtocolFactory."""

    @patch("trcc.device_factory.DeviceProtocolFactory.get_protocol")
    @patch("trcc.device_detector.DeviceDetector.detect")
    def test_cli_send_image(self, mock_detect, mock_get_protocol):
        """trcc send image.png end-to-end via DeviceService."""
        from trcc.cli import send_image

        mock_detect.return_value = [_make_device()]
        mock_protocol = MagicMock()
        mock_protocol.send_image.return_value = True
        mock_get_protocol.return_value = mock_protocol

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            _make_png(f.name)
            try:
                result = send_image(f.name, device="/dev/sg0")
                self.assertEqual(result, 0)
                mock_protocol.send_image.assert_called_once()
                # Verify RGB565 frame size: 320*320*2 = 204800
                data = mock_protocol.send_image.call_args[0][0]
                self.assertEqual(len(data), 320 * 320 * 2)
            finally:
                os.unlink(f.name)

    def test_cli_send_missing_file(self):
        """send_image with nonexistent file returns 1."""
        from trcc.cli import send_image
        result = send_image("/nonexistent/image.png")
        self.assertEqual(result, 1)

    @patch("trcc.device_factory.DeviceProtocolFactory.get_protocol")
    @patch("trcc.device_detector.DeviceDetector.detect")
    def test_cli_send_color(self, mock_detect, mock_get_protocol):
        """trcc color ff0000 end-to-end via DeviceService."""
        from trcc.cli import send_color

        mock_detect.return_value = [_make_device()]
        mock_protocol = MagicMock()
        mock_protocol.send_image.return_value = True
        mock_get_protocol.return_value = mock_protocol

        result = send_color("ff0000", device="/dev/sg0")
        self.assertEqual(result, 0)
        mock_protocol.send_image.assert_called_once()
        # Verify RGB565 frame size
        data = mock_protocol.send_image.call_args[0][0]
        self.assertEqual(len(data), 320 * 320 * 2)

    def test_cli_send_color_invalid_hex(self):
        """send_color with invalid hex returns 1."""
        from trcc.cli import send_color
        result = send_color("xyz")
        self.assertEqual(result, 1)


# ── Pipeline: CLI resume ────────────────────────────────────────────────────

class TestCLIResumePipeline(unittest.TestCase):
    """CLI resume() → DeviceService.detect → load config → ImageService → send."""

    @patch("trcc.device_factory.DeviceProtocolFactory.get_protocol")
    @patch("trcc.device_detector.DeviceDetector.detect")
    @patch("trcc.conf.get_device_config")
    @patch("trcc.conf.device_config_key")
    def test_resume_with_saved_theme(self, mock_key, mock_cfg, mock_detect,
                                     mock_get_protocol):
        """resume() sends last theme with brightness and rotation applied."""
        from trcc.cli import resume

        mock_detect.return_value = [_make_device()]
        mock_key.return_value = "0:87cd_70db"

        mock_protocol = MagicMock()
        mock_protocol.send_image.return_value = True
        mock_get_protocol.return_value = mock_protocol

        with tempfile.TemporaryDirectory() as td:
            _make_png(os.path.join(td, "00.png"))
            mock_cfg.return_value = {
                "theme_path": td,
                "brightness_level": 2,  # 50%
                "rotation": 90,
            }

            result = resume()
            self.assertEqual(result, 0)
            mock_protocol.send_image.assert_called_once()
            # Verify RGB565 frame was sent
            data = mock_protocol.send_image.call_args[0][0]
            self.assertEqual(len(data), 320 * 320 * 2)

    @patch("trcc.device_detector.DeviceDetector.detect")
    def test_resume_no_devices(self, mock_detect):
        """resume() with no devices returns 1."""
        from trcc.cli import resume
        mock_detect.return_value = []
        result = resume()
        self.assertEqual(result, 1)

    @patch("trcc.device_detector.DeviceDetector.detect")
    @patch("trcc.conf.get_device_config")
    @patch("trcc.conf.device_config_key")
    def test_resume_no_saved_theme(self, mock_key, mock_cfg, mock_detect):
        """resume() with no saved theme returns 1."""
        from trcc.cli import resume

        mock_detect.return_value = [_make_device()]
        mock_key.return_value = "0:87cd_70db"
        mock_cfg.return_value = {}  # no theme_path

        result = resume()
        self.assertEqual(result, 1)


# ── Pipeline: CLI detect ────────────────────────────────────────────────────

class TestCLIDetectPipeline(unittest.TestCase):
    """CLI detect() exercises device_detector.detect_devices end-to-end."""

    @patch("trcc.device_detector.detect_devices")
    def test_detect_shows_device(self, mock_detect):
        """detect() with a device returns 0 and formats output."""
        from trcc.cli import detect

        dev = _make_device()
        mock_detect.return_value = [dev]

        with patch("trcc.conf.get_selected_device", return_value="/dev/sg0"):
            result = detect(show_all=True)
        self.assertEqual(result, 0)

    @patch("trcc.device_detector.detect_devices")
    def test_detect_no_devices(self, mock_detect):
        """detect() with no devices returns 1."""
        from trcc.cli import detect
        mock_detect.return_value = []
        result = detect()
        self.assertEqual(result, 1)

    @patch("trcc.device_detector.detect_devices")
    def test_detect_multiple_devices(self, mock_detect):
        """detect --all with multiple devices lists all."""
        from trcc.cli import detect

        devs = [
            _make_device(scsi="/dev/sg0"),
            _make_device(vid=0x0416, pid=0x5406, scsi="/dev/sg1",
                         impl="winbond_lcd"),
        ]
        mock_detect.return_value = devs

        with patch("trcc.conf.get_selected_device", return_value="/dev/sg0"):
            result = detect(show_all=True)
        self.assertEqual(result, 0)


# ── Pipeline: device_detector round-trip ────────────────────────────────────

class TestDeviceDetectorRoundTrip(unittest.TestCase):
    """Verify find_usb_devices → detect_devices → get_default_device chain."""

    @patch("trcc.device_detector.DeviceDetector.find_scsi_device_by_usb_path")
    @patch("trcc.device_detector.DeviceDetector.find_usb_devices")
    def test_usb_to_scsi_mapping(self, mock_find_usb, mock_find_scsi):
        """USB device found → SCSI path assigned → returned in detect_devices."""
        from trcc.device_detector import detect_devices

        dev = _make_device(scsi=None)
        mock_find_usb.return_value = [dev]
        mock_find_scsi.return_value = "/dev/sg0"

        devices = detect_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].scsi_device, "/dev/sg0")

    @patch("trcc.device_detector.DeviceDetector.find_scsi_device_by_usb_path")
    @patch("trcc.device_detector.DeviceDetector.find_usb_devices")
    def test_get_default_prefers_thermalright(self, mock_find_usb, mock_find_scsi):
        """get_default_device prefers Thermalright (VID 0x87CD)."""
        from trcc.device_detector import get_default_device

        devs = [
            _make_device(vid=0x0416, pid=0x5406, scsi="/dev/sg1",
                         usb_path="2-2", impl="winbond_lcd"),
            _make_device(vid=0x87CD, pid=0x70DB, scsi="/dev/sg0",
                         usb_path="2-1"),
        ]
        mock_find_usb.return_value = devs
        mock_find_scsi.side_effect = lambda path: {
            "2-2": "/dev/sg1", "2-1": "/dev/sg0"
        }.get(path)

        device = get_default_device()
        self.assertIsNotNone(device)
        self.assertEqual(device.vid, 0x87CD)


# ── Pipeline: LCDDriver multi-resolution ────────────────────────────────────

class TestMultiResolution(unittest.TestCase):
    """Verify frame sizing and chunk counts for different resolutions."""

    @patch("trcc.data_repository.SysUtils.require_sg_raw")
    @patch("trcc.device_scsi.subprocess.run")
    @patch("trcc.device_lcd.detect_devices")
    @patch("trcc.device_lcd.DeviceService.detect_lcd_resolution", return_value=False)
    def test_480x480_frame_size(self, _, mock_detect, mock_run, mock_sg):
        """480x480 produces correct frame size and chunk count."""
        from trcc.device_lcd import LCDDriver
        from trcc.device_scsi import _get_frame_chunks

        dev = _make_device()
        mock_detect.return_value = [dev]
        mock_run.return_value = MagicMock(returncode=0, stdout=b"\x00" * 512)

        driver = LCDDriver(device_path="/dev/sg0")
        # Override resolution after init
        driver.implementation.width = 480
        driver.implementation.height = 480

        frame = driver.create_solid_color(0, 255, 0)
        self.assertEqual(len(frame), 480 * 480 * 2)

        chunks = _get_frame_chunks(480, 480)
        total = sum(s for _, s in chunks)
        self.assertEqual(total, 480 * 480 * 2)
        # 480*480*2 = 460800, ceil(460800/65536) = 8 chunks
        self.assertEqual(len(chunks), 8)

    @patch("trcc.data_repository.SysUtils.require_sg_raw")
    @patch("trcc.device_scsi.subprocess.run")
    @patch("trcc.device_lcd.detect_devices")
    @patch("trcc.device_lcd.DeviceService.detect_lcd_resolution", return_value=False)
    def test_240x240_frame_size(self, _, mock_detect, mock_run, mock_sg):
        """240x240 produces correct frame size and chunk count."""
        from trcc.device_lcd import LCDDriver
        from trcc.device_scsi import _get_frame_chunks

        dev = _make_device()
        mock_detect.return_value = [dev]
        mock_run.return_value = MagicMock(returncode=0, stdout=b"\x00" * 512)

        driver = LCDDriver(device_path="/dev/sg0")
        # Override resolution after init
        driver.implementation.width = 240
        driver.implementation.height = 240

        frame = driver.create_solid_color(0, 0, 255)
        self.assertEqual(len(frame), 240 * 240 * 2)

        chunks = _get_frame_chunks(240, 240)
        total = sum(s for _, s in chunks)
        self.assertEqual(total, 240 * 240 * 2)
        # 240*240*2 = 115200, ceil(115200/65536) = 2 chunks
        self.assertEqual(len(chunks), 2)


# ── Pipeline: RGB565 conversion consistency ─────────────────────────────────

class TestRGB565Consistency(unittest.TestCase):
    """Verify RGB565 conversion is consistent between LCDDriver and controllers."""

    def test_driver_rgb_matches_controller(self):
        """ImageService.rgb_to_bytes matches ImageService.to_rgb565 for single pixels."""
        # Test a few colors
        from PIL import Image

        from trcc.services import ImageService
        for r, g, b in [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                         (128, 128, 128), (255, 255, 255), (0, 0, 0)]:
            img = Image.new("RGB", (1, 1), (r, g, b))
            controller_bytes = ImageService.to_rgb565(img)

            impl_bytes = ImageService.rgb_to_bytes(r, g, b, '>')

            # Both should produce the same 2 bytes for the same color
            self.assertEqual(controller_bytes, impl_bytes,
                             f"Mismatch for ({r},{g},{b}): "
                             f"controller={controller_bytes.hex()} vs impl={impl_bytes.hex()}")

    def test_rgb565_red_channel(self):
        """Red (255,0,0) → RGB565 big-endian: 0xF800."""
        from trcc.services import ImageService
        pixel = ImageService.rgb_to_bytes(255, 0, 0, '>')
        val = struct.unpack(">H", pixel)[0]
        self.assertEqual(val, 0xF800)

    def test_rgb565_green_channel(self):
        """Green (0,255,0) → RGB565 big-endian: 0x07E0."""
        from trcc.services import ImageService
        pixel = ImageService.rgb_to_bytes(0, 255, 0, '>')
        val = struct.unpack(">H", pixel)[0]
        self.assertEqual(val, 0x07E0)

    def test_rgb565_blue_channel(self):
        """Blue (0,0,255) → RGB565 big-endian: 0x001F."""
        from trcc.services import ImageService
        pixel = ImageService.rgb_to_bytes(0, 0, 255, '>')
        val = struct.unpack(">H", pixel)[0]
        self.assertEqual(val, 0x001F)


# ── Pipeline: theme load → overlay render → send ───────────────────────────

class TestThemeLoadRender(unittest.TestCase):
    """Theme directory → load image → apply overlay → convert RGB565."""

    def test_theme_dir_to_rgb565(self):
        """Load 00.png from theme dir, convert to RGB565, verify size."""
        from PIL import Image

        from trcc.services import ImageService

        with tempfile.TemporaryDirectory() as td:
            bg_path = os.path.join(td, "00.png")
            _make_png(bg_path, 320, 320)

            img = Image.open(bg_path).convert("RGB")
            self.assertEqual(img.size, (320, 320))

            frame = ImageService.to_rgb565(img)
            self.assertEqual(len(frame), 320 * 320 * 2)

    def test_theme_with_mask_overlay(self):
        """Load background + mask → composite → convert to RGB565."""
        from PIL import Image

        from trcc.services import ImageService

        with tempfile.TemporaryDirectory() as td:
            # Create background
            bg = Image.new("RGB", (320, 320), (255, 0, 0))
            bg.save(os.path.join(td, "00.png"))

            # Create mask (RGBA with alpha)
            mask = Image.new("RGBA", (320, 320), (0, 0, 0, 128))
            mask.save(os.path.join(td, "01.png"))

            bg = Image.open(os.path.join(td, "00.png")).convert("RGB")
            mask = Image.open(os.path.join(td, "01.png")).convert("RGBA")

            # Composite (same as controller does)
            composite = bg.copy()
            composite.paste(mask, (0, 0), mask)

            frame = ImageService.to_rgb565(composite)
            self.assertEqual(len(frame), 320 * 320 * 2)

    def test_image_resize_and_convert(self):
        """Oversized image gets resized to device resolution before RGB565."""
        from PIL import Image

        from trcc.services import ImageService

        # Create 800x600 image
        big = Image.new("RGB", (800, 600), (0, 128, 255))
        resized = big.resize((320, 320))
        frame = ImageService.to_rgb565(resized)
        self.assertEqual(len(frame), 320 * 320 * 2)


# ── Pipeline: brightness + rotation ─────────────────────────────────────────

class TestBrightnessRotation(unittest.TestCase):
    """Verify brightness and rotation transforms produce correct output."""

    def test_apply_rotation_90(self):
        """90° rotation swaps dimensions correctly."""
        from PIL import Image

        from trcc.services import ImageService

        img = Image.new("RGB", (320, 320), (255, 0, 0))
        # Draw a marker at (0,0) so rotation is verifiable
        img.putpixel((0, 0), (0, 255, 0))

        rotated = ImageService.apply_rotation(img, 90)
        self.assertEqual(rotated.size, (320, 320))

        # After 90° CW rotation, top-left green pixel moves
        # Verify it's no longer at (0,0)
        self.assertNotEqual(rotated.getpixel((0, 0)), (0, 255, 0))

    def test_apply_rotation_0_noop(self):
        """0° rotation returns identical image."""
        from PIL import Image

        from trcc.services import ImageService

        img = Image.new("RGB", (320, 320), (255, 0, 0))
        img.putpixel((5, 5), (0, 255, 0))

        rotated = ImageService.apply_rotation(img, 0)
        self.assertEqual(rotated.getpixel((5, 5)), (0, 255, 0))

    def test_brightness_reduces_values(self):
        """50% brightness reduces pixel values."""
        from PIL import Image, ImageEnhance

        img = Image.new("RGB", (10, 10), (200, 200, 200))
        enhanced = ImageEnhance.Brightness(img).enhance(0.5)
        r, g, b = enhanced.getpixel((5, 5))
        self.assertLess(r, 200)
        self.assertLess(g, 200)
        self.assertLess(b, 200)

    def test_brightness_100_percent_unchanged(self):
        """100% brightness leaves pixels unchanged."""
        from PIL import Image, ImageEnhance

        img = Image.new("RGB", (10, 10), (200, 200, 200))
        enhanced = ImageEnhance.Brightness(img).enhance(1.0)
        self.assertEqual(enhanced.getpixel((5, 5)), (200, 200, 200))


# ── Pipeline: SCSI header + CRC integrity ───────────────────────────────────

class TestSCSIHeaderIntegrity(unittest.TestCase):
    """Verify SCSI header building produces valid CRC-checked packets."""

    def test_header_format(self):
        """_build_header produces 20-byte header with valid CRC32."""
        import binascii

        from trcc.device_scsi import _build_header

        header = _build_header(0xF5, 0xE100)
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
        from trcc.device_scsi import _get_frame_chunks

        chunks = _get_frame_chunks(320, 320)
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
