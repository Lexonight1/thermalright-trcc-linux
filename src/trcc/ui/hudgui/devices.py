"""hudgui's Devices: the found devices as their symbols, and the side panel a
picked one opens.

His spec (2026-11-05): Devices drops down "a drop down menu listing the found
devices with the names that gui uses in the buttons" -- "use the symbols just
like gui does" -- and "if you click the device a side panel opens up with the
same as the preview panel with another separated chrome bar with the local
themes | cloud masks and cloud backgrounds with setting on the far end, so
installed modules can fit".

The symbols are the gui's (``presentation.device_symbols``, one rule for both
skins), drawn in colour; a device with none shows its name, as in the gui.
The panel is the shared live preview and the tabs' area, under a tab bar of
its own (``chrome.TabBar``).  Tab contents come next -- layout first.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.commands import DeviceState, ListDevices
from ..presentation.device_symbols import device_symbols
from ..qt_assets import Assets
from ..qt_preview import FRAME_EDGE, PreviewSurface
from .chrome import DEVICE_SETTINGS_TAB, DEVICE_TABS, TabBar
from .themes import ThemeGrid

if TYPE_CHECKING:
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection

log = logging.getLogger(__name__)

#: The property a device button carries: the device it picks.
_KEY = "hud_device_key"
_DEVICE_STYLE = ("QPushButton { background: transparent; color: #dfe5ea; "
                 "border: 2px solid transparent; border-radius: 6px; padding: 2px; }"
                 "QPushButton:checked { border-color: #7fd4ff; }"
                 "QPushButton:hover { background: rgba(255, 255, 255, 25); }")


class DeviceMenu(QWidget):
    """The found devices, each as its symbol; one is picked at a time."""

    #: A device was picked -- its key.
    picked = Signal(str)

    def __init__(self, app: CommandBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("DeviceMenu: built")
        self._app = app
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(6)
        #: The device buttons, by key.
        self.buttons: dict[str, QPushButton] = {}
        self.refresh()

    def refresh(self) -> None:
        """The buttons again, from what the App has attached now."""
        for button in self.buttons.values():
            button.deleteLater()
        self.buttons = {}
        while self._column.count():
            self._column.takeAt(0)
        for entry in self._app.dispatch(ListDevices()).devices:
            state = self._app.dispatch(DeviceState(key=entry.key))
            if not state.ok:
                continue
            self._column.addWidget(self._button(entry.key, {
                'name': f"{state.vendor} {state.product}".strip()
                        or f"Device {state.vid:04x}:{state.pid:04x}",
                'button_image': state.button_image,
                'protocol': state.wire,
                'model': state.model,
            }))
        self._column.addStretch(1)
        log.info("DeviceMenu.refresh: %s", list(self.buttons) or "no devices")

    def _button(self, key: str, info: dict) -> QPushButton:
        """One device: its symbol (normal, and lit while picked), else its name."""
        normal, active = device_symbols(info, Assets.exists)
        log.debug("DeviceMenu._button: %s -> %s", key, normal or info['name'])
        button = QPushButton(self)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_DEVICE_STYLE)
        button.setToolTip(info['name'])
        button.setProperty(_KEY, key)
        if normal:
            picture = Assets.colour_pixmap(normal)
            icon = QIcon(picture)
            if active and Assets.exists(active):
                icon.addPixmap(Assets.colour_pixmap(active), QIcon.Mode.Normal,
                               QIcon.State.On)
            button.setIcon(icon)
            button.setIconSize(QSize(picture.width(), picture.height()))
        else:
            button.setText(info['name'])
        button.clicked.connect(self._on_device)
        self.buttons[key] = button
        return button

    def _on_device(self) -> None:
        """A device was clicked -- read which off the button, light it alone."""
        button = self.sender()
        key = "" if button is None else str(button.property(_KEY))
        log.info("DeviceMenu._on_device: %s", key)
        for name, other in self.buttons.items():
            other.setChecked(name == key)
        if key:
            self.picked.emit(key)


class DevicePanel(QWidget):
    """A picked device's side panel: its own chrome tab bar, then the live
    preview beside the chosen tab's area."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("DevicePanel: built")
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        self.tabs = TabBar(self)
        column.addWidget(self.tabs)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.preview = PreviewSurface(app, bus, selection, Assets.pixmap, self)
        self.preview.setFixedWidth(FRAME_EDGE + 16)
        row.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignTop)
        #: Each tab's area, by name: Local Themes is built, the rest are
        #: placeholders until theirs are.
        self.area = QStackedWidget(self)
        self.pages: dict[str, QWidget] = {}
        for name in (*DEVICE_TABS, DEVICE_SETTINGS_TAB):
            page = (ThemeGrid(app, bus, selection) if name == "Local Themes"
                    else _coming(name))
            self.pages[name] = page
            self.area.addWidget(page)
        row.addWidget(self.area, stretch=1)
        column.addLayout(row)
        self.tabs.tab_chosen.connect(self._on_tab)
        self._on_tab(self.tabs.current)

    def _on_tab(self, name: str) -> None:
        """Show *name*'s area."""
        log.info("DevicePanel._on_tab: %s", name)
        self.area.setCurrentWidget(self.pages[name])


class DevicesView(QWidget):
    """What Devices drops down: the device menu, and -- once a device is
    picked -- its side panel beside it."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("DevicesView: built (tabs %s + %s)", DEVICE_TABS, DEVICE_SETTINGS_TAB)
        self._selection = selection
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(18)
        self.menu = DeviceMenu(app, self)
        # Top LEFT: with no device picked the panel beside it is hidden, and
        # a top-only alignment centred the column in the spare width.
        row.addWidget(self.menu, alignment=Qt.AlignmentFlag.AlignTop
                      | Qt.AlignmentFlag.AlignLeft)
        row.addStretch(0)
        self.panel = DevicePanel(app, bus, selection, self)
        self.panel.hide()
        row.addWidget(self.panel, stretch=1)
        self.menu.picked.connect(self._on_picked)

    def _on_picked(self, key: str) -> None:
        """The side panel opens on *key*, its preview showing that device."""
        log.info("DevicesView._on_picked: %s", key)
        self._selection.set_key(key)
        self.panel.show()


def _coming(name: str) -> QLabel:
    """A tab whose contents are not built yet."""
    log.debug("_coming: %s", name)
    label = QLabel(f"{name} -- coming next.")
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    label.setStyleSheet("color: #dfe5ea; font-size: 12pt;")
    return label
