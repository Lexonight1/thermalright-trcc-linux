"""
Tests for dc_writer.py — binary config round-trip, carousel, .tr export/import.
"""

import os
import struct
import tempfile
import unittest
from pathlib import Path

from trcc.dc_parser import DisplayElement, parse_dc_file
from trcc.dc_writer import (
    CarouselConfig,
    ThemeConfig,
    _hex_to_argb,
    _metric_to_hardware_ids,
    _write_string,
    overlay_config_to_theme,
    read_carousel_config,
    write_carousel_config,
    write_dc_file,
    write_tr_export,
    import_theme,
)


class TestWriteString(unittest.TestCase):
    """Test _write_string helper."""

    def _write_and_read(self, s):
        import io
        buf = io.BytesIO()
        _write_string(buf, s)
        return buf.getvalue()

    def test_empty_string(self):
        data = self._write_and_read('')
        self.assertEqual(data, b'\x00')

    def test_short_string(self):
        data = self._write_and_read('ABC')
        self.assertEqual(data[0], 3)  # length
        self.assertEqual(data[1:], b'ABC')

    def test_utf8_string(self):
        data = self._write_and_read('微软雅黑')
        encoded = '微软雅黑'.encode('utf-8')
        self.assertEqual(data[0], len(encoded))
        self.assertEqual(data[1:], encoded)


class TestHexToArgb(unittest.TestCase):
    """Test _hex_to_argb color conversion."""

    def test_6_digit_white(self):
        self.assertEqual(_hex_to_argb('#FFFFFF'), (255, 255, 255, 255))

    def test_6_digit_red(self):
        self.assertEqual(_hex_to_argb('#FF0000'), (255, 255, 0, 0))

    def test_6_digit_no_hash(self):
        self.assertEqual(_hex_to_argb('00FF00'), (255, 0, 255, 0))

    def test_8_digit_with_alpha(self):
        self.assertEqual(_hex_to_argb('#80FF0000'), (128, 255, 0, 0))

    def test_invalid_returns_white(self):
        self.assertEqual(_hex_to_argb('#ZZ'), (255, 255, 255, 255))


class TestMetricToHardwareIds(unittest.TestCase):
    """Test _metric_to_hardware_ids mapping."""

    def test_cpu_temp(self):
        self.assertEqual(_metric_to_hardware_ids('cpu_temp'), (0, 1))

    def test_gpu_usage(self):
        self.assertEqual(_metric_to_hardware_ids('gpu_usage'), (1, 2))

    def test_unknown(self):
        self.assertEqual(_metric_to_hardware_ids('unknown_sensor'), (0, 0))


class TestWriteDcFile(unittest.TestCase):
    """Test write_dc_file produces valid binary."""

    def test_empty_config(self):
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            path = f.name
        try:
            write_dc_file(ThemeConfig(), path)
            with open(path, 'rb') as f:
                data = f.read()
            # Magic byte
            self.assertEqual(data[0], 0xDD)
            # System info enabled (default True)
            self.assertEqual(data[1], 1)
            # Element count = 0
            self.assertEqual(struct.unpack_from('<i', data, 2)[0], 0)
        finally:
            os.unlink(path)

    def test_with_elements(self):
        config = ThemeConfig()
        config.elements = [
            DisplayElement(mode=1, mode_sub=0, x=100, y=50,
                          main_count=0, sub_count=0,
                          font_name='Arial', font_size=24.0,
                          color_argb=(255, 255, 255, 255)),
        ]
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            path = f.name
        try:
            write_dc_file(config, path)
            with open(path, 'rb') as f:
                data = f.read()
            self.assertEqual(data[0], 0xDD)
            count = struct.unpack_from('<i', data, 2)[0]
            self.assertEqual(count, 1)
        finally:
            os.unlink(path)

    def test_rotation_preserved(self):
        config = ThemeConfig(rotation=180)
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            path = f.name
        try:
            write_dc_file(config, path)
            # Read back with parser
            parsed = parse_dc_file(path)
            opts = parsed.get('display_options', {})
            self.assertEqual(opts.get('direction', 0), 180)
        finally:
            os.unlink(path)


class TestRoundTrip(unittest.TestCase):
    """Test write → parse round-trip integrity."""

    def test_roundtrip_empty(self):
        config = ThemeConfig()
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            path = f.name
        try:
            write_dc_file(config, path)
            parsed = parse_dc_file(path)
            self.assertEqual(len(parsed.get('display_elements', [])), 0)
            flags = parsed.get('flags', {})
            self.assertTrue(flags.get('system_info', True))
        finally:
            os.unlink(path)

    def test_roundtrip_elements(self):
        """Write 3 elements, read back, verify fields match."""
        elems = [
            DisplayElement(mode=1, mode_sub=0, x=100, y=50,
                          main_count=0, sub_count=0,
                          font_name='Microsoft YaHei', font_size=32.0,
                          font_style=1, color_argb=(255, 255, 255, 255)),
            DisplayElement(mode=3, mode_sub=0, x=100, y=100,
                          main_count=0, sub_count=0,
                          font_name='Microsoft YaHei', font_size=20.0,
                          color_argb=(255, 200, 200, 200)),
            DisplayElement(mode=0, mode_sub=0, x=50, y=200,
                          main_count=0, sub_count=1,
                          font_name='Microsoft YaHei', font_size=24.0,
                          color_argb=(255, 255, 107, 53)),
        ]
        config = ThemeConfig(elements=elems, rotation=90, mask_enabled=True,
                            mask_x=10, mask_y=20)

        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            path = f.name
        try:
            write_dc_file(config, path)
            parsed = parse_dc_file(path)

            parsed_elems = parsed.get('display_elements', [])
            self.assertEqual(len(parsed_elems), 3)

            # Check first element (time)
            e0 = parsed_elems[0]
            self.assertEqual(e0.mode, 1)
            self.assertEqual(e0.x, 100)
            self.assertEqual(e0.y, 50)
            self.assertEqual(e0.font_size, 32.0)
            self.assertEqual(e0.font_style, 1)

            # Check third element (hardware)
            e2 = parsed_elems[2]
            self.assertEqual(e2.mode, 0)
            self.assertEqual(e2.main_count, 0)
            self.assertEqual(e2.sub_count, 1)
            self.assertEqual(e2.color_argb, (255, 255, 107, 53))

            # Check display options
            opts = parsed.get('display_options', {})
            self.assertEqual(opts.get('direction', 0), 90)

            # Check mask
            mask = parsed.get('mask_settings', {})
            self.assertTrue(mask.get('mask_enabled', False))
            self.assertEqual(mask.get('mask_position', (0, 0)), (10, 20))
        finally:
            os.unlink(path)


class TestOverlayConfigToTheme(unittest.TestCase):
    """Test overlay_config_to_theme conversion."""

    def test_time_element(self):
        config = {'time_0': {
            'x': 10, 'y': 20, 'metric': 'time',
            'color': '#FF6B35', 'font': {'size': 24, 'style': 'bold'},
            'enabled': True,
        }}
        theme = overlay_config_to_theme(config)
        self.assertEqual(len(theme.elements), 1)
        self.assertEqual(theme.elements[0].mode, 1)
        self.assertEqual(theme.elements[0].x, 10)

    def test_hardware_element(self):
        config = {'cpu': {
            'x': 50, 'y': 100, 'metric': 'cpu_temp',
            'color': '#FFFFFF', 'font': {'size': 16},
            'enabled': True,
        }}
        theme = overlay_config_to_theme(config)
        self.assertEqual(theme.elements[0].mode, 0)
        self.assertEqual(theme.elements[0].main_count, 0)
        self.assertEqual(theme.elements[0].sub_count, 1)

    def test_text_element(self):
        config = {'label': {
            'x': 0, 'y': 0, 'text': 'Hello',
            'color': '#00FF00', 'enabled': True,
        }}
        theme = overlay_config_to_theme(config)
        self.assertEqual(theme.elements[0].mode, 4)
        self.assertEqual(theme.elements[0].text, 'Hello')

    def test_disabled_element_skipped(self):
        config = {'item': {
            'x': 0, 'y': 0, 'metric': 'time',
            'color': '#FFF', 'enabled': False,
        }}
        theme = overlay_config_to_theme(config)
        self.assertEqual(len(theme.elements), 0)

    def test_display_size_applied(self):
        theme = overlay_config_to_theme({}, 480, 480)
        self.assertEqual(theme.overlay_w, 480)
        self.assertEqual(theme.overlay_h, 480)


class TestCarouselConfig(unittest.TestCase):
    """Test carousel (Theme.dc) write/read round-trip."""

    def test_roundtrip(self):
        config = CarouselConfig(
            current_theme=2, enabled=True, interval_seconds=5,
            count=3, theme_indices=[0, 2, 4, -1, -1, -1],
            lcd_rotation=2,
        )
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            path = f.name
        try:
            write_carousel_config(config, path)
            loaded = read_carousel_config(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.current_theme, 2)
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.interval_seconds, 5)
            self.assertEqual(loaded.count, 3)
            self.assertEqual(loaded.theme_indices[:3], [0, 2, 4])
            self.assertEqual(loaded.lcd_rotation, 2)
        finally:
            os.unlink(path)

    def test_minimum_interval(self):
        """Interval is clamped to minimum 3."""
        config = CarouselConfig(interval_seconds=1)
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            path = f.name
        try:
            write_carousel_config(config, path)
            loaded = read_carousel_config(path)
            self.assertEqual(loaded.interval_seconds, 3)
        finally:
            os.unlink(path)

    def test_read_nonexistent(self):
        self.assertIsNone(read_carousel_config('/nonexistent/file'))

    def test_read_wrong_magic(self):
        with tempfile.NamedTemporaryFile(suffix='.dc', delete=False) as f:
            f.write(b'\xAA' + b'\x00' * 50)
            path = f.name
        try:
            self.assertIsNone(read_carousel_config(path))
        finally:
            os.unlink(path)


class TestTrExportImport(unittest.TestCase):
    """Test .tr export and import round-trip."""

    def test_export_import_roundtrip(self):
        """Export a theme, import it, verify config1.dc matches."""
        with tempfile.TemporaryDirectory() as src, \
             tempfile.TemporaryDirectory() as dst:

            # Create source theme with background
            from PIL import Image
            bg = Image.new('RGB', (320, 320), (0, 100, 200))
            bg.save(os.path.join(src, '00.png'))

            # Write config1.dc
            config = ThemeConfig(
                elements=[
                    DisplayElement(mode=1, mode_sub=0, x=50, y=50,
                                  main_count=0, sub_count=0,
                                  font_name='Arial', font_size=24.0,
                                  color_argb=(255, 255, 255, 255)),
                ],
                rotation=90, mask_enabled=False,
            )
            write_dc_file(config, os.path.join(src, 'config1.dc'))

            # Export
            tr_path = os.path.join(dst, 'test.tr')
            write_tr_export(config, src, tr_path)
            self.assertTrue(os.path.exists(tr_path))

            # Verify magic header
            with open(tr_path, 'rb') as f:
                magic = f.read(4)
            self.assertEqual(magic, b'\xDD\xDC\xDD\xDC')

            # Import
            import_dir = os.path.join(dst, 'imported')
            import_theme(tr_path, import_dir)

            # Verify imported files
            self.assertTrue(os.path.exists(os.path.join(import_dir, '00.png')))
            self.assertTrue(os.path.exists(os.path.join(import_dir, 'config1.dc')))

            # Verify imported config matches
            parsed = parse_dc_file(os.path.join(import_dir, 'config1.dc'))
            elems = parsed.get('display_elements', [])
            self.assertEqual(len(elems), 1)
            self.assertEqual(elems[0].mode, 1)
            self.assertEqual(elems[0].x, 50)

    def test_import_invalid_magic(self):
        with tempfile.NamedTemporaryFile(suffix='.tr', delete=False) as f:
            f.write(b'\x00\x00\x00\x00')
            path = f.name
        try:
            with self.assertRaises(ValueError):
                import_theme(path, '/tmp/bad_import')
        finally:
            os.unlink(path)
