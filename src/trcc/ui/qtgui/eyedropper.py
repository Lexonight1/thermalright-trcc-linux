"""EyedropperOverlay — pick a single RGB triple off the live desktop.

Shows a frozen full-screen screenshot.  A 12×12 magnifier follows the
cursor with a centred crosshair and a hex/RGB readout, so the user can
land on the pixel they actually want instead of guessing.

Signals:

* ``color_picked(r, g, b)`` — left-click on the chosen pixel.
* ``cancelled()`` — ESC, right-click, or screen-capture failure.

Used by the overlay editor's colour field as a fast alternative to
``QColorDialog`` when the desired colour is "that one, right there on
my taskbar".
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from .screen_overlay import BaseScreenOverlay

log = logging.getLogger(__name__)


class EyedropperOverlay(BaseScreenOverlay):
    """Full-screen overlay for picking one RGB triple from the desktop."""

    color_picked = Signal(int, int, int)
    cancelled = Signal()

    MAGNIFY_SIZE = 12
    MAGNIFY_SCALE = 10
    PREVIEW_OFFSET = 25

    _BORDER_COLOR = QColor(180, 180, 180)
    _CROSSHAIR_COLOR = QColor(255, 255, 255, 200)
    _BG_COLOR = QColor(30, 30, 30, 220)
    _TEXT_COLOR = QColor(255, 255, 255)
    _FONT = QFont("monospace", 10)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_color = QColor(0, 0, 0)
        self._cursor_pos = QPoint()

    def _emit_cancel(self) -> None:
        self.cancelled.emit()

    def paintEvent(self, event) -> None:
        if self._screenshot.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, self._screenshot)
        if not self._cursor_pos.isNull():
            self._draw_magnifier(painter)
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        self._cursor_pos = event.pos()
        self._sample_color_at_cursor()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._accept()
        else:
            self._cancel()

    def _sample_color_at_cursor(self) -> None:
        if self._screenshot.isNull():
            return
        x = max(0, min(self._cursor_pos.x(), self._screenshot.width() - 1))
        y = max(0, min(self._cursor_pos.y(), self._screenshot.height() - 1))
        img = self._screenshot.toImage()
        self._current_color = QColor(img.pixel(x, y))

    def _draw_magnifier(self, painter: QPainter) -> None:
        cx, cy = self._cursor_pos.x(), self._cursor_pos.y()
        half = self.MAGNIFY_SIZE // 2
        mag_w = self.MAGNIFY_SIZE * self.MAGNIFY_SCALE
        mag_h = self.MAGNIFY_SIZE * self.MAGNIFY_SCALE

        # Flip the magnifier away from the edges of the screen so it's
        # always fully visible regardless of cursor position.
        mx = cx + self.PREVIEW_OFFSET
        my = cy + self.PREVIEW_OFFSET
        if mx + mag_w + 60 > self.width():
            mx = cx - self.PREVIEW_OFFSET - mag_w
        if my + mag_h + 40 > self.height():
            my = cy - self.PREVIEW_OFFSET - mag_h - 40

        src_x = max(0, cx - half)
        src_y = max(0, cy - half)
        src_w = min(self.MAGNIFY_SIZE, self._screenshot.width() - src_x)
        src_h = min(self.MAGNIFY_SIZE, self._screenshot.height() - src_y)
        region = self._screenshot.copy(src_x, src_y, src_w, src_h)
        scaled = region.scaled(
            mag_w, mag_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

        total_h = mag_h + 36
        painter.fillRect(mx - 2, my - 2, mag_w + 4, total_h + 4, self._BG_COLOR)
        painter.drawPixmap(mx, my, scaled)

        painter.setPen(QPen(self._BORDER_COLOR, 1))
        painter.drawRect(mx - 1, my - 1, mag_w + 1, mag_h + 1)

        center_x = mx + mag_w // 2
        center_y = my + mag_h // 2
        cell = self.MAGNIFY_SCALE
        painter.setPen(QPen(self._CROSSHAIR_COLOR, 1))
        painter.drawRect(
            center_x - cell // 2, center_y - cell // 2, cell, cell,
        )

        r = self._current_color.red()
        g = self._current_color.green()
        b = self._current_color.blue()
        hex_str = f"#{r:02X}{g:02X}{b:02X}"
        rgb_str = f"RGB({r}, {g}, {b})"

        painter.setFont(self._FONT)
        swatch_y = my + mag_h + 6
        painter.fillRect(mx, swatch_y, 20, 20, QColor(r, g, b))
        painter.setPen(QPen(self._BORDER_COLOR, 1))
        painter.drawRect(mx, swatch_y, 20, 20)
        painter.setPen(self._TEXT_COLOR)
        painter.drawText(mx + 26, swatch_y + 14, f"{hex_str}  {rgb_str}")

    def _accept(self) -> None:
        r = self._current_color.red()
        g = self._current_color.green()
        b = self._current_color.blue()
        log.debug("eyedropper accepted: rgb(%d, %d, %d)", r, g, b)
        self.hide()
        self.color_picked.emit(r, g, b)
        self.deleteLater()
