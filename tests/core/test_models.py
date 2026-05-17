"""Tests for core/models.py – ThemeInfo, DeviceInfo, VideoState, resolution pipeline."""

import tempfile
import unittest
from pathlib import Path

import pytest

from trcc.core.models import (
    ALL_DEVICES,
    FBL_PROFILES,
    SCSI_DEVICES,
    DetectedDevice,
    DeviceInfo,
    DeviceProfile,
    ThemeInfo,
    ThemeType,
    UsbAddress,
    VideoState,
    fbl_to_resolution,
    get_profile,
    parse_hex_color,
    pm_to_fbl,
)
from trcc.services.theme import theme_info_from_directory

# =============================================================================
# ThemeInfo
# =============================================================================

class TestThemeInfoFromDirectory(unittest.TestCase):
    """theme_info_from_directory() filesystem scanning."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _make_theme(self, name, files=('00.png',)):
        d = Path(self.tmpdir) / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b'\x89PNG')
        return d

    def test_basic_theme(self):
        d = self._make_theme('001a', ['00.png'])
        info = theme_info_from_directory(d)
        self.assertEqual(info.name, '001a')
        self.assertEqual(info.theme_type, ThemeType.LOCAL)
        self.assertIsNotNone(info.background_path)

    def test_animated_theme(self):
        d = self._make_theme('002a', ['00.png', 'Theme.zt'])
        info = theme_info_from_directory(d)
        self.assertTrue(info.is_animated)
        self.assertIsNotNone(info.animation_path)

    def test_mask_only_theme(self):
        d = self._make_theme('mask', ['01.png'])
        info = theme_info_from_directory(d)
        self.assertTrue(info.is_mask_only)
        self.assertIsNone(info.background_path)

    def test_resolution_passed_through(self):
        d = self._make_theme('003a', ['00.png'])
        info = theme_info_from_directory(d, resolution=(480, 480))
        self.assertEqual(info.resolution, (480, 480))

    def test_thumbnail_fallback_to_background(self):
        """When Theme.png missing, thumbnail falls back to 00.png."""
        d = self._make_theme('004a', ['00.png'])
        info = theme_info_from_directory(d)
        self.assertIsNotNone(info.thumbnail_path)
        self.assertEqual(info.thumbnail_path.name, '00.png')

    def test_with_config_dc(self):
        d = self._make_theme('005a', ['00.png', 'config1.dc'])
        info = theme_info_from_directory(d)
        self.assertIsNotNone(info.config_path)


class TestThemeInfoFromVideo(unittest.TestCase):
    """ThemeInfo.from_video() cloud theme creation."""

    def test_basic(self):
        info = ThemeInfo.from_video(Path('/tmp/a_test.mp4'))
        self.assertEqual(info.name, 'a_test')
        self.assertEqual(info.theme_type, ThemeType.CLOUD)
        self.assertTrue(info.is_animated)

    def test_category_from_name(self):
        info = ThemeInfo.from_video(Path('/tmp/b_galaxy.mp4'))
        self.assertEqual(info.category, 'b')


# =============================================================================
# DeviceInfo
# =============================================================================

class TestDeviceInfo(unittest.TestCase):

    def test_resolution_str(self):
        d = DeviceInfo(name='LCD', path='/dev/sg0', resolution=(480, 480))
        self.assertEqual(d.resolution_str, '480x480')

    def test_defaults(self):
        d = DeviceInfo(name='LCD', path='/dev/sg0')
        self.assertEqual(d.brightness, 65)
        self.assertEqual(d.rotation, 0)
        self.assertTrue(d.connected)

    def test_button_image_default(self):
        d = DeviceInfo(name='LCD', path='/dev/sg0')
        self.assertEqual(d.button_image, 'A1CZTV')

    def test_button_image_custom(self):
        d = DeviceInfo(name='LCD', path='/dev/sg0',
                       button_image='A1FROZEN WARFRAME PRO')
        self.assertEqual(d.button_image, 'A1FROZEN WARFRAME PRO')


class TestDeviceInfoWireRoundTrip(unittest.TestCase):
    """DeviceInfo.to_wire_dict / from_wire_dict — JSON-safe IPC payload."""

    def _equal(self, a: DeviceInfo, b: DeviceInfo) -> None:
        # All identity + state fields preserved through the round-trip.
        for field in (
            'name', 'path', 'resolution', 'vendor', 'product', 'model',
            'vid', 'pid', 'usb_address', 'device_index', 'fbl_code', 'protocol',
            'device_type', 'implementation', 'button_image', 'pm_byte',
            'sub_byte', 'led_style_id', 'led_style_sub',
            'connected', 'brightness', 'rotation',
        ):
            self.assertEqual(getattr(a, field), getattr(b, field),
                             f"field mismatch: {field}")

    def test_minimal_round_trip(self):
        from trcc.core.models.device import DeviceInfo
        original = DeviceInfo(name='LCD', path='/dev/sg0')
        restored = DeviceInfo.from_wire_dict(original.to_wire_dict())
        self._equal(original, restored)

    def test_full_scsi_device_round_trip(self):
        """SCSI device — usb_address=None, all identity fields populated."""
        from trcc.core.models.device import DeviceInfo
        original = DeviceInfo(
            name='Frozen Warframe Pro',
            path='/dev/sg5',
            resolution=(320, 320),
            vendor='ALi Corp',
            product='Xsail',
            model='FROZEN_WARFRAME',
            vid=0x0402, pid=0x3922,
            usb_address=None,
            device_index=0,
            fbl_code=100,
            protocol='scsi',
            device_type=1,
            implementation='ali_corp_lcd_v1',
            button_image='A1FROZEN WARFRAME PRO',
            pm_byte=32, sub_byte=1,
            connected=True, brightness=65, rotation=0,
        )
        restored = DeviceInfo.from_wire_dict(original.to_wire_dict())
        self._equal(original, restored)

    def test_hid_device_with_usb_address(self):
        """HID/Bulk/LY devices carry a UsbAddress — must round-trip."""
        from trcc.core.models.device import DeviceInfo, UsbAddress
        original = DeviceInfo(
            name='Trofeo Vision 1280x480',
            path='usb:5:2',
            resolution=(1280, 480),
            vid=0x0418, pid=0x5303,
            usb_address=UsbAddress(bus=5, address=2),
            device_index=1,
            fbl_code=128,
            protocol='hid',
            device_type=3,
            pm_byte=6, sub_byte=1,
        )
        restored = DeviceInfo.from_wire_dict(original.to_wire_dict())
        self._equal(original, restored)
        # Specifically verify UsbAddress is fully reconstructed (not a dict).
        assert restored.usb_address is not None
        self.assertEqual(restored.usb_address.bus, 5)
        self.assertEqual(restored.usb_address.address, 2)

    def test_resolution_tuple_survives_json_listification(self):
        """JSON has no tuple type; from_wire_dict must coerce list → tuple."""
        from trcc.core.models.device import DeviceInfo
        original = DeviceInfo(name='LCD', path='/dev/sg0', resolution=(800, 480))
        wire = original.to_wire_dict()
        # Simulate a JSON round trip — every tuple becomes a list.
        import json
        roundtripped = json.loads(json.dumps(wire))
        restored = DeviceInfo.from_wire_dict(roundtripped)
        self.assertEqual(restored.resolution, (800, 480))
        self.assertIsInstance(restored.resolution, tuple)


class TestDeviceInfoFromDetected(unittest.TestCase):
    """DeviceInfo.from_detected() factory classmethod."""

    def _make_detected(self, **overrides) -> DetectedDevice:
        defaults = dict(
            vid=0x0416, pid=0x8001,
            vendor_name="Winbond", product_name="LED Controller",
            usb_path="2-1.4", scsi_device=None,
            implementation="hid_led", model="AX120_DIGITAL",
            button_image="A1AX120 DIGITAL", protocol="led",
            device_type=1,
        )
        defaults.update(overrides)
        return DetectedDevice(**defaults)

    def test_all_fields_round_trip(self):
        d = self._make_detected()
        info = DeviceInfo.from_detected(d, device_index=3)
        self.assertEqual(info.name, "Winbond LED Controller")
        self.assertEqual(info.vid, 0x0416)
        self.assertEqual(info.pid, 0x8001)
        self.assertEqual(info.model, "AX120_DIGITAL")
        self.assertEqual(info.button_image, "A1AX120 DIGITAL")
        self.assertEqual(info.protocol, "led")
        self.assertEqual(info.implementation, "hid_led")
        self.assertEqual(info.device_index, 3)

    def test_scsi_path_used_when_present(self):
        d = self._make_detected(scsi_device="/dev/sg0")
        info = DeviceInfo.from_detected(d)
        self.assertEqual(info.path, "/dev/sg0")

    def test_usb_path_when_no_scsi(self):
        d = self._make_detected(scsi_device=None, vid=0x0416, pid=0x8001)
        info = DeviceInfo.from_detected(d)
        self.assertEqual(info.path, "2-1.4")

    def test_device_index_defaults_to_zero(self):
        d = self._make_detected()
        info = DeviceInfo.from_detected(d)
        self.assertEqual(info.device_index, 0)

    def test_led_fields_default_none(self):
        d = self._make_detected()
        info = DeviceInfo.from_detected(d)
        self.assertIsNone(info.led_style_id)
        self.assertEqual(info.led_style_sub, 0)

    def test_usb_address_threaded_for_non_scsi(self):
        """Regression #133: ``usb_address`` must survive DetectedDevice →
        DeviceInfo for every non-SCSI protocol, otherwise the factory's
        bulk/hid/ly/led lambdas raise AttributeError on ``di.usb_address``.
        Cycles ALL_DEVICES so any new device added to the registry inherits
        the regression check."""
        for (vid, pid), entry in ALL_DEVICES.items():
            if entry.protocol == 'scsi':
                continue
            with self.subTest(vid=vid, pid=pid, protocol=entry.protocol):
                d = self._make_detected(
                    vid=vid, pid=pid,
                    vendor_name=entry.vendor, product_name=entry.product,
                    usb_path="usb:3:12", scsi_device=None,
                    protocol=entry.protocol, device_type=entry.device_type,
                    implementation=entry.implementation,
                    button_image=entry.button_image, model=entry.model,
                )
                info = DeviceInfo.from_detected(d, device_index=0)
                self.assertEqual(info.usb_address, UsbAddress(3, 12))
                self.assertEqual(info.path, "usb:3:12")

    def test_usb_address_threaded_for_scsi(self):
        """SCSI path wins for DeviceInfo.path, but usb_address still carries
        the physical USB location parsed from usb_path."""
        for (vid, pid), entry in SCSI_DEVICES.items():
            with self.subTest(vid=vid, pid=pid):
                d = self._make_detected(
                    vid=vid, pid=pid,
                    vendor_name=entry.vendor, product_name=entry.product,
                    usb_path="usb:1:5", scsi_device="/dev/sg0",
                    protocol=entry.protocol, device_type=entry.device_type,
                    implementation=entry.implementation,
                    button_image=entry.button_image, model=entry.model,
                )
                info = DeviceInfo.from_detected(d)
                self.assertEqual(info.path, "/dev/sg0")
                self.assertEqual(info.usb_address, UsbAddress(1, 5))

    def test_usb_address_none_when_usb_path_unparseable(self):
        d = self._make_detected(usb_path="2-1.4")  # legacy, not 'usb:bus:addr'
        info = DeviceInfo.from_detected(d)
        self.assertIsNone(info.usb_address)


# =============================================================================
# Resolution Pipeline (pm_to_fbl, fbl_to_resolution)
# =============================================================================

class TestPmToFbl(unittest.TestCase):
    """PM byte → FBL byte mapping (C# FormCZTVInit)."""

    def test_identity_for_unknown_pm(self):
        """Unknown PM values pass through as PM=FBL."""
        self.assertEqual(pm_to_fbl(36), 36)
        self.assertEqual(pm_to_fbl(72), 72)

    def test_known_overrides(self):
        """PM values with explicit FBL overrides."""
        self.assertEqual(pm_to_fbl(5), 50)
        self.assertEqual(pm_to_fbl(7), 64)
        self.assertEqual(pm_to_fbl(9), 224)
        self.assertEqual(pm_to_fbl(32), 100)
        self.assertEqual(pm_to_fbl(64), 114)
        self.assertEqual(pm_to_fbl(65), 192)

    def test_new_v212_overrides(self):
        """New PM→FBL entries from v2.1.2 audit."""
        self.assertEqual(pm_to_fbl(13), 224)   # 960x320
        self.assertEqual(pm_to_fbl(14), 64)    # 640x480
        self.assertEqual(pm_to_fbl(15), 224)   # 640x172
        self.assertEqual(pm_to_fbl(16), 224)   # 960x540
        self.assertEqual(pm_to_fbl(17), 224)   # 960x320
        self.assertEqual(pm_to_fbl(66), 192)   # 1920x462
        self.assertEqual(pm_to_fbl(68), 192)   # 1280x480
        self.assertEqual(pm_to_fbl(69), 192)   # 1920x440

    def test_pm_sub_compound_keys(self):
        """PM+SUB compound keys for special device configurations."""
        self.assertEqual(pm_to_fbl(1, sub=48), 114)  # 1600x720
        self.assertEqual(pm_to_fbl(1, sub=49), 192)  # 1920x462
        # Without sub, PM=1 → FBL=1 (identity)
        self.assertEqual(pm_to_fbl(1), 1)


class TestFblToResolution(unittest.TestCase):
    """FBL byte → (width, height) mapping."""

    def test_every_fbl_resolution(self):
        """Every FBL in FBL_PROFILES resolves to the correct resolution."""
        expected = {
            36:  (240, 240),
            37:  (240, 240),
            50:  (320, 240),
            51:  (320, 240),
            53:  (320, 240),
            54:  (360, 360),
            58:  (320, 240),
            64:  (640, 480),
            72:  (480, 480),
            100: (320, 320),
            101: (320, 320),
            102: (320, 320),
            114: (1600, 720),
            128: (1280, 480),
            129: (480, 480),
            192: (1920, 462),
            224: (854, 480),
        }
        for fbl, res in expected.items():
            with self.subTest(fbl=fbl):
                self.assertEqual(fbl_to_resolution(fbl), res)

    def test_unknown_fbl_defaults_320x320(self):
        self.assertEqual(fbl_to_resolution(999), (320, 320))

    def test_fbl_224_default_854x480(self):
        """FBL 224 without PM disambiguation → 854x480."""
        self.assertEqual(fbl_to_resolution(224), (854, 480))

    def test_fbl_224_disambiguation(self):
        """FBL 224 with PM byte → correct resolution."""
        self.assertEqual(fbl_to_resolution(224, pm=9), (854, 480))   # default
        self.assertEqual(fbl_to_resolution(224, pm=10), (960, 540))
        self.assertEqual(fbl_to_resolution(224, pm=11), (854, 480))  # default
        self.assertEqual(fbl_to_resolution(224, pm=12), (800, 480))

    def test_fbl_224_new_resolutions(self):
        """New FBL 224 entries from v2.1.2: 960x320 and 640x172."""
        self.assertEqual(fbl_to_resolution(224, pm=13), (960, 320))
        self.assertEqual(fbl_to_resolution(224, pm=15), (640, 172))
        self.assertEqual(fbl_to_resolution(224, pm=16), (960, 540))
        self.assertEqual(fbl_to_resolution(224, pm=17), (960, 320))

    def test_fbl_192_default_1920x462(self):
        """FBL 192 without PM disambiguation → 1920x462."""
        self.assertEqual(fbl_to_resolution(192), (1920, 462))
        self.assertEqual(fbl_to_resolution(192, pm=65), (1920, 462))

    def test_fbl_192_disambiguation(self):
        """New FBL 192 entries from v2.1.2: 1280x480 and 1920x440."""
        self.assertEqual(fbl_to_resolution(192, pm=68), (1280, 480))
        self.assertEqual(fbl_to_resolution(192, pm=69), (1920, 440))


class TestEndToEndResolutionPipeline(unittest.TestCase):
    """Full PM → pm_to_fbl → fbl_to_resolution pipeline per C# v2.1.2 reference."""

    def _resolve(self, pm: int, sub: int = 0) -> tuple[int, int]:
        fbl = pm_to_fbl(pm, sub)
        return fbl_to_resolution(fbl, pm)

    def test_pm5_320x240(self):
        self.assertEqual(self._resolve(5), (320, 240))

    def test_pm7_640x480(self):
        self.assertEqual(self._resolve(7), (640, 480))

    def test_pm9_854x480(self):
        self.assertEqual(self._resolve(9), (854, 480))

    def test_pm10_960x540(self):
        self.assertEqual(self._resolve(10), (960, 540))

    def test_pm12_800x480(self):
        self.assertEqual(self._resolve(12), (800, 480))

    def test_pm13_960x320(self):
        self.assertEqual(self._resolve(13), (960, 320))

    def test_pm14_640x480(self):
        self.assertEqual(self._resolve(14), (640, 480))

    def test_pm15_640x172(self):
        self.assertEqual(self._resolve(15), (640, 172))

    def test_pm16_960x540(self):
        self.assertEqual(self._resolve(16), (960, 540))

    def test_pm17_960x320(self):
        self.assertEqual(self._resolve(17), (960, 320))

    def test_pm32_320x320(self):
        self.assertEqual(self._resolve(32), (320, 320))

    def test_pm64_1600x720(self):
        self.assertEqual(self._resolve(64), (1600, 720))

    def test_pm65_1920x462(self):
        self.assertEqual(self._resolve(65), (1920, 462))

    def test_pm66_1920x462(self):
        self.assertEqual(self._resolve(66), (1920, 462))

    def test_pm68_1280x480(self):
        self.assertEqual(self._resolve(68), (1280, 480))

    def test_pm69_1920x440(self):
        self.assertEqual(self._resolve(69), (1920, 440))

    def test_pm1_sub48_1600x720(self):
        self.assertEqual(self._resolve(1, sub=48), (1600, 720))

    def test_pm1_sub49_1920x462(self):
        self.assertEqual(self._resolve(1, sub=49), (1920, 462))


# =============================================================================
# DeviceProfile — encoding properties per FBL
# =============================================================================


class TestDeviceProfileCompleteness(unittest.TestCase):
    """Every FBL in FBL_PROFILES has correct encoding properties."""

    def test_all_fbls_accounted_for(self):
        """FBL_PROFILES contains exactly the expected 18 device entries."""
        expected_fbls = {36, 37, 50, 51, 52, 53, 54, 58, 64, 72,
                         100, 101, 102, 114, 128, 129, 192, 224}
        self.assertEqual(set(FBL_PROFILES.keys()), expected_fbls)

    def test_jpeg_fbls(self):
        """Only JPEG FBLs are flagged as JPEG."""
        jpeg_fbls = {fbl for fbl, p in FBL_PROFILES.items() if p.jpeg}
        self.assertEqual(jpeg_fbls, {54, 114, 128, 192, 224})

    def test_big_endian_fbls(self):
        """Only big-endian FBLs are flagged as big-endian."""
        be_fbls = {fbl for fbl, p in FBL_PROFILES.items() if p.big_endian}
        self.assertEqual(be_fbls, {100, 101, 102})

    def test_rotate_fbls(self):
        """Only portrait/landscape-rotated FBLs are flagged."""
        rot_fbls = {fbl for fbl, p in FBL_PROFILES.items() if p.rotate}
        self.assertEqual(rot_fbls, {50, 51, 52, 53, 58, 64, 114, 128, 192, 224})

    def test_byte_order_property(self):
        """byte_order returns '>' for big-endian, '<' for little-endian."""
        for fbl, profile in FBL_PROFILES.items():
            with self.subTest(fbl=fbl):
                expected = '>' if profile.big_endian else '<'
                self.assertEqual(profile.byte_order, expected)

    def test_resolution_property(self):
        """resolution property returns (width, height) tuple."""
        for fbl, profile in FBL_PROFILES.items():
            with self.subTest(fbl=fbl):
                self.assertEqual(profile.resolution, (profile.width, profile.height))

    def test_get_profile_returns_profile_for_every_fbl(self):
        """get_profile() returns a DeviceProfile for every known FBL."""
        for fbl in FBL_PROFILES:
            with self.subTest(fbl=fbl):
                p = get_profile(fbl)
                self.assertIsInstance(p, DeviceProfile)
                self.assertEqual(p.resolution, FBL_PROFILES[fbl].resolution)

    def test_get_profile_unknown_fbl_defaults_320x320(self):
        """Unknown FBL defaults to 320x320 big-endian."""
        p = get_profile(999)
        self.assertEqual(p.resolution, (320, 320))
        self.assertTrue(p.big_endian)
        self.assertFalse(p.jpeg)


class TestDeviceProfilePerFbl(unittest.TestCase):
    """Per-FBL encoding property tests — one test per device type."""

    def test_fbl_36_240x240_rgb565_le(self):
        p = get_profile(36)
        self.assertEqual(p.resolution, (240, 240))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)
        self.assertFalse(p.rotate)

    def test_fbl_37_240x240_rgb565_le(self):
        """FBL 37 is an alias for 36 — same device, same encoding."""
        p = get_profile(37)
        self.assertEqual(p.resolution, (240, 240))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)

    def test_fbl_50_320x240_rgb565_le_rotated(self):
        p = get_profile(50)
        self.assertEqual(p.resolution, (320, 240))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)
        self.assertTrue(p.rotate)

    def test_fbl_51_320x240_rgb565_le(self):
        """FBL 51 HID Type 2 — little-endian RGB565 (SPIMode=2 only for SPI mode 1)."""
        p = get_profile(51)
        self.assertEqual(p.resolution, (320, 240))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)
        self.assertTrue(p.rotate)

    def test_fbl_53_320x240_rgb565_le(self):
        """FBL 53 HID Type 2 — little-endian RGB565."""
        p = get_profile(53)
        self.assertEqual(p.resolution, (320, 240))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)
        self.assertTrue(p.rotate)

    def test_fbl_54_360x360_jpeg(self):
        p = get_profile(54)
        self.assertEqual(p.resolution, (360, 360))
        self.assertTrue(p.jpeg)
        self.assertFalse(p.rotate)

    def test_fbl_58_320x240_rgb565_le_rotated(self):
        p = get_profile(58)
        self.assertEqual(p.resolution, (320, 240))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)
        self.assertTrue(p.rotate)

    def test_fbl_64_640x480_rgb565_le_rotated(self):
        p = get_profile(64)
        self.assertEqual(p.resolution, (640, 480))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)
        self.assertTrue(p.rotate)

    def test_fbl_72_480x480_rgb565_le(self):
        p = get_profile(72)
        self.assertEqual(p.resolution, (480, 480))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)
        self.assertFalse(p.rotate)

    def test_fbl_100_320x320_rgb565_be(self):
        p = get_profile(100)
        self.assertEqual(p.resolution, (320, 320))
        self.assertFalse(p.jpeg)
        self.assertTrue(p.big_endian)
        self.assertFalse(p.rotate)

    def test_fbl_101_320x320_rgb565_be(self):
        """FBL 101 is an alias for 100."""
        p = get_profile(101)
        self.assertEqual(p.resolution, (320, 320))
        self.assertTrue(p.big_endian)

    def test_fbl_102_320x320_rgb565_be(self):
        """FBL 102 is an alias for 100."""
        p = get_profile(102)
        self.assertEqual(p.resolution, (320, 320))
        self.assertTrue(p.big_endian)

    def test_fbl_114_1600x720_jpeg_rotated(self):
        p = get_profile(114)
        self.assertEqual(p.resolution, (1600, 720))
        self.assertTrue(p.jpeg)
        self.assertTrue(p.rotate)

    def test_fbl_128_1280x480_jpeg_rotated(self):
        p = get_profile(128)
        self.assertEqual(p.resolution, (1280, 480))
        self.assertTrue(p.jpeg)
        self.assertTrue(p.rotate)

    def test_fbl_129_480x480_rgb565_le(self):
        """FBL 129 is an alias for 72."""
        p = get_profile(129)
        self.assertEqual(p.resolution, (480, 480))
        self.assertFalse(p.jpeg)
        self.assertFalse(p.big_endian)

    def test_fbl_192_1920x462_jpeg_rotated(self):
        p = get_profile(192)
        self.assertEqual(p.resolution, (1920, 462))
        self.assertTrue(p.jpeg)
        self.assertTrue(p.rotate)

    def test_fbl_224_854x480_jpeg_rotated(self):
        p = get_profile(224)
        self.assertEqual(p.resolution, (854, 480))
        self.assertTrue(p.jpeg)
        self.assertTrue(p.rotate)


# =============================================================================
# VideoState
# =============================================================================

class TestVideoState(unittest.TestCase):

    def test_progress_zero_frames(self):
        s = VideoState(total_frames=0)
        self.assertEqual(s.progress, 0.0)

    def test_progress_halfway(self):
        s = VideoState(current_frame=50, total_frames=100)
        self.assertAlmostEqual(s.progress, 50.0)

    def test_time_str(self):
        s = VideoState(current_frame=960, total_frames=1920, fps=16.0)
        self.assertEqual(s.current_time_str, '01:00')
        self.assertEqual(s.total_time_str, '02:00')

    def test_frame_interval(self):
        s = VideoState(fps=16.0)
        self.assertEqual(s.frame_interval_ms, 62)

    def test_frame_interval_zero_fps(self):
        s = VideoState(fps=0)
        self.assertEqual(s.frame_interval_ms, 62)

    def test_time_str_zero_fps(self):
        s = VideoState(fps=0)
        self.assertEqual(s.current_time_str, '00:00')


class TestVideoStateTotalTimeStr(unittest.TestCase):

    def test_zero_fps(self):
        vs = VideoState()
        vs.fps = 0
        self.assertEqual(vs.total_time_str, "00:00")


# =============================================================================
# parse_hex_color
# =============================================================================

class TestParseHexColor:
    """parse_hex_color() — shared hex color parser."""

    @pytest.mark.parametrize("input_hex,expected", [
        ("ff0000", (255, 0, 0)),
        ("#ff0000", (255, 0, 0)),
        ("00ff00", (0, 255, 0)),
        ("#00FF00", (0, 255, 0)),
        ("0000ff", (0, 0, 255)),
        ("000000", (0, 0, 0)),
        ("ffffff", (255, 255, 255)),
        ("#abcdef", (171, 205, 239)),
    ])
    def test_valid_colors(self, input_hex, expected):
        assert parse_hex_color(input_hex) == expected

    @pytest.mark.parametrize("invalid", [
        "", "fff", "fffffff", "gggggg", "#xyz", "12345",
        "#12345g", "not-a-color",
    ])
    def test_invalid_returns_none(self, invalid):
        assert parse_hex_color(invalid) is None


# =============================================================================
# ProtocolTraits
# =============================================================================


class TestProtocolTraits:
    """PROTOCOL_TRAITS registry — single source of truth for protocol behavior."""

    def test_all_protocols_have_traits(self):
        """Every known protocol has an entry in PROTOCOL_TRAITS."""
        from trcc.core.models import PROTOCOL_TRAITS
        for proto in ('scsi', 'hid', 'bulk', 'ly', 'led'):
            assert proto in PROTOCOL_TRAITS, f"Missing traits for {proto}"

    def test_scsi_traits(self):
        from trcc.core.models import PROTOCOL_TRAITS
        t = PROTOCOL_TRAITS['scsi']
        assert t.udev_subsystems == ('scsi_generic',)
        assert t.backend_key == 'sg_raw'
        assert t.fallback_backend is None
        assert t.requires_reboot is True
        assert t.supports_jpeg is False
        assert t.is_led is False

    def test_hid_traits(self):
        from trcc.core.models import PROTOCOL_TRAITS
        t = PROTOCOL_TRAITS['hid']
        assert t.udev_subsystems == ('hidraw', 'usb')
        assert t.backend_key == 'pyusb'
        assert t.fallback_backend == 'hidapi'
        assert t.requires_reboot is False

    def test_bulk_ly_support_jpeg(self):
        from trcc.core.models import PROTOCOL_TRAITS
        assert PROTOCOL_TRAITS['bulk'].supports_jpeg is True
        assert PROTOCOL_TRAITS['ly'].supports_jpeg is True

    def test_led_is_led(self):
        from trcc.core.models import PROTOCOL_TRAITS
        t = PROTOCOL_TRAITS['led']
        assert t.is_led is True
        assert t.supports_jpeg is False

    def test_only_scsi_requires_reboot(self):
        from trcc.core.models import PROTOCOL_TRAITS
        for name, t in PROTOCOL_TRAITS.items():
            if name == 'scsi':
                assert t.requires_reboot is True
            else:
                assert t.requires_reboot is False, f"{name} should not require reboot"

    def test_traits_are_frozen(self):
        from trcc.core.models import PROTOCOL_TRAITS
        t = PROTOCOL_TRAITS['scsi']
        with pytest.raises(AttributeError):
            t.requires_reboot = False  # type: ignore[misc]


# VID/PID constants for the three C# device-class families
_LED  = (0x0416, 0x8001)  # case 1
_HID2 = (0x0416, 0x5302)  # case 2
_BULK = (0x87AD, 0x70DB)  # case 257


class TestGetButtonImage:
    """get_button_image() — (VID, PID, PM, SUB) → button image name (#69)."""

    def test_stream_vision_pm7_sub1(self):
        """Bulk PM=7 SUB=1 → Stream Vision (not Frozen Warframe Pro)."""
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 7, 1) == 'A1Stream Vision'

    def test_fbl64_sub0_is_frozen_warframe_pro(self):
        """Bulk PM=64 SUB=0 → Frozen Warframe Pro."""
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 64, 0) == 'A1FROZEN WARFRAME PRO'

    def test_pm7_sub1_differs_from_fbl64(self):
        """PM=7 SUB=1 and PM=64 SUB=0 must resolve differently (#69)."""
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 7, 1) != get_button_image(*_BULK, 64, 0)

    def test_pm32_sub1_frozen_warframe_pro(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 32, 1) == 'A1FROZEN WARFRAME PRO'

    def test_unknown_pm_returns_none(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 255) is None

    def test_unknown_vid_pid_returns_none(self):
        from trcc.core.models import get_button_image
        assert get_button_image(0xDEAD, 0xBEEF, 1, 0) is None

    # -- Bulk family (C# case 257) --

    def test_bulk_pm100_frozen_warframe_pro(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 100, 0) == 'A1FROZEN WARFRAME PRO'

    def test_bulk_pm101_elite_vision(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 101, 0) == 'A1ELITE VISION'

    def test_bulk_pm50_frozen_warframe(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 50, 0) == 'A1FROZEN WARFRAME'

    # -- PM=9 SUB split (C#: sub<5→LC2JD, sub>=5→LF19) --

    def test_pm9_sub0_lc2jd(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 9, 0) == 'A1LC2JD'

    def test_pm9_sub4_lc2jd(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 9, 4) == 'A1LC2JD'

    def test_pm9_sub5_lf19(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 9, 5) == 'A1LF19'

    # -- HID T2 family (C# case 2) --

    def test_hid_t2_pm49_frozen_warframe(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_HID2, 49, 0) == 'A1FROZEN WARFRAME'

    def test_bulk_pm65_sub2_lf14(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_BULK, 65, 2) == 'A1LF14'

    # -- LED family (C# case 1) --

    def test_led_pm80_lf12(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_LED, 80, 0) == 'A1LF12'

    def test_led_pm1_frozen_horizon(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_LED, 1, 0) == 'A1FROZEN HORIZON PRO'

    def test_led_pm208_cz1(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_LED, 208, 0) == 'A1CZ1'

    def test_led_unknown_pm_returns_none(self):
        from trcc.core.models import get_button_image
        assert get_button_image(*_LED, 255, 0) is None

    # -- PM collision across VID/PID families: VID/PID scoping disambiguates --

    def test_pm1_led_vs_bulk_differ(self):
        """PM=1: LED family → FROZEN_HORIZON_PRO, Bulk family → GRAND_VISION."""
        from trcc.core.models import get_button_image
        assert get_button_image(*_LED, 1, 0) == 'A1FROZEN HORIZON PRO'
        assert get_button_image(*_BULK, 1, 0) == 'A1GRAND VISION'

    def test_pm49_led_vs_hid_t2_differ(self):
        """PM=49: LED family → LF10, HID T2 family → FROZEN_WARFRAME."""
        from trcc.core.models import get_button_image
        assert get_button_image(*_LED, 49, 0) == 'A1LF10'
        assert get_button_image(*_HID2, 49, 0) == 'A1FROZEN WARFRAME'


# =============================================================================
# VariantOverride registry + lookup
# =============================================================================


class TestGetVariantOverride:
    """get_variant_override() — (VID, PID, PM, SUB) → full override record."""

    def test_known_bulk_pm_returns_override(self):
        from trcc.core.models import VariantOverride, get_variant_override
        override = get_variant_override(*_BULK, 7, 1)
        assert override is not None
        assert isinstance(override, VariantOverride)
        assert override.button_image == 'A1Stream Vision'

    def test_known_led_pm_returns_override(self):
        from trcc.core.models import get_variant_override
        override = get_variant_override(*_LED, 80, 0)
        assert override is not None
        assert override.button_image == 'A1LF12'

    def test_unknown_pm_returns_none(self):
        from trcc.core.models import get_variant_override
        assert get_variant_override(*_BULK, 255, 0) is None

    def test_panel_cutout_only_on_levita_row(self):
        """Phase B fills panel_cutout on the Levita row; other rows leave it empty."""
        from trcc.core.models import get_variant_override
        # Levita: Bulk PM=64 SUB=3 — right-side camera notch on 1600x720 panel.
        levita = get_variant_override(*_BULK, 64, 3)
        assert levita is not None
        assert levita.panel_cutout is not None
        assert levita.panel_cutout.x > 800  # right half of a 1600-wide canvas
        # Sibling rows on the same PM stay flat (no notch).
        sibling = get_variant_override(*_BULK, 64, 0)
        assert sibling is not None
        assert sibling.panel_cutout is None


# =============================================================================
# DeviceInfo enrichment chokepoints
# =============================================================================


class TestEnrichFromHandshake:
    """DeviceInfo.enrich_from_handshake — single chokepoint for handshake-driven mutation."""

    @staticmethod
    def _make_device(button_image: str = 'A1CZTV'):
        # Bulk family (0x87AD:0x70DB) — most PM rows live here in the
        # VID/PID-scoped registry, so the enrich path resolves to a real
        # VariantOverride and exercises the production lookup flow.
        return DeviceInfo(
            name='LCD Display', path='usb:1:1', vendor='Test', product='LCD',
            model='CZTV', vid=0x87AD, pid=0x70DB, device_index=0,
            protocol='bulk', device_type=4, implementation='bulk_usblcdnew',
            button_image=button_image,
        )

    def test_copies_resolution_pm_sub_fbl(self):
        from trcc.core.models import HandshakeResult
        dev = self._make_device()
        dev.enrich_from_handshake(HandshakeResult(
            resolution=(320, 320), model_id=100, pm_byte=0, sub_byte=0))
        assert dev.resolution == (320, 320)
        assert dev.fbl_code == 100
        assert dev.pm_byte == 0
        assert dev.sub_byte == 0

    def test_resolves_button_image_via_variant_override(self):
        from trcc.core.models import HandshakeResult
        dev = self._make_device()
        # PM=7 SUB=1 → A1Stream Vision (HID-style fingerprint)
        dev.enrich_from_handshake(HandshakeResult(
            resolution=(480, 480), model_id=64, pm_byte=7, sub_byte=1))
        assert dev.button_image == 'A1Stream Vision'

    def test_falls_back_to_fbl_when_pm_zero(self):
        """PM=0 → use fbl_code as effective_pm (SCSI / some Bulk devices)."""
        from trcc.core.models import HandshakeResult
        dev = self._make_device()
        # PM=0, FBL=100 → effective_pm=100 → A1FROZEN WARFRAME PRO
        dev.enrich_from_handshake(HandshakeResult(
            resolution=(320, 320), model_id=100, pm_byte=0, sub_byte=0))
        assert dev.button_image == 'A1FROZEN WARFRAME PRO'

    def test_unknown_pm_keeps_prior_button_image(self):
        from trcc.core.models import HandshakeResult
        dev = self._make_device(button_image='A1CZTV')
        dev.enrich_from_handshake(HandshakeResult(
            resolution=(320, 320), model_id=255, pm_byte=0, sub_byte=0))
        assert dev.button_image == 'A1CZTV'

    def test_none_result_is_no_op(self):
        dev = self._make_device()
        dev.resolution = (320, 320)
        dev.enrich_from_handshake(None)
        assert dev.resolution == (320, 320)

    def test_zero_resolution_is_ignored(self):
        """Failed handshake returns resolution=(0,0) — must not overwrite prior."""
        from trcc.core.models import HandshakeResult
        dev = self._make_device()
        dev.resolution = (480, 480)
        dev.enrich_from_handshake(HandshakeResult(resolution=(0, 0)))
        assert dev.resolution == (480, 480)


class TestEnrichFromLedProbe:
    """DeviceInfo.enrich_from_led_probe — sister chokepoint for LED probe results."""

    @staticmethod
    def _make_led_device():
        return DeviceInfo(
            name='LED', path='hid:0416:8001', vendor='Test', product='LED',
            vid=0x0416, pid=0x8001, device_index=0,
            protocol='led', device_type=1, implementation='hid_led',
        )

    def test_none_probe_is_no_op(self):
        dev = self._make_led_device()
        dev.enrich_from_led_probe(None)
        assert dev.pm_byte == 0

    def test_probe_without_style_is_no_op(self):
        from trcc.core.models import LedHandshakeInfo
        dev = self._make_led_device()
        dev.enrich_from_led_probe(LedHandshakeInfo(pm=80, sub_type=0, style=None))
        assert dev.pm_byte == 0  # not written because style was None

    def test_probe_with_style_writes_all_fields(self):
        """Probe with non-None style → pm/sub/led_style_id/model written; LED variant override resolved."""
        from trcc.core.models import LED_STYLES, LedHandshakeInfo
        dev = self._make_led_device()
        # PM=80 → LF12 (resolves through LED variant table)
        style_id = LED_STYLES.by_name('LF12')
        style = LED_STYLES[style_id]
        dev.enrich_from_led_probe(LedHandshakeInfo(
            pm=80, sub_type=0, style=style, style_sub=0))
        assert dev.pm_byte == 80
        assert dev.sub_byte == 0
        assert dev.button_image == 'A1LF12'


# =============================================================================
# Overlay config builders (parse_metric_spec, build_overlay_config)
# =============================================================================


class TestParseMetricSpec:
    """Tests for parse_metric_spec() — CLI metric spec → overlay element."""

    def test_basic_spec(self):
        from trcc.core.models import parse_metric_spec
        key, elem = parse_metric_spec('gpu_temp:10,20', 0)
        assert key == 'cli_elem_0'
        assert elem['x'] == 10
        assert elem['y'] == 20
        assert elem['metric'] == 'gpu_temp'
        assert elem['enabled'] is True
        assert elem['color'] == '#ffffff'
        assert elem['font']['size'] == 14

    def test_with_color_override(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('cpu_percent:50,100:ff0000', 1)
        assert elem['color'] == '#ff0000'
        assert elem['font']['size'] == 14

    def test_with_color_and_size_override(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('time:150,10:ffffff:24', 2)
        assert elem['color'] == '#ffffff'
        assert elem['font']['size'] == 24
        assert elem['metric'] == 'time'

    def test_custom_defaults(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec(
            'gpu_usage:5,5', 0,
            default_color='00ff00', default_size=20,
            default_font='Arial', default_style='bold')
        assert elem['color'] == '#00ff00'
        assert elem['font']['size'] == 20
        assert elem['font']['name'] == 'Arial'
        assert elem['font']['style'] == 'bold'

    def test_time_format_field(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('time:10,10', 0)
        assert 'time_format' in elem

    def test_date_format_field(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('date:10,10', 0)
        assert 'date_format' in elem

    def test_temp_metric_has_temp_unit(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('cpu_temp:10,10', 0)
        assert 'temp_unit' in elem

    def test_invalid_key_raises(self):
        from trcc.core.models import parse_metric_spec
        with pytest.raises(ValueError, match="Unknown metric key"):
            parse_metric_spec('not_a_metric:10,10', 0)

    def test_missing_coords_raises(self):
        from trcc.core.models import parse_metric_spec
        with pytest.raises(ValueError, match="Invalid"):
            parse_metric_spec('gpu_temp', 0)

    def test_bad_coords_raises(self):
        from trcc.core.models import parse_metric_spec
        with pytest.raises(ValueError, match="Invalid coordinates"):
            parse_metric_spec('gpu_temp:abc,def', 0)

    def test_bad_size_raises(self):
        from trcc.core.models import parse_metric_spec
        with pytest.raises(ValueError, match="Invalid size"):
            parse_metric_spec('gpu_temp:10,20:ff0000:notanint', 0)

    def test_color_with_hash_stripped(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('gpu_temp:10,20:#aabbcc', 0)
        assert elem['color'] == '#aabbcc'

    def test_empty_color_uses_default(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('gpu_temp:10,20::18', 0)
        assert elem['color'] == '#ffffff'
        assert elem['font']['size'] == 18

    def test_per_metric_font_override(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('gpu_temp:10,20:ff0000:18:Arial:bold', 0)
        assert elem['font']['name'] == 'Arial'
        assert elem['font']['style'] == 'bold'
        assert elem['font']['size'] == 18
        assert elem['color'] == '#ff0000'

    def test_per_metric_font_without_style(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('cpu_temp:10,20::16:Courier', 0)
        assert elem['font']['name'] == 'Courier'
        assert elem['font']['style'] == 'regular'
        assert elem['font']['size'] == 16

    def test_per_metric_font_uses_global_when_empty(self):
        from trcc.core.models import parse_metric_spec
        _, elem = parse_metric_spec('gpu_temp:10,20', 0,
                                     default_font='Mono', default_style='bold')
        assert elem['font']['name'] == 'Mono'
        assert elem['font']['style'] == 'bold'


class TestBuildOverlayConfig:
    """Tests for build_overlay_config() — multiple specs → config dict."""

    def test_single_metric(self):
        from trcc.core.models import build_overlay_config
        config = build_overlay_config(['gpu_temp:10,20'])
        assert len(config) == 1
        assert 'cli_elem_0' in config

    def test_multiple_metrics(self):
        from trcc.core.models import build_overlay_config
        config = build_overlay_config([
            'gpu_temp:10,20',
            'cpu_percent:10,50',
            'time:150,10',
        ])
        assert len(config) == 3

    def test_global_defaults_applied(self):
        from trcc.core.models import build_overlay_config
        config = build_overlay_config(
            ['gpu_temp:10,20'],
            default_color='00ff00',
            default_font_size=20,
            default_font='Arial',
            default_style='bold',
        )
        elem = config['cli_elem_0']
        assert elem['color'] == '#00ff00'
        assert elem['font']['size'] == 20
        assert elem['font']['name'] == 'Arial'
        assert elem['font']['style'] == 'bold'

    def test_format_overrides(self):
        from trcc.core.models import build_overlay_config
        config = build_overlay_config(
            ['time:10,10', 'date:10,30', 'cpu_temp:10,50'],
            time_format=1, date_format=2, temp_unit=1,
        )
        assert config['cli_elem_0']['time_format'] == 1
        assert config['cli_elem_1']['date_format'] == 2
        assert config['cli_elem_2']['temp_unit'] == 1

    def test_invalid_metric_raises(self):
        from trcc.core.models import build_overlay_config
        with pytest.raises(ValueError, match="Unknown metric key"):
            build_overlay_config(['bogus:10,10'])

    def test_empty_list(self):
        from trcc.core.models import build_overlay_config
        config = build_overlay_config([])
        assert config == {}


class TestValidOverlayKeys:
    """Tests for VALID_OVERLAY_KEYS completeness."""

    def test_contains_hardware_metrics(self):
        from trcc.core.models import HARDWARE_METRICS, VALID_OVERLAY_KEYS
        for metric_name in HARDWARE_METRICS.values():
            assert metric_name in VALID_OVERLAY_KEYS

    def test_contains_time_date_weekday(self):
        from trcc.core.models import VALID_OVERLAY_KEYS
        assert 'time' in VALID_OVERLAY_KEYS
        assert 'date' in VALID_OVERLAY_KEYS
        assert 'weekday' in VALID_OVERLAY_KEYS

    def test_is_frozenset(self):
        from trcc.core.models import VALID_OVERLAY_KEYS
        assert isinstance(VALID_OVERLAY_KEYS, frozenset)


if __name__ == '__main__':
    unittest.main()
