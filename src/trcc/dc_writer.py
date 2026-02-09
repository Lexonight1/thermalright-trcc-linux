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
from typing import List, Optional, Tuple

# Import shared DisplayElement from parser (DRY)
from trcc.binary_reader import BinaryReader
from trcc.dc_parser import DisplayElement


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


def write_dc_file(config: ThemeConfig, filepath: str) -> None:
    """
    Write a config1.dc file in Windows-compatible binary format.

    Args:
        config: ThemeConfig with all theme settings
        filepath: Path to write config1.dc
    """
    with open(filepath, 'wb') as f:
        # Magic byte: 0xDD for user/cloud themes
        f.write(struct.pack('B', 0xDD))

        # System info enabled flag
        f.write(struct.pack('?', config.system_info_enabled))

        # Element count
        f.write(struct.pack('<i', len(config.elements)))

        # Write each element
        for elem in config.elements:
            # 6 int32s: mode, modeSub, x, y, mainCount, subCount
            f.write(struct.pack('<i', elem.mode))
            f.write(struct.pack('<i', elem.mode_sub))
            f.write(struct.pack('<i', elem.x))
            f.write(struct.pack('<i', elem.y))
            f.write(struct.pack('<i', elem.main_count))
            f.write(struct.pack('<i', elem.sub_count))

            # Font name (length-prefixed string)
            _write_string(f, elem.font_name)

            # Font size (float)
            f.write(struct.pack('<f', elem.font_size))

            # Font style, unit, charset (3 bytes)
            f.write(struct.pack('B', elem.font_style))
            f.write(struct.pack('B', elem.font_unit))
            f.write(struct.pack('B', elem.font_charset))

            # Color ARGB (4 bytes)
            a, r, g, b = elem.color_argb
            f.write(struct.pack('BBBB', a, r, g, b))

            # Text content (length-prefixed string)
            _write_string(f, elem.text)

        # Display options
        f.write(struct.pack('?', config.background_display))   # myBjxs
        f.write(struct.pack('?', config.transparent_display))  # myTpxs
        f.write(struct.pack('<i', config.rotation))            # directionB
        f.write(struct.pack('<i', config.ui_mode))             # myUIMode
        f.write(struct.pack('<i', config.display_mode))        # myMode

        # Overlay settings
        f.write(struct.pack('?', config.overlay_enabled))      # myYcbk
        f.write(struct.pack('<i', config.overlay_x))           # JpX
        f.write(struct.pack('<i', config.overlay_y))           # JpY
        f.write(struct.pack('<i', config.overlay_w))           # JpW
        f.write(struct.pack('<i', config.overlay_h))           # JpH

        # Mask settings
        f.write(struct.pack('?', config.mask_enabled))         # myMbxs
        f.write(struct.pack('<i', config.mask_x))              # XvalMB
        f.write(struct.pack('<i', config.mask_y))              # YvalMB


def write_tr_export(config: ThemeConfig, theme_path: str, export_path: str) -> None:
    """
    Write a .tr export file (Windows buttonDaoChu).

    The .tr format is a config1.dc with magic header 0xDD,0xDC,0xDD,0xDC
    followed by embedded image data.

    Args:
        config: ThemeConfig with all theme settings
        theme_path: Source theme directory (for 00.png, 01.png)
        export_path: Path to write .tr file
    """
    with open(export_path, 'wb') as f:
        # Magic header for .tr export
        f.write(struct.pack('BBBB', 0xDD, 0xDC, 0xDD, 0xDC))

        # System info enabled flag
        f.write(struct.pack('?', config.system_info_enabled))

        # Element count
        f.write(struct.pack('<i', len(config.elements)))

        # Write each element (same as config1.dc)
        for elem in config.elements:
            f.write(struct.pack('<i', elem.mode))
            f.write(struct.pack('<i', elem.mode_sub))
            f.write(struct.pack('<i', elem.x))
            f.write(struct.pack('<i', elem.y))
            f.write(struct.pack('<i', elem.main_count))
            f.write(struct.pack('<i', elem.sub_count))
            _write_string(f, elem.font_name)
            f.write(struct.pack('<f', elem.font_size))
            f.write(struct.pack('B', elem.font_style))
            f.write(struct.pack('B', elem.font_unit))
            f.write(struct.pack('B', elem.font_charset))
            a, r, g, b = elem.color_argb
            f.write(struct.pack('BBBB', a, r, g, b))
            _write_string(f, elem.text)

        # Display options
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

        # Padding (10240 bytes of 0xDC) - Windows pattern
        f.write(bytes([0xDC] * 10240))

        # Embed images if present
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
            # Static background
            with open(bg_path, 'rb') as img:
                img_data = img.read()
                f.write(struct.pack('<i', 0))  # marker: not Theme.zt
                f.write(struct.pack('<i', len(img_data)))
                f.write(img_data)
        elif os.path.exists(zt_path):
            # Video frames (Theme.zt)
            with open(zt_path, 'rb') as zt:
                zt_header = zt.read(1)
                if zt_header == b'\xDC':
                    frame_count = struct.unpack('<i', zt.read(4))[0]
                    f.write(struct.pack('<i', frame_count))
                    # Timestamps
                    for _ in range(frame_count):
                        ts = struct.unpack('<i', zt.read(4))[0]
                        f.write(struct.pack('<i', ts))
                    # Frame data
                    for _ in range(frame_count):
                        frame_len = struct.unpack('<i', zt.read(4))[0]
                        frame_data = zt.read(frame_len)
                        f.write(struct.pack('<i', frame_len))
                        f.write(frame_data)
                else:
                    f.write(struct.pack('<i', 0))  # Invalid zt
        else:
            f.write(struct.pack('<i', 0))  # No background


def _write_string(f, s: str) -> None:
    """Write a length-prefixed UTF-8 string (Windows BinaryWriter.Write(string))."""
    if not s:
        f.write(struct.pack('B', 0))
        return

    encoded = s.encode('utf-8')
    length = len(encoded)

    # Windows BinaryWriter uses 7-bit encoded length for strings
    # For lengths < 128, it's just one byte
    if length < 128:
        f.write(struct.pack('B', length))
    else:
        # Multi-byte length encoding (unlikely for font names)
        f.write(struct.pack('B', (length & 0x7F) | 0x80))
        f.write(struct.pack('B', length >> 7))

    f.write(encoded)


def overlay_config_to_theme(overlay_config: dict,
                           display_width: int = 320,
                           display_height: int = 320) -> ThemeConfig:
    """
    Convert overlay renderer config dict to ThemeConfig for saving.

    Args:
        overlay_config: Dict from dc_parser.dc_to_overlay_config() or overlay editor
        display_width: Display width for overlay rect
        display_height: Display height for overlay rect

    Returns:
        ThemeConfig ready for write_dc_file()
    """
    theme = ThemeConfig()
    theme.overlay_w = display_width
    theme.overlay_h = display_height

    # Map our config keys back to display elements
    for key, cfg in overlay_config.items():
        if not cfg.get('enabled', True):
            continue

        elem = DisplayElement(
            mode=0, mode_sub=0, x=cfg.get('x', 0), y=cfg.get('y', 0),
            main_count=0, sub_count=0,
        )

        # Parse font settings
        font_cfg = cfg.get('font', {})
        elem.font_name = font_cfg.get('name', 'Microsoft YaHei')
        # Use raw DC value if available, otherwise fall back to rendered size
        elem.font_size = font_cfg.get('size_raw', font_cfg.get('size', 24.0))
        elem.font_style = 1 if font_cfg.get('style', 'regular') == 'bold' else 0
        elem.font_unit = font_cfg.get('unit', 3)
        elem.font_charset = font_cfg.get('charset', 134)

        # Parse color
        color_hex = cfg.get('color', '#FFFFFF')
        elem.color_argb = _hex_to_argb(color_hex)

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
            elif metric.startswith('cpu') or metric.startswith('gpu'):
                elem.mode = 0
                elem.main_count, elem.sub_count = _metric_to_hardware_ids(metric)
            else:
                elem.mode = 0
                elem.main_count, elem.sub_count = _metric_to_hardware_ids(metric)
        elif 'text' in cfg:
            elem.mode = 4
            elem.text = cfg['text']

        theme.elements.append(elem)

    return theme


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


def _metric_to_hardware_ids(metric: str) -> Tuple[int, int]:
    """Map metric name to hardware category/sensor IDs."""
    mapping = {
        'cpu_temp': (0, 1),
        'cpu_percent': (0, 2),
        'cpu_freq': (0, 3),
        'cpu_power': (0, 4),
        'gpu_temp': (1, 1),
        'gpu_usage': (1, 2),
        'gpu_clock': (1, 3),
        'gpu_power': (1, 4),
        'mem_percent': (2, 1),
        'mem_clock': (2, 2),
        'disk_activity': (3, 1),
    }
    return mapping.get(metric, (0, 0))


def save_theme(theme_path: str,
               background_image=None,
               mask_image=None,
               overlay_config: Optional[dict] = None,
               mask_position: Optional[Tuple[int, int]] = None,
               display_width: int = 320,
               display_height: int = 320,
               dc_data: Optional[dict] = None) -> None:
    """
    Save a complete theme to disk in Windows-compatible format.

    Creates:
        - 00.png: Background image
        - 01.png: Mask image (if provided)
        - config1.dc: Binary configuration
        - Theme.png: Preview thumbnail (same as 00.png)

    Args:
        theme_path: Directory to save theme
        background_image: PIL Image for background (00.png)
        mask_image: PIL Image for mask overlay (01.png)
        overlay_config: Dict of overlay elements
        mask_position: (x, y) position for mask
        display_width: Display width
        display_height: Display height
        dc_data: Original parsed DC data for lossless round-trip (from parse_dc_file)
    """
    os.makedirs(theme_path, exist_ok=True)

    # Save background image
    if background_image:
        bg_path = os.path.join(theme_path, "00.png")
        background_image.save(bg_path)

        # Save preview thumbnail (background-only fallback;
        # controller generates a better one with overlays/mask applied)
        preview_path = os.path.join(theme_path, "Theme.png")
        if not os.path.exists(preview_path):
            thumb = background_image.copy()
            thumb.thumbnail((120, 120))
            thumb.save(preview_path)

    # Save mask image
    if mask_image:
        mask_path = os.path.join(theme_path, "01.png")
        mask_image.save(mask_path)

    # Build and save config
    if overlay_config:
        theme = overlay_config_to_theme(overlay_config, display_width, display_height)
    else:
        theme = ThemeConfig()
        theme.overlay_w = display_width
        theme.overlay_h = display_height

    # Merge display options from original DC data (lossless round-trip)
    if dc_data:
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
            theme.overlay_w = rect.get('w', display_width)
            theme.overlay_h = rect.get('h', display_height)

    # Set mask position
    if mask_position:
        theme.mask_enabled = True
        theme.mask_x, theme.mask_y = mask_position
    elif mask_image:
        theme.mask_enabled = True

    # Write config1.dc (binary backup for Windows compatibility)
    config_path = os.path.join(theme_path, "config1.dc")
    write_dc_file(theme, config_path)

    # Write config.json (human-readable, preferred on load)
    display_options = {}
    if dc_data:
        display_options = dc_data.get('display_options', {})
    mask_settings = {}
    if mask_position:
        mask_settings = {'enabled': True, 'center_x': mask_position[0], 'center_y': mask_position[1]}
    elif mask_image:
        mask_settings = {'enabled': True}

    # Detect video/animation files in theme directory
    video_file = None
    theme_dir = Path(theme_path)
    zt_path = theme_dir / 'Theme.zt'
    if zt_path.exists():
        video_file = 'Theme.zt'
    else:
        mp4_files = list(theme_dir.glob('*.mp4'))
        if mp4_files:
            video_file = mp4_files[0].name

    write_config_json(theme_path, overlay_config, display_options, mask_settings, video_file)


def write_config_json(theme_path: str,
                      overlay_config: Optional[dict] = None,
                      display_options: Optional[dict] = None,
                      mask_settings: Optional[dict] = None,
                      video_file: Optional[str] = None) -> None:
    """
    Write theme config as human-readable JSON.

    Creates config.json alongside config1.dc so users and developers
    can view and edit theme settings with any text editor.

    Args:
        theme_path: Theme directory
        overlay_config: Overlay elements dict (from renderer.config / dc_to_overlay_config)
        display_options: Display settings (rotation, bg_display, etc.)
        mask_settings: Mask settings (enabled, center_x, center_y)
        video_file: Video/animation filename (e.g., 'Theme.zt' or 'a001.mp4')
    """
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


def export_theme(theme_path: str, export_path: str) -> None:
    """
    Export a theme as a .tr file for sharing.

    Args:
        theme_path: Source theme directory
        export_path: Destination .tr file path
    """
    from .dc_parser import parse_dc_file

    config_file = os.path.join(theme_path, "config1.dc")

    if os.path.exists(config_file):
        # Parse existing config
        parsed = parse_dc_file(config_file)

        # Convert to ThemeConfig
        theme = ThemeConfig()
        theme.system_info_enabled = parsed.get('flags', {}).get('system_info', True)

        # Convert display elements
        for pe in parsed.get('display_elements', []):
            elem = DisplayElement(
                mode=pe.mode,
                mode_sub=pe.mode_sub,
                x=pe.x,
                y=pe.y,
                main_count=pe.main_count,
                sub_count=pe.sub_count,
                font_name=pe.font_name,
                font_size=pe.font_size,
                font_style=pe.font_style,
                color_argb=pe.color_argb,
                text=pe.text,
            )
            theme.elements.append(elem)

        # Copy mask settings
        mask = parsed.get('mask_settings', {})
        theme.mask_enabled = mask.get('mask_enabled', False)
        mask_pos = mask.get('mask_position', (0, 0))
        theme.mask_x, theme.mask_y = mask_pos

        # Copy display options
        opts = parsed.get('display_options', {})
        theme.background_display = opts.get('background_display', True)
        theme.transparent_display = opts.get('transparent_display', False)
        theme.rotation = opts.get('direction', 0)
    else:
        # Create minimal config
        theme = ThemeConfig()

    # Write .tr export
    write_tr_export(theme, theme_path, export_path)


def import_theme(tr_path: str, theme_path: str) -> None:
    """
    Import a .tr file to create a theme directory.

    Args:
        tr_path: Source .tr file path
        theme_path: Destination theme directory
    """
    os.makedirs(theme_path, exist_ok=True)

    with open(tr_path, 'rb') as f:
        data = f.read()

    # Verify magic header
    if len(data) < 4 or data[0:4] != b'\xdd\xdc\xdd\xdc':
        raise ValueError("Invalid .tr file: wrong magic header")

    reader = BinaryReader(data, pos=4)
    read_int32 = reader.read_int32
    read_bool = reader.read_bool
    read_string = reader.read_string
    # Parse config (same format as config1.dc but with magic header)
    system_info = read_bool()
    count = read_int32()

    elements = []
    for _ in range(count):
        elem = DisplayElement(
            mode=read_int32(),
            mode_sub=read_int32(),
            x=read_int32(),
            y=read_int32(),
            main_count=read_int32(),
            sub_count=read_int32(),
        )
        elem.font_name, elem.font_size, elem.font_style, elem.font_unit, \
            elem.font_charset, a, r, g, b = reader.read_font_color()
        elem.color_argb = (a, r, g, b)
        elem.text = read_string()
        elements.append(elem)

    # Read display options
    bg_display = read_bool()
    trans_display = read_bool()
    rotation = read_int32()
    ui_mode = read_int32()
    display_mode = read_int32()
    overlay_enabled = read_bool()
    jp_x = read_int32()
    jp_y = read_int32()
    jp_w = read_int32()
    jp_h = read_int32()
    mask_enabled = read_bool()
    mask_x = read_int32()
    mask_y = read_int32()

    # Skip padding (10240 bytes of 0xDC)
    reader.skip(10240)

    # Read mask image (01.png)
    if reader.has_bytes(4):
        mask_size = read_int32()
        if mask_size > 0 and reader.has_bytes(mask_size):
            mask_data = reader.read_bytes(mask_size)
            with open(os.path.join(theme_path, "01.png"), 'wb') as f:
                f.write(mask_data)

    # Read background: either 00.png or Theme.zt
    if reader.has_bytes(4):
        marker = read_int32()
        if marker == 0:
            # Static background (00.png)
            if reader.has_bytes(4):
                bg_size = read_int32()
                if bg_size > 0 and reader.has_bytes(bg_size):
                    bg_data = reader.read_bytes(bg_size)
                    with open(os.path.join(theme_path, "00.png"), 'wb') as f:
                        f.write(bg_data)
        elif marker > 0:
            # Video frames (Theme.zt)
            frame_count = marker
            zt_path = os.path.join(theme_path, "Theme.zt")
            with open(zt_path, 'wb') as zt:
                zt.write(struct.pack('B', 0xDC))
                zt.write(struct.pack('<i', frame_count))
                # Timestamps
                for _ in range(frame_count):
                    if reader.has_bytes(4):
                        ts = read_int32()
                        zt.write(struct.pack('<i', ts))
                # Frame data
                for _ in range(frame_count):
                    if reader.has_bytes(4):
                        frame_len = read_int32()
                        if reader.has_bytes(frame_len):
                            frame_data = reader.read_bytes(frame_len)
                            zt.write(struct.pack('<i', frame_len))
                            zt.write(frame_data)

    # Create and save config1.dc
    theme = ThemeConfig()
    theme.elements = elements
    theme.system_info_enabled = system_info
    theme.background_display = bg_display
    theme.transparent_display = trans_display
    theme.rotation = rotation
    theme.ui_mode = ui_mode
    theme.display_mode = display_mode
    theme.overlay_enabled = overlay_enabled
    theme.overlay_x = jp_x
    theme.overlay_y = jp_y
    theme.overlay_w = jp_w
    theme.overlay_h = jp_h
    theme.mask_enabled = mask_enabled
    theme.mask_x = mask_x
    theme.mask_y = mask_y

    write_dc_file(theme, os.path.join(theme_path, "config1.dc"))


# =============================================================================
# Carousel Configuration (Theme.dc) - Windows LunBo
# =============================================================================
#
# Binary format (0xDC header):
#   byte:   0xDC magic
#   int32:  current_theme_index (myTheme)
#   bool:   carousel_enabled (isLunbo)
#   int32:  carousel_interval_seconds (myLunBoTimer, min 3)
#   int32:  carousel_count (lunBoCount)
#   int32[6]: carousel_indices (lunBoArray, -1 = empty slot)
#   int32:  lcd_rotation (myLddVal, 1-4)
#
# See: FormCZTV.cs ReadFileThemeSub() line 1010 and line 4591

@dataclass
class CarouselConfig:
    """Carousel/slideshow configuration."""
    current_theme: int = 0             # myTheme - index of current theme
    enabled: bool = False              # isLunbo
    interval_seconds: int = 3          # myLunBoTimer (minimum 3)
    count: int = 0                     # lunBoCount
    theme_indices: List[int] = field(default_factory=lambda: [-1, -1, -1, -1, -1, -1])
    lcd_rotation: int = 1              # myLddVal (1-4)


def write_carousel_config(config: CarouselConfig, filepath: str) -> None:
    """Write carousel configuration to Theme.dc."""
    with open(filepath, 'wb') as f:
        # Magic header
        f.write(struct.pack('B', 0xDC))

        # Current theme index
        f.write(struct.pack('<i', config.current_theme))

        # Carousel enabled flag
        f.write(struct.pack('?', config.enabled))

        # Interval in seconds (min 3)
        interval = max(3, config.interval_seconds)
        f.write(struct.pack('<i', interval))

        # Count of themes in carousel
        f.write(struct.pack('<i', config.count))

        # 6 theme indices (-1 = empty)
        indices = config.theme_indices[:6]
        while len(indices) < 6:
            indices.append(-1)
        for idx in indices:
            f.write(struct.pack('<i', idx))

        # LCD rotation (1-4)
        f.write(struct.pack('<i', config.lcd_rotation))


def read_carousel_config(filepath: str) -> Optional[CarouselConfig]:
    """Read carousel configuration from Theme.dc."""
    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, 'rb') as f:
            # Check magic header
            magic = struct.unpack('B', f.read(1))[0]
            if magic != 0xDC:
                return None

            config = CarouselConfig()

            # Current theme index
            config.current_theme = struct.unpack('<i', f.read(4))[0]

            # Carousel enabled
            config.enabled = struct.unpack('?', f.read(1))[0]

            # Interval
            config.interval_seconds = struct.unpack('<i', f.read(4))[0]

            # Count
            config.count = struct.unpack('<i', f.read(4))[0]

            # 6 theme indices
            config.theme_indices = []
            for _ in range(6):
                idx = struct.unpack('<i', f.read(4))[0]
                config.theme_indices.append(idx)

            # LCD rotation (optional - may not exist in older files)
            try:
                config.lcd_rotation = struct.unpack('<i', f.read(4))[0]
            except struct.error:
                config.lcd_rotation = 1

            return config

    except Exception:
        return None


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
        write_dc_file(config, os.path.join(test_path, "config1.dc"))
        print(f"Test theme created at: {test_path}")

        # Verify by reading back
        from trcc.dc_parser import parse_dc_file
        parsed = parse_dc_file(os.path.join(test_path, "config1.dc"))
        print(f"Verified: {len(parsed['display_elements'])} elements")

    elif cmd == 'export' and len(sys.argv) > 2:
        theme_path = sys.argv[2]
        export_path = theme_path.rstrip('/') + '.tr'
        export_theme(theme_path, export_path)
        print(f"Exported to: {export_path}")

    elif cmd == 'import' and len(sys.argv) > 2:
        tr_path = sys.argv[2]
        theme_name = Path(tr_path).stem
        theme_path = f"/tmp/imported_{theme_name}"
        import_theme(tr_path, theme_path)
        print(f"Imported to: {theme_path}")

    else:
        print(f"Unknown command: {cmd}")
