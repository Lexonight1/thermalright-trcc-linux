#!/usr/bin/env python3
"""
Writer for TRCC config1.dc binary configuration files.
Creates themes in Windows-compatible format.

Based on decompiled Windows TRCC code:
- FormCZTV.cs buttonBCZT_Click (save theme): lines 5497-5655
- FormCZTV.cs buttonDaoChu_Click (export .tr): lines 5657-5820

Binary Format (0xDD - User/Cloud themes):
    byte: 0xDD magic
    bool: myXtxx (system info enabled)
    int32: element count
    For each element (UCXiTongXianShiSub):
        int32: myMode (0=hardware, 1=time, 2=weekday, 3=date, 4=custom)
        int32: myModeSub (format variant)
        int32: myX (x position)
        int32: myY (y position)
        int32: myMainCount (hardware category)
        int32: mySubCount (hardware sensor)
        string: font name (length-prefixed)
        float: font size
        byte: font style (0=Regular, 1=Bold, 2=Italic)
        byte: font unit (GraphicsUnit)
        byte: font charset
        byte: color alpha
        byte: color red
        byte: color green
        byte: color blue
        string: text content (length-prefixed)
    bool: myBjxs (background display)
    bool: myTpxs (transparent display)
    int32: directionB (rotation)
    int32: myUIMode
    int32: myMode
    bool: myYcbk (overlay enabled)
    int32: JpX, JpY, JpW, JpH (overlay rect)
    bool: myMbxs (mask enabled)
    int32: XvalMB, YvalMB (mask position)

Export Format (.tr files):
    byte[4]: 0xDD, 0xDC, 0xDD, 0xDC (magic header)
    Then same as above...
    Followed by embedded binary data for images
"""

import os
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, List, Optional, Tuple

# Import shared types from parser (single source of truth)
from trcc.binary_reader import BinaryReader
from trcc.dc_parser import METRIC_TO_IDS, DisplayElement


@dataclass
class ThemeConfig:
    """Complete theme configuration for saving."""
    # Display elements (UCXiTongXianShiSubArray)
    elements: List[DisplayElement] = field(default_factory=list)

    # System info global enable
    system_info_enabled: bool = True

    # Display options
    background_display: bool = True    # myBjxs
    transparent_display: bool = False  # myTpxs
    rotation: int = 0                  # directionB (0/90/180/270)
    ui_mode: int = 0                   # myUIMode
    display_mode: int = 0              # myMode

    # Overlay settings
    overlay_enabled: bool = True       # myYcbk
    overlay_x: int = 0                 # JpX
    overlay_y: int = 0                 # JpY
    overlay_w: int = 320               # JpW
    overlay_h: int = 320               # JpH

    # Mask settings
    mask_enabled: bool = False         # myMbxs
    mask_x: int = 0                    # XvalMB
    mask_y: int = 0                    # YvalMB


@dataclass
class CarouselConfig:
    """Carousel/slideshow configuration."""
    current_theme: int = 0             # myTheme - index of current theme
    enabled: bool = False              # isLunbo
    interval_seconds: int = 3          # myLunBoTimer (minimum 3)
    count: int = 0                     # lunBoCount
    theme_indices: List[int] = field(default_factory=lambda: [-1, -1, -1, -1, -1, -1])
    lcd_rotation: int = 1              # myLddVal (1-4)


class DcWriter:
    """Writer for TRCC config1.dc binary configuration files."""

    # ── Core binary writing ──

    @staticmethod
    def write(config: ThemeConfig, filepath: str) -> None:
        """Write a config1.dc file in Windows-compatible binary format."""
        with open(filepath, 'wb') as f:
            f.write(struct.pack('B', 0xDD))
            f.write(struct.pack('?', config.system_info_enabled))
            f.write(struct.pack('<i', len(config.elements)))
            for elem in config.elements:
                DcWriter._write_element(f, elem)
            DcWriter._write_display_options(f, config)

    @staticmethod
    def write_tr(config: ThemeConfig, theme_path: str, export_path: str) -> None:
        """Write a .tr export file (Windows buttonDaoChu).

        The .tr format is a config1.dc with magic header 0xDD,0xDC,0xDD,0xDC
        followed by embedded image data.
        """
        with open(export_path, 'wb') as f:
            # Magic header for .tr export
            f.write(struct.pack('BBBB', 0xDD, 0xDC, 0xDD, 0xDC))

            f.write(struct.pack('?', config.system_info_enabled))
            f.write(struct.pack('<i', len(config.elements)))
            for elem in config.elements:
                DcWriter._write_element(f, elem)
            DcWriter._write_display_options(f, config)

            # Padding (10240 bytes of 0xDC) - Windows pattern
            f.write(bytes([0xDC] * 10240))

            # Embed images if present
            DcWriter._write_tr_images(f, theme_path)

    # ── Element & option serialization ──

    @staticmethod
    def _write_string(f: IO[bytes], s: str) -> None:
        """Write a length-prefixed UTF-8 string (Windows BinaryWriter.Write(string))."""
        if not s:
            f.write(struct.pack('B', 0))
            return

        encoded = s.encode('utf-8')
        length = len(encoded)

        # Windows BinaryWriter uses 7-bit encoded length for strings
        if length < 128:
            f.write(struct.pack('B', length))
        else:
            f.write(struct.pack('B', (length & 0x7F) | 0x80))
            f.write(struct.pack('B', length >> 7))

        f.write(encoded)

    @staticmethod
    def _write_element(f: IO[bytes], elem: DisplayElement) -> None:
        """Write a single UCXiTongXianShiSub element to a binary stream."""
        f.write(struct.pack('<i', elem.mode))
        f.write(struct.pack('<i', elem.mode_sub))
        f.write(struct.pack('<i', elem.x))
        f.write(struct.pack('<i', elem.y))
        f.write(struct.pack('<i', elem.main_count))
        f.write(struct.pack('<i', elem.sub_count))
        DcWriter._write_string(f, elem.font_name)
        f.write(struct.pack('<f', elem.font_size))
        f.write(struct.pack('B', elem.font_style))
        f.write(struct.pack('B', elem.font_unit))
        f.write(struct.pack('B', elem.font_charset))
        a, r, g, b = elem.color_argb
        f.write(struct.pack('BBBB', a, r, g, b))
        DcWriter._write_string(f, elem.text)

    @staticmethod
    def _write_display_options(f: IO[bytes], config: ThemeConfig) -> None:
        """Write display options + overlay + mask settings to a binary stream."""
        f.write(struct.pack('?', config.background_display))
        f.write(struct.pack('?', config.transparent_display))
        f.write(struct.pack('<i', config.rotation))
        f.write(struct.pack('<i', config.ui_mode))
        f.write(struct.pack('<i', config.display_mode))
        f.write(struct.pack('?', config.overlay_enabled))
        f.write(struct.pack('<i', config.overlay_x))
        f.write(struct.pack('<i', config.overlay_y))
        f.write(struct.pack('<i', config.overlay_w))
        f.write(struct.pack('<i', config.overlay_h))
        f.write(struct.pack('?', config.mask_enabled))
        f.write(struct.pack('<i', config.mask_x))
        f.write(struct.pack('<i', config.mask_y))

    @staticmethod
    def _write_tr_images(f: IO[bytes], theme_path: str) -> None:
        """Write embedded image data for .tr export."""
        bg_path = os.path.join(theme_path, "00.png")
        mask_path = os.path.join(theme_path, "01.png")
        zt_path = os.path.join(theme_path, "Theme.zt")

        # Write mask image (01.png)
        if os.path.exists(mask_path):
            with open(mask_path, 'rb') as img:
                img_data = img.read()
                f.write(struct.pack('<i', len(img_data)))
                f.write(img_data)
        else:
            f.write(struct.pack('<i', 0))

        # Write background: either 00.png or Theme.zt
        if os.path.exists(bg_path):
            with open(bg_path, 'rb') as img:
                img_data = img.read()
                f.write(struct.pack('<i', 0))  # marker: not Theme.zt
                f.write(struct.pack('<i', len(img_data)))
                f.write(img_data)
        elif os.path.exists(zt_path):
            DcWriter._write_tr_zt_frames(f, zt_path)
        else:
            f.write(struct.pack('<i', 0))  # No background

    @staticmethod
    def _write_tr_zt_frames(f: IO[bytes], zt_path: str) -> None:
        """Write Theme.zt video frames into .tr export."""
        with open(zt_path, 'rb') as zt:
            zt_header = zt.read(1)
            if zt_header == b'\xDC':
                frame_count = struct.unpack('<i', zt.read(4))[0]
                f.write(struct.pack('<i', frame_count))
                for _ in range(frame_count):
                    ts = struct.unpack('<i', zt.read(4))[0]
                    f.write(struct.pack('<i', ts))
                for _ in range(frame_count):
                    frame_len = struct.unpack('<i', zt.read(4))[0]
                    frame_data = zt.read(frame_len)
                    f.write(struct.pack('<i', frame_len))
                    f.write(frame_data)
            else:
                f.write(struct.pack('<i', 0))  # Invalid zt

    # ── Overlay config → ThemeConfig conversion ──

    @staticmethod
    def overlay_to_theme(overlay_config: dict,
                         display_width: int = 320,
                         display_height: int = 320) -> ThemeConfig:
        """Convert overlay renderer config dict to ThemeConfig for saving."""
        theme = ThemeConfig()
        theme.overlay_w = display_width
        theme.overlay_h = display_height

        for _key, cfg in overlay_config.items():
            if not cfg.get('enabled', True):
                continue

            elem = DisplayElement(
                mode=0, mode_sub=0, x=cfg.get('x', 0), y=cfg.get('y', 0),
                main_count=0, sub_count=0,
            )

            # Parse font settings
            font_cfg = cfg.get('font', {})
            elem.font_name = font_cfg.get('name', 'Microsoft YaHei')
            elem.font_size = font_cfg.get('size_raw', font_cfg.get('size', 24.0))
            elem.font_style = 1 if font_cfg.get('style', 'regular') == 'bold' else 0
            elem.font_unit = font_cfg.get('unit', 3)
            elem.font_charset = font_cfg.get('charset', 134)

            # Parse color
            color_hex = cfg.get('color', '#FFFFFF')
            elem.color_argb = DcWriter._hex_to_argb(color_hex)

            # Determine element mode
            if 'metric' in cfg:
                metric = cfg['metric']
                if metric == 'time':
                    elem.mode = 1
                    elem.mode_sub = cfg.get('time_format', cfg.get('mode_sub', 0))
                elif metric == 'weekday':
                    elem.mode = 2
                elif metric == 'date':
                    elem.mode = 3
                    elem.mode_sub = cfg.get('date_format', cfg.get('mode_sub', 0))
                else:
                    elem.mode = 0
                    elem.main_count, elem.sub_count = METRIC_TO_IDS.get(metric, (0, 0))
            elif 'text' in cfg:
                elem.mode = 4
                elem.text = cfg['text']

            theme.elements.append(elem)

        return theme

    @staticmethod
    def _hex_to_argb(hex_color: str) -> Tuple[int, int, int, int]:
        """Convert hex color string to ARGB tuple."""
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 6:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            return (255, r, g, b)
        elif len(hex_color) == 8:
            a = int(hex_color[0:2], 16)
            r = int(hex_color[2:4], 16)
            g = int(hex_color[4:6], 16)
            b = int(hex_color[6:8], 16)
            return (a, r, g, b)
        return (255, 255, 255, 255)

    # ── Theme save (full directory) ──

    @staticmethod
    def save_theme(theme_path: str,
                   background_image=None,
                   mask_image=None,
                   overlay_config: Optional[dict] = None,
                   mask_position: Optional[Tuple[int, int]] = None,
                   display_width: int = 320,
                   display_height: int = 320,
                   dc_data: Optional[dict] = None) -> None:
        """Save a complete theme to disk in Windows-compatible format.

        Creates: 00.png, 01.png (mask), config1.dc, Theme.png (preview), config.json.
        """
        os.makedirs(theme_path, exist_ok=True)

        # Save background image
        if background_image:
            bg_path = os.path.join(theme_path, "00.png")
            background_image.save(bg_path)

            # Save preview thumbnail (fallback; controller generates a better one)
            preview_path = os.path.join(theme_path, "Theme.png")
            if not os.path.exists(preview_path):
                thumb = background_image.copy()
                thumb.thumbnail((120, 120))
                thumb.save(preview_path)

        # Save mask image
        if mask_image:
            mask_path = os.path.join(theme_path, "01.png")
            mask_image.save(mask_path)

        # Build theme config
        if overlay_config:
            theme = DcWriter.overlay_to_theme(overlay_config, display_width, display_height)
        else:
            theme = ThemeConfig()
            theme.overlay_w = display_width
            theme.overlay_h = display_height

        # Merge display options from original DC data (lossless round-trip)
        if dc_data:
            DcWriter._merge_dc_display_options(theme, dc_data)

        # Set mask position
        if mask_position:
            theme.mask_enabled = True
            theme.mask_x, theme.mask_y = mask_position
        elif mask_image:
            theme.mask_enabled = True

        # Write config1.dc (binary backup for Windows compatibility)
        config_path = os.path.join(theme_path, "config1.dc")
        DcWriter.write(theme, config_path)

        # Write config.json (human-readable, preferred on load)
        display_options = dc_data.get('display_options', {}) if dc_data else {}
        mask_settings: dict = {}
        if mask_position:
            mask_settings = {'enabled': True, 'center_x': mask_position[0], 'center_y': mask_position[1]}
        elif mask_image:
            mask_settings = {'enabled': True}

        # Detect video/animation files
        video_file = DcWriter._detect_video_file(theme_path)

        DcWriter.write_json(theme_path, overlay_config, display_options, mask_settings, video_file)

    @staticmethod
    def _merge_dc_display_options(theme: ThemeConfig, dc_data: dict) -> None:
        """Merge display options from parsed DC data into ThemeConfig."""
        opts = dc_data.get('display_options', {})
        if 'bg_display' in opts:
            theme.background_display = opts['bg_display']
        if 'tp_display' in opts:
            theme.transparent_display = opts['tp_display']
        if 'rotation' in opts:
            theme.rotation = opts['rotation']
        if 'ui_mode' in opts:
            theme.ui_mode = opts['ui_mode']
        if 'display_mode' in opts:
            theme.display_mode = opts['display_mode']
        if 'overlay_enabled' in opts:
            theme.overlay_enabled = opts['overlay_enabled']
        if 'overlay_rect' in opts:
            rect = opts['overlay_rect']
            theme.overlay_x = rect.get('x', 0)
            theme.overlay_y = rect.get('y', 0)
            theme.overlay_w = rect.get('w', theme.overlay_w)
            theme.overlay_h = rect.get('h', theme.overlay_h)

    @staticmethod
    def _detect_video_file(theme_path: str) -> Optional[str]:
        """Detect video/animation file in theme directory."""
        theme_dir = Path(theme_path)
        zt_path = theme_dir / 'Theme.zt'
        if zt_path.exists():
            return 'Theme.zt'
        mp4_files = list(theme_dir.glob('*.mp4'))
        if mp4_files:
            return mp4_files[0].name
        return None

    # ── JSON config ──

    @staticmethod
    def write_json(theme_path: str,
                   overlay_config: Optional[dict] = None,
                   display_options: Optional[dict] = None,
                   mask_settings: Optional[dict] = None,
                   video_file: Optional[str] = None) -> None:
        """Write theme config as human-readable JSON alongside config1.dc."""
        import json

        data = {
            'version': 1,
            'display': {
                'rotation': display_options.get('rotation', 0) if display_options else 0,
                'background_visible': display_options.get('bg_display', True) if display_options else True,
                'screencast_visible': display_options.get('tp_display', False) if display_options else False,
                'overlay_enabled': display_options.get('overlay_enabled', True) if display_options else True,
            },
            'animation': {
                'file': video_file,
            } if video_file else {},
            'mask': mask_settings or {},
            'elements': overlay_config or {},
        }

        json_path = os.path.join(theme_path, 'config.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── Export / Import (.tr) ──

    @staticmethod
    def export_theme(theme_path: str, export_path: str) -> None:
        """Export a theme as a .tr file for sharing."""
        from .dc_parser import DcParser

        config_file = os.path.join(theme_path, "config1.dc")

        if os.path.exists(config_file):
            parsed = DcParser.parse(config_file)
            theme = DcWriter._parsed_to_theme_config(parsed)
        else:
            theme = ThemeConfig()

        DcWriter.write_tr(theme, theme_path, export_path)

    @staticmethod
    def _parsed_to_theme_config(parsed: dict) -> ThemeConfig:
        """Convert parsed DC dict to ThemeConfig."""
        theme = ThemeConfig()
        theme.system_info_enabled = parsed.get('flags', {}).get('system_info', True)

        for pe in parsed.get('display_elements', []):
            elem = DisplayElement(
                mode=pe.mode, mode_sub=pe.mode_sub,
                x=pe.x, y=pe.y,
                main_count=pe.main_count, sub_count=pe.sub_count,
                font_name=pe.font_name, font_size=pe.font_size,
                font_style=pe.font_style,
                color_argb=pe.color_argb, text=pe.text,
            )
            theme.elements.append(elem)

        mask = parsed.get('mask_settings', {})
        theme.mask_enabled = mask.get('mask_enabled', False)
        mask_pos = mask.get('mask_position', (0, 0))
        theme.mask_x, theme.mask_y = mask_pos

        opts = parsed.get('display_options', {})
        theme.background_display = opts.get('background_display', True)
        theme.transparent_display = opts.get('transparent_display', False)
        theme.rotation = opts.get('direction', 0)

        return theme

    @staticmethod
    def import_theme(tr_path: str, theme_path: str) -> None:
        """Import a .tr file to create a theme directory."""
        os.makedirs(theme_path, exist_ok=True)

        with open(tr_path, 'rb') as f:
            data = f.read()

        if len(data) < 4 or data[0:4] != b'\xdd\xdc\xdd\xdc':
            raise ValueError("Invalid .tr file: wrong magic header")

        reader = BinaryReader(data, pos=4)
        theme = DcWriter._read_tr_config(reader)

        # Skip padding (10240 bytes of 0xDC)
        reader.skip(10240)

        # Extract embedded images
        DcWriter._read_tr_images(reader, theme_path)

        # Write config1.dc from parsed config
        DcWriter.write(theme, os.path.join(theme_path, "config1.dc"))

    @staticmethod
    def _read_tr_config(reader: BinaryReader) -> ThemeConfig:
        """Read ThemeConfig from .tr binary stream (after magic header)."""
        system_info = reader.read_bool()
        count = reader.read_int32()

        elements: list[DisplayElement] = []
        for _ in range(count):
            elem = DisplayElement(
                mode=reader.read_int32(),
                mode_sub=reader.read_int32(),
                x=reader.read_int32(),
                y=reader.read_int32(),
                main_count=reader.read_int32(),
                sub_count=reader.read_int32(),
            )
            elem.font_name, elem.font_size, elem.font_style, elem.font_unit, \
                elem.font_charset, a, r, g, b = reader.read_font_color()
            elem.color_argb = (a, r, g, b)
            elem.text = reader.read_string()
            elements.append(elem)

        # Read display options
        theme = ThemeConfig(
            elements=elements,
            system_info_enabled=system_info,
            background_display=reader.read_bool(),
            transparent_display=reader.read_bool(),
            rotation=reader.read_int32(),
            ui_mode=reader.read_int32(),
            display_mode=reader.read_int32(),
            overlay_enabled=reader.read_bool(),
            overlay_x=reader.read_int32(),
            overlay_y=reader.read_int32(),
            overlay_w=reader.read_int32(),
            overlay_h=reader.read_int32(),
            mask_enabled=reader.read_bool(),
            mask_x=reader.read_int32(),
            mask_y=reader.read_int32(),
        )
        return theme

    @staticmethod
    def _read_tr_images(reader: BinaryReader, theme_path: str) -> None:
        """Read embedded images from .tr binary stream."""
        # Read mask image (01.png)
        if reader.has_bytes(4):
            mask_size = reader.read_int32()
            if mask_size > 0 and reader.has_bytes(mask_size):
                mask_data = reader.read_bytes(mask_size)
                with open(os.path.join(theme_path, "01.png"), 'wb') as f:
                    f.write(mask_data)

        # Read background: either 00.png or Theme.zt
        if reader.has_bytes(4):
            marker = reader.read_int32()
            if marker == 0:
                # Static background (00.png)
                if reader.has_bytes(4):
                    bg_size = reader.read_int32()
                    if bg_size > 0 and reader.has_bytes(bg_size):
                        bg_data = reader.read_bytes(bg_size)
                        with open(os.path.join(theme_path, "00.png"), 'wb') as f:
                            f.write(bg_data)
            elif marker > 0:
                # Video frames (Theme.zt)
                DcWriter._read_tr_zt_frames(reader, theme_path, marker)

    @staticmethod
    def _read_tr_zt_frames(reader: BinaryReader, theme_path: str, frame_count: int) -> None:
        """Read Theme.zt video frames from .tr binary stream."""
        zt_path = os.path.join(theme_path, "Theme.zt")
        with open(zt_path, 'wb') as zt:
            zt.write(struct.pack('B', 0xDC))
            zt.write(struct.pack('<i', frame_count))
            for _ in range(frame_count):
                if reader.has_bytes(4):
                    ts = reader.read_int32()
                    zt.write(struct.pack('<i', ts))
            for _ in range(frame_count):
                if reader.has_bytes(4):
                    frame_len = reader.read_int32()
                    if reader.has_bytes(frame_len):
                        frame_data = reader.read_bytes(frame_len)
                        zt.write(struct.pack('<i', frame_len))
                        zt.write(frame_data)

    # ── Carousel config (Theme.dc) ──

    @staticmethod
    def write_carousel(config: CarouselConfig, filepath: str) -> None:
        """Write carousel configuration to Theme.dc."""
        with open(filepath, 'wb') as f:
            f.write(struct.pack('B', 0xDC))
            f.write(struct.pack('<i', config.current_theme))
            f.write(struct.pack('?', config.enabled))
            f.write(struct.pack('<i', max(3, config.interval_seconds)))
            f.write(struct.pack('<i', config.count))

            indices = config.theme_indices[:6]
            while len(indices) < 6:
                indices.append(-1)
            for idx in indices:
                f.write(struct.pack('<i', idx))

            f.write(struct.pack('<i', config.lcd_rotation))

    @staticmethod
    def read_carousel(filepath: str) -> Optional[CarouselConfig]:
        """Read carousel configuration from Theme.dc."""
        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, 'rb') as f:
                magic = struct.unpack('B', f.read(1))[0]
                if magic != 0xDC:
                    return None

                config = CarouselConfig(
                    current_theme=struct.unpack('<i', f.read(4))[0],
                    enabled=struct.unpack('?', f.read(1))[0],
                    interval_seconds=struct.unpack('<i', f.read(4))[0],
                    count=struct.unpack('<i', f.read(4))[0],
                    theme_indices=[struct.unpack('<i', f.read(4))[0] for _ in range(6)],
                )

                try:
                    config.lcd_rotation = struct.unpack('<i', f.read(4))[0]
                except struct.error:
                    config.lcd_rotation = 1

                return config

        except Exception:
            return None


# Backward-compat aliases
write_dc_file = DcWriter.write
write_tr_export = DcWriter.write_tr
_write_string = DcWriter._write_string
_write_element = DcWriter._write_element
_write_display_options = DcWriter._write_display_options
overlay_config_to_theme = DcWriter.overlay_to_theme
_hex_to_argb = DcWriter._hex_to_argb
_metric_to_hardware_ids = lambda metric: METRIC_TO_IDS.get(metric, (0, 0))  # noqa: E731
save_theme = DcWriter.save_theme
write_config_json = DcWriter.write_json
export_theme = DcWriter.export_theme
import_theme = DcWriter.import_theme
write_carousel_config = DcWriter.write_carousel
read_carousel_config = DcWriter.read_carousel


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dc_writer.py <command> [args]")
        print("Commands:")
        print("  test           - Create a test theme")
        print("  export <path>  - Export theme at path to .tr")
        print("  import <tr>    - Import .tr file")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'test':
        # Create a test theme
        test_path = tempfile.mkdtemp(prefix='trcc_test_theme_')

        config = ThemeConfig()
        config.elements = [
            DisplayElement(
                mode=1, mode_sub=0,  # Time, 24-hour
                x=100, y=50,
                font_name="Microsoft YaHei", font_size=32,
                color_argb=(255, 255, 255, 255),
            ),
            DisplayElement(
                mode=3, mode_sub=0,  # Date, yyyy/MM/dd
                x=100, y=100,
                font_name="Microsoft YaHei", font_size=20,
                color_argb=(255, 200, 200, 200),
            ),
            DisplayElement(
                mode=0, mode_sub=0,  # Hardware - CPU temp
                x=50, y=200,
                main_count=0, sub_count=1,
                font_name="Microsoft YaHei", font_size=24,
                color_argb=(255, 255, 107, 53),  # Orange
            ),
        ]

        os.makedirs(test_path, exist_ok=True)
        DcWriter.write(config, os.path.join(test_path, "config1.dc"))
        print(f"Test theme created at: {test_path}")

        # Verify by reading back
        from trcc.dc_parser import DcParser
        parsed = DcParser.parse(os.path.join(test_path, "config1.dc"))
        print(f"Verified: {len(parsed['display_elements'])} elements")

    elif cmd == 'export' and len(sys.argv) > 2:
        theme_path = sys.argv[2]
        export_path = theme_path.rstrip('/') + '.tr'
        DcWriter.export_theme(theme_path, export_path)
        print(f"Exported to: {export_path}")

    elif cmd == 'import' and len(sys.argv) > 2:
        tr_path = sys.argv[2]
        theme_name = Path(tr_path).stem
        theme_path = f"/tmp/imported_{theme_name}"
        DcWriter.import_theme(tr_path, theme_path)
        print(f"Imported to: {theme_path}")

    else:
        print(f"Unknown command: {cmd}")
