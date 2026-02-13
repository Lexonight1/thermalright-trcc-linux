"""Tests for theme_io – .tr export/import round-trip and C# string encoding."""

import io
import os
import struct
import tempfile
import unittest

from PIL import Image

from trcc.theme_io import ThemeIO, export_theme, import_theme


class TestCSharpString(unittest.TestCase):
    """C# BinaryWriter 7-bit encoded length-prefixed strings."""

    def _roundtrip(self, text: str) -> str:
        buf = io.BytesIO()
        ThemeIO._write_csharp_string(buf, text)
        buf.seek(0)
        return ThemeIO._read_csharp_string(buf)

    def test_empty(self):
        self.assertEqual(self._roundtrip(''), '')

    def test_ascii(self):
        self.assertEqual(self._roundtrip('Hello'), 'Hello')

    def test_unicode(self):
        self.assertEqual(self._roundtrip('微软雅黑'), '微软雅黑')

    def test_long_string(self):
        """Strings >127 bytes need multi-byte length prefix."""
        s = 'A' * 200
        self.assertEqual(self._roundtrip(s), s)

    def test_very_long_string(self):
        s = 'X' * 20000
        self.assertEqual(self._roundtrip(s), s)

    def test_length_encoding_single_byte(self):
        """Length < 128 → single byte."""
        buf = io.BytesIO()
        ThemeIO._write_csharp_string(buf, 'AB')
        buf.seek(0)
        length_byte = struct.unpack('B', buf.read(1))[0]
        self.assertEqual(length_byte, 2)

    def test_length_encoding_multi_byte(self):
        """Length >= 128 → first byte has high bit set."""
        buf = io.BytesIO()
        ThemeIO._write_csharp_string(buf, 'A' * 200)
        buf.seek(0)
        first = struct.unpack('B', buf.read(1))[0]
        self.assertTrue(first & 0x80)  # continuation bit set


class TestExportImportRoundtrip(unittest.TestCase):
    """Full .tr export → import round-trip."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_minimal_roundtrip(self):
        """Export with no background/mask, import and verify config."""
        tr_path = os.path.join(self.tmpdir, 'test.tr')
        out_dir = os.path.join(self.tmpdir, 'imported')

        export_theme(
            output_path=tr_path,
            overlay_elements=[],
            show_system_info=False,
            show_background=True,
            show_screenshot=False,
            direction=90,
            ui_mode=1,
            mode=0,
            hide_screenshot_bg=True,
            screenshot_rect=(0, 0, 320, 320),
            show_mask=False,
            mask_center=(160, 160),
            mask_image=None,
            background_image=None,
        )

        result = import_theme(tr_path, out_dir)
        self.assertFalse(result['show_system_info'])
        self.assertTrue(result['show_background'])
        self.assertEqual(result['direction'], 90)

    def test_header_magic(self):
        tr_path = os.path.join(self.tmpdir, 'test.tr')
        export_theme(
            output_path=tr_path,
            overlay_elements=[], show_system_info=True,
            show_background=True, show_screenshot=False,
            direction=0, ui_mode=1, mode=0,
            hide_screenshot_bg=True, screenshot_rect=(0, 0, 320, 320),
            show_mask=False, mask_center=(160, 160),
            mask_image=None, background_image=None,
        )
        with open(tr_path, 'rb') as f:
            self.assertEqual(f.read(4), b'\xDD\xDC\xDD\xDC')

    def test_with_background_image(self):
        tr_path = os.path.join(self.tmpdir, 'test.tr')
        out_dir = os.path.join(self.tmpdir, 'imported')
        bg = Image.new('RGB', (320, 320), (0, 100, 200))

        export_theme(
            output_path=tr_path,
            overlay_elements=[], show_system_info=True,
            show_background=True, show_screenshot=False,
            direction=0, ui_mode=1, mode=0,
            hide_screenshot_bg=True, screenshot_rect=(0, 0, 320, 320),
            show_mask=False, mask_center=(160, 160),
            mask_image=None, background_image=bg,
        )

        result = import_theme(tr_path, out_dir)
        self.assertTrue(result['has_background'])
        self.assertTrue(os.path.exists(os.path.join(out_dir, '00.png')))

    def test_with_mask_image(self):
        tr_path = os.path.join(self.tmpdir, 'test.tr')
        out_dir = os.path.join(self.tmpdir, 'imported')
        mask = Image.new('RGBA', (320, 320), (255, 0, 0, 128))

        export_theme(
            output_path=tr_path,
            overlay_elements=[], show_system_info=True,
            show_background=True, show_screenshot=False,
            direction=0, ui_mode=1, mode=0,
            hide_screenshot_bg=True, screenshot_rect=(0, 0, 320, 320),
            show_mask=True, mask_center=(160, 160),
            mask_image=mask, background_image=None,
        )

        result = import_theme(tr_path, out_dir)
        self.assertTrue(result['has_mask'])
        self.assertTrue(os.path.exists(os.path.join(out_dir, '01.png')))

    def test_overlay_elements_roundtrip(self):
        tr_path = os.path.join(self.tmpdir, 'test.tr')
        out_dir = os.path.join(self.tmpdir, 'imported')

        elements = [
            {'mode': 1, 'mode_sub': 0, 'x': 10, 'y': 20,
             'main_count': 0, 'sub_count': 0,
             'font_name': 'Arial', 'font_size': 24.0,
             'color': '#FF6B35', 'text': ''},
            {'mode': 4, 'mode_sub': 0, 'x': 50, 'y': 100,
             'main_count': 0, 'sub_count': 0,
             'font_name': 'Microsoft YaHei', 'font_size': 16.0,
             'color': '#FFFFFF', 'text': 'Hello'},
        ]

        export_theme(
            output_path=tr_path,
            overlay_elements=elements, show_system_info=True,
            show_background=True, show_screenshot=False,
            direction=0, ui_mode=1, mode=0,
            hide_screenshot_bg=True, screenshot_rect=(0, 0, 320, 320),
            show_mask=False, mask_center=(160, 160),
            mask_image=None, background_image=None,
        )

        result = import_theme(tr_path, out_dir)
        self.assertEqual(len(result['elements']), 2)

        e0 = result['elements'][0]
        self.assertEqual(e0['mode'], 1)
        self.assertEqual(e0['x'], 10)
        self.assertEqual(e0['y'], 20)
        self.assertEqual(e0['font_name'], 'Arial')
        self.assertAlmostEqual(e0['font_size'], 24.0, places=1)

        e1 = result['elements'][1]
        self.assertEqual(e1['mode'], 4)
        self.assertEqual(e1['text'], 'Hello')

    def test_screenshot_rect_roundtrip(self):
        tr_path = os.path.join(self.tmpdir, 'test.tr')
        out_dir = os.path.join(self.tmpdir, 'imported')

        export_theme(
            output_path=tr_path,
            overlay_elements=[], show_system_info=False,
            show_background=False, show_screenshot=True,
            direction=180, ui_mode=2, mode=1,
            hide_screenshot_bg=False, screenshot_rect=(10, 20, 300, 280),
            show_mask=True, mask_center=(100, 200),
            mask_image=None, background_image=None,
        )

        result = import_theme(tr_path, out_dir)
        self.assertTrue(result['show_screenshot'])
        self.assertEqual(result['direction'], 180)
        self.assertEqual(result['screenshot_rect'], (10, 20, 300, 280))
        self.assertTrue(result['show_mask'])
        self.assertEqual(result['mask_center'], (100, 200))

    def test_invalid_header_raises(self):
        bad_path = os.path.join(self.tmpdir, 'bad.tr')
        with open(bad_path, 'wb') as f:
            f.write(b'\xAA\xBB\xCC\xDD' + b'\x00' * 100)
        with self.assertRaises(ValueError):
            import_theme(bad_path, os.path.join(self.tmpdir, 'out'))


class TestExportImportVideo(unittest.TestCase):
    """Video/Theme.zt round-trip."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _make_theme_zt(self) -> str:
        """Create a minimal Theme.zt file with 2 frames."""
        zt_path = os.path.join(self.tmpdir, 'Theme.zt')
        with open(zt_path, 'wb') as f:
            f.write(struct.pack('B', 0xDC))  # Header
            f.write(struct.pack('<i', 2))     # 2 frames
            # Timestamps
            f.write(struct.pack('<i', 0))
            f.write(struct.pack('<i', 62))
            # Frame data
            frame1 = b'\x00\x01\x02\x03'
            frame2 = b'\x04\x05\x06\x07\x08'
            f.write(struct.pack('<i', len(frame1)))
            f.write(frame1)
            f.write(struct.pack('<i', len(frame2)))
            f.write(frame2)
        return zt_path

    def test_export_with_theme_zt(self):
        """Export with Theme.zt embeds video frames in .tr."""
        zt_path = self._make_theme_zt()
        tr_path = os.path.join(self.tmpdir, 'video.tr')

        export_theme(
            output_path=tr_path,
            overlay_elements=[], show_system_info=False,
            show_background=True, show_screenshot=False,
            direction=0, ui_mode=4, mode=0,
            hide_screenshot_bg=True, screenshot_rect=(0, 0, 320, 320),
            show_mask=False, mask_center=(160, 160),
            mask_image=None, background_image=None,
            theme_zt_path=zt_path,
        )

        self.assertTrue(os.path.exists(tr_path))
        self.assertGreater(os.path.getsize(tr_path), 100)

    def test_import_theme_zt_roundtrip(self):
        """Export with Theme.zt, then import and verify video extraction."""
        zt_path = self._make_theme_zt()
        tr_path = os.path.join(self.tmpdir, 'video.tr')
        out_dir = os.path.join(self.tmpdir, 'imported')

        export_theme(
            output_path=tr_path,
            overlay_elements=[], show_system_info=False,
            show_background=True, show_screenshot=False,
            direction=0, ui_mode=4, mode=0,
            hide_screenshot_bg=True, screenshot_rect=(0, 0, 320, 320),
            show_mask=False, mask_center=(160, 160),
            mask_image=None, background_image=None,
            theme_zt_path=zt_path,
        )

        result = import_theme(tr_path, out_dir)
        self.assertTrue(result['has_video'])
        self.assertTrue(os.path.exists(os.path.join(out_dir, 'Theme.zt')))

        # Verify extracted .zt file structure
        with open(os.path.join(out_dir, 'Theme.zt'), 'rb') as f:
            header = f.read(1)
            self.assertEqual(header, b'\xDC')
            frame_count = struct.unpack('<i', f.read(4))[0]
            self.assertEqual(frame_count, 2)

    def test_alternative_header_dc_dc(self):
        """A .tr file starting with 0xDC 0xDC returns default config."""
        alt_path = os.path.join(self.tmpdir, 'alt.tr')
        with open(alt_path, 'wb') as f:
            f.write(bytes([0xDC, 0xDC]) + b'\x00' * 200)

        result = import_theme(alt_path, os.path.join(self.tmpdir, 'out'))
        # Should return defaults without crashing
        self.assertIn('show_system_info', result)
        self.assertEqual(result['elements'], [])


if __name__ == '__main__':
    unittest.main()
