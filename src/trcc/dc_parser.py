#!/usr/bin/env python3
"""
Parser for TRCC config1.dc binary configuration files.
These files store theme overlay settings (fonts, colors, positions).

Based on decompiled Windows TRCC code:
- UCXiTongXianShiSub.cs - Display element structure
- FormCZTV.cs - Config file read/write
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from trcc.binary_reader import BinaryReader


@dataclass
class FontConfig:
    """Font configuration from .dc file"""
    name: str
    size: float
    style: int      # 0=Regular, 1=Bold, 2=Italic
    unit: int       # GraphicsUnit
    charset: int
    color_argb: tuple  # (alpha, red, green, blue)


@dataclass
class ElementConfig:
    """Element position and font config"""
    x: int
    y: int
    font: Optional[FontConfig] = None
    enabled: bool = True


@dataclass
class DisplayElement:
    """
    Display element from UCXiTongXianShiSub (time, date, weekday, hardware info, custom text).

    myMode values:
        0 = Hardware info (CPU/GPU metrics)
        1 = Time
        2 = Weekday (SUN, MON, TUE, etc.)
        3 = Date
        4 = Custom text

    myModeSub values (format variants):
        For mode 1 (Time):
            0 = HH:mm (24-hour)
            1 = hh:mm AM/PM (12-hour)
            2 = HH:mm (same as 0)
        For mode 3 (Date):
            0 = yyyy/MM/dd
            1 = yyyy/MM/dd (same as 0)
            2 = dd/MM/yyyy
            3 = MM/dd
            4 = dd/MM
    """
    mode: int           # Display type (0=hardware, 1=time, 2=weekday, 3=date, 4=custom)
    mode_sub: int       # Format variant
    x: int              # X position
    y: int              # Y position
    main_count: int = 0     # For hardware info - sensor category
    sub_count: int = 0      # For hardware info - specific sensor
    font_name: str = "Microsoft YaHei"
    font_size: float = 24.0
    font_style: int = 0  # 0=Regular, 1=Bold, 2=Italic
    font_unit: int = 3   # GraphicsUnit.Point
    font_charset: int = 134  # GB2312 (Windows default: new Font("微软雅黑", 36f, 0, 3, 134))
    color_argb: tuple = (255, 255, 255, 255)  # ARGB
    text: str = ""      # Custom text content

    @property
    def mode_name(self) -> str:
        """Get human-readable mode name"""
        names = {0: 'hardware', 1: 'time', 2: 'weekday', 3: 'date', 4: 'custom'}
        return names.get(self.mode, f'unknown_{self.mode}')

    @property
    def color_hex(self) -> str:
        """Get color as hex string"""
        _, r, g, b = self.color_argb
        return f"#{r:02x}{g:02x}{b:02x}"


# Single source of truth for hardware sensor ↔ metric name mapping.
# dc_writer and dc_config import from here.
HARDWARE_METRICS: Dict[Tuple[int, int], str] = {
    (0, 1): 'cpu_temp',
    (0, 2): 'cpu_percent',
    (0, 3): 'cpu_freq',
    (0, 4): 'cpu_power',
    (1, 1): 'gpu_temp',
    (1, 2): 'gpu_usage',
    (1, 3): 'gpu_clock',
    (1, 4): 'gpu_power',
    (2, 1): 'mem_percent',
    (2, 2): 'mem_clock',
    (3, 1): 'disk_activity',
}

METRIC_TO_IDS: Dict[str, Tuple[int, int]] = {v: k for k, v in HARDWARE_METRICS.items()}


class DcParser:
    """Parser for TRCC config1.dc binary configuration files."""

    @staticmethod
    def _clamp_font_size(raw: float, default: float = 24.0) -> float:
        """Clamp a parsed font size to valid range, or return default if invalid."""
        if 0 < raw < 100:
            return max(8, min(72, raw))
        return default

    @staticmethod
    def get_hardware_metric_name(main_count: int, sub_count: int) -> str:
        """Map hardware sensor indices to metric names."""
        return HARDWARE_METRICS.get((main_count, sub_count), f'sensor_{main_count}_{sub_count}')

    @staticmethod
    def metric_to_hardware_ids(metric: str) -> Tuple[int, int]:
        """Map metric name to hardware (main_count, sub_count) IDs."""
        return METRIC_TO_IDS.get(metric, (0, 0))

    @staticmethod
    def parse(filepath: str) -> dict:
        """
        Parse a TRCC config1.dc file and extract overlay configuration.

        Based on C# BinaryReader pattern from FormCZTV.ReadSystemConfiguration.
        """
        with open(filepath, 'rb') as f:
            data = f.read()

        # Check magic byte first
        if not data or data[0] not in (0xdc, 0xdd):
            raise ValueError(f"Invalid magic byte: 0x{data[0]:02x}, expected 0xdc or 0xdd")

        # Format-aware minimum size: 0xDD can be 46 bytes (0 elements), 0xDC needs ~100+
        min_size = 46 if data[0] == 0xdd else 100
        if len(data) < min_size:
            raise ValueError(f"File too small to be valid .dc: {len(data)} bytes")

        result: dict = {
            'version': struct.unpack_from('<I', data, 0)[0],
            'elements': {},
            'fonts': [],
            'flags': {},
            'display_elements': [],  # UCXiTongXianShiSub array
        }

        # For 0xDD format (cloud themes), parse differently - display elements come first
        if data[0] == 0xdd:
            return DcParser._parse_dd_format(data)

        r = BinaryReader(data, pos=1)  # Skip magic byte 0xdc
        read_int32 = r.read_int32
        read_bool = r.read_bool
        read_string = r.read_string
        read_float = r.read_float
        read_byte = r.read_byte

        # Read header
        read_int32()  # Skip first int
        read_int32()  # Skip second int

        # Read enable flags (8 booleans)
        # Windows FormCZTV.cs order: flag4→subCount=3(Clock), flag5→subCount=2(Usage%)
        flag_custom = read_bool()
        flag_sysinfo = read_bool()  # myXtxx - system info global
        flag_cpu_temp = read_bool()
        flag_cpu_freq = read_bool()   # Clock/MHz (was incorrectly named cpu_usage)
        flag_cpu_usage = read_bool()  # Usage/% (was incorrectly named cpu_freq)
        flag_gpu_temp = read_bool()
        flag_gpu_freq = read_bool()   # Clock/MHz (was incorrectly named gpu_usage)
        flag_gpu_usage = read_bool()  # Usage/% (was incorrectly named gpu_clock)

        result['flags'] = {
            'custom_text': flag_custom,
            'system_info': flag_sysinfo,
            'cpu_temp': flag_cpu_temp,
            'cpu_freq': flag_cpu_freq,
            'cpu_usage': flag_cpu_usage,
            'gpu_temp': flag_gpu_temp,
            'gpu_clock': flag_gpu_freq,   # Key matches element type, variable named for clarity
            'gpu_usage': flag_gpu_usage,
        }

        read_int32()  # Skip another int

        # Read font configurations (13 total)
        fonts = []
        custom_text = ""

        for i in range(13):
            try:
                if i == 0:
                    custom_text = read_string()
                    font_name = read_string()
                else:
                    font_name = read_string()

                font_size = read_float()
                style = read_byte()
                unit = read_byte()
                charset = read_byte()
                alpha = read_byte()
                red = read_byte()
                green = read_byte()
                blue = read_byte()

                fonts.append(FontConfig(
                    name=font_name or "Default",
                    size=DcParser._clamp_font_size(font_size),
                    style=style,
                    unit=unit,
                    charset=charset,
                    color_argb=(alpha, red, green, blue)
                ))
            except (struct.error, IndexError):
                fonts.append(FontConfig(
                    name="Default",
                    size=24,
                    style=1,
                    unit=0,
                    charset=0,
                    color_argb=(255, 128, 128, 128)
                ))

        result['fonts'] = fonts
        result['custom_text'] = custom_text

        # After fonts, there are 2 bools and 2 int32s before positions
        try:
            myBjxs = read_bool()
            myTpxs = read_bool()
            directionB = read_int32()
            myUIMode = read_int32()

            result['display_options'] = {
                'background_display': myBjxs,
                'transparent_display': myTpxs,
                'direction': directionB,
                'ui_mode': myUIMode,
            }
        except (struct.error, IndexError):
            result['display_options'] = {
                'background_display': True,
                'transparent_display': False,
                'direction': 0,
                'ui_mode': 0,
            }

        # Read positions - 13 pairs of int32 (X, Y)
        element_order = [
            'custom_text',
            'cpu_temp',
            'cpu_label',
            'cpu_freq',
            'cpu_freq_label',
            'cpu_usage',
            'cpu_usage_label',
            'gpu_temp',
            'gpu_label',
            'gpu_clock',
            'gpu_clock_label',
            'gpu_usage',
            'gpu_usage_label',
        ]

        # Map element names to their corresponding flag keys
        element_to_flag = {
            'custom_text': 'custom_text',
            'cpu_temp': 'cpu_temp',
            'cpu_label': 'cpu_temp',
            'cpu_usage': 'cpu_usage',
            'cpu_usage_label': 'cpu_usage',
            'cpu_freq': 'cpu_freq',
            'cpu_freq_label': 'cpu_freq',
            'gpu_temp': 'gpu_temp',
            'gpu_label': 'gpu_temp',
            'gpu_usage': 'gpu_usage',
            'gpu_usage_label': 'gpu_usage',
            'gpu_clock': 'gpu_clock',
            'gpu_clock_label': 'gpu_clock',
        }

        for i, elem_name in enumerate(element_order):
            try:
                if not r.has_bytes(8):
                    break
                x = read_int32()
                y = read_int32()

                font = fonts[i] if i < len(fonts) else None
                flag_key = element_to_flag.get(elem_name, elem_name)
                enabled = result['flags'].get(flag_key, True)

                result['elements'][elem_name] = ElementConfig(
                    x=x, y=y, font=font, enabled=enabled
                )
            except (struct.error, IndexError):
                pass

        # For 0xDC format, time/date/weekday are stored in a specific location
        if data[0] == 0xdc:
            try:
                read_string()  # Skip custom text string
                read_bool()    # num8 (unknown)
                read_int32()   # num5 (myMode)
                myYcbk = read_bool()
                JpX = read_int32()
                JpY = read_int32()
                JpW = read_int32()
                JpH = read_int32()
                myMbxs = read_bool()
                XvalMB = read_int32()
                YvalMB = read_int32()

                result['mask_settings'] = {
                    'overlay_enabled': myYcbk,
                    'overlay_rect': (JpX, JpY, JpW, JpH),
                    'mask_enabled': myMbxs,
                    'mask_position': (XvalMB, YvalMB),
                }

                flag10 = read_bool()
                flag11 = read_bool()
                flag12 = read_bool()
                date_format = read_int32()
                time_format = read_int32()
                date_x = read_int32()
                date_y = read_int32()
                time_x = read_int32()
                time_y = read_int32()

                date_font_name, date_font_size, date_font_style, _, _, \
                    date_alpha, date_red, date_green, date_blue = r.read_font_color()

                time_font_name, time_font_size, time_font_style, _, _, \
                    time_alpha, time_red, time_green, time_blue = r.read_font_color()

                flag13 = read_bool()
                weekday_x = read_int32()
                weekday_y = read_int32()

                weekday_font_name, weekday_font_size, weekday_font_style, _, _, \
                    weekday_alpha, weekday_red, weekday_green, weekday_blue = r.read_font_color()

                display_elements = []

                if flag10 and flag11:
                    display_elements.append(DisplayElement(
                        mode=3, mode_sub=date_format, x=date_x, y=date_y,
                        font_name=date_font_name or "Microsoft YaHei",
                        font_size=DcParser._clamp_font_size(date_font_size, 20),
                        font_style=date_font_style,
                        color_argb=(date_alpha, date_red, date_green, date_blue),
                    ))

                if flag10 and flag12:
                    display_elements.append(DisplayElement(
                        mode=1, mode_sub=time_format, x=time_x, y=time_y,
                        font_name=time_font_name or "Microsoft YaHei",
                        font_size=DcParser._clamp_font_size(time_font_size, 32),
                        font_style=time_font_style,
                        color_argb=(time_alpha, time_red, time_green, time_blue),
                    ))

                if flag10 and flag13:
                    display_elements.append(DisplayElement(
                        mode=2, mode_sub=0, x=weekday_x, y=weekday_y,
                        font_name=weekday_font_name or "Microsoft YaHei",
                        font_size=DcParser._clamp_font_size(weekday_font_size, 20),
                        font_style=weekday_font_style,
                        color_argb=(weekday_alpha, weekday_red, weekday_green, weekday_blue),
                    ))

                result['display_elements'] = display_elements

            except (struct.error, IndexError):
                pass
        else:
            try:
                display_elements = DcParser._parse_display_elements(data, r.pos)
                result['display_elements'] = display_elements
            except Exception:
                pass

        return result

    @staticmethod
    def _parse_dd_format(data: bytes) -> dict:
        """Parse 0xDD format config (cloud themes)."""
        result: dict = {
            'version': struct.unpack_from('<I', data, 0)[0],
            'elements': {},
            'fonts': [],
            'flags': {},
            'display_elements': [],
        }

        r = BinaryReader(data, pos=1)
        read_int32 = r.read_int32
        read_bool = r.read_bool
        read_string = r.read_string
        try:
            myXtxx = read_bool()
            result['flags']['system_info'] = myXtxx

            count = read_int32()
            if count < 0 or count > 100:
                return result

            display_elements = []

            for _ in range(count):
                mode = read_int32()
                mode_sub = read_int32()
                x = read_int32()
                y = read_int32()
                main_count = read_int32()
                sub_count = read_int32()

                font_name, font_size, font_style, font_unit, font_charset, \
                    alpha, red, green, blue = r.read_font_color()
                custom_text = read_string()

                elem = DisplayElement(
                    mode=mode, mode_sub=mode_sub, x=x, y=y,
                    main_count=main_count, sub_count=sub_count,
                    font_name=font_name or "Microsoft YaHei",
                    font_size=DcParser._clamp_font_size(font_size),
                    font_style=font_style, font_unit=font_unit,
                    font_charset=font_charset,
                    color_argb=(alpha, red, green, blue),
                    text=custom_text,
                )
                display_elements.append(elem)

            result['display_elements'] = display_elements

            try:
                myBjxs = read_bool()
                myTpxs = read_bool()
                directionB = read_int32()
                myUIMode = read_int32()
                myMode = read_int32()
                myYcbk = read_bool()
                JpX = read_int32()
                JpY = read_int32()
                JpW = read_int32()
                JpH = read_int32()
                myMbxs = read_bool()
                XvalMB = read_int32()
                YvalMB = read_int32()

                result['display_options'] = {
                    'background_display': myBjxs,
                    'screencast_display': myTpxs,
                    'direction': directionB,
                    'ui_mode': myUIMode,
                    'mode': myMode,
                }

                result['mask_settings'] = {
                    'overlay_enabled': myYcbk,
                    'overlay_rect': (JpX, JpY, JpW, JpH),
                    'mask_enabled': myMbxs,
                    'mask_position': (XvalMB, YvalMB),
                }
            except (struct.error, IndexError):
                pass

        except (struct.error, IndexError):
            pass

        return result

    @staticmethod
    def _parse_display_elements(data: bytes, start_pos: int) -> List[DisplayElement]:
        """Parse UCXiTongXianShiSubArray from config data."""
        r = BinaryReader(data, pos=start_pos)
        elements: List[DisplayElement] = []

        if not r.has_bytes(4):
            return elements

        count = r.read_int32()
        if count < 0 or count > 100:
            return elements

        for _ in range(count):
            try:
                if not r.has_bytes(24):
                    break

                mode = r.read_int32()
                mode_sub = r.read_int32()
                x = r.read_int32()
                y = r.read_int32()
                main_count = r.read_int32()
                sub_count = r.read_int32()

                font_name, font_size, font_style, _, _, \
                    alpha, red, green, blue = r.read_font_color()
                text = r.read_string()

                elem = DisplayElement(
                    mode=mode, mode_sub=mode_sub, x=x, y=y,
                    main_count=main_count, sub_count=sub_count,
                    font_name=font_name or "Microsoft YaHei",
                    font_size=DcParser._clamp_font_size(font_size),
                    font_style=font_style,
                    color_argb=(alpha, red, green, blue),
                    text=text,
                )
                elements.append(elem)

            except (struct.error, IndexError):
                break

        return elements

    @staticmethod
    def to_overlay_config(dc_config: dict, display_width: int = 320, display_height: int = 320) -> dict:
        """Convert parsed .dc config to overlay renderer config format."""
        elements = dc_config.get('elements', {})
        display_elements = dc_config.get('display_elements', [])

        overlay_config: dict = {}

        DPI_SCALE = 96.0 / 72.0
        MAX_FONT_SIZE = 48
        MIN_FONT_SIZE = 12

        time_count = 0
        date_count = 0
        weekday_count = 0

        for elem in display_elements:
            raw_size = elem.font_size * DPI_SCALE
            font_size = int(max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, raw_size)))

            config_entry: dict = {
                'x': elem.x,
                'y': elem.y,
                'color': elem.color_hex,
                'font': {
                    'size': font_size,
                    'size_raw': elem.font_size,
                    'style': 'bold' if elem.font_style == 1 else 'regular',
                    'name': elem.font_name,
                    'unit': elem.font_unit,
                    'charset': elem.font_charset,
                },
                'enabled': True,
                'mode_sub': elem.mode_sub,
            }

            if elem.mode == 1:
                key = 'time' if time_count == 0 else f'time_{time_count}'
                config_entry['metric'] = 'time'
                config_entry['time_format'] = elem.mode_sub
                overlay_config[key] = config_entry
                time_count += 1

            elif elem.mode == 2:
                key = 'weekday' if weekday_count == 0 else f'weekday_{weekday_count}'
                config_entry['metric'] = 'weekday'
                overlay_config[key] = config_entry
                weekday_count += 1

            elif elem.mode == 3:
                key = 'date' if date_count == 0 else f'date_{date_count}'
                config_entry['metric'] = 'date'
                config_entry['date_format'] = elem.mode_sub
                overlay_config[key] = config_entry
                date_count += 1

            elif elem.mode == 4:
                key = f'custom_{len([k for k in overlay_config if k.startswith("custom")])}'
                config_entry['text'] = elem.text
                overlay_config[key] = config_entry

            elif elem.mode == 0:
                hw_key = f'hw_{elem.main_count}_{elem.sub_count}'
                config_entry['metric'] = DcParser.get_hardware_metric_name(elem.main_count, elem.sub_count)
                config_entry['temp_unit'] = elem.mode_sub
                overlay_config[hw_key] = config_entry

        # Map legacy TRCC elements (for older config format)
        mapping = {
            'custom_text': ('custom_text', None, None),
            'cpu_temp': ('cpu_temp', 'cpu_temp', None),
            'cpu_label': ('cpu_label', None, 'CPU'),
            'cpu_usage': ('cpu_usage', 'cpu_percent', None),
            'cpu_usage_label': ('cpu_usage_label', None, 'CPU'),
            'cpu_freq': ('cpu_freq', 'cpu_freq', None),
            'cpu_freq_label': ('cpu_freq_label', None, 'CPU'),
            'gpu_temp': ('gpu_temp', 'gpu_temp', None),
            'gpu_label': ('gpu_label', None, 'GPU'),
            'gpu_usage': ('gpu_usage', 'gpu_usage', None),
            'gpu_usage_label': ('gpu_usage_label', None, 'GPU'),
            'gpu_clock': ('gpu_clock', 'gpu_clock', None),
            'gpu_clock_label': ('gpu_clock_label', None, 'GPU'),
        }

        for our_key, (dc_key, metric, label_text) in mapping.items():
            elem = elements.get(dc_key)
            if not elem:
                continue

            font_size = 24
            color = "#FF6B35" if 'cpu' in dc_key else "#35A7FF"

            if elem.font:
                raw_size = elem.font.size * DPI_SCALE
                font_size = int(max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, raw_size)))
                _, r, g, b = elem.font.color_argb
                color = f"#{r:02x}{g:02x}{b:02x}"

            config_entry = {
                'x': elem.x,
                'y': elem.y,
                'color': color,
                'font': {'size': font_size, 'style': 'bold' if (elem.font and elem.font.style == 1) else 'regular'},
                'enabled': elem.enabled,
            }

            if our_key == 'custom_text':
                custom_text = dc_config.get('custom_text', '')
                if custom_text:
                    config_entry['text'] = custom_text
                else:
                    continue
            elif label_text is not None:
                config_entry['text'] = label_text
            elif metric is not None:
                config_entry['metric'] = metric
            else:
                continue

            if our_key not in overlay_config:
                overlay_config[our_key] = config_entry

        return overlay_config

    @staticmethod
    def load_json(filepath: str) -> Optional[Tuple[dict, dict]]:
        """Load theme config from a JSON file (config.json)."""
        import json

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        if not isinstance(data, dict):
            return None

        if 'dc' in data:
            overlay_config = data.get('dc', {})
            display_options: dict = {}
            if data.get('background'):
                display_options['background_path'] = data['background']
            if data.get('mask'):
                display_options['mask_path'] = data['mask']
            if data.get('mask_position'):
                display_options['mask_position'] = tuple(data['mask_position'])
            display_options['overlay_enabled'] = bool(overlay_config)
            return overlay_config, display_options

        if 'elements' not in data:
            return None

        overlay_config = data.get('elements', {})

        display = data.get('display', {})
        display_options = {}
        if 'rotation' in display:
            display_options['rotation'] = display['rotation']
        if 'background_visible' in display:
            display_options['bg_display'] = display['background_visible']
        if 'screencast_visible' in display:
            display_options['tp_display'] = display['screencast_visible']
        if 'overlay_enabled' in display:
            display_options['overlay_enabled'] = display['overlay_enabled']

        animation = data.get('animation', {})
        if animation and animation.get('file'):
            display_options['animation_file'] = animation['file']

        mask = data.get('mask', {})
        if mask:
            display_options['mask_enabled'] = mask.get('enabled', False)
            if 'center_x' in mask and 'center_y' in mask:
                display_options['mask_position'] = (mask['center_x'], mask['center_y'])

        return overlay_config, display_options

    @staticmethod
    def list_configs(base_path: str) -> list:
        """List all config1.dc files in theme directories."""
        base = Path(base_path)
        return sorted(str(dc_file) for dc_file in base.rglob('config1.dc'))

    @staticmethod
    def validate_theme(theme_path: str, display_width: int = 320, display_height: int = 320) -> dict:
        """Validate a theme's config and return any issues found."""
        import os

        result: dict = {
            'valid': True,
            'format': None,
            'issues': [],
            'warnings': [],
        }

        config_path = os.path.join(theme_path, 'config1.dc')

        if not os.path.exists(config_path):
            result['valid'] = False
            result['issues'].append('Missing config1.dc')
            return result

        try:
            parsed = DcParser.parse(config_path)
            magic = parsed['version'] & 0xFF
            result['format'] = f'0x{magic:02X}'

            overlay = DcParser.to_overlay_config(parsed)
            display_elements = parsed.get('display_elements', [])

            if magic == 0xdd:
                has_date_elem = any(e.mode == 3 for e in display_elements)
                has_time_elem = any(e.mode == 1 for e in display_elements)
                has_date_config = any(k.startswith('date') for k in overlay)
                has_time_config = any(k.startswith('time') for k in overlay)

                if has_date_config and not has_date_elem:
                    result['issues'].append('Date in config but not in display_elements (0xDD bug)')
                    result['valid'] = False
                if has_time_config and not has_time_elem:
                    result['issues'].append('Time in config but not in display_elements (0xDD bug)')
                    result['valid'] = False

            for key, cfg in overlay.items():
                x, y = cfg.get('x', 0), cfg.get('y', 0)
                if x < 0 or x > display_width or y < 0 or y > display_height:
                    result['warnings'].append(
                        f'{key}: position ({x}, {y}) outside {display_width}x{display_height}')

            mask = parsed.get('mask_settings', {})
            if mask.get('mask_enabled'):
                pos = mask.get('mask_position', (0, 0))
                if pos[0] < 0 or pos[0] > display_width or pos[1] < 0 or pos[1] > display_height:
                    result['warnings'].append(f'Mask position {pos} may be outside bounds')

            mask_file = os.path.join(theme_path, '01.png')
            bg_file = os.path.join(theme_path, '00.png')
            preview_file = os.path.join(theme_path, 'Theme.png')

            if not os.path.exists(mask_file) and not os.path.exists(bg_file):
                result['warnings'].append('No 00.png or 01.png - theme may be transparent only')

            if not os.path.exists(preview_file):
                result['warnings'].append('No Theme.png preview')

        except Exception as e:
            result['valid'] = False
            result['issues'].append(f'Parse error: {e}')

        return result

    @staticmethod
    def validate_all(themes_dir: str, verbose: bool = False) -> dict:
        """Validate all themes in a directory."""
        import os

        summary: dict = {
            'total': 0,
            'valid': 0,
            'invalid': 0,
            'with_warnings': 0,
            'dc_format': 0,
            'dd_format': 0,
            'problems': [],
        }

        if not os.path.exists(themes_dir):
            return summary

        themes = sorted([d for d in os.listdir(themes_dir)
                        if os.path.isdir(os.path.join(themes_dir, d))])

        for theme in themes:
            theme_path = os.path.join(themes_dir, theme)
            result = DcParser.validate_theme(theme_path)

            summary['total'] += 1

            if result['format'] == '0xDC':
                summary['dc_format'] += 1
            elif result['format'] == '0xDD':
                summary['dd_format'] += 1

            if result['valid']:
                summary['valid'] += 1
            else:
                summary['invalid'] += 1
                summary['problems'].append({
                    'theme': theme,
                    'issues': result['issues'],
                })

            if result['warnings']:
                summary['with_warnings'] += 1
                if verbose:
                    print(f"{theme}: {result['warnings']}")

        return summary


# Backward-compat aliases
parse_dc_file = DcParser.parse
parse_dd_format = DcParser._parse_dd_format
parse_display_elements = DcParser._parse_display_elements
dc_to_overlay_config = DcParser.to_overlay_config
get_hardware_metric_name = DcParser.get_hardware_metric_name
load_config_json = DcParser.load_json
list_theme_configs = DcParser.list_configs
validate_theme = DcParser.validate_theme
validate_all_themes = DcParser.validate_all


if __name__ == '__main__':
    import json
    import sys

    if len(sys.argv) < 2:
        dc_path = "/home/ignorant/Downloads/TRCCCAPEN/Data/USBLCD/Theme320320/Theme1/config1.dc"
    else:
        dc_path = sys.argv[1]

    print(f"Parsing: {dc_path}")
    print("=" * 60)

    config = DcParser.parse(dc_path)

    print(f"Version: 0x{config['version']:04x}")
    print(f"\nFonts ({len(config['fonts'])}):")
    for i, font in enumerate(config['fonts']):
        print(f"  [{i}] {font.name}: size={font.size:.1f}, style={font.style}, "
              f"color=({font.color_argb[1]},{font.color_argb[2]},{font.color_argb[3]})")

    print("\nElement Positions:")
    for name, elem in config['elements'].items():
        color_str = ""
        if elem.font:
            r, g, b = elem.font.color_argb[1:4]
            color_str = f" color=#{r:02x}{g:02x}{b:02x}"
        print(f"  {name:20s}: x={elem.x:3d}, y={elem.y:3d}{color_str}")

    if config['display_elements']:
        print(f"\nDisplay Elements ({len(config['display_elements'])}):")
        for i, elem in enumerate(config['display_elements']):
            print(f"  [{i}] {elem.mode_name}: x={elem.x}, y={elem.y}, "
                  f"format={elem.mode_sub}, color={elem.color_hex}")

    print("\nOverlay Config (for renderer):")
    overlay = DcParser.to_overlay_config(config)
    print(json.dumps(overlay, indent=2))
