"""hudgui's chrome bar: the silver menu across the window's top edge.

The window is frameless, so this bar IS its top edge: drag it to move the
window, and it carries its own minimise and close.  Drawn in code (gradients,
no images), so it scales with the window.

The menu is a toggle group, as he described it (2026-11-05): a click lights a
button and drops its panel down, clearing the last one lit; a second click on
the same button pulls the panel back up.  The bar only reports clicks
(``menu_clicked``); the window decides what is open and lights it back
(``set_active``), so the bar never disagrees with what is shown.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ...core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The bar's height, in pixels.
CHROME_HEIGHT = 40
#: The menu, left to right -- each item drops down its own panel.  Themes are
#: a device's: they are tabs in its side panel (his spec, 2026-11-05).
MENU = ("Devices", "Sensors", "Modules", "Control Center", "About")
#: A device side panel's own tabs: left to right, then Settings at the far
#: end, the room between kept for an installed module's tab (his spec).
DEVICE_TABS = ("Local Themes", "Cloud Masks", "Cloud Backgrounds")
DEVICE_SETTINGS_TAB = "Settings"
#: The property a menu button carries: the item it opens.
_ITEM = "hud_menu_item"

_MENU_STYLE = """
QPushButton {
    color: #1b2026; background: transparent; border: 1px solid transparent;
    border-radius: 13px; padding: 4px 16px; font-weight: bold; font-size: 10pt;
}
QPushButton:hover { background: rgba(255, 255, 255, 110); border-color: rgba(60, 66, 74, 90); }
QPushButton:checked {
    color: #031520;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f2fdff, stop:0.5 #a9e6ff, stop:1 #58c3f2);
    border: 1px solid #2f6f8f;
}
"""
_WINDOW_BUTTON_STYLE = """
QPushButton {
    color: #1b2026; border: 1px solid #6c737c; border-radius: 11px; font-weight: bold;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #aeb4bc);
}
QPushButton:hover { background: #ffffff; }
"""


class SilverStrip(QWidget):
    """A strip of brushed silver chrome -- the top bar, and a device panel's
    tab bar, painted once."""

    def paintEvent(self, event: QPaintEvent) -> None:
        """Brushed silver: a bright top edge, a soft middle seam, a dark rim."""
        frame_log.debug("%s.paintEvent", type(self).__name__)
        painter = QPainter(self)
        silver = QLinearGradient(0, 0, 0, self.height())
        silver.setColorAt(0.0, QColor("#fbfcfd"))
        silver.setColorAt(0.45, QColor("#dfe3e8"))
        silver.setColorAt(0.55, QColor("#c3c9d0"))
        silver.setColorAt(1.0, QColor("#959ca5"))
        painter.fillRect(self.rect(), silver)
        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawLine(0, 0, self.width(), 0)
        painter.setPen(QColor("#4e555e"))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


class TabBar(SilverStrip):
    """A device panel's own chrome tab bar: its tabs as a group (one lit at a
    time), then Settings at the far end -- the room between for modules."""

    #: A tab was chosen -- its name.
    tab_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("TabBar: %s ... %s", ", ".join(DEVICE_TABS), DEVICE_SETTINGS_TAB)
        self.setFixedHeight(CHROME_HEIGHT - 6)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(4)
        #: The tabs, by name.
        self.tabs: dict[str, QPushButton] = {}
        for name in DEVICE_TABS:
            row.addWidget(self._tab(name))
        row.addStretch(1)                         # an installed module's tab goes here
        row.addWidget(self._tab(DEVICE_SETTINGS_TAB))
        self.choose(DEVICE_TABS[0])

    def _tab(self, name: str) -> QPushButton:
        """One tab button."""
        log.debug("TabBar._tab: %s", name)
        button = QPushButton(name, self)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_MENU_STYLE)
        button.setProperty(_ITEM, name)
        button.clicked.connect(self._on_tab)
        self.tabs[name] = button
        return button

    def _on_tab(self) -> None:
        """A tab was clicked -- read which off the button."""
        button = self.sender()
        name = "" if button is None else str(button.property(_ITEM))
        log.info("TabBar._on_tab: %s", name)
        if name:
            self.choose(name)
            self.tab_chosen.emit(name)

    def choose(self, name: str) -> None:
        """Light *name*'s tab and no other."""
        log.debug("TabBar.choose: %s", name)
        for tab, button in self.tabs.items():
            button.setChecked(tab == name)

    @property
    def current(self) -> str:
        """The lit tab."""
        found = next((t for t, b in self.tabs.items() if b.isChecked()), "")
        log.debug("TabBar.current: %s", found)
        return found


class ChromeBar(SilverStrip):
    """The silver bar: title, menu, live readout, minimise and close."""

    #: A menu button was clicked -- its item.
    menu_clicked = Signal(str)
    minimise_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("ChromeBar: %s", ", ".join(MENU))
        self.setFixedHeight(CHROME_HEIGHT)
        self._drag_from: QPoint | None = None
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 10, 0)
        row.setSpacing(4)
        title = QLabel("TRCC", self)
        title.setStyleSheet("color: #2a3038; font-weight: 900; font-size: 12pt; "
                            "letter-spacing: 2px; padding-right: 14px;")
        row.addWidget(title)
        #: The menu buttons, by item.
        self.buttons: dict[str, QPushButton] = {}
        for item in MENU:
            button = QPushButton(item, self)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(_MENU_STYLE)
            button.setProperty(_ITEM, item)
            button.clicked.connect(self._on_menu)
            row.addWidget(button)
            self.buttons[item] = button
        row.addStretch(1)
        #: The live numbers on the bar: CPU and GPU temperature.
        self.readout = QLabel("", self)
        self.readout.setStyleSheet("color: #1b2026; font-weight: bold; "
                                   "font-family: monospace; padding-right: 12px;")
        row.addWidget(self.readout)
        for text, tip, signal in (("–", "Minimise", self.minimise_clicked),
                                  ("×", "Close this window -- the panels keep playing",
                                   self.close_clicked)):
            button = QPushButton(text, self)
            button.setFixedSize(22, 22)
            button.setToolTip(tip)
            button.setStyleSheet(_WINDOW_BUTTON_STYLE)
            button.clicked.connect(signal)
            row.addWidget(button)

    def _on_menu(self) -> None:
        """A menu button was clicked -- read which off the button."""
        button = self.sender()
        item = "" if button is None else str(button.property(_ITEM))
        log.info("ChromeBar._on_menu: %s", item)
        if item:
            self.menu_clicked.emit(item)

    def set_active(self, item: str | None) -> None:
        """Light *item*'s button and no other (None: none lit)."""
        log.debug("ChromeBar.set_active: %s", item)
        for name, button in self.buttons.items():
            button.setChecked(name == item)

    def show_temps(self, cpu: float, gpu: float, unit: str) -> None:
        """The live readout: CPU and GPU temperature in the user's unit."""
        log.debug("ChromeBar.show_temps: %.0f / %.0f °%s", cpu, gpu, unit)
        self.readout.setText(f"CPU {cpu:3.0f}°{unit}   GPU {gpu:3.0f}°{unit}")

    # ── The bar is the window's handle ─────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Pressing the bar's background picks the window up."""
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            self._drag_from = (event.globalPosition().toPoint()
                               - window.frameGeometry().topLeft())
            log.debug("ChromeBar.mousePressEvent: drag from %s", self._drag_from)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Dragging moves the window with the pointer."""
        frame_log.debug("ChromeBar.mouseMoveEvent: dragging=%s", self._drag_from is not None)
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_from)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Let go of the window."""
        log.debug("ChromeBar.mouseReleaseEvent")
        self._drag_from = None
        super().mouseReleaseEvent(event)
