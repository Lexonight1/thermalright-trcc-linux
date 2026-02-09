#!/usr/bin/env python3
"""
Interactive HSV color wheel widget for LED control panels.

Renders a conical-gradient rainbow ring using QPainter. Click/drag on
the ring selects a hue (0-360) and emits ``hue_changed``.  A white
circle indicator tracks the current position on the ring.

Original implementation by Lcstyle (GitHub PR #9).
"""

import math

try:
    from PyQt6.QtCore import QPointF, Qt, pyqtSignal
    from PyQt6.QtGui import (
        QBrush,
        QColor,
        QConicalGradient,
        QPainter,
        QPainterPath,
        QPen,
    )
    from PyQt6.QtWidgets import QWidget
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False


if PYQT6_AVAILABLE:

    class UCColorWheel(QWidget):
        """Circular hue ring with click/drag selection.

        Attributes:
            hue_changed: Emitted when the user selects a hue (0-360).
        """

        hue_changed = pyqtSignal(int)

        # Ring geometry (relative to widget center)
        OUTER_RADIUS = 97
        INNER_RADIUS = 72
        SELECTOR_RADIUS = 8

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self._hue = 0
            self._dragging = False

        # ----------------------------------------------------------------
        # Public API
        # ----------------------------------------------------------------

        def set_hue(self, hue: int) -> None:
            """Set the current hue without emitting a signal."""
            self._hue = hue % 360
            self.update()

        # ----------------------------------------------------------------
        # Painting
        # ----------------------------------------------------------------

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            cx = self.width() / 2.0
            cy = self.height() / 2.0
            outer = self.OUTER_RADIUS
            inner = self.INNER_RADIUS

            # --- Conical gradient (12 stops at 30° intervals) ---
            gradient = QConicalGradient(cx, cy, 0)
            for i in range(13):
                stop = i / 12.0
                gradient.setColorAt(stop, QColor.fromHsv(int(stop * 360) % 360, 255, 255))

            # --- Ring via path subtraction ---
            ring = QPainterPath()
            ring.addEllipse(QPointF(cx, cy), outer, outer)
            hole = QPainterPath()
            hole.addEllipse(QPointF(cx, cy), inner, inner)
            ring = ring.subtracted(hole)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawPath(ring)

            # --- Selector indicator ---
            angle_rad = math.radians(self._hue)
            mid_r = (outer + inner) / 2.0
            sx = cx + mid_r * math.cos(angle_rad)
            sy = cy - mid_r * math.sin(angle_rad)

            # White outline circle
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.setBrush(QBrush(QColor.fromHsv(self._hue, 255, 255)))
            painter.drawEllipse(QPointF(sx, sy), self.SELECTOR_RADIUS, self.SELECTOR_RADIUS)

            painter.end()

        # ----------------------------------------------------------------
        # Mouse interaction
        # ----------------------------------------------------------------

        def mousePressEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._update_hue_from_pos(event.position())

        def mouseMoveEvent(self, event):
            if self._dragging:
                self._update_hue_from_pos(event.position())

        def mouseReleaseEvent(self, event):
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = False

        def _update_hue_from_pos(self, pos):
            cx = self.width() / 2.0
            cy = self.height() / 2.0
            dx = pos.x() - cx
            dy = -(pos.y() - cy)  # invert Y for math coords
            angle = math.degrees(math.atan2(dy, dx))
            if angle < 0:
                angle += 360
            hue = int(angle) % 360
            if hue != self._hue:
                self._hue = hue
                self.update()
                self.hue_changed.emit(hue)
