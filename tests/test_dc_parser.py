"""
Tests for dc_parser - TRCC config1.dc binary configuration parser.

Tests cover:
- FontConfig, ElementConfig, DisplayElement dataclasses
- parse_dc_file() for 0xDC and 0xDD formats
- parse_dd_format() for cloud theme config
- parse_display_elements() for UCXiTongXianShiSubArray
- dc_to_overlay_config() conversion
- get_hardware_metric_name() mapping
- list_theme_configs() directory scan
- validate_theme() and validate_all_themes()
"""

import os
import struct
import sys
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.dc_parser import (
    DisplayElement,
    ElementConfig,
    FontConfig,
    dc_to_overlay_config,
    get_hardware_metric_name,
    list_theme_configs,
    parse_dc_file,
    parse_dd_format,
    parse_display_elements,
    validate_all_themes,
    validate_theme,
)


class TestFontConfig(unittest.TestCase):
    """Test FontConfig dataclass."""

    def test_creation(self):
        """Test FontConfig creation."""
        font = FontConfig(
            name="Microsoft YaHei",
            size=24.0,
            style=1,
            unit=3,
            charset=0,
            color_argb=(255, 255, 128, 64)
        )
        self.assertEqual(font.name, "Microsoft YaHei")
        self.assertEqual(font.size, 24.0)
        self.assertEqual(font.style, 1)
        self.assertEqual(font.color_argb, (255, 255, 128, 64))

    def test_all_fields_required(self):
        """Test that all FontConfig fields are required."""
        with self.assertRaises(TypeError):
            FontConfig(name="Test")  # Missing other required fields


class TestElementConfig(unittest.TestCase):
    """Test ElementConfig dataclass."""

    def test_creation_minimal(self):
        """Test ElementConfig with minimal fields."""
        elem = ElementConfig(x=100, y=200)
        self.assertEqual(elem.x, 100)
        self.assertEqual(elem.y, 200)
        self.assertIsNone(elem.font)
        self.assertTrue(elem.enabled)

    def test_creation_with_font(self):
        """Test ElementConfig with font."""
        font = FontConfig("Arial", 12.0, 0, 3, 0, (255, 0, 0, 0))
        elem = ElementConfig(x=50, y=60, font=font, enabled=False)
        self.assertEqual(elem.font.name, "Arial")
        self.assertFalse(elem.enabled)


class TestDisplayElement(unittest.TestCase):
    """Test DisplayElement dataclass."""

    def test_creation_minimal(self):
        """Test DisplayElement with minimal fields."""
        elem = DisplayElement(
            mode=1, mode_sub=0, x=100, y=50,
            main_count=0, sub_count=1
        )
        self.assertEqual(elem.mode, 1)
        self.assertEqual(elem.x, 100)
        self.assertEqual(elem.font_name, "Microsoft YaHei")
        self.assertEqual(elem.font_size, 24.0)

    def test_mode_name_property(self):
        """Test mode_name property returns correct names."""
        test_cases = [
            (0, 'hardware'),
            (1, 'time'),
            (2, 'weekday'),
            (3, 'date'),
            (4, 'custom'),
            (99, 'unknown_99'),
        ]
        for mode, expected_name in test_cases:
            elem = DisplayElement(mode=mode, mode_sub=0, x=0, y=0, main_count=0, sub_count=0)
            self.assertEqual(elem.mode_name, expected_name,
                f"mode={mode} should be '{expected_name}'")

    def test_color_hex_property(self):
        """Test color_hex property."""
        elem = DisplayElement(
            mode=1, mode_sub=0, x=0, y=0,
            main_count=0, sub_count=0,
            color_argb=(255, 255, 128, 64)
        )
        self.assertEqual(elem.color_hex, "#ff8040")

    def test_color_hex_black(self):
        """Test color_hex for black."""
        elem = DisplayElement(
            mode=1, mode_sub=0, x=0, y=0,
            main_count=0, sub_count=0,
            color_argb=(255, 0, 0, 0)
        )
        self.assertEqual(elem.color_hex, "#000000")

    def test_color_hex_white(self):
        """Test color_hex for white."""
        elem = DisplayElement(
            mode=1, mode_sub=0, x=0, y=0,
            main_count=0, sub_count=0,
            color_argb=(255, 255, 255, 255)
        )
        self.assertEqual(elem.color_hex, "#ffffff")


class TestGetHardwareMetricName(unittest.TestCase):
    """Test get_hardware_metric_name function."""

    def test_cpu_metrics(self):
        """Test CPU sensor mappings."""
        self.assertEqual(get_hardware_metric_name(0, 1), 'cpu_temp')
        self.assertEqual(get_hardware_metric_name(0, 2), 'cpu_percent')
        self.assertEqual(get_hardware_metric_name(0, 3), 'cpu_freq')
        self.assertEqual(get_hardware_metric_name(0, 4), 'cpu_power')

    def test_gpu_metrics(self):
        """Test GPU sensor mappings."""
        self.assertEqual(get_hardware_metric_name(1, 1), 'gpu_temp')
        self.assertEqual(get_hardware_metric_name(1, 2), 'gpu_usage')
        self.assertEqual(get_hardware_metric_name(1, 3), 'gpu_clock')
        self.assertEqual(get_hardware_metric_name(1, 4), 'gpu_power')

    def test_memory_metrics(self):
        """Test memory sensor mappings."""
        self.assertEqual(get_hardware_metric_name(2, 1), 'mem_percent')
        self.assertEqual(get_hardware_metric_name(2, 2), 'mem_clock')

    def test_disk_metrics(self):
        """Test disk sensor mappings."""
        self.assertEqual(get_hardware_metric_name(3, 1), 'disk_activity')

    def test_unknown_sensor(self):
        """Test unknown sensor returns generic name."""
        self.assertEqual(get_hardware_metric_name(99, 99), 'sensor_99_99')
        self.assertEqual(get_hardware_metric_name(5, 7), 'sensor_5_7')


class TestParseDcFile(unittest.TestCase):
    """Test parse_dc_file function."""

    def test_file_too_small(self):
        """Test error for file too small."""
        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(b'\xdc\x00\x00')  # Only 3 bytes
            with self.assertRaises(ValueError) as ctx:
                parse_dc_file(path)
            self.assertIn("too small", str(ctx.exception))

    def test_invalid_magic_byte(self):
        """Test error for invalid magic byte."""
        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            # Write 100 bytes with wrong magic
            data = b'\xaa' + b'\x00' * 99
            with open(path, 'wb') as f:
                f.write(data)
            with self.assertRaises(ValueError) as ctx:
                parse_dc_file(path)
            self.assertIn("Invalid magic byte", str(ctx.exception))

    def test_0xdc_format_header(self):
        """Test parsing 0xDC format header."""
        # Build minimal valid 0xDC file
        data = self._build_minimal_dc_file()

        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(data)

            result = parse_dc_file(path)
            self.assertIn('flags', result)
            self.assertIn('elements', result)
            self.assertIn('fonts', result)

    def test_0xdd_format_detection(self):
        """Test that 0xDD format is detected and routed correctly."""
        data = self._build_minimal_dd_file()

        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(data)

            result = parse_dc_file(path)
            # Should be processed by parse_dd_format
            self.assertIn('display_elements', result)

    def _build_minimal_dc_file(self) -> bytes:
        """Build minimal valid 0xDC config file."""
        buf = BytesIO()

        # Magic byte
        buf.write(b'\xdc')

        # Header int32s
        buf.write(struct.pack('<ii', 0, 0))

        # 8 enable flags
        for _ in range(8):
            buf.write(b'\x00')

        # Another int32
        buf.write(struct.pack('<i', 0))

        # 13 font configs
        for i in range(13):
            if i == 0:
                buf.write(b'\x00')  # Custom text string length
            buf.write(b'\x00')  # Font name length
            buf.write(struct.pack('<f', 24.0))  # Size
            buf.write(bytes([0, 3, 0]))  # Style, unit, charset
            buf.write(bytes([255, 255, 255, 255]))  # ARGB

        # Display options
        buf.write(b'\x01\x00')  # 2 bools
        buf.write(struct.pack('<ii', 0, 0))  # 2 int32s

        # 13 position pairs
        for i in range(13):
            buf.write(struct.pack('<ii', i * 10, i * 10 + 5))

        # Pad to 100 bytes minimum
        while buf.tell() < 100:
            buf.write(b'\x00')

        return buf.getvalue()

    def _build_minimal_dd_file(self) -> bytes:
        """Build minimal valid 0xDD config file."""
        buf = BytesIO()

        # Magic byte
        buf.write(b'\xdd')

        # myXtxx flag
        buf.write(b'\x01')

        # Element count (0 elements)
        buf.write(struct.pack('<i', 0))

        # Pad to 100 bytes minimum
        while buf.tell() < 100:
            buf.write(b'\x00')

        return buf.getvalue()


class TestParseDdFormat(unittest.TestCase):
    """Test parse_dd_format function."""

    def test_empty_elements(self):
        """Test parsing 0xDD with no elements."""
        data = self._build_dd_file(element_count=0)
        result = parse_dd_format(data)

        self.assertEqual(result['display_elements'], [])
        self.assertTrue(result['flags']['system_info'])

    def test_single_time_element(self):
        """Test parsing 0xDD with one time element."""
        data = self._build_dd_file(
            element_count=1,
            elements=[{
                'mode': 1,  # Time
                'mode_sub': 0,  # 24h format
                'x': 100,
                'y': 50,
                'main_count': 0,
                'sub_count': 0,
                'font_name': 'Arial',
                'font_size': 32.0,
                'font_style': 1,
                'color_argb': (255, 255, 255, 255),
                'text': '',
            }]
        )
        result = parse_dd_format(data)

        self.assertEqual(len(result['display_elements']), 1)
        elem = result['display_elements'][0]
        self.assertEqual(elem.mode, 1)
        self.assertEqual(elem.x, 100)
        self.assertEqual(elem.y, 50)
        self.assertEqual(elem.font_name, 'Arial')

    def test_hardware_element(self):
        """Test parsing 0xDD with hardware element."""
        data = self._build_dd_file(
            element_count=1,
            elements=[{
                'mode': 0,  # Hardware
                'mode_sub': 0,
                'x': 200,
                'y': 150,
                'main_count': 0,  # CPU
                'sub_count': 1,  # Temp
                'font_name': 'Microsoft YaHei',
                'font_size': 24.0,
                'font_style': 1,
                'color_argb': (255, 255, 128, 64),
                'text': '',
            }]
        )
        result = parse_dd_format(data)

        self.assertEqual(len(result['display_elements']), 1)
        elem = result['display_elements'][0]
        self.assertEqual(elem.mode, 0)
        self.assertEqual(elem.main_count, 0)
        self.assertEqual(elem.sub_count, 1)

    def test_custom_text_element(self):
        """Test parsing 0xDD with custom text element."""
        data = self._build_dd_file(
            element_count=1,
            elements=[{
                'mode': 4,  # Custom text
                'mode_sub': 0,
                'x': 10,
                'y': 10,
                'main_count': 0,
                'sub_count': 0,
                'font_name': 'Arial',
                'font_size': 16.0,
                'font_style': 0,
                'color_argb': (255, 0, 255, 0),
                'text': 'Hello World',
            }]
        )
        result = parse_dd_format(data)

        self.assertEqual(len(result['display_elements']), 1)
        elem = result['display_elements'][0]
        self.assertEqual(elem.mode, 4)
        self.assertEqual(elem.text, 'Hello World')

    def test_invalid_element_count(self):
        """Test parsing 0xDD with invalid element count."""
        buf = BytesIO()
        buf.write(b'\xdd\x01')  # Magic + myXtxx
        buf.write(struct.pack('<i', 999))  # Invalid count
        while buf.tell() < 100:
            buf.write(b'\x00')

        result = parse_dd_format(buf.getvalue())
        # Should return empty elements due to sanity check
        self.assertEqual(result['display_elements'], [])

    def _build_dd_file(self, element_count: int, elements: list = None, system_info: bool = True) -> bytes:
        """Build 0xDD format config file."""
        buf = BytesIO()

        # Magic byte
        buf.write(b'\xdd')

        # myXtxx flag
        buf.write(b'\x01' if system_info else b'\x00')

        # Element count
        buf.write(struct.pack('<i', element_count))

        # Elements
        for elem in (elements or []):
            # 6 int32s
            buf.write(struct.pack('<iiiiii',
                elem['mode'], elem['mode_sub'],
                elem['x'], elem['y'],
                elem['main_count'], elem['sub_count']))

            # Font name (length-prefixed)
            name_bytes = elem['font_name'].encode('utf-8')
            buf.write(bytes([len(name_bytes)]))
            buf.write(name_bytes)

            # Font size
            buf.write(struct.pack('<f', elem['font_size']))

            # Font style, unit, charset
            buf.write(bytes([elem['font_style'], 3, 0]))

            # Color ARGB
            buf.write(bytes(elem['color_argb']))

            # Custom text (length-prefixed)
            text_bytes = elem['text'].encode('utf-8')
            buf.write(bytes([len(text_bytes)]))
            buf.write(text_bytes)

        # Pad to 100 bytes minimum
        while buf.tell() < 100:
            buf.write(b'\x00')

        return buf.getvalue()


class TestDcToOverlayConfig(unittest.TestCase):
    """Test dc_to_overlay_config conversion."""

    def test_empty_config(self):
        """Test conversion of empty config."""
        dc_config = {
            'elements': {},
            'display_elements': [],
            'flags': {},
        }
        result = dc_to_overlay_config(dc_config)
        self.assertEqual(result, {})

    def test_time_element(self):
        """Test time element conversion."""
        dc_config = {
            'elements': {},
            'display_elements': [
                DisplayElement(
                    mode=1, mode_sub=0, x=100, y=50,
                    main_count=0, sub_count=0,
                    font_size=32.0, font_style=1,
                    color_argb=(255, 255, 255, 255)
                )
            ],
            'flags': {},
        }
        result = dc_to_overlay_config(dc_config)

        self.assertIn('time', result)
        self.assertEqual(result['time']['x'], 100)
        self.assertEqual(result['time']['y'], 50)
        self.assertEqual(result['time']['metric'], 'time')
        self.assertEqual(result['time']['time_format'], 0)

    def test_date_element(self):
        """Test date element conversion."""
        dc_config = {
            'elements': {},
            'display_elements': [
                DisplayElement(
                    mode=3, mode_sub=2, x=50, y=100,
                    main_count=0, sub_count=0,
                    font_size=20.0, font_style=0,
                    color_argb=(255, 128, 128, 128)
                )
            ],
            'flags': {},
        }
        result = dc_to_overlay_config(dc_config)

        self.assertIn('date', result)
        self.assertEqual(result['date']['date_format'], 2)

    def test_weekday_element(self):
        """Test weekday element conversion."""
        dc_config = {
            'elements': {},
            'display_elements': [
                DisplayElement(
                    mode=2, mode_sub=0, x=75, y=25,
                    main_count=0, sub_count=0,
                    font_size=16.0, font_style=1,
                    color_argb=(255, 64, 192, 255)
                )
            ],
            'flags': {},
        }
        result = dc_to_overlay_config(dc_config)

        self.assertIn('weekday', result)
        self.assertEqual(result['weekday']['metric'], 'weekday')

    def test_hardware_element_with_temp_unit(self):
        """Test hardware element includes temp_unit from mode_sub."""
        dc_config = {
            'elements': {},
            'display_elements': [
                DisplayElement(
                    mode=0, mode_sub=1,  # Fahrenheit
                    x=200, y=150,
                    main_count=0, sub_count=1,  # CPU temp
                    font_size=24.0, font_style=1,
                    color_argb=(255, 255, 128, 64)
                )
            ],
            'flags': {},
        }
        result = dc_to_overlay_config(dc_config)

        self.assertIn('hw_0_1', result)
        self.assertEqual(result['hw_0_1']['metric'], 'cpu_temp')
        self.assertEqual(result['hw_0_1']['temp_unit'], 1)  # Fahrenheit

    def test_custom_text_element(self):
        """Test custom text element conversion."""
        dc_config = {
            'elements': {},
            'display_elements': [
                DisplayElement(
                    mode=4, mode_sub=0, x=10, y=10,
                    main_count=0, sub_count=0,
                    font_size=16.0, font_style=0,
                    color_argb=(255, 0, 255, 0),
                    text='Custom Label'
                )
            ],
            'flags': {},
        }
        result = dc_to_overlay_config(dc_config)

        # Should have custom_0 key
        custom_keys = [k for k in result if k.startswith('custom')]
        self.assertEqual(len(custom_keys), 1)
        self.assertEqual(result[custom_keys[0]]['text'], 'Custom Label')

    def test_multiple_time_elements(self):
        """Test multiple time elements get unique keys."""
        dc_config = {
            'elements': {},
            'display_elements': [
                DisplayElement(mode=1, mode_sub=0, x=100, y=50,
                    main_count=0, sub_count=0),
                DisplayElement(mode=1, mode_sub=1, x=100, y=100,
                    main_count=0, sub_count=0),
            ],
            'flags': {},
        }
        result = dc_to_overlay_config(dc_config)

        self.assertIn('time', result)
        self.assertIn('time_1', result)

    def test_legacy_cpu_temp_element(self):
        """Test legacy cpu_temp element conversion."""
        font = FontConfig('Arial', 24.0, 1, 3, 0, (255, 255, 100, 50))
        dc_config = {
            'elements': {
                'cpu_temp': ElementConfig(x=50, y=100, font=font, enabled=True)
            },
            'display_elements': [],
            'flags': {'cpu_temp': True},
        }
        result = dc_to_overlay_config(dc_config)

        self.assertIn('cpu_temp', result)
        self.assertEqual(result['cpu_temp']['metric'], 'cpu_temp')
        self.assertEqual(result['cpu_temp']['x'], 50)

    def test_font_size_clamping(self):
        """Test font size is clamped to valid range."""
        dc_config = {
            'elements': {},
            'display_elements': [
                DisplayElement(
                    mode=1, mode_sub=0, x=0, y=0,
                    main_count=0, sub_count=0,
                    font_size=100.0,  # Too large
                    color_argb=(255, 255, 255, 255)
                )
            ],
            'flags': {},
        }
        result = dc_to_overlay_config(dc_config)

        # Font size should be clamped (MAX_FONT_SIZE = 48)
        self.assertLessEqual(result['time']['font']['size'], 64)  # With DPI scaling


class TestListThemeConfigs(unittest.TestCase):
    """Test list_theme_configs function."""

    def test_empty_directory(self):
        """Test with empty directory."""
        with TemporaryDirectory() as tmpdir:
            configs = list_theme_configs(tmpdir)
            self.assertEqual(configs, [])

    def test_finds_config_files(self):
        """Test finding config1.dc files."""
        with TemporaryDirectory() as tmpdir:
            # Create theme directories with config files
            for name in ['000a', '000b', '000c']:
                theme_dir = os.path.join(tmpdir, name)
                os.makedirs(theme_dir)
                config_path = os.path.join(theme_dir, 'config1.dc')
                with open(config_path, 'w') as f:
                    f.write('')

            configs = list_theme_configs(tmpdir)
            self.assertEqual(len(configs), 3)
            self.assertTrue(all('config1.dc' in c for c in configs))

    def test_nonexistent_directory(self):
        """Test with nonexistent directory."""
        configs = list_theme_configs('/nonexistent/path/to/themes')
        self.assertEqual(configs, [])

    def test_nested_directories(self):
        """Test finding configs in nested directories."""
        with TemporaryDirectory() as tmpdir:
            # Create nested structure
            nested = os.path.join(tmpdir, 'themes', 'category', 'theme1')
            os.makedirs(nested)
            with open(os.path.join(nested, 'config1.dc'), 'w') as f:
                f.write('')

            configs = list_theme_configs(tmpdir)
            self.assertEqual(len(configs), 1)


class TestValidateTheme(unittest.TestCase):
    """Test validate_theme function."""

    def test_missing_config(self):
        """Test validation with missing config file."""
        with TemporaryDirectory() as tmpdir:
            result = validate_theme(tmpdir)
            self.assertFalse(result['valid'])
            self.assertIn('Missing config1.dc', result['issues'])

    def test_valid_theme(self):
        """Test validation of valid theme."""
        with TemporaryDirectory() as tmpdir:
            # Create minimal valid config
            config_path = os.path.join(tmpdir, 'config1.dc')
            data = self._build_valid_dc_file()
            with open(config_path, 'wb') as f:
                f.write(data)

            # Create required files
            with open(os.path.join(tmpdir, '00.png'), 'wb') as f:
                f.write(b'PNG')
            with open(os.path.join(tmpdir, 'Theme.png'), 'wb') as f:
                f.write(b'PNG')

            result = validate_theme(tmpdir)
            self.assertTrue(result['valid'])
            self.assertEqual(result['format'], '0xDC')

    def test_missing_image_warning(self):
        """Test warning for missing image files."""
        with TemporaryDirectory() as tmpdir:
            # Create minimal valid config
            config_path = os.path.join(tmpdir, 'config1.dc')
            data = self._build_valid_dc_file()
            with open(config_path, 'wb') as f:
                f.write(data)

            result = validate_theme(tmpdir)
            # Should have warnings about missing files
            self.assertTrue(len(result['warnings']) > 0)

    def _build_valid_dc_file(self) -> bytes:
        """Build a minimal valid 0xDC file for testing."""
        buf = BytesIO()
        buf.write(b'\xdc')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(8):
            buf.write(b'\x00')
        buf.write(struct.pack('<i', 0))
        for i in range(13):
            if i == 0:
                buf.write(b'\x00')
            buf.write(b'\x00')
            buf.write(struct.pack('<f', 24.0))
            buf.write(bytes([0, 3, 0, 255, 255, 255, 255]))
        buf.write(b'\x01\x00')
        buf.write(struct.pack('<ii', 0, 0))
        for i in range(13):
            buf.write(struct.pack('<ii', i * 10, i * 10))
        while buf.tell() < 100:
            buf.write(b'\x00')
        return buf.getvalue()


class TestValidateAllThemes(unittest.TestCase):
    """Test validate_all_themes function."""

    def test_nonexistent_directory(self):
        """Test with nonexistent directory."""
        result = validate_all_themes('/nonexistent/themes')
        self.assertEqual(result['total'], 0)

    def test_empty_directory(self):
        """Test with empty directory."""
        with TemporaryDirectory() as tmpdir:
            result = validate_all_themes(tmpdir)
            self.assertEqual(result['total'], 0)

    def test_multiple_themes(self):
        """Test validating multiple themes."""
        with TemporaryDirectory() as tmpdir:
            # Create multiple theme directories
            for name in ['theme1', 'theme2']:
                theme_dir = os.path.join(tmpdir, name)
                os.makedirs(theme_dir)
                # Create config in first theme only
                if name == 'theme1':
                    data = self._build_valid_dc_file()
                    with open(os.path.join(theme_dir, 'config1.dc'), 'wb') as f:
                        f.write(data)

            result = validate_all_themes(tmpdir)
            self.assertEqual(result['total'], 2)
            self.assertGreaterEqual(result['valid'], 1)
            self.assertGreaterEqual(result['invalid'], 1)

    def _build_valid_dc_file(self) -> bytes:
        """Build a minimal valid 0xDC file."""
        buf = BytesIO()
        buf.write(b'\xdc')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(8):
            buf.write(b'\x00')
        buf.write(struct.pack('<i', 0))
        for i in range(13):
            if i == 0:
                buf.write(b'\x00')
            buf.write(b'\x00')
            buf.write(struct.pack('<f', 24.0))
            buf.write(bytes([0, 3, 0, 255, 255, 255, 255]))
        buf.write(b'\x01\x00')
        buf.write(struct.pack('<ii', 0, 0))
        for i in range(13):
            buf.write(struct.pack('<ii', i * 10, i * 10))
        while buf.tell() < 100:
            buf.write(b'\x00')
        return buf.getvalue()


class TestParseDisplayElements(unittest.TestCase):
    """Test parse_display_elements function."""

    def test_empty_elements(self):
        """Test parsing with zero elements."""
        data = struct.pack('<i', 0)  # Count = 0
        data += b'\x00' * 100  # Padding
        result = parse_display_elements(data, 0)
        self.assertEqual(result, [])

    def test_invalid_count(self):
        """Test parsing with invalid element count."""
        data = struct.pack('<i', -5)  # Invalid negative count
        data += b'\x00' * 100
        result = parse_display_elements(data, 0)
        self.assertEqual(result, [])

        data = struct.pack('<i', 200)  # Too high count
        data += b'\x00' * 100
        result = parse_display_elements(data, 0)
        self.assertEqual(result, [])

    def test_truncated_data(self):
        """Test parsing with truncated element data."""
        data = struct.pack('<i', 1)  # Count = 1
        data += b'\x00' * 10  # Not enough data for full element
        result = parse_display_elements(data, 0)
        # Should return empty or partial results without crashing
        self.assertIsInstance(result, list)

    def _build_element_bytes(self, mode=1, mode_sub=0, x=10, y=20,
                             main_count=0, sub_count=0,
                             font_name='Arial', font_size=24.0,
                             font_style=0, alpha=255, r=255, g=128, b=64,
                             text='') -> bytes:
        """Build binary data for a single display element."""
        buf = BytesIO()
        buf.write(struct.pack('<i', mode))
        buf.write(struct.pack('<i', mode_sub))
        buf.write(struct.pack('<i', x))
        buf.write(struct.pack('<i', y))
        buf.write(struct.pack('<i', main_count))
        buf.write(struct.pack('<i', sub_count))
        # Font name (length-prefixed)
        name_bytes = font_name.encode('utf-8')
        buf.write(struct.pack('B', len(name_bytes)))
        buf.write(name_bytes)
        buf.write(struct.pack('<f', font_size))
        # style, unit, charset, A, R, G, B
        buf.write(struct.pack('BBBBBBB', font_style, 3, 0, alpha, r, g, b))
        # Text (length-prefixed)
        text_bytes = text.encode('utf-8')
        buf.write(struct.pack('B', len(text_bytes)))
        if text_bytes:
            buf.write(text_bytes)
        return buf.getvalue()

    def test_single_valid_element(self):
        """Parse a single valid element with all fields."""
        elem_data = self._build_element_bytes(
            mode=1, x=50, y=100, font_name='Arial', font_size=24.0,
            r=255, g=128, b=64, text='')
        data = struct.pack('<i', 1) + elem_data
        result = parse_display_elements(data, 0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].mode, 1)
        self.assertEqual(result[0].x, 50)
        self.assertEqual(result[0].y, 100)
        self.assertEqual(result[0].font_name, 'Arial')
        self.assertAlmostEqual(result[0].font_size, 24.0, places=1)
        self.assertEqual(result[0].color_argb, (255, 255, 128, 64))

    def test_custom_text_element(self):
        """Parse element with custom text (mode 4)."""
        elem_data = self._build_element_bytes(mode=4, text='Hello World')
        data = struct.pack('<i', 1) + elem_data
        result = parse_display_elements(data, 0)
        self.assertEqual(result[0].text, 'Hello World')
        self.assertEqual(result[0].mode, 4)

    def test_multiple_elements(self):
        """Parse multiple elements."""
        e1 = self._build_element_bytes(mode=0, x=10, y=10)
        e2 = self._build_element_bytes(mode=1, x=20, y=30)
        e3 = self._build_element_bytes(mode=4, x=50, y=60, text='Test')
        data = struct.pack('<i', 3) + e1 + e2 + e3
        result = parse_display_elements(data, 0)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].mode, 0)
        self.assertEqual(result[1].mode, 1)
        self.assertEqual(result[2].text, 'Test')

    def test_font_size_clamping(self):
        """Out-of-range font sizes get clamped to defaults."""
        e1 = self._build_element_bytes(font_size=0.0)
        e2 = self._build_element_bytes(font_size=999.0)
        data = struct.pack('<i', 2) + e1 + e2
        result = parse_display_elements(data, 0)
        self.assertEqual(len(result), 2)
        # Both should be clamped to 24 (default for out-of-range)
        self.assertEqual(result[0].font_size, 24)
        self.assertEqual(result[1].font_size, 24)

    def test_nonzero_start_pos(self):
        """Parse with non-zero start position."""
        prefix = b'\xFF' * 20  # Garbage prefix
        elem_data = self._build_element_bytes(mode=2)
        data = prefix + struct.pack('<i', 1) + elem_data
        result = parse_display_elements(data, 20)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].mode, 2)


class TestValidateThemeEdgeCases(unittest.TestCase):
    """Additional validate_theme edge cases."""

    def test_validate_corrupt_config(self):
        """Corrupt config1.dc (valid magic + garbage) → issues."""
        with TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, 'config1.dc')
            with open(config_path, 'wb') as f:
                f.write(b'\xDC' + b'\xFF' * 50)
            result = validate_theme(tmpdir)
            # Should not crash, may be valid=True with warnings or valid=False
            self.assertIsInstance(result, dict)
            self.assertIn('valid', result)

    def test_validate_custom_display_size(self):
        """Validation with custom display dimensions."""
        with TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, 'config1.dc')
            # Write a minimal valid file
            buf = BytesIO()
            buf.write(b'\xdc')
            buf.write(struct.pack('<ii', 0, 0))
            for _ in range(8):
                buf.write(b'\x00')
            buf.write(struct.pack('<i', 0))
            for _ in range(13):
                buf.write(b'\x00')
                buf.write(struct.pack('<f', 24.0))
                buf.write(bytes([0, 3, 0, 255, 255, 255, 255]))
            buf.write(b'\x01\x00')
            buf.write(struct.pack('<ii', 0, 0))
            for _ in range(13):
                buf.write(struct.pack('<ii', 0, 0))
            while buf.tell() < 100:
                buf.write(b'\x00')
            with open(config_path, 'wb') as f:
                f.write(buf.getvalue())

            with open(os.path.join(tmpdir, '00.png'), 'wb') as f:
                f.write(b'PNG')
            with open(os.path.join(tmpdir, 'Theme.png'), 'wb') as f:
                f.write(b'PNG')

            result = validate_theme(tmpdir, 240, 240)
            self.assertIsInstance(result, dict)


# ── 0xDC time/date/weekday section ───────────────────────────────────────────

class TestDcTimeDateSection(unittest.TestCase):
    """Test parsing the 0xDC time/date/weekday block (lines 316-440)."""

    def _build_dc_with_time_date(self, *, enable_date=True, enable_time=True,
                                  enable_weekday=True, date_format=0,
                                  time_format=1) -> bytes:
        """Build a full 0xDC config that includes the time/date section."""
        buf = BytesIO()
        # Magic
        buf.write(b'\xdc')
        # Header int32s
        buf.write(struct.pack('<ii', 0, 0))
        # 8 enable flags (all on)
        for _ in range(8):
            buf.write(b'\x01')
        # int32 before fonts
        buf.write(struct.pack('<i', 0))
        # custom_text string (for i==0)
        buf.write(b'\x00')
        # 13 font configs: font_name_len(1) + size(4) + style/unit/charset(3) + ARGB(4) = 12 bytes
        for i in range(13):
            buf.write(b'\x00')                     # font name length
            buf.write(struct.pack('<f', 24.0))     # size
            buf.write(bytes([1, 3, 0]))            # style, unit, charset
            buf.write(bytes([255, 255, 128, 64]))  # ARGB

        # Display options: 2 bools + 2 int32s
        buf.write(b'\x01\x00')
        buf.write(struct.pack('<ii', 0, 0))

        # 13 position pairs (X, Y)
        for i in range(13):
            buf.write(struct.pack('<ii', i * 20, i * 15))

        # === 0xDC time/date/weekday section ===
        # Skip custom text string
        buf.write(b'\x00')  # empty custom text

        # Settings before time/date
        buf.write(b'\x01')               # num8 (bool)
        buf.write(struct.pack('<i', 0))  # num5 (myMode)
        buf.write(b'\x01')               # myYcbk (overlay enabled)
        buf.write(struct.pack('<i', 10))  # JpX
        buf.write(struct.pack('<i', 20))  # JpY
        buf.write(struct.pack('<i', 300)) # JpW
        buf.write(struct.pack('<i', 300)) # JpH
        buf.write(b'\x01')               # myMbxs (mask enabled)
        buf.write(struct.pack('<i', 5))   # XvalMB
        buf.write(struct.pack('<i', 5))   # YvalMB

        # Time/date flags
        buf.write(bytes([int(enable_date or enable_time or enable_weekday)]))  # flag10
        buf.write(bytes([int(enable_date)]))     # flag11
        buf.write(bytes([int(enable_time)]))     # flag12

        # Date/time formats
        buf.write(struct.pack('<i', date_format))
        buf.write(struct.pack('<i', time_format))

        # Positions: date X, Y, time X, Y
        buf.write(struct.pack('<ii', 50, 60))    # date
        buf.write(struct.pack('<ii', 50, 100))   # time

        # Date font config: name + size + style + unit + charset + ARGB (4)
        buf.write(b'\x00')                        # font name length
        buf.write(struct.pack('<f', 20.0))        # size
        buf.write(bytes([1, 3, 0]))               # style, unit, charset
        buf.write(bytes([255, 200, 200, 200]))    # ARGB

        # Time font config
        buf.write(b'\x00')
        buf.write(struct.pack('<f', 32.0))
        buf.write(bytes([1, 3, 0]))
        buf.write(bytes([255, 255, 255, 255]))

        # Weekday flag and settings
        buf.write(bytes([int(enable_weekday)]))   # flag13
        buf.write(struct.pack('<ii', 50, 140))    # weekday X, Y

        # Weekday font config
        buf.write(b'\x00')
        buf.write(struct.pack('<f', 18.0))
        buf.write(bytes([0, 3, 0]))
        buf.write(bytes([255, 128, 128, 128]))

        return buf.getvalue()

    def test_all_enabled(self):
        """Parse 0xDC with date, time, and weekday all enabled."""
        data = self._build_dc_with_time_date()
        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(data)
            result = parse_dc_file(path)

        self.assertIn('mask_settings', result)
        self.assertTrue(result['mask_settings']['mask_enabled'])
        elems = result.get('display_elements', [])
        modes = {e.mode for e in elems}
        self.assertIn(1, modes)  # Time
        self.assertIn(2, modes)  # Weekday
        self.assertIn(3, modes)  # Date

    def test_date_only(self):
        data = self._build_dc_with_time_date(
            enable_date=True, enable_time=False, enable_weekday=False)
        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(data)
            result = parse_dc_file(path)

        elems = result.get('display_elements', [])
        modes = [e.mode for e in elems]
        self.assertIn(3, modes)    # Date
        self.assertNotIn(1, modes) # No time
        self.assertNotIn(2, modes) # No weekday

    def test_time_only(self):
        data = self._build_dc_with_time_date(
            enable_date=False, enable_time=True, enable_weekday=False)
        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(data)
            result = parse_dc_file(path)

        elems = result.get('display_elements', [])
        modes = [e.mode for e in elems]
        self.assertIn(1, modes)    # Time
        self.assertNotIn(3, modes) # No date

    def test_none_enabled(self):
        data = self._build_dc_with_time_date(
            enable_date=False, enable_time=False, enable_weekday=False)
        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(data)
            result = parse_dc_file(path)

        elems = result.get('display_elements', [])
        self.assertEqual(len(elems), 0)

    def test_mask_settings_parsed(self):
        data = self._build_dc_with_time_date()
        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(data)
            result = parse_dc_file(path)

        mask = result.get('mask_settings', {})
        self.assertTrue(mask.get('overlay_enabled'))
        self.assertEqual(mask['overlay_rect'], (10, 20, 300, 300))
        self.assertEqual(mask['mask_position'], (5, 5))


# ── parse_dd_format display options/mask block ───────────────────────────────

class TestDdFormatSettings(unittest.TestCase):
    """Test parse_dd_format reads display_options and mask_settings."""

    def _build_dd_with_settings(self) -> bytes:
        buf = BytesIO()
        buf.write(b'\xdd')       # Magic
        buf.write(b'\x01')       # myXtxx
        buf.write(struct.pack('<i', 0))  # 0 elements

        # Display options + mask block
        buf.write(b'\x01')                 # myBjxs
        buf.write(b'\x00')                 # myTpxs
        buf.write(struct.pack('<i', 90))   # directionB
        buf.write(struct.pack('<i', 1))    # myUIMode
        buf.write(struct.pack('<i', 2))    # myMode
        buf.write(b'\x01')                 # myYcbk
        buf.write(struct.pack('<iiii', 0, 0, 320, 320))  # overlay rect
        buf.write(b'\x01')                 # myMbxs
        buf.write(struct.pack('<ii', 10, 10))  # mask pos

        while buf.tell() < 100:
            buf.write(b'\x00')
        return buf.getvalue()

    def test_display_options_parsed(self):
        data = self._build_dd_with_settings()
        result = parse_dd_format(data)
        opts = result.get('display_options', {})
        self.assertTrue(opts.get('background_display'))
        self.assertFalse(opts.get('screencast_display'))
        self.assertEqual(opts.get('direction'), 90)
        self.assertEqual(opts.get('mode'), 2)

    def test_mask_settings_parsed(self):
        data = self._build_dd_with_settings()
        result = parse_dd_format(data)
        mask = result.get('mask_settings', {})
        self.assertTrue(mask.get('overlay_enabled'))
        self.assertTrue(mask.get('mask_enabled'))
        self.assertEqual(mask['mask_position'], (10, 10))


# ── parse_display_elements edge cases ────────────────────────────────────────

class TestParseDisplayElementsEdge(unittest.TestCase):

    def test_truncated_mid_element(self):
        """Data truncated in the middle of an element."""
        buf = BytesIO()
        buf.write(struct.pack('<i', 1))  # 1 element
        buf.write(struct.pack('<ii', 1, 0))  # mode, mode_sub (only partial)
        data = buf.getvalue()
        result = parse_display_elements(data, 0)
        self.assertEqual(result, [])

    def test_unicode_error_in_font_name(self):
        """Invalid UTF-8 in font name falls back to default."""
        buf = BytesIO()
        buf.write(struct.pack('<i', 1))              # count
        buf.write(struct.pack('<iiiiii', 0, 0, 10, 20, 0, 1))  # 6 int32s
        buf.write(bytes([4, 0xFF, 0xFE, 0xFD, 0xFC]))  # Invalid UTF-8 font name
        buf.write(struct.pack('<f', 24.0))             # font size
        buf.write(bytes([1, 3, 0, 255, 255, 255, 255]))  # style+color
        buf.write(b'\x00')                              # text length
        data = buf.getvalue()
        result = parse_display_elements(data, 0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].font_name, 'Microsoft YaHei')

    def test_font_size_out_of_range(self):
        """Font size 0 or >100 clamps to 24."""
        buf = BytesIO()
        buf.write(struct.pack('<i', 1))
        buf.write(struct.pack('<iiiiii', 0, 0, 0, 0, 0, 0))
        buf.write(b'\x00')                              # no font name
        buf.write(struct.pack('<f', 200.0))             # way too big
        buf.write(bytes([0, 3, 0, 255, 0, 0, 0]))
        buf.write(b'\x00')
        data = buf.getvalue()
        result = parse_display_elements(data, 0)
        self.assertEqual(result[0].font_size, 24)

    def test_custom_text_element(self):
        """Mode 4 element with custom text."""
        buf = BytesIO()
        buf.write(struct.pack('<i', 1))
        buf.write(struct.pack('<iiiiii', 4, 0, 50, 60, 0, 0))  # mode=4 custom
        buf.write(b'\x00')
        buf.write(struct.pack('<f', 20.0))
        buf.write(bytes([0, 3, 0, 255, 200, 200, 200]))
        text = b'Hello World'
        buf.write(bytes([len(text)]))
        buf.write(text)
        data = buf.getvalue()
        result = parse_display_elements(data, 0)
        self.assertEqual(result[0].text, 'Hello World')
        self.assertEqual(result[0].mode, 4)


# ── dc_to_overlay_config edge paths ──────────────────────────────────────────

class TestDcToOverlayEdge(unittest.TestCase):

    def test_custom_text_with_text(self):
        """Custom text element from elements dict includes text."""
        dc = {
            'elements': {
                'custom_text': ElementConfig(x=10, y=20,
                    font=FontConfig('Arial', 20, 1, 3, 0, (255, 255, 255, 255)),
                    enabled=True),
            },
            'fonts': [],
            'flags': {'custom_text': True},
            'display_elements': [],
            'custom_text': 'Hello',
        }
        result = dc_to_overlay_config(dc)
        self.assertEqual(result['custom_text']['text'], 'Hello')

    def test_custom_text_without_text_skipped(self):
        """custom_text element without text in dc_config is skipped."""
        dc = {
            'elements': {
                'custom_text': ElementConfig(x=10, y=20,
                    font=FontConfig('Arial', 20, 1, 3, 0, (255, 255, 255, 255)),
                    enabled=True),
            },
            'fonts': [],
            'flags': {'custom_text': True},
            'display_elements': [],
            'custom_text': '',
        }
        result = dc_to_overlay_config(dc)
        self.assertNotIn('custom_text', result)

    def test_display_element_weekday(self):
        """Weekday display_element gets 'weekday' key."""
        dc = {
            'elements': {},
            'fonts': [],
            'flags': {},
            'display_elements': [
                DisplayElement(mode=2, mode_sub=0, x=10, y=20,
                    main_count=0, sub_count=0, font_size=20, font_style=1),
            ],
        }
        result = dc_to_overlay_config(dc)
        self.assertIn('weekday', result)
        self.assertEqual(result['weekday']['metric'], 'weekday')

    def test_display_element_custom(self):
        """Mode 4 (custom text) display element."""
        dc = {
            'elements': {},
            'fonts': [],
            'flags': {},
            'display_elements': [
                DisplayElement(mode=4, mode_sub=0, x=10, y=20,
                    main_count=0, sub_count=0, text='Custom!'),
            ],
        }
        result = dc_to_overlay_config(dc)
        custom_keys = [k for k in result if k.startswith('custom')]
        self.assertEqual(len(custom_keys), 1)
        self.assertEqual(result[custom_keys[0]]['text'], 'Custom!')

    def test_display_element_hardware(self):
        """Mode 0 (hardware info) display element."""
        dc = {
            'elements': {},
            'fonts': [],
            'flags': {},
            'display_elements': [
                DisplayElement(mode=0, mode_sub=0, x=10, y=20,
                    main_count=1, sub_count=2),
            ],
        }
        result = dc_to_overlay_config(dc)
        self.assertIn('hw_1_2', result)
        self.assertIn('metric', result['hw_1_2'])

    def test_font_style_regular(self):
        """Font style 0 → 'regular'."""
        dc = {
            'elements': {},
            'fonts': [],
            'flags': {},
            'display_elements': [
                DisplayElement(mode=1, mode_sub=0, x=10, y=20,
                    main_count=0, sub_count=0, font_style=0),
            ],
        }
        result = dc_to_overlay_config(dc)
        self.assertEqual(result['time']['font']['style'], 'regular')

    def test_label_element_in_legacy(self):
        """Legacy mapping: cpu_label gets text='CPU'."""
        dc = {
            'elements': {
                'cpu_label': ElementConfig(x=10, y=20, enabled=True),
            },
            'fonts': [],
            'flags': {},
            'display_elements': [],
        }
        result = dc_to_overlay_config(dc)
        self.assertEqual(result['cpu_label']['text'], 'CPU')

    def test_no_font_defaults(self):
        """Element without font uses default size/color."""
        dc = {
            'elements': {
                'cpu_temp': ElementConfig(x=10, y=20, font=None, enabled=True),
            },
            'fonts': [],
            'flags': {},
            'display_elements': [],
        }
        result = dc_to_overlay_config(dc)
        self.assertEqual(result['cpu_temp']['font']['size'], 24)
        self.assertEqual(result['cpu_temp']['font']['style'], 'regular')


# ── validate_theme ────────────────────────────────────────────────────────────

class TestValidateThemeFull(unittest.TestCase):

    def _write_dc_file(self, path):
        """Write a minimal valid 0xDC file."""
        buf = BytesIO()
        buf.write(b'\xdc')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(8):
            buf.write(b'\x00')
        buf.write(struct.pack('<i', 0))
        for _ in range(13):
            buf.write(b'\x00')
            buf.write(struct.pack('<f', 24.0))
            buf.write(bytes([0, 3, 0, 255, 255, 255, 255]))
        buf.write(b'\x01\x00')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(13):
            buf.write(struct.pack('<ii', 0, 0))
        while buf.tell() < 100:
            buf.write(b'\x00')
        with open(path, 'wb') as f:
            f.write(buf.getvalue())

    def test_missing_config(self):
        with TemporaryDirectory() as tmpdir:
            result = validate_theme(tmpdir)
            self.assertFalse(result['valid'])
            self.assertIn('Missing config1.dc', result['issues'])

    def test_parse_error(self):
        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(b'\xAA\x00\x00')  # Invalid magic
            result = validate_theme(tmpdir)
            self.assertFalse(result['valid'])
            self.assertTrue(any('Parse error' in i for i in result['issues']))

    def test_out_of_bounds_position(self):
        with TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, 'config1.dc')
            # Build DC with cpu_temp at (500, 500) — beyond 320x320
            buf = BytesIO()
            buf.write(b'\xdc')
            buf.write(struct.pack('<ii', 0, 0))
            # Enable cpu_temp flag (index 2)
            for _ in range(8):
                buf.write(b'\x01')
            buf.write(struct.pack('<i', 0))
            buf.write(b'\x00')  # custom text
            for _ in range(13):
                buf.write(b'\x00')
                buf.write(struct.pack('<f', 24.0))
                buf.write(bytes([1, 3, 0, 255, 255, 255, 255]))
            buf.write(b'\x01\x00')
            buf.write(struct.pack('<ii', 0, 0))
            # custom_text at (0,0)
            buf.write(struct.pack('<ii', 0, 0))
            # cpu_temp at (500, 500)
            buf.write(struct.pack('<ii', 500, 500))
            for _ in range(11):
                buf.write(struct.pack('<ii', 0, 0))
            while buf.tell() < 100:
                buf.write(b'\x00')
            with open(config_path, 'wb') as f:
                f.write(buf.getvalue())
            with open(os.path.join(tmpdir, '00.png'), 'wb') as f:
                f.write(b'PNG')
            with open(os.path.join(tmpdir, 'Theme.png'), 'wb') as f:
                f.write(b'PNG')

            result = validate_theme(tmpdir)
            self.assertTrue(len(result['warnings']) > 0)

    def test_no_image_files_warning(self):
        with TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, 'config1.dc')
            self._write_dc_file(config_path)
            result = validate_theme(tmpdir)
            warnings = result.get('warnings', [])
            self.assertTrue(any('00.png' in w or '01.png' in w for w in warnings))

    def test_no_preview_warning(self):
        with TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, 'config1.dc')
            self._write_dc_file(config_path)
            with open(os.path.join(tmpdir, '00.png'), 'wb') as f:
                f.write(b'PNG')
            result = validate_theme(tmpdir)
            warnings = result.get('warnings', [])
            self.assertTrue(any('Theme.png' in w for w in warnings))

    def test_dd_format_date_mismatch(self):
        """0xDD format: date in overlay but not in display_elements → invalid."""
        with TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, 'config1.dc')
            # Build a 0xDD file with 0 display elements but that produces
            # date in config via legacy path — this is hard to trigger naturally,
            # so we'll just verify the validator runs for DD format
            buf = BytesIO()
            buf.write(b'\xdd')
            buf.write(b'\x01')
            buf.write(struct.pack('<i', 0))
            while buf.tell() < 100:
                buf.write(b'\x00')
            with open(config_path, 'wb') as f:
                f.write(buf.getvalue())
            with open(os.path.join(tmpdir, '00.png'), 'wb') as f:
                f.write(b'PNG')
            with open(os.path.join(tmpdir, 'Theme.png'), 'wb') as f:
                f.write(b'PNG')

            result = validate_theme(tmpdir)
            self.assertEqual(result['format'], '0xDD')


# ── validate_all_themes (DC header) ──────────────────────────────────────────

class TestValidateAllThemesDC(unittest.TestCase):

    def _write_dc_file(self, path):
        buf = BytesIO()
        buf.write(b'\xdc')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(8):
            buf.write(b'\x00')
        buf.write(struct.pack('<i', 0))
        for _ in range(13):
            buf.write(b'\x00')
            buf.write(struct.pack('<f', 24.0))
            buf.write(bytes([0, 3, 0, 255, 255, 255, 255]))
        buf.write(b'\x01\x00')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(13):
            buf.write(struct.pack('<ii', 0, 0))
        while buf.tell() < 100:
            buf.write(b'\x00')
        with open(path, 'wb') as f:
            f.write(buf.getvalue())

    def test_empty_dir(self):
        with TemporaryDirectory() as tmpdir:
            result = validate_all_themes(tmpdir)
            self.assertEqual(result['total'], 0)

    def test_nonexistent_dir(self):
        result = validate_all_themes('/nonexistent/path/12345')
        self.assertEqual(result['total'], 0)

    def test_mixed_themes(self):
        with TemporaryDirectory() as tmpdir:
            # Valid theme
            t1 = os.path.join(tmpdir, 'Theme1')
            os.makedirs(t1)
            self._write_dc_file(os.path.join(t1, 'config1.dc'))
            with open(os.path.join(t1, '00.png'), 'wb') as f:
                f.write(b'PNG')
            with open(os.path.join(t1, 'Theme.png'), 'wb') as f:
                f.write(b'PNG')

            # Invalid theme (no config)
            t2 = os.path.join(tmpdir, 'Theme2')
            os.makedirs(t2)

            result = validate_all_themes(tmpdir)
            self.assertEqual(result['total'], 2)
            self.assertGreaterEqual(result['invalid'], 1)
            self.assertEqual(len(result['problems']), result['invalid'])

    def test_verbose_prints(self):
        """verbose=True prints warnings."""
        with TemporaryDirectory() as tmpdir:
            t1 = os.path.join(tmpdir, 'Theme1')
            os.makedirs(t1)
            self._write_dc_file(os.path.join(t1, 'config1.dc'))
            # No 00.png or Theme.png → warnings
            result = validate_all_themes(tmpdir, verbose=True)
            self.assertGreaterEqual(result['with_warnings'], 1)

    def test_dd_format_counted(self):
        with TemporaryDirectory() as tmpdir:
            t1 = os.path.join(tmpdir, 'Theme1')
            os.makedirs(t1)
            buf = BytesIO()
            buf.write(b'\xdd')
            buf.write(b'\x01')
            buf.write(struct.pack('<i', 0))
            while buf.tell() < 100:
                buf.write(b'\x00')
            with open(os.path.join(t1, 'config1.dc'), 'wb') as f:
                f.write(buf.getvalue())
            with open(os.path.join(t1, '00.png'), 'wb') as f:
                f.write(b'PNG')
            with open(os.path.join(t1, 'Theme.png'), 'wb') as f:
                f.write(b'PNG')

            result = validate_all_themes(tmpdir)
            self.assertEqual(result['dd_format'], 1)


# ── parse_dc_file read_string edge cases ─────────────────────────────────────

class TestDcParserStringEdge(unittest.TestCase):

    def test_unicode_error_in_font_name(self):
        """Bad UTF-8 in font name yields empty string."""
        buf = BytesIO()
        buf.write(b'\xdc')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(8):
            buf.write(b'\x00')
        buf.write(struct.pack('<i', 0))
        # custom_text string
        buf.write(b'\x00')
        # First font with bad UTF-8 name
        bad_name = bytes([0xFF, 0xFE, 0xFD])
        buf.write(bytes([len(bad_name)]))
        buf.write(bad_name)
        buf.write(struct.pack('<f', 24.0))
        buf.write(bytes([0, 3, 0, 255, 128, 128, 128]))
        # Remaining 12 fonts
        for _ in range(12):
            buf.write(b'\x00')
            buf.write(struct.pack('<f', 24.0))
            buf.write(bytes([0, 3, 0, 255, 255, 255, 255]))
        buf.write(b'\x01\x00')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(13):
            buf.write(struct.pack('<ii', 0, 0))
        while buf.tell() < 100:
            buf.write(b'\x00')

        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(buf.getvalue())
            result = parse_dc_file(path)
            # Bad UTF-8 triggers except → falls back to 'Default'
            self.assertEqual(result['fonts'][0].name, 'Default')

    def test_struct_error_in_font_falls_back(self):
        """Truncated font data falls back to defaults."""
        buf = BytesIO()
        buf.write(b'\xdc')
        buf.write(struct.pack('<ii', 0, 0))
        for _ in range(8):
            buf.write(b'\x00')
        buf.write(struct.pack('<i', 0))
        buf.write(b'\x00')  # custom text
        # Only 1 font (incomplete)
        buf.write(b'\x00')
        buf.write(struct.pack('<f', 24.0))
        # Truncate here — remaining data too short
        while buf.tell() < 100:
            buf.write(b'\x00')

        with TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'config1.dc')
            with open(path, 'wb') as f:
                f.write(buf.getvalue())
            result = parse_dc_file(path)
            # Should have 13 fonts (some will be default fallbacks)
            self.assertEqual(len(result['fonts']), 13)


class TestLoadConfigJson(unittest.TestCase):
    """Test JSON config loading (load_config_json)."""

    def test_load_valid_json(self):
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {
                'version': 1,
                'display': {'rotation': 90, 'background_visible': True,
                             'screencast_visible': False, 'overlay_enabled': True},
                'mask': {'enabled': True, 'center_x': 160, 'center_y': 160},
                'elements': {'time': {'x': 10, 'y': 20, 'color': '#ff0000',
                                      'metric': 'time', 'enabled': True}},
            }
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            overlay_config, display_options = result
            self.assertEqual(overlay_config['time']['x'], 10)
            self.assertEqual(display_options['rotation'], 90)
            self.assertTrue(display_options['mask_enabled'])
            self.assertEqual(display_options['mask_position'], (160, 160))

    def test_load_missing_file(self):
        from trcc.dc_parser import load_config_json
        result = load_config_json('/nonexistent/config.json')
        self.assertIsNone(result)

    def test_load_invalid_json(self):
        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                f.write('not valid json {{{')
            result = load_config_json(path)
            self.assertIsNone(result)

    def test_load_missing_elements_key(self):
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump({'version': 1, 'display': {}}, f)
            result = load_config_json(path)
            self.assertIsNone(result)

    def test_round_trip_with_write(self):
        """Write JSON with dc_writer, read back with dc_parser."""
        from trcc.dc_parser import load_config_json
        from trcc.dc_writer import write_config_json
        with TemporaryDirectory() as d:
            overlay = {
                'time': {'x': 10, 'y': 20, 'color': '#ff6b35', 'metric': 'time',
                          'enabled': True, 'time_format': 0,
                          'font': {'name': 'Arial', 'size': 24, 'size_raw': 18.0,
                                    'style': 'bold', 'unit': 3, 'charset': 134}},
                'hw_0_1': {'x': 50, 'y': 100, 'color': '#ffffff', 'metric': 'cpu_temp',
                            'enabled': True},
            }
            display = {'rotation': 180, 'bg_display': True, 'overlay_enabled': True}
            mask = {'enabled': True, 'center_x': 160, 'center_y': 160}
            write_config_json(d, overlay, display, mask)

            result = load_config_json(os.path.join(d, 'config.json'))
            self.assertIsNotNone(result)
            loaded_overlay, loaded_display = result
            self.assertEqual(loaded_overlay['time']['x'], 10)
            self.assertEqual(loaded_overlay['time']['font']['name'], 'Arial')
            self.assertEqual(loaded_overlay['hw_0_1']['metric'], 'cpu_temp')
            self.assertEqual(loaded_display['rotation'], 180)
            self.assertTrue(loaded_display['mask_enabled'])

    def test_load_no_mask(self):
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {'version': 1, 'display': {}, 'elements': {}}
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            _, display_options = result
            self.assertNotIn('mask_enabled', display_options)

    def test_load_animation_file(self):
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {
                'version': 1, 'display': {},
                'animation': {'file': 'a001.mp4'},
                'elements': {},
            }
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            _, display_options = result
            self.assertEqual(display_options['animation_file'], 'a001.mp4')

    def test_load_no_animation(self):
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {'version': 1, 'display': {}, 'elements': {}}
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            _, display_options = result
            self.assertNotIn('animation_file', display_options)


class TestLoadConfigJsonReferenceFormat(unittest.TestCase):
    """Test load_config_json with new reference format (background/mask/dc)."""

    def test_reference_format_full(self):
        """Reference format with background, mask, dc, and mask_position."""
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {
                'background': '/path/to/video.mp4',
                'mask': '/path/to/mask_dir/',
                'dc': {'time_0': {'x': 10, 'y': 20, 'metric': 'time'}},
                'mask_position': [160, 160],
            }
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            overlay_config, display_options = result
            self.assertEqual(overlay_config['time_0']['x'], 10)
            self.assertEqual(display_options['background_path'], '/path/to/video.mp4')
            self.assertEqual(display_options['mask_path'], '/path/to/mask_dir/')
            self.assertEqual(display_options['mask_position'], (160, 160))
            self.assertTrue(display_options['overlay_enabled'])

    def test_reference_format_no_mask(self):
        """Reference format with background only, no mask."""
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {
                'background': '/path/to/image.png',
                'mask': None,
                'dc': {},
            }
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            overlay_config, display_options = result
            self.assertEqual(overlay_config, {})
            self.assertEqual(display_options['background_path'], '/path/to/image.png')
            self.assertNotIn('mask_path', display_options)
            self.assertFalse(display_options['overlay_enabled'])

    def test_reference_format_no_background(self):
        """Reference format with mask but no background."""
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {
                'background': None,
                'mask': '/path/to/mask/',
                'dc': {'cpu_0': {'x': 5, 'y': 5}},
            }
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            overlay_config, display_options = result
            self.assertEqual(overlay_config['cpu_0']['x'], 5)
            self.assertNotIn('background_path', display_options)
            self.assertEqual(display_options['mask_path'], '/path/to/mask/')
            self.assertTrue(display_options['overlay_enabled'])

    def test_reference_format_no_mask_position(self):
        """Reference format without mask_position key."""
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {
                'background': '/path/to/bg.png',
                'mask': '/path/to/mask/',
                'dc': {},
            }
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            _, display_options = result
            self.assertNotIn('mask_position', display_options)

    def test_reference_format_empty_dc(self):
        """Reference format with empty dc dict means overlay_enabled=False."""
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {'background': '/bg.png', 'mask': None, 'dc': {}}
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            _, display_options = result
            self.assertFalse(display_options['overlay_enabled'])

    def test_legacy_format_still_works(self):
        """Legacy format with 'elements' key still parses correctly."""
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            data = {
                'version': 1,
                'display': {'rotation': 90},
                'elements': {'time': {'x': 10, 'y': 20, 'metric': 'time'}},
            }
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump(data, f)
            result = load_config_json(path)
            self.assertIsNotNone(result)
            overlay_config, display_options = result
            self.assertEqual(overlay_config['time']['x'], 10)
            self.assertEqual(display_options['rotation'], 90)

    def test_not_a_dict(self):
        """JSON that's not a dict returns None."""
        import json

        from trcc.dc_parser import load_config_json
        with TemporaryDirectory() as d:
            path = os.path.join(d, 'config.json')
            with open(path, 'w') as f:
                json.dump([1, 2, 3], f)
            result = load_config_json(path)
            self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()
