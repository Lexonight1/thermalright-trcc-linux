"""QtRenderer — concrete Renderer implementation backed by PySide6.

Offscreen QImage/QPainter — no QApplication needed for rendering.  Used
by services (DisplayService, OverlayService) that accept a Renderer via
DI.  Encapsulates every Qt call; everything else stays framework-blind.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, Qt
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


_FONT_CACHE: dict[tuple[int, bool, bool, str], QFont] = {}


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
        log.info("QtRenderer: bootstrapping offscreen QGuiApplication (headless mode)")
        QGuiApplication(sys.argv)
    else:
        log.debug("QtRenderer: reusing existing QGuiApplication")


def _rgb_tuple_to_qcolor(color: tuple[int, ...]) -> QColor:
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
                       color: tuple[int, ...] | None = None) -> Any:
        img = QImage(width, height, QImage.Format.Format_ARGB32)
        if color is None:
            img.fill(Qt.GlobalColor.transparent)
        else:
            img.fill(_rgb_tuple_to_qcolor(color))
        return img

    def open_image(self, path: Path) -> Any:
        log.debug("QtRenderer.open_image: %s", path)
        img = QImage(str(path))
        if img.isNull():
            log.error("QtRenderer.open_image: QImage.isNull for %s", path)
            raise TrccError(f"Failed to load image: {path}")
        if img.format() != QImage.Format.Format_ARGB32:
            img = img.convertToFormat(QImage.Format.Format_ARGB32)
        return img

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.width(), surface.height())

    # ── Compositing ───────────────────────────────────────────────────

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
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

    def flip_horizontal(self, surface: Any) -> Any:
        """Mirror surface across the vertical axis (X → -X)."""
        return surface.mirrored(horizontal=True, vertical=False)

    # ── Adjustments ───────────────────────────────────────────────────

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        """Dim a surface by overlaying semi-transparent black.

        FIXME (post-Phase E cleanup): this matches legacy's QPainter-
        overlay implementation byte-for-byte, but the cleaner math is
        ``pixel * (percent / 100)`` — a literal multiply per channel.
        Both produce visually identical output; the QPainter approach
        is GPU-accelerated and matches what legacy already ships to
        users, so we adopt it for the Phase C parity gate.

        After Phase E (legacy deletion) we can revisit and switch to
        the pure-math implementation if there's a reason; the diff is
        sub-perceptible so there's no rush.

        Math: ``alpha = int(255 * (1 - percent/100))`` then source-over
        composite black at that alpha.  ``percent >= 100`` is a no-op
        (legacy doesn't implement brightness boost above 100% either).
        """
        if percent >= 100:
            return surface
        result = surface.copy()
        alpha = int(255 * (1.0 - percent / 100.0))
        painter = QPainter(result)
        painter.fillRect(
            QRect(0, 0, result.width(), result.height()),
            QColor(0, 0, 0, alpha),
        )
        painter.end()
        return result

    # ── Text ──────────────────────────────────────────────────────────

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None:
        """Draw *text* centered on ``(x, y)`` — matches C# DrawString
        with ``RectangleF(myX - w/2, myY - h/2, w, h)``.
        """
        font = self._get_font(size, bold, italic)
        painter = QPainter(surface)
        painter.setPen(QPen(QColor(color)))
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(text)
        text_h = metrics.height()
        rect = QRect(x - text_w // 2, y - text_h // 2, text_w, text_h)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

    def _get_font(self, size: int, bold: bool,
                  italic: bool, family: str = "") -> QFont:
        cache_key = (size, bold, italic, family)
        cached = _FONT_CACHE.get(cache_key)
        if cached is not None:
            return cached
        log.debug("QtRenderer: caching font (size=%d bold=%s italic=%s family=%r)",
                  size, bold, italic, family)
        font = QFont(family) if family else QFont()
        font.setPointSize(size)
        font.setBold(bold)
        font.setItalic(italic)
        _FONT_CACHE[cache_key] = font
        return font

    # ── Encoding ──────────────────────────────────────────────────────

    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes:
        """Encode QImage → RGB565 bytes (2 bytes per pixel).

        Ports the legacy implementation verbatim — three steps that the
        prior cutover encoder skipped, each of which caused visibly
        wrong colors on the LCD:

        1. Convert to ``Format_RGB32`` FIRST.  Qt's default painting
           format is ``Format_ARGB32_Premultiplied`` (channels multiplied
           by alpha for fast compositing).  Going straight to RGB16
           preserves the premultiplied values — anything that was painted
           with transparency comes out darkened/desaturated.
        2. Use Qt's native ``Format_RGB16`` conversion + grab the raw
           bytes — matches the device's expected RGB565 packing exactly
           (Qt rounds the same way the device's panel expects).
        3. Honour ``byte_order`` per the device's ``DeviceProfile.big_endian``
           flag.  ``Format_RGB16`` writes native-endian bytes; swap if
           the device wants the other order.
        """
        # Strip premultiplied alpha before quantizing.
        if surface.format() != QImage.Format.Format_RGB32:
            surface = surface.convertToFormat(QImage.Format.Format_RGB32)
        rgb16 = surface.convertToFormat(QImage.Format.Format_RGB16)
        w, h = rgb16.width(), rgb16.height()
        bpl = rgb16.bytesPerLine()
        raw = bytes(rgb16.constBits())

        # Strip row padding if bytesPerLine > w*2.
        if bpl == w * 2:
            data = raw
        else:
            data = b"".join(raw[y * bpl:y * bpl + w * 2] for y in range(h))

        # Format_RGB16 is native-endian; swap if device expects the
        # other byte order.
        if byte_order == ">" and sys.byteorder == "little":
            arr = bytearray(data)
            arr[0::2], arr[1::2] = arr[1::2], arr[0::2]
            return bytes(arr)
        if byte_order == "<" and sys.byteorder == "big":
            arr = bytearray(data)
            arr[0::2], arr[1::2] = arr[1::2], arr[0::2]
            return bytes(arr)
        return data

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
            log.debug("QtRenderer.encode_jpeg: q=%d size=%d (target ≤%d)",
                      q, len(data), max_size)
            if len(data) <= max_size:
                return data
        log.warning("QtRenderer.encode_jpeg: %d bytes exceeds target %d "
                    "even at q=30", len(data), max_size)
        return data  # last attempt, may still exceed

    def encode_png(self, surface: Any) -> bytes:
        """Encode QImage → PNG bytes (lossless).

        Used by ``GET /devices/{key}/display/preview`` for dashboard
        snapshots — JPEG would chew up overlay text + small CJK glyphs.
        """
        from PySide6.QtCore import QBuffer, QIODevice
        qbuf = QBuffer()
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        surface.save(qbuf, "PNG")
        qbuf.close()
        return bytes(qbuf.data().data())

    # ── Legacy boundary (raw RGB24 video frame → QImage) ──────────────

    def from_raw_rgb24(self, frame: RawFrame) -> Any:
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
