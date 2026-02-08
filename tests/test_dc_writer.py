"""
Tests for dc_writer.py — binary config round-trip, carousel, .tr export/import.
"""

import os
import struct
import tempfile
import unittest

from trcc.dc_parser import DisplayElement, parse_dc_file
from trcc.dc_writer import (
    CarouselConfig,
    ThemeConfig,
    _hex_to_argb,
    _metric_to_hardware_ids,
    _write_string,
    export_theme,
    import_theme,
    overlay_config_to_theme,
    read_carousel_config,
    save_theme,
    write_carousel_config,
    write_config_json,
    write_dc_file,
    write_tr_export,
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


# ── save_theme ───────────────────────────────────────────────────────────────

class TestSaveTheme(unittest.TestCase):
    """Test save_theme() full theme directory creation."""

    def test_save_with_background(self):
        from PIL import Image
        bg = Image.new('RGB', (320, 320), (255, 0, 0))
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_dir = os.path.join(tmpdir, 'MyTheme')
            save_theme(theme_dir, background_image=bg)
            self.assertTrue(os.path.exists(os.path.join(theme_dir, '00.png')))
            self.assertTrue(os.path.exists(os.path.join(theme_dir, 'Theme.png')))
            self.assertTrue(os.path.exists(os.path.join(theme_dir, 'config1.dc')))

    def test_save_with_mask(self):
        from PIL import Image
        mask = Image.new('RGBA', (200, 200), (0, 255, 0, 128))
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_dir = os.path.join(tmpdir, 'MaskTheme')
            save_theme(theme_dir, mask_image=mask, mask_position=(50, 50))
            self.assertTrue(os.path.exists(os.path.join(theme_dir, '01.png')))
            # Verify config has mask enabled
            parsed = parse_dc_file(os.path.join(theme_dir, 'config1.dc'))
            mask_settings = parsed.get('mask_settings', {})
            self.assertTrue(mask_settings.get('mask_enabled', False))

    def test_save_with_overlay_config(self):
        overlay = {
            'cpu_temp': {
                'enabled': True, 'x': 10, 'y': 20,
                'color': '#FFFFFF', 'font_size': 16,
                'font': {'name': 'Arial'}, 'metric': 'cpu_temp',
                'format': 'CPU: {value}°C',
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_dir = os.path.join(tmpdir, 'OverlayTheme')
            save_theme(theme_dir, overlay_config=overlay)
            self.assertTrue(os.path.exists(os.path.join(theme_dir, 'config1.dc')))
            parsed = parse_dc_file(os.path.join(theme_dir, 'config1.dc'))
            self.assertGreater(len(parsed.get('display_elements', [])), 0)

    def test_save_minimal(self):
        """Save theme with no images or config → creates empty config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_dir = os.path.join(tmpdir, 'EmptyTheme')
            save_theme(theme_dir)
            self.assertTrue(os.path.exists(os.path.join(theme_dir, 'config1.dc')))

    def test_save_thumbnail_size(self):
        from PIL import Image
        bg = Image.new('RGB', (480, 480), (0, 0, 255))
        with tempfile.TemporaryDirectory() as tmpdir:
            theme_dir = os.path.join(tmpdir, 'ThumbTheme')
            save_theme(theme_dir, background_image=bg)
            thumb = Image.open(os.path.join(theme_dir, 'Theme.png'))
            self.assertLessEqual(thumb.width, 120)
            self.assertLessEqual(thumb.height, 120)


# ── export_theme wrapper ─────────────────────────────────────────────────────

class TestExportTheme(unittest.TestCase):
    """Test export_theme() high-level wrapper."""

    def test_export_with_config(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as src, \
             tempfile.TemporaryDirectory() as dst:
            # Create source theme
            bg = Image.new('RGB', (320, 320), (0, 100, 200))
            bg.save(os.path.join(src, '00.png'))
            config = ThemeConfig(
                elements=[
                    DisplayElement(mode=1, mode_sub=0, x=30, y=40,
                                  font_name='Arial', font_size=20.0,
                                  color_argb=(255, 255, 0, 0)),
                ],
                rotation=180,
            )
            write_dc_file(config, os.path.join(src, 'config1.dc'))

            tr_path = os.path.join(dst, 'exported.tr')
            export_theme(src, tr_path)
            self.assertTrue(os.path.exists(tr_path))

            # Import and verify roundtrip
            import_dir = os.path.join(dst, 'imported')
            import_theme(tr_path, import_dir)
            parsed = parse_dc_file(os.path.join(import_dir, 'config1.dc'))
            elems = parsed.get('display_elements', [])
            self.assertEqual(len(elems), 1)
            self.assertEqual(elems[0].mode, 1)
            self.assertEqual(elems[0].x, 30)

    def test_export_no_config(self):
        """Export theme dir with no config1.dc → minimal .tr created."""
        from PIL import Image
        with tempfile.TemporaryDirectory() as src, \
             tempfile.TemporaryDirectory() as dst:
            bg = Image.new('RGB', (320, 320), (50, 50, 50))
            bg.save(os.path.join(src, '00.png'))

            tr_path = os.path.join(dst, 'noconfig.tr')
            export_theme(src, tr_path)
            self.assertTrue(os.path.exists(tr_path))

            # Verify it's importable
            import_dir = os.path.join(dst, 'imported')
            import_theme(tr_path, import_dir)
            self.assertTrue(os.path.exists(os.path.join(import_dir, 'config1.dc')))

    def test_export_preserves_mask(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as src, \
             tempfile.TemporaryDirectory() as dst:
            bg = Image.new('RGB', (320, 320), (0, 0, 0))
            bg.save(os.path.join(src, '00.png'))
            mask = Image.new('RGBA', (100, 100), (255, 0, 0, 128))
            mask.save(os.path.join(src, '01.png'))

            config = ThemeConfig(mask_enabled=True, mask_x=50, mask_y=60)
            write_dc_file(config, os.path.join(src, 'config1.dc'))

            tr_path = os.path.join(dst, 'mask.tr')
            export_theme(src, tr_path)

            import_dir = os.path.join(dst, 'imported')
            import_theme(tr_path, import_dir)
            self.assertTrue(os.path.exists(os.path.join(import_dir, '01.png')))


# ── .tr video/Theme.zt round-trip ────────────────────────────────────────────

class TestTrVideoRoundtrip(unittest.TestCase):
    """Test .tr export/import with Theme.zt video frames."""

    def _make_theme_zt(self, path, frame_count=2):
        """Create synthetic Theme.zt file."""
        with open(path, 'wb') as f:
            f.write(struct.pack('B', 0xDC))
            f.write(struct.pack('<i', frame_count))
            # Timestamps
            for i in range(frame_count):
                f.write(struct.pack('<i', i * 62))
            # Frame data
            for i in range(frame_count):
                frame = bytes([i & 0xFF] * 8)
                f.write(struct.pack('<i', len(frame)))
                f.write(frame)

    def test_tr_roundtrip_with_zt(self):
        """Export theme with Theme.zt → import → verify .zt reconstructed."""
        with tempfile.TemporaryDirectory() as src, \
             tempfile.TemporaryDirectory() as dst:
            # Create source with Theme.zt
            self._make_theme_zt(os.path.join(src, 'Theme.zt'))
            config = ThemeConfig()
            write_dc_file(config, os.path.join(src, 'config1.dc'))

            tr_path = os.path.join(dst, 'video.tr')
            write_tr_export(config, src, tr_path)

            import_dir = os.path.join(dst, 'imported')
            import_theme(tr_path, import_dir)

            zt_path = os.path.join(import_dir, 'Theme.zt')
            self.assertTrue(os.path.exists(zt_path))

            # Verify .zt structure
            with open(zt_path, 'rb') as f:
                header = f.read(1)
                self.assertEqual(header, b'\xDC')
                frame_count = struct.unpack('<i', f.read(4))[0]
                self.assertEqual(frame_count, 2)
                # Verify timestamps
                ts0 = struct.unpack('<i', f.read(4))[0]
                ts1 = struct.unpack('<i', f.read(4))[0]
                self.assertEqual(ts0, 0)
                self.assertEqual(ts1, 62)

    def test_tr_no_background_no_zt(self):
        """Export with neither 00.png nor Theme.zt → marker=0."""
        with tempfile.TemporaryDirectory() as src, \
             tempfile.TemporaryDirectory() as dst:
            config = ThemeConfig()
            write_dc_file(config, os.path.join(src, 'config1.dc'))

            tr_path = os.path.join(dst, 'empty.tr')
            write_tr_export(config, src, tr_path)

            import_dir = os.path.join(dst, 'imported')
            import_theme(tr_path, import_dir)

            # Neither 00.png nor Theme.zt should exist
            self.assertFalse(os.path.exists(os.path.join(import_dir, '00.png')))
            self.assertFalse(os.path.exists(os.path.join(import_dir, 'Theme.zt')))


# ── Targeted coverage: edge paths ────────────────────────────────────────────

class TestWriteStringMultiByte(unittest.TestCase):
    """Cover multi-byte length encoding for strings ≥128 chars (lines 270-271)."""

    def test_long_string(self):
        import io
        buf = io.BytesIO()
        long_name = 'A' * 200
        _write_string(buf, long_name)
        data = buf.getvalue()
        # First byte: (200 & 0x7F) | 0x80 = 0xC8, second: 200 >> 7 = 1
        self.assertEqual(data[0], (200 & 0x7F) | 0x80)
        self.assertEqual(data[1], 200 >> 7)
        self.assertIn(long_name.encode('utf-8'), data)


class TestOverlayConfigToThemeMetrics(unittest.TestCase):
    """Cover weekday/date/cpu/gpu/text metric branches (lines 321-330)."""

    def test_weekday_metric(self):
        config = {'weekday_0': {'enabled': True, 'x': 10, 'y': 20, 'metric': 'weekday'}}
        theme = overlay_config_to_theme(config)
        elem = theme.elements[0]
        self.assertEqual(elem.mode, 2)

    def test_date_metric(self):
        config = {'date_0': {'enabled': True, 'x': 10, 'y': 20, 'metric': 'date', 'date_format': 3}}
        theme = overlay_config_to_theme(config)
        elem = theme.elements[0]
        self.assertEqual(elem.mode, 3)
        self.assertEqual(elem.mode_sub, 3)

    def test_cpu_metric(self):
        config = {'cpu_0': {'enabled': True, 'x': 10, 'y': 20, 'metric': 'cpu_temp'}}
        theme = overlay_config_to_theme(config)
        elem = theme.elements[0]
        self.assertEqual(elem.mode, 0)

    def test_other_metric(self):
        config = {'mem_0': {'enabled': True, 'x': 10, 'y': 20, 'metric': 'mem_percent'}}
        theme = overlay_config_to_theme(config)
        elem = theme.elements[0]
        self.assertEqual(elem.mode, 0)

    def test_text_element(self):
        config = {'text_0': {'enabled': True, 'x': 10, 'y': 20, 'text': 'Hello'}}
        theme = overlay_config_to_theme(config)
        elem = theme.elements[0]
        self.assertEqual(elem.mode, 4)
        self.assertEqual(elem.text, 'Hello')


class TestSaveThemeMask(unittest.TestCase):
    """Cover mask_image without position (line 431)."""

    def test_mask_image_only(self):
        with tempfile.TemporaryDirectory() as d:
            # Create minimal theme with mask but no position
            from PIL import Image
            bg = Image.new('RGB', (320, 320), 'black')
            mask = Image.new('RGBA', (320, 320), (255, 0, 0, 128))
            save_theme(d, bg, mask_image=mask, mask_position=None)
            self.assertTrue(os.path.exists(os.path.join(d, 'config1.dc')))


class TestCarouselConfigRoundTrip(unittest.TestCase):
    """Cover write_carousel_config and read_carousel_config (lines 704, 747-753)."""

    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'Theme.dc')
            config = CarouselConfig()
            config.current_theme = 2
            config.enabled = True
            config.interval_seconds = 30
            config.count = 3
            config.theme_indices = [0, 1, 2]  # less than 6 — tests padding
            config.lcd_rotation = 2
            write_carousel_config(config, path)

            loaded = read_carousel_config(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.current_theme, 2)
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.interval_seconds, 30)
            self.assertEqual(loaded.lcd_rotation, 2)
            # Padded to 6 indices
            self.assertEqual(len(loaded.theme_indices), 6)

    def test_read_truncated_no_rotation(self):
        """Carousel file without rotation field → defaults to 1 (lines 752-753)."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'Theme.dc')
            with open(path, 'wb') as f:
                f.write(struct.pack('B', 0xDC))      # magic
                f.write(struct.pack('<i', 0))          # current_theme
                f.write(struct.pack('?', True))        # enabled
                f.write(struct.pack('<i', 10))         # interval
                f.write(struct.pack('<i', 1))          # count
                for i in range(6):
                    f.write(struct.pack('<i', i))      # indices
                # NO rotation field — should trigger struct.error fallback

            loaded = read_carousel_config(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.lcd_rotation, 1)  # default


class TestWriteConfigJson(unittest.TestCase):
    """Test JSON config writing."""

    def test_writes_valid_json(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            overlay = {'time': {'x': 10, 'y': 20, 'color': '#ff0000', 'metric': 'time', 'enabled': True}}
            write_config_json(d, overlay)
            json_path = os.path.join(d, 'config.json')
            self.assertTrue(os.path.exists(json_path))
            with open(json_path) as f:
                data = json.load(f)
            self.assertEqual(data['version'], 1)
            self.assertEqual(data['elements']['time']['x'], 10)
            self.assertEqual(data['elements']['time']['color'], '#ff0000')

    def test_includes_display_options(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            display = {'rotation': 90, 'bg_display': True, 'tp_display': False, 'overlay_enabled': True}
            write_config_json(d, {}, display)
            with open(os.path.join(d, 'config.json')) as f:
                data = json.load(f)
            self.assertEqual(data['display']['rotation'], 90)
            self.assertTrue(data['display']['background_visible'])

    def test_includes_mask_settings(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            mask = {'enabled': True, 'center_x': 160, 'center_y': 160}
            write_config_json(d, {}, {}, mask)
            with open(os.path.join(d, 'config.json')) as f:
                data = json.load(f)
            self.assertTrue(data['mask']['enabled'])
            self.assertEqual(data['mask']['center_x'], 160)

    def test_empty_config(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            write_config_json(d)
            with open(os.path.join(d, 'config.json')) as f:
                data = json.load(f)
            self.assertEqual(data['elements'], {})
            self.assertEqual(data['display']['rotation'], 0)

    def test_includes_animation_file(self):
        """write_config_json with video_file includes animation section."""
        import json
        with tempfile.TemporaryDirectory() as d:
            write_config_json(d, {}, {}, {}, video_file='a001.mp4')
            with open(os.path.join(d, 'config.json')) as f:
                data = json.load(f)
            self.assertEqual(data['animation']['file'], 'a001.mp4')

    def test_no_animation_when_no_video(self):
        """write_config_json without video_file has empty animation."""
        import json
        with tempfile.TemporaryDirectory() as d:
            write_config_json(d, {})
            with open(os.path.join(d, 'config.json')) as f:
                data = json.load(f)
            self.assertEqual(data['animation'], {})

    def test_save_theme_writes_both_formats(self):
        """save_theme() should create both config1.dc and config.json."""
        with tempfile.TemporaryDirectory() as d:
            overlay = {'time': {'x': 10, 'y': 20, 'color': '#ffffff',
                                'font': {'name': 'Arial', 'size': 24, 'size_raw': 18.0,
                                          'style': 'regular', 'unit': 3, 'charset': 134},
                                'metric': 'time', 'enabled': True}}
            save_theme(d, overlay_config=overlay)
            self.assertTrue(os.path.exists(os.path.join(d, 'config1.dc')))
            self.assertTrue(os.path.exists(os.path.join(d, 'config.json')))

    def test_save_theme_includes_mp4_in_json(self):
        """save_theme() should detect MP4 and include it in config.json."""
        import json
        with tempfile.TemporaryDirectory() as d:
            # Create a fake MP4 file
            mp4_path = os.path.join(d, 'a001.mp4')
            with open(mp4_path, 'wb') as f:
                f.write(b'\x00' * 100)
            save_theme(d, overlay_config={})
            with open(os.path.join(d, 'config.json')) as f:
                data = json.load(f)
            self.assertEqual(data['animation']['file'], 'a001.mp4')
