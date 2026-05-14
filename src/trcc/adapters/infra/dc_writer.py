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

import logging
import os
import struct
from pathlib import Path
from typing import IO

from trcc.core.models import (
    METRIC_TO_IDS,
    CarouselConfig,
    DisplayElement,
    OverlayMode,
    ThemeConfig,
)

from .binary_reader import BinaryReader

log = logging.getLogger(__name__)

# ── Core binary writing ──────────────────────────────────────────────────


def write(config: ThemeConfig, filepath: str) -> None:
    """Write a config1.dc file in Windows-compatible binary format."""
    log.debug("write: filepath=%s elements=%d", filepath, len(config.elements))
    with open(filepath, 'wb') as f:
        f.write(struct.pack('B', 0xDD))
        f.write(struct.pack('?', config.system_info_enabled))
        f.write(struct.pack('<i', len(config.elements)))
        for elem in config.elements:
            _write_element(f, elem)
        _write_display_options(f, config)


def write_tr(config: ThemeConfig, theme_path: str, export_path: str) -> None:
    """Write a .tr export file (Windows buttonDaoChu).

    The .tr format is a config1.dc with magic header 0xDD,0xDC,0xDD,0xDC
    followed by embedded image data.
    """
    log.debug("write_tr: theme_path=%s export_path=%s", theme_path, export_path)
    with open(export_path, 'wb') as f:
        f.write(struct.pack('BBBB', 0xDD, 0xDC, 0xDD, 0xDC))
        f.write(struct.pack('?', config.system_info_enabled))
        f.write(struct.pack('<i', len(config.elements)))
        for elem in config.elements:
            _write_element(f, elem)
        _write_display_options(f, config)
        f.write(bytes([0xDC] * 10240))
        _write_tr_images(f, theme_path)


# ── Element & option serialization ───────────────────────────────────────


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


def _write_element(f: IO[bytes], elem: DisplayElement) -> None:
    """Write a single UCXiTongXianShiSub element to a binary stream."""
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


def _write_tr_images(f: IO[bytes], theme_path: str) -> None:
    """Write embedded image data for .tr export."""
    bg_path = os.path.join(theme_path, "00.png")
    mask_path = os.path.join(theme_path, "01.png")
    zt_path = os.path.join(theme_path, "Theme.zt")

    if os.path.exists(mask_path):
        with open(mask_path, 'rb') as img:
            img_data = img.read()
            f.write(struct.pack('<i', len(img_data)))
            f.write(img_data)
    else:
        f.write(struct.pack('<i', 0))

    if os.path.exists(bg_path):
        with open(bg_path, 'rb') as img:
            img_data = img.read()
            f.write(struct.pack('<i', 0))
            f.write(struct.pack('<i', len(img_data)))
            f.write(img_data)
    elif os.path.exists(zt_path):
        _write_tr_zt_frames(f, zt_path)
    else:
        f.write(struct.pack('<i', 0))


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
            f.write(struct.pack('<i', 0))


# ── Overlay config → ThemeConfig conversion ──────────────────────────────


def overlay_to_theme(overlay_config: dict,
                     display_width: int,
                     display_height: int) -> ThemeConfig:
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

        font_cfg = cfg.get('font', {})
        elem.font_name = font_cfg.get('name', 'Microsoft YaHei')
        elem.font_size = font_cfg.get('size_raw', font_cfg.get('size', 24.0))
        elem.font_style = 1 if font_cfg.get('style', 'regular') == 'bold' else 0
        elem.font_unit = font_cfg.get('unit', 3)
        elem.font_charset = font_cfg.get('charset', 134)

        color_hex = cfg.get('color', '#FFFFFF')
        elem.color_argb = _hex_to_argb(color_hex)

        if 'metric' in cfg:
            metric = cfg['metric']
            if metric == 'time':
                elem.mode = OverlayMode.TIME
                elem.mode_sub = cfg.get('time_format', cfg.get('mode_sub', 0))
            elif metric == 'weekday':
                elem.mode = OverlayMode.WEEKDAY
            elif metric == 'date':
                elem.mode = OverlayMode.DATE
                elem.mode_sub = cfg.get('date_format', cfg.get('mode_sub', 0))
            else:
                elem.mode = OverlayMode.HARDWARE
                elem.main_count, elem.sub_count = METRIC_TO_IDS.get(metric, (0, 0))
        elif 'text' in cfg:
            elem.mode = OverlayMode.CUSTOM
            elem.text = cfg['text']

        theme.elements.append(elem)

    return theme


def _hex_to_argb(hex_color: str) -> tuple[int, int, int, int]:
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


# ── Theme save (full directory) ──────────────────────────────────────────


def save_theme(theme_path: str,
               background_image=None,
               mask_image=None,
               overlay_config: dict | None = None,
               mask_position: tuple[int, int] | None = None,
               *,
               display_width: int,
               display_height: int,
               dc_data: dict | None = None) -> None:
    """Save a complete theme to disk in Windows-compatible format.

    Creates: 00.png, 01.png (mask), config1.dc, Theme.png (preview), config.json.
    """
    log.debug("save_theme: theme_path=%s display=%dx%d", theme_path, display_width, display_height)
    os.makedirs(theme_path, exist_ok=True)

    if background_image:
        bg_path = os.path.join(theme_path, "00.png")
        background_image.save(bg_path, "PNG")
        preview_path = os.path.join(theme_path, "Theme.png")
        if not os.path.exists(preview_path):
            from PySide6.QtCore import Qt
            iw, ih = background_image.width(), background_image.height()
            scale = min(120 / iw, 120 / ih, 1.0)
            thumb = background_image.scaled(
                max(1, int(iw * scale)),
                max(1, int(ih * scale)),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            thumb.save(preview_path, "PNG")

    if mask_image:
        mask_path = os.path.join(theme_path, "01.png")
        mask_image.save(mask_path, "PNG")

    if overlay_config:
        theme = overlay_to_theme(overlay_config, display_width, display_height)
    else:
        theme = ThemeConfig()
        theme.overlay_w = display_width
        theme.overlay_h = display_height

    if dc_data:
        _merge_dc_display_options(theme, dc_data)

    if mask_position:
        theme.mask_enabled = True
        theme.mask_x, theme.mask_y = mask_position
    elif mask_image:
        theme.mask_enabled = True

    config_path = os.path.join(theme_path, "config1.dc")
    write(theme, config_path)

    display_options = dc_data.get('display_options', {}) if dc_data else {}
    mask_settings: dict = {}
    if mask_position:
        mask_settings = {'enabled': True, 'center_x': mask_position[0], 'center_y': mask_position[1]}
    elif mask_image:
        mask_settings = {'enabled': True}

    video_file = _detect_video_file(theme_path)
    write_json(theme_path, overlay_config, display_options, mask_settings, video_file)


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


def _detect_video_file(theme_path: str) -> str | None:
    """Detect video/animation file in theme directory."""
    theme_dir = Path(theme_path)
    zt_path = theme_dir / 'Theme.zt'
    if zt_path.exists():
        return 'Theme.zt'
    mp4_files = list(theme_dir.glob('*.mp4'))
    if mp4_files:
        return mp4_files[0].name
    return None


# ── JSON config ──────────────────────────────────────────────────────────


def write_json(theme_path: str,
               overlay_config: dict | None = None,
               display_options: dict | None = None,
               mask_settings: dict | None = None,
               video_file: str | None = None) -> None:
    """Write theme config as human-readable JSON alongside config1.dc."""
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

    from ...core.io import atomic_write_json
    json_path = os.path.join(theme_path, 'config.json')
    atomic_write_json(json_path, data, ensure_ascii=False)


# ── Export / Import (.tr) ────────────────────────────────────────────────


def export_theme(theme_path: str, export_path: str) -> None:
    """Export a theme as a .tr file for sharing."""
    log.debug("export_theme: theme_path=%s export_path=%s", theme_path, export_path)
    from .dc_parser import DcParser

    config_file = os.path.join(theme_path, "config1.dc")
    if os.path.exists(config_file):
        parsed = DcParser.parse(config_file)
        theme = _parsed_to_theme_config(parsed)
    else:
        theme = ThemeConfig()

    write_tr(theme, theme_path, export_path)


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


def import_theme(tr_path: str, theme_path: str) -> None:
    """Import a .tr file to create a theme directory."""
    log.debug("import_theme: tr_path=%s theme_path=%s", tr_path, theme_path)
    os.makedirs(theme_path, exist_ok=True)

    with open(tr_path, 'rb') as f:
        data = f.read()

    if len(data) < 4 or data[0:4] != b'\xdd\xdc\xdd\xdc':
        raise ValueError("Invalid .tr file: wrong magic header")

    reader = BinaryReader(data, pos=4)
    theme = _read_tr_config(reader)
    reader.skip(10240)
    _read_tr_images(reader, theme_path)
    write(theme, os.path.join(theme_path, "config1.dc"))


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

    return ThemeConfig(
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


def _read_tr_images(reader: BinaryReader, theme_path: str) -> None:
    """Read embedded images from .tr binary stream."""
    if reader.has_bytes(4):
        mask_size = reader.read_int32()
        if mask_size > 0 and reader.has_bytes(mask_size):
            mask_data = reader.read_bytes(mask_size)
            with open(os.path.join(theme_path, "01.png"), 'wb') as f:
                f.write(mask_data)

    if reader.has_bytes(4):
        marker = reader.read_int32()
        if marker == 0:
            if reader.has_bytes(4):
                bg_size = reader.read_int32()
                if bg_size > 0 and reader.has_bytes(bg_size):
                    bg_data = reader.read_bytes(bg_size)
                    with open(os.path.join(theme_path, "00.png"), 'wb') as f:
                        f.write(bg_data)
        elif marker > 0:
            _read_tr_zt_frames(reader, theme_path, marker)


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


# ── Carousel config (Theme.dc) ──────────────────────────────────────────


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


def read_carousel(filepath: str) -> CarouselConfig | None:
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

    except (OSError, struct.error, ValueError) as e:
        log.debug("dc_writer: load failed: %s", e)
        return None


def _metric_to_hardware_ids(metric: str) -> tuple[int, int]:
    """Map a metric name to its (hardware_kind, hardware_idx) pair."""
    return METRIC_TO_IDS.get(metric, (0, 0))
