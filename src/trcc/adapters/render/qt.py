"""QtRenderer — concrete Renderer implementation backed by PySide6.

Offscreen QImage/QPainter — no QApplication needed for rendering.  Used
by services (DisplayService, OverlayService) that accept a Renderer via
DI.  Encapsulates every Qt call; everything else stays framework-blind.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
)

from ...core.errors import TrccError
from ...core.models import RawFrame
from ...core.ports import Renderer

log = logging.getLogger(__name__)


_FONT_CACHE: Dict[Tuple[int, bool, bool, str], QFont] = {}


def _ensure_qt_app() -> None:
    """Make sure a QGuiApplication exists.  Needed for QPainter text.

    Safe to call in CLI / API processes — creates a headless offscreen
    QGuiApplication on first call, reuses it thereafter.  No-op if a
    QApplication is already running (GUI mode).
    """
    if QGuiApplication.instance() is None:
        # Offscreen platform plugin = no window system needed
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QGuiApplication(sys.argv)


def _rgb_tuple_to_qcolor(color: Tuple[int, ...]) -> QColor:
    """(r, g, b) or (r, g, b, a) → QColor."""
    if len(color) == 3:
        return QColor(color[0], color[1], color[2])
    if len(color) == 4:
        return QColor(color[0], color[1], color[2], color[3])
    raise TrccError(f"Invalid color tuple (need 3 or 4 ints): {color}")


class QtRenderer(Renderer):
    """Rendering backend using PySide6 QImage/QPainter.

    All operations are offscreen.  Surfaces are QImage instances; the
    ABC uses `Any` because core must not import PySide6.  The
    constructor ensures a QGuiApplication exists so headless callers
    (CLI, API) can use this renderer without manually bootstrapping Qt.
    """

    def __init__(self) -> None:
        _ensure_qt_app()

    # ── Surfaces ──────────────────────────────────────────────────────

    def create_surface(self, width: int, height: int,
                       color: Optional[Tuple[int, ...]] = None) -> Any:
        img = QImage(width, height, QImage.Format.Format_ARGB32)
        if color is None:
            img.fill(Qt.GlobalColor.transparent)
        else:
            img.fill(_rgb_tuple_to_qcolor(color))
        return img

    def open_image(self, path: Path) -> Any:
        img = QImage(str(path))
        if img.isNull():
            raise TrccError(f"Failed to load image: {path}")
        if img.format() != QImage.Format.Format_ARGB32:
            img = img.convertToFormat(QImage.Format.Format_ARGB32)
        return img

    def surface_size(self, surface: Any) -> Tuple[int, int]:
        return (surface.width(), surface.height())

    # ── Compositing ───────────────────────────────────────────────────

    def composite(self, base: Any, overlay: Any,
                  position: Tuple[int, int],
                  mask: Any | None = None) -> Any:
        result = QImage(base)
        painter = QPainter(result)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if mask is not None:
            masked = QImage(overlay)
            mask_painter = QPainter(masked)
            mask_painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_DestinationIn,
            )
            mask_painter.drawImage(0, 0, mask)
            mask_painter.end()
            painter.drawImage(position[0], position[1], masked)
        else:
            painter.drawImage(position[0], position[1], overlay)
        painter.end()
        return result

    def resize(self, surface: Any, width: int, height: int) -> Any:
        return surface.scaled(
            width, height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def rotate(self, surface: Any, degrees: int) -> Any:
        if degrees % 360 == 0:
            return QImage(surface)
        xform = QTransform().rotate(degrees)
        return surface.transformed(xform, Qt.TransformationMode.SmoothTransformation)

    # ── Adjustments ───────────────────────────────────────────────────

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        """Linear brightness adjust.  100 = unchanged, 0 = black, >100 brighter."""
        if percent == 100:
            return QImage(surface)

        factor = max(0, min(200, percent)) / 100.0
        result = QImage(surface.size(), QImage.Format.Format_ARGB32)
        result.fill(Qt.GlobalColor.transparent)
        for y in range(surface.height()):
            for x in range(surface.width()):
                pixel = QColor(surface.pixel(x, y))
                r = min(255, int(pixel.red() * factor))
                g = min(255, int(pixel.green() * factor))
                b = min(255, int(pixel.blue() * factor))
                result.setPixelColor(x, y, QColor(r, g, b, pixel.alpha()))
        return result

    # ── Text ──────────────────────────────────────────────────────────

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None:
        font = self._get_font(size, bold, italic)
        painter = QPainter(surface)
        painter.setPen(QPen(QColor(color)))
        painter.setFont(font)
        painter.drawText(x, y, text)
        painter.end()

    def _get_font(self, size: int, bold: bool,
                  italic: bool, family: str = "") -> QFont:
        cache_key = (size, bold, italic, family)
        cached = _FONT_CACHE.get(cache_key)
        if cached is not None:
            return cached
        font = QFont(family) if family else QFont()
        font.setPointSize(size)
        font.setBold(bold)
        font.setItalic(italic)
        _FONT_CACHE[cache_key] = font
        return font

    # ── Encoding ──────────────────────────────────────────────────────

    def encode_rgb565(self, surface: Any) -> bytes:
        """Encode QImage → RGB565 big-endian bytes (2 bytes per pixel)."""
        img = surface.convertToFormat(QImage.Format.Format_RGB16)
        w, h = img.width(), img.height()
        result = bytearray(w * h * 2)
        for y in range(h):
            for x in range(w):
                pixel = img.pixel(x, y)
                r = (pixel >> 16) & 0xFF
                g = (pixel >> 8) & 0xFF
                b = pixel & 0xFF
                r5 = (r >> 3) & 0x1F
                g6 = (g >> 2) & 0x3F
                b5 = (b >> 3) & 0x1F
                rgb565 = (r5 << 11) | (g6 << 5) | b5
                # big-endian
                offset = (y * w + x) * 2
                result[offset] = (rgb565 >> 8) & 0xFF
                result[offset + 1] = rgb565 & 0xFF
        return bytes(result)

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        """Encode QImage → JPEG bytes.  Optionally retry lower quality until ≤ max_size."""
        def _save(q: int) -> bytes:
            from PySide6.QtCore import QBuffer, QIODevice
            qbuf = QBuffer()
            qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
            surface.save(qbuf, "JPEG", q)
            qbuf.close()
            return bytes(qbuf.data().data())

        data = _save(quality)
        if not max_size or len(data) <= max_size:
            return data
        # Shrink-quality loop
        for q in (85, 75, 60, 45, 30):
            data = _save(q)
            if len(data) <= max_size:
                return data
        return data  # last attempt, may still exceed

    # ── Legacy boundary (raw RGB24 video frame → QImage) ──────────────

    def from_raw_rgb24(self, frame: "RawFrame") -> Any:
        qimg = QImage(
            frame.data, frame.width, frame.height,
            frame.width * 3,
            QImage.Format.Format_RGB888,
        ).copy()  # .copy() detaches from input buffer
        return qimg.convertToFormat(QImage.Format.Format_ARGB32)

    # ── Convenience: QPixmap export for GUI preview ───────────────────

    @staticmethod
    def to_pixmap(surface: Any) -> QPixmap:
        """Convert a QImage surface to a QPixmap (for GUI display)."""
        return QPixmap.fromImage(surface)
