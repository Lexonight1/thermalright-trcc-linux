"""The LCD brightness slider -- the C#'s ``UCScrollB``.

``TRCC.DCUserControl/UCScrollB.cs``: a 180x24 control on FormCZTV at
(164, 680) (``FormCZTV.cs:8881-8888``).  A track from x=72 to x=172 at y=9,
one pixel per percent, filled from the left with ``P亮度条``; a three-digit
number box at (29, 4).  0-100, default 100.

The gui used the C#'s *hidden* dynamic-island button (``buttonLDD``,
``Visible = false``) as a three-step brightness cycle instead, so the
brightness the panel can take -- any of 101 values -- was three in this skin.

One difference, on purpose: the C# writes on every mouse move and every
keystroke.  ``SetBrightness`` persists, so this sends once -- on release, or
on Enter / leaving the box -- as qtgui's slider and the gui's region fields
already do.  A drag still moves the bar and the number live.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator, QPainter
from PySide6.QtWidgets import QLineEdit, QWidget

from ...core.logs import per_frame
from .assets import Assets

log = logging.getLogger(__name__)
#: Per mouse-move and per paint: a drag must not flood the log.
frame_log = per_frame(__name__)

#: ``UCScrollB.cs:15-19``: the track's left edge, its length (one pixel per
#: percent) and its top.
_TRACK_X, _TRACK_W, _TRACK_Y = 72, 100, 9


class UCBrightness(QWidget):
    """0-100, dragged on the track or typed in the box."""

    #: The user settled on a value -- released the drag, or finished typing.
    changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("UCBrightness.__init__")
        self.setFixedSize(180, 24)
        self._background = Assets.load_pixmap("settings_brightness.png")
        self._bar = Assets.load_pixmap("shared_brightness_bar.png")
        self._value = 100
        self._dragging = False
        # UCScrollB.cs:187-202 -- 36x16 at (29, 4), white on RGB(35, 34, 39).
        self._box = QLineEdit(str(self._value), self)
        self._box.setGeometry(29, 4, 36, 16)
        self._box.setMaxLength(3)
        self._box.setValidator(QIntValidator(0, 100, self._box))
        self._box.setFrame(False)
        self._box.setStyleSheet(
            "QLineEdit { color: white; background: rgb(35, 34, 39); }")
        self._box.editingFinished.connect(self._on_typed)

    @property
    def value(self) -> int:
        frame_log.debug("UCBrightness.value: %d", self._value)
        return self._value

    def show_value(self, percent: int) -> None:
        """Show *percent* -- what the App holds.  Sends nothing."""
        log.debug("UCBrightness.show_value: %d -> %d", self._value, percent)
        self._set(percent)

    def _set(self, percent: int) -> None:
        """Move the bar and the number to *percent*, clamped to 0-100."""
        frame_log.debug("UCBrightness._set: %d", percent)
        self._value = max(0, min(100, percent))
        self._box.blockSignals(True)
        self._box.setText(str(self._value))
        self._box.blockSignals(False)
        self.update()

    def paintEvent(self, event) -> None:
        """The background, then the left ``value`` pixels of the bar
        (``UCScrollB.cs:145-161``)."""
        frame_log.debug("UCBrightness.paintEvent: %d", self._value)
        painter = QPainter(self)
        if not self._background.isNull():
            painter.drawPixmap(0, 0, self._background)
        if not self._bar.isNull() and self._value:
            painter.drawPixmap(_TRACK_X, _TRACK_Y, self._bar,
                               0, 0, self._value, self._bar.height())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._set(self._value_at(event))
        log.debug("UCBrightness.mousePressEvent: %d", self._value)

    def mouseMoveEvent(self, event) -> None:
        frame_log.debug("UCBrightness.mouseMoveEvent: dragging=%s", self._dragging)
        if self._dragging:
            self._set(self._value_at(event))

    def mouseReleaseEvent(self, event) -> None:
        """The end of a drag is the edit (``UCScrollB.cs:131-143``)."""
        if not self._dragging:
            return
        self._dragging = False
        self._set(self._value_at(event))
        log.info("UCBrightness.mouseReleaseEvent: %d", self._value)
        self.changed.emit(self._value)

    def _on_typed(self) -> None:
        """Enter or leaving the box: send what was typed, capped at 100."""
        text = self._box.text()
        log.info("UCBrightness._on_typed: %r", text)
        if not text.isdigit():
            self._set(self._value)           # put back what is shown
            return
        self._set(int(text))
        self.changed.emit(self._value)

    @staticmethod
    def _value_at(event) -> int:
        """The percent under the pointer (``Math_myVal``, UCScrollB.cs:96)."""
        frame_log.debug("UCBrightness._value_at: x=%s", event.position().x())
        return max(0, min(100, int(event.position().x()) - _TRACK_X))
