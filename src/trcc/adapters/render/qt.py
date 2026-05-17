"""Qt rendering backend — QImage/QPainter.

PySide6 is a required dependency, so this renderer is always available.
QImage is CPU-based software rendering — works headless (CLI) and with
a display (GUI). QPainter on QImage is C++ native.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QImage,
    QPainter,
    QTransform,
)

from ...core.models import JPEG_MAX_BYTES
from ...core.ports import Renderer
from ..infra.data_repository import FONT_SEARCH_DIRS

log = logging.getLogger(__name__)


class QtRenderer(Renderer):
    """Full rendering backend using PySide6 QImage/QPainter.

    Single renderer for GUI, CLI, and API — replaces PilRenderer.
    All image operations use Qt's native C++ implementation.
    """

    def __init__(self) -> None:
        self._font_cache: dict[tuple, QFont] = {}
        self._font_path_cache: dict[tuple[str, bool], str | None] = {}
        self._jpeg_quality_hint: int = 95

    # ── Surface lifecycle ─────────────────────────────────────────

    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        if color is not None and len(color) > 3:
            # RGBA color → premultiplied alpha surface
            img = QImage(width, height,
                         QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(QColor(*color))
        elif color is not None:
            img = QImage(width, height, QImage.Format.Format_RGB32)
            img.fill(QColor(*color))
        else:
            img = QImage(width, height,
                         QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
        return img

    def copy_surface(self, surface: Any) -> Any:
        return surface.copy()

    def convert_to_rgba(self, surface: Any) -> Any:
        if surface.format() != QImage.Format.Format_ARGB32_Premultiplied:
            return surface.convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied)
        return surface

    def convert_to_rgb(self, surface: Any) -> Any:
        if surface.format() != QImage.Format.Format_RGB32:
            return surface.convertToFormat(QImage.Format.Format_RGB32)
        return surface

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.width(), surface.height())

    # ── Compositing ───────────────────────────────────────────────

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        painter = QPainter(base)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawImage(position[0], position[1], overlay)
        painter.end()
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        return surface.scaled(
            width, height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)

    # ── Text ──────────────────────────────────────────────────────

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, font: Any, anchor: str = 'mm') -> None:
        painter = QPainter(surface)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(QColor(color))

        fm = QFontMetrics(font)
        br = fm.boundingRect(text)

        # Anchor: 'mm' = middle-middle (center on x,y)
        if anchor == 'mm':
            dx = x - br.width() // 2
            dy = y + fm.ascent() - fm.height() // 2
        elif anchor == 'lt':
            dx = x
            dy = y + fm.ascent()
        else:
            dx = x - br.width() // 2
            dy = y + fm.ascent() - fm.height() // 2

        painter.drawText(dx, dy, text)
        painter.end()

    def get_font(self, size: int, bold: bool = False,
                 italic: bool = False,
                 font_name: str | None = None) -> Any:
        key = (size, bold, italic, font_name)
        if key in self._font_cache:
            return self._font_cache[key]

        font = self._resolve_font(size, bold, italic, font_name)
        self._font_cache[key] = font
        return font

    def _resolve_font(self, size: int, bold: bool,
                      italic: bool,
                      font_name: str | None) -> QFont:
        """Resolve font name → QFont with the same fallback chain as FontResolver."""
        def _apply(f: QFont) -> QFont:
            f.setPixelSize(size)
            f.setBold(bold)
            f.setItalic(italic)
            return f

        # User-specified font name
        if font_name and font_name != 'Microsoft YaHei':
            path = self._resolve_font_path(font_name, bold)
            if path:
                font_id = QFontDatabase.addApplicationFont(path)
                if font_id >= 0:
                    if (families := QFontDatabase.applicationFontFamilies(font_id)):
                        return _apply(QFont(families[0]))

        # Search bundled + system fonts
        bold_suffix = '-Bold' if bold else ''
        bold_style = 'Bold' if bold else 'Regular'
        msyh_name = 'MSYHBD.TTC' if bold else 'MSYH.TTC'

        font_filenames = [
            msyh_name, msyh_name.lower(),
            'NotoSansCJK-VF.ttc', 'NotoSansCJK-Regular.ttc',
            'NotoSans[wght].ttf', f'NotoSans-{bold_style}.ttf',
            f'DejaVuSans{bold_suffix}.ttf',
        ]

        for font_dir in FONT_SEARCH_DIRS:
            for fname in font_filenames:
                path = os.path.join(font_dir, fname)
                if os.path.exists(path):
                    font_id = QFontDatabase.addApplicationFont(path)
                    if font_id >= 0:
                        if (families := QFontDatabase.applicationFontFamilies(font_id)):
                            return _apply(QFont(families[0]))

        # Fallback: Qt default sans-serif
        return _apply(QFont('Sans'))

    def _resolve_font_path(self, font_name: str,
                           bold: bool) -> str | None:
        """Resolve font family name → file path (fc-match + manual scan)."""
        key = (font_name, bold)
        if key in self._font_path_cache:
            return self._font_path_cache[key]

        path = self._fc_match(font_name, bold)
        if not path:
            path = self._manual_scan(font_name)
        self._font_path_cache[key] = path
        return path

    @staticmethod
    def _fc_match(font_name: str, bold: bool) -> str | None:
        try:
            style = 'Bold' if bold else 'Regular'
            result = subprocess.run(
                ['fc-match', f'{font_name}:style={style}',
                 '--format=%{file}'],
                capture_output=True, text=True, timeout=2)
            if (result.returncode == 0 and result.stdout
                    and os.path.exists(result.stdout)):
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    @staticmethod
    def _manual_scan(font_name: str) -> str | None:
        name_lower = font_name.lower().replace(' ', '')
        for font_dir in FONT_SEARCH_DIRS:
            if not os.path.isdir(font_dir):
                continue
            for fname in os.listdir(font_dir):
                if name_lower in fname.lower().replace(' ', ''):
                    return os.path.join(font_dir, fname)
        return None

    def clear_font_cache(self) -> None:
        self._font_cache.clear()

    # ── Image adjustments ─────────────────────────────────────────

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        if percent >= 100:
            return surface
        result = surface.copy()
        painter = QPainter(result)
        alpha = int(255 * (1.0 - percent / 100.0))
        painter.fillRect(QRect(0, 0, result.width(), result.height()),
                         QColor(0, 0, 0, alpha))
        painter.end()
        return result

    def apply_rotation(self, surface: Any, degrees: int) -> Any:
        if degrees == 0:
            return surface
        transform = QTransform()
        transform.rotate(degrees)
        return surface.transformed(transform, Qt.TransformationMode.SmoothTransformation)

    def flip_horizontal(self, surface: Any) -> Any:
        return surface.mirrored(horizontal=True, vertical=False)

    # ── Device encoding ───────────────────────────────────────────

    def encode_rgb565(self, surface: Any, byte_order: str = '>') -> bytes:
        # Ensure opaque RGB32 first — premultiplied alpha surfaces produce
        # darkened RGB values if converted directly to RGB16.
        if surface.format() != QImage.Format.Format_RGB32:
            surface = surface.convertToFormat(QImage.Format.Format_RGB32)
        rgb16 = surface.convertToFormat(QImage.Format.Format_RGB16)
        w, h = rgb16.width(), rgb16.height()
        bpl = rgb16.bytesPerLine()
        raw = bytes(rgb16.constBits())

        # Strip row padding if bytesPerLine > w*2
        if bpl == w * 2:
            data = raw
        else:
            data = b''.join(raw[y * bpl:y * bpl + w * 2] for y in range(h))

        # Format_RGB16 is native-endian (little on x86).
        # Swap bytes if device needs big-endian.
        if byte_order == '>' and sys.byteorder == 'little':
            arr = bytearray(data)
            # Swap adjacent bytes: [lo, hi] → [hi, lo]
            arr[0::2], arr[1::2] = arr[1::2], arr[0::2]
            return bytes(arr)
        if byte_order == '<' and sys.byteorder == 'big':
            arr = bytearray(data)
            arr[0::2], arr[1::2] = arr[1::2], arr[0::2]
            return bytes(arr)
        return data

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = JPEG_MAX_BYTES) -> bytes:
        # JPEG encoder needs RGB888 (not RGB32)
        rgb = surface.convertToFormat(QImage.Format.Format_RGB888)
        hint = self._jpeg_quality_hint

        # Fast path: try cached quality first
        if hint < quality:
            data = self._jpeg_encode(rgb, hint)
            if len(data) < max_size:
                for q in range(min(quality, hint + 10), hint, -5):
                    d2 = self._jpeg_encode(rgb, q)
                    if len(d2) < max_size:
                        self._jpeg_quality_hint = q
                        return d2
                return data

        # Normal path: scan from top quality down
        for q in range(quality, 4, -5):
            data = self._jpeg_encode(rgb, q)
            if len(data) < max_size:
                self._jpeg_quality_hint = q
                return data

        # Fallback: minimum quality
        self._jpeg_quality_hint = 5
        return self._jpeg_encode(rgb, 5)

    @staticmethod
    def _jpeg_encode(surface: QImage, quality: int) -> bytes:
        buf = QByteArray()
        qbuf = QBuffer(buf)
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        surface.save(qbuf, 'jpeg', quality)  # type: ignore[call-overload]  # PySide6 stubs say bytes, runtime needs str
        qbuf.close()
        return bytes(buf.data())

    # ── File I/O ──────────────────────────────────────────────────

    def open_image(self, path: Any) -> Any:
        img = QImage(str(path))
        if img.isNull():
            log.warning("Failed to load image: %s", path)
            return QImage(1, 1, QImage.Format.Format_RGB32)
        if img.hasAlphaChannel():
            return img.convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied)
        return img.convertToFormat(QImage.Format.Format_RGB32)

    def from_raw_rgb24(self, frame: Any) -> Any:
        """RawFrame (RGB24 bytes) → QImage (RGB32)."""
        qimg = QImage(frame.data, frame.width, frame.height,
                      frame.width * 3, QImage.Format.Format_RGB888)
        return qimg.convertToFormat(QImage.Format.Format_RGB32)

    # ── Wire serialization ────────────────────────────────────────

    def encode_for_wire(self, surface: Any) -> bytes:
        """QImage → PNG bytes for IPC transport.

        Lossless so the GUI preview matches the device output exactly.
        """
        buf = QByteArray()
        qbuf = QBuffer(buf)
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        # type: ignore on the format str — PySide6 stubs say bytes, runtime needs str.
        surface.save(qbuf, 'png')  # type: ignore[call-overload]
        qbuf.close()
        return bytes(buf.data())

    def decode_from_wire(self, data: bytes) -> Any:
        """PNG bytes → QImage (Format_RGB32 / Format_ARGB32 by content)."""
        img = QImage()
        # Same Qt stub vs. runtime mismatch as encode_jpeg — format str
        # is what loadFromData actually accepts at runtime.
        img.loadFromData(data, 'png')  # type: ignore[arg-type]
        if img.isNull():
            log.warning("decode_from_wire: empty/invalid PNG payload (%d bytes)",
                        len(data))
            return QImage(1, 1, QImage.Format.Format_RGB32)
        if img.hasAlphaChannel():
            return img.convertToFormat(
                QImage.Format.Format_ARGB32_Premultiplied)
        return img.convertToFormat(QImage.Format.Format_RGB32)

    # ── Drawing primitives ────────────────────────────────────────

    def fill_rect(self, surface: Any, x: int, y: int,
                  w: int, h: int, color: tuple[int, ...]) -> None:
        painter = QPainter(surface)
        painter.fillRect(x, y, w, h, QColor(*color))
        painter.end()

    def draw_rect_outline(self, surface: Any, x: int, y: int,
                          w: int, h: int, color: tuple[int, ...]) -> None:
        painter = QPainter(surface)
        painter.setPen(QColor(*color))
        painter.drawRect(x, y, w - 1, h - 1)
        painter.end()

    def get_pixels_rgb(self, surface: Any, cols: int,
                       rows: int) -> list[list[tuple[int, int, int]]]:
        thumb = surface.scaled(
            cols, rows,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ).convertToFormat(QImage.Format.Format_RGB888)
        return [
            [
                (
                    thumb.pixelColor(x, y).red(),
                    thumb.pixelColor(x, y).green(),
                    thumb.pixelColor(x, y).blue(),
                )
                for x in range(cols)
            ]
            for y in range(rows)
        ]

