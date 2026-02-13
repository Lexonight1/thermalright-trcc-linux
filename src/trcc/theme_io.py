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

from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

from PIL import Image

_HEADER = bytes([0xDD, 0xDC, 0xDD, 0xDC])
_PADDING_SIZE = 10240


class ThemeIO:
    """Export and import .tr theme files (Windows TRCC format)."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def export(
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
        mask_image: Optional[Image.Image],
        background_image: Optional[Image.Image],
        theme_zt_path: Optional[str] = None,
    ) -> bool:
        """Export theme to .tr file.

        Returns True on success.
        """
        with open(output_path, 'wb') as f:
            f.write(_HEADER)
            f.write(struct.pack('?', show_system_info))

            # Overlay elements
            f.write(struct.pack('<i', len(overlay_elements)))
            for elem in overlay_elements:
                ThemeIO._write_element(f, elem)

            # Display state
            f.write(struct.pack('?', show_background))
            f.write(struct.pack('?', show_screenshot))
            f.write(struct.pack('<i', direction))
            f.write(struct.pack('<i', ui_mode))
            f.write(struct.pack('<i', mode))
            f.write(struct.pack('?', hide_screenshot_bg))
            for v in screenshot_rect:
                f.write(struct.pack('<i', v))
            f.write(struct.pack('?', show_mask))
            f.write(struct.pack('<i', mask_center[0]))
            f.write(struct.pack('<i', mask_center[1]))

            # Padding
            f.write(bytes([0xDC] * _PADDING_SIZE))

            # Mask image (01.png)
            ThemeIO._write_image(f, mask_image)

            # Background: either 00.png or Theme.zt
            ThemeIO._write_background(f, background_image, theme_zt_path)

        return True

    @staticmethod
    def import_theme(
        input_path: str,
        output_dir: str,
    ) -> Dict[str, Any]:
        """Import theme from .tr file.

        Extracts 00.png, 01.png, Theme.zt into output_dir.
        Returns dict with theme config.
        """
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)

        result: Dict[str, Any] = {
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
            header = f.read(4)
            if header == _HEADER:
                pass
            elif header[0] == 0xDC and header[1] == 0xDC:
                f.seek(0)
                f.read(2)
                return result
            else:
                raise ValueError(f"Invalid .tr file header: {header.hex()}")

            result['show_system_info'] = struct.unpack('?', f.read(1))[0]

            # Overlay elements
            elem_count = struct.unpack('<i', f.read(4))[0]
            for _ in range(elem_count):
                result['elements'].append(ThemeIO._read_element(f))

            # Display state
            result['show_background'] = struct.unpack('?', f.read(1))[0]
            result['show_screenshot'] = struct.unpack('?', f.read(1))[0]
            result['direction'] = struct.unpack('<i', f.read(4))[0]
            result['ui_mode'] = struct.unpack('<i', f.read(4))[0]
            result['mode'] = struct.unpack('<i', f.read(4))[0]
            result['hide_screenshot_bg'] = struct.unpack('?', f.read(1))[0]
            jx, jy, jw, jh = (struct.unpack('<i', f.read(4))[0] for _ in range(4))
            result['screenshot_rect'] = (jx, jy, jw, jh)
            result['show_mask'] = struct.unpack('?', f.read(1))[0]
            mx = struct.unpack('<i', f.read(4))[0]
            my = struct.unpack('<i', f.read(4))[0]
            result['mask_center'] = (mx, my)

            # Skip padding
            f.read(_PADDING_SIZE)

            # Mask image
            mask_len = struct.unpack('<i', f.read(4))[0]
            if mask_len > 0:
                mask_data = f.read(mask_len)
                Image.open(io.BytesIO(mask_data)).save(output / '01.png')
                result['has_mask'] = True

            # Background or video
            marker = struct.unpack('<i', f.read(4))[0]
            if marker == 0:
                bg_len = struct.unpack('<i', f.read(4))[0]
                if bg_len > 0:
                    bg_data = f.read(bg_len)
                    Image.open(io.BytesIO(bg_data)).save(output / '00.png')
                    result['has_background'] = True
            elif marker > 0:
                ThemeIO._read_video_frames(f, output, marker)
                result['has_video'] = True

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_csharp_string(f: BinaryIO) -> str:
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

    @staticmethod
    def _write_csharp_string(f: BinaryIO, s: str) -> None:
        """Write a C# BinaryWriter string (7-bit encoded length prefix)."""
        data = s.encode('utf-8')
        length = len(data)
        while length >= 0x80:
            f.write(struct.pack('B', (length & 0x7F) | 0x80))
            length >>= 7
        f.write(struct.pack('B', length))
        f.write(data)

    @staticmethod
    def _write_element(f: BinaryIO, elem: Dict[str, Any]) -> None:
        """Write a single overlay element to the .tr file."""
        f.write(struct.pack('<i', elem.get('mode', 0)))
        f.write(struct.pack('<i', elem.get('mode_sub', 0)))
        f.write(struct.pack('<i', elem.get('x', 100)))
        f.write(struct.pack('<i', elem.get('y', 100)))
        f.write(struct.pack('<i', elem.get('main_count', 0)))
        f.write(struct.pack('<i', elem.get('sub_count', 1)))
        ThemeIO._write_csharp_string(f, elem.get('font_name', 'Microsoft YaHei'))
        f.write(struct.pack('<f', elem.get('font_size', 36.0)))
        f.write(struct.pack('B', elem.get('font_style', 0)))
        f.write(struct.pack('B', 3))   # GraphicsUnit.Point
        f.write(struct.pack('B', 134))  # Chinese charset
        # Color ARGB
        color = elem.get('color', '#FFFFFF')
        if isinstance(color, str) and color.startswith('#'):
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
        else:
            r, g, b = 255, 255, 255
        f.write(struct.pack('BBBB', 255, r, g, b))
        ThemeIO._write_csharp_string(f, elem.get('text', ''))

    @staticmethod
    def _read_element(f: BinaryIO) -> Dict[str, Any]:
        """Read a single overlay element from the .tr file."""
        elem: Dict[str, Any] = {
            'mode': struct.unpack('<i', f.read(4))[0],
            'mode_sub': struct.unpack('<i', f.read(4))[0],
            'x': struct.unpack('<i', f.read(4))[0],
            'y': struct.unpack('<i', f.read(4))[0],
            'main_count': struct.unpack('<i', f.read(4))[0],
            'sub_count': struct.unpack('<i', f.read(4))[0],
            'font_name': ThemeIO._read_csharp_string(f),
            'font_size': struct.unpack('<f', f.read(4))[0],
            'font_style': struct.unpack('B', f.read(1))[0],
        }
        f.read(2)  # font_unit, gdi_charset
        _a, r, g, b = struct.unpack('BBBB', f.read(4))
        elem['color'] = f'#{r:02x}{g:02x}{b:02x}'
        elem['text'] = ThemeIO._read_csharp_string(f)
        return elem

    @staticmethod
    def _write_image(f: BinaryIO, image: Optional[Image.Image]) -> None:
        """Write an optional PNG image with length prefix."""
        if image:
            buf = io.BytesIO()
            image.save(buf, format='PNG')
            data = buf.getvalue()
            f.write(struct.pack('<i', len(data)))
            f.write(data)
        else:
            f.write(struct.pack('<i', 0))

    @staticmethod
    def _write_background(
        f: BinaryIO,
        background_image: Optional[Image.Image],
        theme_zt_path: Optional[str],
    ) -> None:
        """Write background (static PNG or Theme.zt video)."""
        if background_image:
            buf = io.BytesIO()
            background_image.save(buf, format='PNG')
            bg_data = buf.getvalue()
            f.write(struct.pack('<i', 0))  # marker: not Theme.zt
            f.write(struct.pack('<i', len(bg_data)))
            f.write(bg_data)
        elif theme_zt_path and Path(theme_zt_path).exists():
            with open(theme_zt_path, 'rb') as zt:
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
            f.write(struct.pack('<i', 0))  # marker: not Theme.zt
            f.write(struct.pack('<i', 0))  # bg_len = 0

    @staticmethod
    def _read_video_frames(f: BinaryIO, output: Path, frame_count: int) -> None:
        """Read Theme.zt video frames and write to output directory."""
        zt_path = output / 'Theme.zt'
        with open(zt_path, 'wb') as zt:
            zt.write(struct.pack('B', 0xDC))
            zt.write(struct.pack('<i', frame_count))
            for _ in range(frame_count):
                ts = struct.unpack('<i', f.read(4))[0]
                zt.write(struct.pack('<i', ts))
            for _ in range(frame_count):
                frame_len = struct.unpack('<i', f.read(4))[0]
                frame_data = f.read(frame_len)
                zt.write(struct.pack('<i', frame_len))
                zt.write(frame_data)


# Backward-compat aliases
export_theme = ThemeIO.export
import_theme = ThemeIO.import_theme
