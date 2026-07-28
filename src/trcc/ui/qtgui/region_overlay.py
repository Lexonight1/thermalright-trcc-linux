"""RegionSelectOverlay — drag a rectangle to choose a screen region.

Lives next to :mod:`screen_overlay` because they share the same frozen-
screenshot base.  Where the eyedropper picks a single pixel, this
overlay returns a rectangle ``(x, y, w, h)`` that the screencast pipe
re-grabs on every tick.

Visuals:

* Whole screen dimmed; the selection rectangle "punches through" with
  the original screenshot.
* A floating size label (``WxH``) tracks the cursor so users can hit
  exact aspect ratios.

Signals:

* :sig:`region_selected(x, y, w, h)` — left-mouse drag completed.
* :sig:`cancelled()` — ESC or right-click.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen

from ..screen_overlay import BaseScreenOverlay

log = logging.getLogger(__name__)


class RegionSelectOverlay(BaseScreenOverlay):
    """Full-screen overlay returning a chosen ``(x, y, w, h)`` rectangle."""

    region_selected = Signal(int, int, int, int)
    cancelled = Signal()

    _DIM = QColor(0, 0, 0, 120)
    _BORDER = QColor(200, 200, 200)
    _BORDER_W = 2
    _SIZE_FONT = QFont("sans-serif", 11)
    _SIZE_BG = QColor(0, 0, 0, 180)
    _SIZE_TEXT = QColor(255, 255, 255)
    _MIN_EDGE = 10
    _HINT = "Click and drag to choose a region.  ESC to cancel."

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._selecting = False
        self._start = QPoint()
        self._end = QPoint()

    def _emit_cancel(self) -> None:
        self.cancelled.emit()

    def paintEvent(self, event) -> None:
        if self._screenshot.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, self._screenshot)
        painter.fillRect(self.rect(), self._DIM)

        if self._selecting and self._start != self._end:
            sel = self._selection_rect()
            # "Punch through" the dim layer with the original screenshot.
            painter.drawPixmap(sel, self._screenshot, sel)
            pen = QPen(self._BORDER, self._BORDER_W)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(sel)
            self._draw_size_label(painter, sel)
        else:
            painter.setPen(self._SIZE_TEXT)
            painter.setFont(QFont("sans-serif", 14))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, self._HINT,
            )
        painter.end()

    def _draw_size_label(self, painter: QPainter, sel: QRect) -> None:
        label = f"{sel.width()} × {sel.height()}"
        painter.setFont(self._SIZE_FONT)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(label) + 12
        th = fm.height() + 6
        lx = sel.center().x() - tw // 2
        ly = sel.bottom() + 8
        if ly + th > self.height():
            ly = sel.top() - th - 8
        painter.fillRect(lx, ly, tw, th, self._SIZE_BG)
        painter.setPen(self._SIZE_TEXT)
        painter.drawText(lx + 6, ly + fm.ascent() + 3, label)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._selecting = True
            self._start = event.pos()
            self._end = event.pos()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._cancel()

    def mouseMoveEvent(self, event) -> None:
        if self._selecting:
            self._end = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._selecting:
            return
        self._end = event.pos()
        self._selecting = False
        sel = self._selection_rect()
        if sel.width() >= self._MIN_EDGE and sel.height() >= self._MIN_EDGE:
            self._confirm(sel)
        else:
            self.update()

    def _selection_rect(self) -> QRect:
        return QRect(self._start, self._end).normalized()

    def _confirm(self, sel: QRect) -> None:
        self.hide()
        self.region_selected.emit(
            sel.x(), sel.y(), sel.width(), sel.height(),
        )
        self.deleteLater()
