"""The panel a menu item drops down over the preview.

Framed the way he described it (2026-11-05): Star-Trek console framing --
a thick rounded ELBOW along the top and down the left, the title in a pill on
it -- drawn in greyscale chrome around a clear body, so the preview and the
glass show through.  The body carries a light tint, enough to keep the
panel's own text readable over a moving picture.

Its contents come from the window (``set_content``); this class is only the
frame and the motion: it slides down (``drop``) and back up (``pull_up``).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: How long the panel takes to drop down or pull up, in milliseconds.
SLIDE_MS = 180
#: The elbow: its top bar's height and its side bar's width, in pixels.
ELBOW_TOP, ELBOW_SIDE = 22, 16
#: The space the frame takes before the contents start.
_INSET = (ELBOW_SIDE + 14, ELBOW_TOP + 12, 16, 14)


def _chrome(rect: QRectF) -> QLinearGradient:
    """Greyscale chrome, light at the top of *rect* to dark at its bottom."""
    frame_log.debug("_chrome: %s", rect)
    chrome = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    chrome.setColorAt(0.0, QColor("#f4f6f8"))
    chrome.setColorAt(0.5, QColor("#b9bfc7"))
    chrome.setColorAt(1.0, QColor("#7c838c"))
    return chrome


class DropPanel(QWidget):
    """One menu item's panel, sliding down from under the bar."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        log.info("DropPanel: built")
        self._title = ""
        self._height = 0
        self._slide = QPropertyAnimation(self, b"geometry", self)
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.finished.connect(self._slid)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*_INSET)
        self._content: QWidget = QLabel("", self)
        self._layout.addWidget(self._content)
        self.hide()

    @property
    def title(self) -> str:
        """The item whose panel this is now ("" when none)."""
        log.debug("DropPanel.title: %r", self._title)
        return self._title

    def set_content(self, widget: QWidget) -> None:
        """Put *widget* inside the frame, in place of what was there."""
        log.info("DropPanel.set_content: %s", type(widget).__name__)
        self._layout.removeWidget(self._content)
        self._content.deleteLater()
        self._content = widget
        widget.setParent(self)
        self._layout.addWidget(widget)

    def drop(self, title: str, top: int, width: int, height: int) -> None:
        """Slide down, *width* x *height*, from *top*, titled *title*."""
        log.info("DropPanel.drop: %s %dx%d at y=%d", title, width, height, top)
        self._title, self._height = title, height
        self._slide.stop()
        self.setGeometry(QRect(0, top, width, 0))
        self.show()
        self.raise_()
        self._slide.setStartValue(QRect(0, top, width, 0))
        self._slide.setEndValue(QRect(0, top, width, height))
        self._slide.start()

    def pull_up(self) -> None:
        """Slide back up under the bar, then hide."""
        log.info("DropPanel.pull_up: %s", self._title)
        self._slide.stop()
        start = self.geometry()
        self._slide.setStartValue(start)
        self._slide.setEndValue(QRect(start.x(), start.y(), start.width(), 0))
        self._title = ""
        self._slide.start()

    def _slid(self) -> None:
        """A slide finished: an empty panel (pulled up) leaves the screen."""
        log.debug("DropPanel._slid: %r height=%d", self._title, self.height())
        if not self._title:
            self.hide()

    # ── Drawn, not pictured ────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:
        """The tint, the chrome rim, the elbow and its pill title."""
        frame_log.debug("DropPanel.paintEvent: %r", self._title)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        whole = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 14, 20, 150))
        painter.drawRoundedRect(whole, 14, 14)

        elbow = QPainterPath()
        elbow.addRoundedRect(QRectF(whole.left(), whole.top(), whole.width(), ELBOW_TOP), 11, 11)
        elbow.addRoundedRect(QRectF(whole.left(), whole.top(), ELBOW_SIDE,
                                    whole.height() * 0.62), 8, 8)
        painter.setBrush(_chrome(QRectF(whole.left(), whole.top(), whole.width(), ELBOW_TOP)))
        painter.drawPath(elbow.simplified())

        painter.setPen(QColor(200, 205, 212, 170))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(whole, 14, 14)

        if self._title:
            font = QFont(self.font())
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            pill = QRectF(whole.left() + 34, whole.top() + 3,
                          painter.fontMetrics().horizontalAdvance(self._title.upper()) + 28,
                          ELBOW_TOP - 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2a3038"))
            painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
            painter.setPen(QColor("#e9edf1"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, self._title.upper())
        painter.end()
