"""Tests for theme_io – .tr export/import round-trip and C# string encoding."""

import io
import os
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from trcc.theme_io import (
    _read_csharp_string,
    _write_csharp_string,
    export_theme,
    import_theme,
)


class TestCSharpString(unittest.TestCase):
    """C# BinaryWriter 7-bit encoded length-prefixed strings."""

    def _roundtrip(self, text: str) -> str:
        buf = io.BytesIO()
        _write_csharp_string(buf, text)
        buf.seek(0)
        return _read_csharp_string(buf)

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
        _write_csharp_string(buf, 'AB')
        buf.seek(0)
        length_byte = struct.unpack('B', buf.read(1))[0]
        self.assertEqual(length_byte, 2)

    def test_length_encoding_multi_byte(self):
        """Length >= 128 → first byte has high bit set."""
        buf = io.BytesIO()
        _write_csharp_string(buf, 'A' * 200)
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


if __name__ == '__main__':
    unittest.main()
