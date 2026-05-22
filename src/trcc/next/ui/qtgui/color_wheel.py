"""ColorWheel — circular HSV hue picker.

A pure widget that paints a conical gradient ring and lets the user
click or drag to pick a hue.  Emits ``hue_changed(int)`` with the
current hue 0-359.  Saturation and value are fixed at 255 — callers
that want richer pickers should combine this with QColorDialog.

Why not just QColorDialog?  Two reasons:

* The LED panel needs a tactile, always-visible hue control (legacy's
  UCColorA spent its whole real estate on this) — opening a modal
  every click is friction.
* The colour wheel can be reused inside other panels (overlay editor's
  colour field, future zone editors) without spawning a dialog.

This widget intentionally does not own an on/off button — that's
LED-panel state, kept out of a reusable colour primitive (legacy's
mistake to fix in next/).
"""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


class ColorWheel(QWidget):
    """Circular hue ring with click + drag selection.

    The ring is painted via :class:`QConicalGradient` — no bundled
    asset required, so the widget works on a fresh install with no
    network access.  ``set_hue()`` is non-emitting; ``hue_changed`` is
    emitted only from user interaction.
    """

    hue_changed = Signal(int)

    _RING_THICKNESS_RATIO = 0.18
    _SELECTOR_RADIUS = 9
    _SELECTOR_PEN_W = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(160, 160)
        self._hue = 0
        self._dragging = False

    # ── Public API ───────────────────────────────────────────────────

    def hue(self) -> int:
        return self._hue

    def set_hue(self, hue: int) -> None:
        """Set hue without emitting the change signal."""
        self._hue = hue % 360
        self.update()

    # ── Geometry helpers ─────────────────────────────────────────────

    def _center(self) -> tuple[float, float]:
        return self.width() / 2.0, self.height() / 2.0

    def _outer_r(self) -> float:
        return min(self.width(), self.height()) / 2.0 - 4

    def _inner_r(self) -> float:
        return self._outer_r() * (1.0 - self._RING_THICKNESS_RATIO)

    def _on_ring(self, x: float, y: float) -> bool:
        cx, cy = self._center()
        dist = math.hypot(x - cx, y - cy)
        # Be generous on the inside (easier to click) than the outside.
        return self._inner_r() - 8 <= dist <= self._outer_r() + 4

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self._center()
        outer = self._outer_r()
        inner = self._inner_r()

        # Conical gradient ring — 12 stops across 360°.
        gradient = QConicalGradient(cx, cy, 0)
        for i in range(13):
            stop = i / 12.0
            gradient.setColorAt(
                stop, QColor.fromHsv(int(stop * 360) % 360, 255, 255),
            )

        ring = QPainterPath()
        ring.addEllipse(QPointF(cx, cy), outer, outer)
        hole = QPainterPath()
        hole.addEllipse(QPointF(cx, cy), inner, inner)
        annulus = ring.subtracted(hole)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(annulus)

        # Selector indicator on the ring midpoint.
        mid_r = (outer + inner) / 2.0
        # QConicalGradient hue starts at 3 o'clock and rotates CCW.  Our
        # selector therefore uses standard math angles.
        angle_rad = math.radians(self._hue)
        sx = cx + mid_r * math.cos(angle_rad)
        sy = cy - mid_r * math.sin(angle_rad)

        painter.setPen(QPen(QColor(255, 255, 255), self._SELECTOR_PEN_W))
        painter.setBrush(QBrush(QColor.fromHsv(self._hue, 255, 255)))
        painter.drawEllipse(
            QPointF(sx, sy), self._SELECTOR_RADIUS, self._SELECTOR_RADIUS,
        )

        painter.end()

    # ── Mouse interaction ────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if self._on_ring(pos.x(), pos.y()):
            self._dragging = True
            self._update_hue_from_pos(pos.x(), pos.y())

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            pos = event.position()
            self._update_hue_from_pos(pos.x(), pos.y())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def _update_hue_from_pos(self, x: float, y: float) -> None:
        cx, cy = self._center()
        dx = x - cx
        dy = -(y - cy)  # screen → math Y
        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360
        hue = int(angle) % 360
        if hue != self._hue:
            self._hue = hue
            self.update()
            self.hue_changed.emit(hue)
