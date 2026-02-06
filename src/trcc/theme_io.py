"""
Theme export/import (.tr file format).

Matches Windows TRCC buttonDaoChu_Click (export) and buttonDaoRu_Click (import).

.tr file format (binary):
    Header: 0xDD 0xDC 0xDD 0xDC (4 bytes)
    bool: show_system_info
    int: overlay_element_count
    per element:
        int: mode (0-4)
        int: mode_sub
        int: x, y
        int: main_count, sub_count
        string: font_name
        float: font_size
        byte: font_style, font_unit, gdi_charset
        byte: color_a, r, g, b
        string: text
    bool: show_background
    bool: show_screenshot
    int: direction (0/90/180/270)
    int: ui_mode, mode
    bool: hide_screenshot_bg
    int: screenshot_x, y, w, h
    bool: show_mask
    int: mask_center_x, y
    padding: 10240 bytes of 0xDC
    int: mask_image_length (0 if no mask)
    bytes: mask_image_data (PNG)
    int: background_type (0 = has Theme.zt, non-zero = has 00.png)
    if background_type != 0:
        int: background_image_length
        bytes: background_image_data (PNG)
    else:
        Theme.zt data (frame_count, timestamps, frames)
"""

import struct
import io
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def _read_csharp_string(f) -> str:
    """Read a C# BinaryWriter string (7-bit encoded length prefix)."""
    length = 0
    shift = 0
    while True:
        b = struct.unpack('B', f.read(1))[0]
        length |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    if length == 0:
        return ''
    return f.read(length).decode('utf-8', errors='replace')


def _write_csharp_string(f, s: str):
    """Write a C# BinaryWriter string (7-bit encoded length prefix)."""
    data = s.encode('utf-8')
    length = len(data)
    # Write 7-bit encoded length
    while length >= 0x80:
        f.write(struct.pack('B', (length & 0x7F) | 0x80))
        length >>= 7
    f.write(struct.pack('B', length))
    f.write(data)


def export_theme(
    output_path: str,
    overlay_elements: List[Dict[str, Any]],
    show_system_info: bool,
    show_background: bool,
    show_screenshot: bool,
    direction: int,
    ui_mode: int,
    mode: int,
    hide_screenshot_bg: bool,
    screenshot_rect: Tuple[int, int, int, int],
    show_mask: bool,
    mask_center: Tuple[int, int],
    mask_image: Optional['Image.Image'],
    background_image: Optional['Image.Image'],
    theme_zt_path: Optional[str] = None,
) -> bool:
    """Export theme to .tr file.

    Args:
        output_path: Path to save .tr file
        overlay_elements: List of element config dicts
        show_system_info: Whether overlay is enabled
        show_background: Background display toggle
        show_screenshot: Screenshot display toggle
        direction: Rotation degrees (0/90/180/270)
        ui_mode: Display mode (1=bg, 2=screen, 4=video)
        mode: Sub-mode
        hide_screenshot_bg: Hide screenshot background
        screenshot_rect: (x, y, w, h) for screenshot region
        show_mask: Mask visibility
        mask_center: (x, y) center coordinates for mask
        mask_image: PIL Image for mask (01.png) or None
        background_image: PIL Image for background (00.png) or None
        theme_zt_path: Path to Theme.zt if using video instead of static bg

    Returns:
        True on success
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL required for theme export")

    with open(output_path, 'wb') as f:
        # Header
        f.write(bytes([0xDD, 0xDC, 0xDD, 0xDC]))

        # Show system info
        f.write(struct.pack('?', show_system_info))

        # Overlay elements
        f.write(struct.pack('<i', len(overlay_elements)))
        for elem in overlay_elements:
            f.write(struct.pack('<i', elem.get('mode', 0)))
            f.write(struct.pack('<i', elem.get('mode_sub', 0)))
            f.write(struct.pack('<i', elem.get('x', 100)))
            f.write(struct.pack('<i', elem.get('y', 100)))
            f.write(struct.pack('<i', elem.get('main_count', 0)))
            f.write(struct.pack('<i', elem.get('sub_count', 1)))
            _write_csharp_string(f, elem.get('font_name', 'Microsoft YaHei'))
            f.write(struct.pack('<f', elem.get('font_size', 36.0)))
            f.write(struct.pack('B', elem.get('font_style', 0)))
            f.write(struct.pack('B', 3))  # GraphicsUnit.Point
            f.write(struct.pack('B', 134))  # Chinese charset
            # Color ARGB
            color = elem.get('color', '#FFFFFF')
            if isinstance(color, str) and color.startswith('#'):
                r = int(color[1:3], 16)
                g = int(color[3:5], 16)
                b = int(color[5:7], 16)
            else:
                r, g, b = 255, 255, 255
            f.write(struct.pack('BBBB', 255, r, g, b))  # A, R, G, B
            _write_csharp_string(f, elem.get('text', ''))

        # Display state
        f.write(struct.pack('?', show_background))
        f.write(struct.pack('?', show_screenshot))
        f.write(struct.pack('<i', direction))
        f.write(struct.pack('<i', ui_mode))
        f.write(struct.pack('<i', mode))
        f.write(struct.pack('?', hide_screenshot_bg))
        f.write(struct.pack('<i', screenshot_rect[0]))
        f.write(struct.pack('<i', screenshot_rect[1]))
        f.write(struct.pack('<i', screenshot_rect[2]))
        f.write(struct.pack('<i', screenshot_rect[3]))
        f.write(struct.pack('?', show_mask))
        f.write(struct.pack('<i', mask_center[0]))
        f.write(struct.pack('<i', mask_center[1]))

        # Padding (10240 bytes of 0xDC)
        f.write(bytes([0xDC] * 10240))

        # Mask image (01.png)
        if mask_image:
            buf = io.BytesIO()
            mask_image.save(buf, format='PNG')
            mask_data = buf.getvalue()
            f.write(struct.pack('<i', len(mask_data)))
            f.write(mask_data)
        else:
            f.write(struct.pack('<i', 0))

        # Background: either 00.png or Theme.zt
        if background_image:
            buf = io.BytesIO()
            background_image.save(buf, format='PNG')
            bg_data = buf.getvalue()
            f.write(struct.pack('<i', 0))  # marker: not Theme.zt
            f.write(struct.pack('<i', len(bg_data)))
            f.write(bg_data)
        elif theme_zt_path and Path(theme_zt_path).exists():
            # Embed Theme.zt data
            with open(theme_zt_path, 'rb') as zt:
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
            f.write(struct.pack('<i', 0))  # No background

    return True


def import_theme(
    input_path: str,
    output_dir: str,
) -> Dict[str, Any]:
    """Import theme from .tr file.

    Args:
        input_path: Path to .tr file
        output_dir: Directory to extract files (00.png, 01.png, Theme.zt)

    Returns:
        Dict with theme config (elements, display state, etc.)
    """
    if not PIL_AVAILABLE:
        raise RuntimeError("PIL required for theme import")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    result = {
        'elements': [],
        'show_system_info': True,
        'show_background': True,
        'show_screenshot': False,
        'direction': 0,
        'ui_mode': 1,
        'mode': 0,
        'hide_screenshot_bg': True,
        'screenshot_rect': (0, 0, 320, 320),
        'show_mask': False,
        'mask_center': (160, 160),
        'has_background': False,
        'has_mask': False,
        'has_video': False,
    }

    with open(input_path, 'rb') as f:
        # Check header
        header = f.read(4)
        if header == bytes([0xDD, 0xDC, 0xDD, 0xDC]):
            # Standard .tr format
            pass
        elif header[0] == 0xDC and header[1] == 0xDC:
            # Alternative format (0xDC 0xDC prefix)
            f.seek(0)
            f.read(2)  # Skip 0xDC 0xDC
            # Handle differently...
            return result
        else:
            raise ValueError(f"Invalid .tr file header: {header.hex()}")

        # Show system info
        result['show_system_info'] = struct.unpack('?', f.read(1))[0]

        # Overlay elements
        elem_count = struct.unpack('<i', f.read(4))[0]
        for _ in range(elem_count):
            elem = {
                'mode': struct.unpack('<i', f.read(4))[0],
                'mode_sub': struct.unpack('<i', f.read(4))[0],
                'x': struct.unpack('<i', f.read(4))[0],
                'y': struct.unpack('<i', f.read(4))[0],
                'main_count': struct.unpack('<i', f.read(4))[0],
                'sub_count': struct.unpack('<i', f.read(4))[0],
                'font_name': _read_csharp_string(f),
                'font_size': struct.unpack('<f', f.read(4))[0],
                'font_style': struct.unpack('B', f.read(1))[0],
            }
            f.read(2)  # font_unit, gdi_charset
            a, r, g, b = struct.unpack('BBBB', f.read(4))
            elem['color'] = f'#{r:02x}{g:02x}{b:02x}'
            elem['text'] = _read_csharp_string(f)
            result['elements'].append(elem)

        # Display state
        result['show_background'] = struct.unpack('?', f.read(1))[0]
        result['show_screenshot'] = struct.unpack('?', f.read(1))[0]
        result['direction'] = struct.unpack('<i', f.read(4))[0]
        result['ui_mode'] = struct.unpack('<i', f.read(4))[0]
        result['mode'] = struct.unpack('<i', f.read(4))[0]
        result['hide_screenshot_bg'] = struct.unpack('?', f.read(1))[0]
        jx = struct.unpack('<i', f.read(4))[0]
        jy = struct.unpack('<i', f.read(4))[0]
        jw = struct.unpack('<i', f.read(4))[0]
        jh = struct.unpack('<i', f.read(4))[0]
        result['screenshot_rect'] = (jx, jy, jw, jh)
        result['show_mask'] = struct.unpack('?', f.read(1))[0]
        mx = struct.unpack('<i', f.read(4))[0]
        my = struct.unpack('<i', f.read(4))[0]
        result['mask_center'] = (mx, my)

        # Skip padding (10240 bytes)
        f.read(10240)

        # Mask image
        mask_len = struct.unpack('<i', f.read(4))[0]
        if mask_len > 0:
            mask_data = f.read(mask_len)
            mask_img = Image.open(io.BytesIO(mask_data))
            mask_img.save(output_path / '01.png')
            result['has_mask'] = True

        # Background or video
        marker = struct.unpack('<i', f.read(4))[0]
        if marker == 0:
            # Static background (00.png)
            bg_len = struct.unpack('<i', f.read(4))[0]
            if bg_len > 0:
                bg_data = f.read(bg_len)
                bg_img = Image.open(io.BytesIO(bg_data))
                bg_img.save(output_path / '00.png')
                result['has_background'] = True
        elif marker > 0:
            # Theme.zt (video frames)
            frame_count = marker
            zt_path = output_path / 'Theme.zt'
            with open(zt_path, 'wb') as zt:
                zt.write(struct.pack('B', 0xDC))
                zt.write(struct.pack('<i', frame_count))
                # Timestamps
                for _ in range(frame_count):
                    ts = struct.unpack('<i', f.read(4))[0]
                    zt.write(struct.pack('<i', ts))
                # Frame data
                for _ in range(frame_count):
                    frame_len = struct.unpack('<i', f.read(4))[0]
                    frame_data = f.read(frame_len)
                    zt.write(struct.pack('<i', frame_len))
                    zt.write(frame_data)
            result['has_video'] = True

    return result
