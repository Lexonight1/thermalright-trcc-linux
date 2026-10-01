"""PreviewPopup — the widescreen preview in a window of its own.

The C#'s ``FormScreenImage``: double-clicking the docked preview of a
widescreen (``isBiliPingmu``) panel moves the ONE preview control into this
frameless, always-on-top window (``FormCZTV.UpDateFormUCScreenImage``), where
editing keeps working; its power button moves it back
(``UpDateFormScreenImage``).  It is dragged by its background and shows in the
taskbar like any form.

The window is its art: ``ResetFormScreenImage`` sizes it to the background
image and puts the power button at ``width - 44``, and the popped preview sits
at a fixed origin -- so the layout here is computed from the art, never
tabulated (``ui/presentation/lcd_panel.POPUP_*``).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget

from ...core.logs import per_frame
from ..presentation.lcd_panel import (
    POPUP_DOCK_BUTTON,
    POPUP_PREVIEW_MARGIN,
    POPUP_PREVIEW_ORIGIN,
)
from .assets import Assets
from .base import ImageLabel
from .constants import Styles

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class PreviewPopup(QWidget):
    """A frameless top-level the preview label is re-parented into."""

    #: The power button, or closing the window: put the preview back.
    dock_requested = Signal()

    def __init__(self) -> None:
        # Parentless on purpose: a top-level with a parent becomes a transient
        # of it on X11 and drops out of the taskbar, where the C# form shows.
        super().__init__(None, Qt.WindowType.Window
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        log.debug("PreviewPopup.__init__")
        self.setWindowTitle("TRCC")
        self._art = QPixmap()
        self._grab: QPoint | None = None     # fallback drag, see mousePressEvent
        self._placed = False                 # the C# centres on the FIRST show
        self._dock = QPushButton(self)
        self._dock.setIcon(QIcon(Assets.load_pixmap("app_power")))
        self._dock.setStyleSheet(Styles.FLAT_BUTTON)
        self._dock.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dock.setToolTip("Dock the preview")
        self._dock.clicked.connect(self._on_dock_clicked)

    def show_art(self, name: str) -> tuple[int, int, int, int]:
        """Size the window to *name*; return the preview's ``(x, y, w, h)``."""
        self._art = Assets.load_pixmap(name)
        width, height = self._art.width(), self._art.height()
        inset, top, side = POPUP_DOCK_BUTTON
        self._dock.setGeometry(width - inset, top, side, side)
        self._dock.setIconSize(self._dock.size())
        self.setFixedSize(width, height)
        x, y = POPUP_PREVIEW_ORIGIN
        rect = (x, y, width - POPUP_PREVIEW_MARGIN[0],
                height - POPUP_PREVIEW_MARGIN[1])
        log.info("PreviewPopup.show_art: %s -> window %dx%d, preview %s",
                 name, width, height, rect)
        self.update()
        return rect

    def present(self) -> None:
        """Show the window -- centred the first time, as the C# form starts."""
        log.info("PreviewPopup.present: placed=%s", self._placed)
        if not self._placed and (screen := self.screen()) is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())
            self._placed = True
        self.show()
        self.raise_()

    def _on_dock_clicked(self) -> None:
        log.info("PreviewPopup._on_dock_clicked")
        self.dock_requested.emit()

    def paintEvent(self, event) -> None:
        """Draw the art; the preview label and the button sit on top of it."""
        frame_log.debug("PreviewPopup.paintEvent: %dx%d", self._art.width(),
                        self._art.height())
        QPainter(self).drawPixmap(0, 0, self._art)

    def mousePressEvent(self, event) -> None:
        """Drag by the background, as the C# form does.

        ``startSystemMove`` hands the move to the window manager, the one way
        that works on Wayland; where it is refused, fall back to moving the
        window by hand.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        handle = self.windowHandle()
        moved = handle is not None and handle.startSystemMove()
        log.debug("PreviewPopup.mousePressEvent: system move=%s", moved)
        self._grab = None if moved else event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:
        frame_log.debug("PreviewPopup.mouseMoveEvent: dragging=%s",
                        self._grab is not None)
        if self._grab is not None:
            self.move(event.globalPosition().toPoint() - self._grab)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        log.debug("PreviewPopup.mouseReleaseEvent: at %s", self.pos())
        self._grab = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:
        """Closing the window docks the preview -- it is never destroyed here,
        since it holds the one preview label until the dock moves it back."""
        log.info("PreviewPopup.closeEvent: docking instead")
        event.ignore()
        self.dock_requested.emit()


class PreviewPopOut:
    """Where the one preview label lives: its bezel, or the pop-out window.

    The C# moves ONE preview control between the two
    (``UpDateFormUCScreenImage`` / ``UpDateFormScreenImage``), so editing works
    in either and there is no second preview to keep in step.  It owns the
    gesture too: a double-click pops a widescreen panel out, and is ignored
    while out (the C# clears ``isBiliPingmu``).
    """

    def __init__(self, label: ImageLabel, container: QWidget) -> None:
        log.debug("PreviewPopOut.__init__")
        self._label = label
        self._container = container
        self._window: PreviewPopup | None = None    # built on the first pop-out
        self._art: str | None = None
        self._docked_rect = (0, 0, label.width(), label.height())
        self._popped = False
        label.double_clicked.connect(self._on_double_clicked)

    @property
    def popped(self) -> bool:
        log.debug("PreviewPopOut.popped: %s", self._popped)
        return self._popped

    @property
    def window(self) -> PreviewPopup | None:
        log.debug("PreviewPopOut.window: built=%s", self._window is not None)
        return self._window

    def follow(self, art: str | None, docked_rect: tuple[int, int, int, int]) -> None:
        """A new resolution: its pop-out art (None = cannot pop) and bezel area.

        Popped and still widescreen, the window re-lays -- a rotation swaps in
        the portrait art, as the C# does (FormCZTV.cs:4862-4956).  Anything
        else docks: a square device cannot be shown in a widescreen frame.
        """
        log.info("PreviewPopOut.follow: art=%s docked=%s popped=%s", art,
                 docked_rect, self._popped)
        self._art, self._docked_rect = art, docked_rect
        if self._popped and art:
            self._show(art)
        else:
            self._dock()

    def pop_out(self) -> None:
        log.info("PreviewPopOut.pop_out: art=%s popped=%s", self._art, self._popped)
        if self._art and not self._popped:
            self._show(self._art)

    def dock(self) -> None:
        log.info("PreviewPopOut.dock: popped=%s", self._popped)
        if self._popped:
            self._dock()

    def _on_double_clicked(self) -> None:
        """Deferred one turn of the loop: the label is moved to another
        window, which is not done from inside its own mouse event."""
        log.info("PreviewPopOut._on_double_clicked: art=%s", self._art)
        QTimer.singleShot(0, self.pop_out)

    def _show(self, art: str) -> None:
        if self._window is None:
            self._window = PreviewPopup()
            self._window.dock_requested.connect(self.dock)
        x, y, w, h = self._window.show_art(art)
        if not self._popped:
            self._label.setParent(self._window)
            self._popped = True
        self._place(x, y, w, h)
        self._window.present()
        log.info("PreviewPopOut._show: %s preview %dx%d at (%d,%d)", art, w, h, x, y)

    def _dock(self) -> None:
        if self._popped:
            self._label.setParent(self._container)
            self._popped = False
            if self._window is not None:
                self._window.hide()
        self._place(*self._docked_rect)
        log.debug("PreviewPopOut._dock: %s", self._docked_rect)

    def _place(self, x: int, y: int, w: int, h: int) -> None:
        log.debug("PreviewPopOut._place: %dx%d at (%d,%d)", w, h, x, y)
        self._label.move(x, y)
        self._label.resize_area(w, h)
        self._label.show()
