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
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.commands import DeviceState, ListDevices
from ...core.events import DeviceConnected, DeviceDisconnected, LanguageChanged
from ...core.logs import per_frame
from ..presentation.device_choice import DeviceChoice
from ..presentation.device_symbols import device_symbols
from ..presentation.theme_settings import ThemeSettings
from ..qt_assets import Assets
from ..qt_preview import FRAME_EDGE, PreviewSurface
from ..settings_panel.binding import SettingsBinding, SettingsSteps
from ..settings_panel.uc_theme_setting import UCThemeSetting
from .chrome import DEVICE_SETTINGS_TAB, DEVICE_TABS, TabBar
from .settings_look import hud_skin
from .sources import DeviceControls
from .themes import CloudBackgrounds, CloudMasks, LocalThemes
from .words import language

if TYPE_CHECKING:
    from ...core.events import SensorsUpdated
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection
    from ..ui_state import UiStateStore

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

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
        #: Each attached device's kind ("lcd" / "led"), by key.
        self.kinds: dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        """The buttons again, from what the App has attached now."""
        for button in self.buttons.values():
            button.deleteLater()
        self.buttons, self.kinds = {}, {}
        while self._column.count():
            self._column.takeAt(0)
        for entry in self._app.dispatch(ListDevices()).devices:
            state = self._app.dispatch(DeviceState(key=entry.key))
            if not state.ok:
                continue
            self.kinds[entry.key] = entry.kind
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

    def show_picked(self, key: str) -> None:
        """Light *key*'s button alone -- or none, for no device."""
        log.debug("DeviceMenu.show_picked: %r", key)
        for name, button in self.buttons.items():
            button.setChecked(name == key)

    def _on_device(self) -> None:
        """A device was clicked -- read which off the button, light it alone."""
        button = self.sender()
        key = "" if button is None else str(button.property(_KEY))
        log.info("DeviceMenu._on_device: %s", key)
        self.show_picked(key)
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
        # The preview, and under it brightness and rotation -- as the gui
        # has them, on every tab (his call).
        under = QVBoxLayout()
        under.setSpacing(8)
        self.preview = PreviewSurface(app, bus, selection, Assets.pixmap, self)
        self.preview.setFixedWidth(FRAME_EDGE + 16)
        self.controls = DeviceControls(app, bus, selection, self)
        self.controls.setFixedWidth(FRAME_EDGE + 16)
        under.addWidget(self.preview)
        under.addWidget(self.controls)
        under.addStretch(1)
        row.addLayout(under)
        #: Each tab's area, by name.
        self.area = QStackedWidget(self)
        self.pages: dict[str, QWidget] = {}
        pages = dict(zip((*DEVICE_TABS, DEVICE_SETTINGS_TAB),
                         (LocalThemes, CloudMasks, CloudBackgrounds, SettingsPage),
                         strict=True))
        for name, kind in pages.items():
            page = kind(app, bus, selection)
            self.pages[name] = page
            self.area.addWidget(page)
        row.addWidget(self.area, stretch=1)
        column.addLayout(row)
        self.tabs.tab_chosen.connect(self._on_tab)
        self._on_tab(self.tabs.current)
        # Moving an overlay element by hand -- only while Settings is open, so
        # browsing themes never moves one by accident.
        self.preview.pressed.connect(self._on_pressed)
        self.preview.dragged.connect(self._on_dragged)
        self.preview.released.connect(self._on_released)

    def _on_tab(self, name: str) -> None:
        """Show *name*'s area."""
        log.info("DevicePanel._on_tab: %s", name)
        self.area.setCurrentWidget(self.pages[name])

    def _editor(self) -> SettingsBinding | None:
        """The Settings panel's binding, while the Settings tab is the one shown."""
        page = self.pages[DEVICE_SETTINGS_TAB]
        found = (page.binding if isinstance(page, SettingsPage)
                 and self.tabs.current == DEVICE_SETTINGS_TAB else None)
        log.debug("DevicePanel._editor: %s", found is not None)
        return found

    def _on_pressed(self, x: int, y: int) -> None:
        log.debug("DevicePanel._on_pressed: (%d, %d)", x, y)
        if (editor := self._editor()) is not None:
            editor.drag.grab_at(x, y)

    def _on_dragged(self, x: int, y: int) -> None:
        frame_log.debug("DevicePanel._on_dragged: (%d, %d)", x, y)
        if (editor := self._editor()) is not None:
            editor.drag.drag_to(x, y)

    def _on_released(self) -> None:
        log.debug("DevicePanel._on_released")
        if (editor := self._editor()) is not None:
            editor.drag.let_go()


class DevicesView(QWidget):
    """What Devices drops down: the device menu, and -- once a device is
    picked -- its side panel beside it."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 ui_state: UiStateStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("DevicesView: built (tabs %s + %s)", DEVICE_TABS, DEVICE_SETTINGS_TAB)
        self._selection = selection
        #: Which device is on screen -- the gui's rules, shared by every skin.
        self._choice = DeviceChoice(ui_state.state.last_device_key,
                                    ui_state.set_last_device_key)
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
        # A device plugged in or lost after this view was built: the menu
        # listed only what was attached when it was made.
        for signal in (bus.device_connected, bus.device_disconnected):
            signal.connect(self._on_fleet_changed,
                           type=Qt.ConnectionType.QueuedConnection)
        self._show(self._choice.opened(self.menu.kinds))

    def _on_picked(self, key: str) -> None:
        """The user picked *key*."""
        log.info("DevicesView._on_picked: %s", key)
        self._show(self._choice.picked(key))

    def _on_fleet_changed(self, event: DeviceConnected | DeviceDisconnected) -> None:
        """A device came or went, from any UI -- the menu again, and the device
        ``DeviceChoice`` puts on screen, if it moves one."""
        log.info("DevicesView._on_fleet_changed: %s %s",
                 type(event).__name__, event.key)
        self.menu.refresh()
        answer = (self._choice.connected(event.key, self.menu.kinds)
                  if isinstance(event, DeviceConnected)
                  else self._choice.disconnected(event.key, self.menu.kinds))
        self._show(self._selection.key if answer is None else answer)

    def _show(self, key: str) -> None:
        """The side panel on *key*, its preview showing that device -- or, with
        no device at all, hidden."""
        log.debug("DevicesView._show: %r", key)
        self.menu.show_picked(key)
        self._selection.set_key(key)
        self.panel.setVisible(bool(key))


class SettingsPage(QScrollArea):
    """The Settings tab: the gui's Settings panel -- its layout, its behaviour --
    in hudgui's look (``settings_look.hud_skin``), bound to the App by the
    shared ``SettingsBinding``.  One panel for every GUI; only the skin is
    hudgui's own (his rule, 2026-11-10)."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("SettingsPage: the shared Settings panel, hud skin")
        self._selection = selection
        self.setWidgetResizable(False)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget "
                           "{ background: transparent; }")
        self.panel = UCThemeSetting(skin=hud_skin())
        self.binding = SettingsBinding(self.panel, app, self._device_key,
                                       SettingsSteps(self, app))
        self.setWidget(self.panel)
        # Never scrolled: the panel is drawn at its own size, the gui's.
        self.setMinimumSize(self.panel.size())
        bus.follow_settings(self._on_settings_event)
        bus.app_settings_changed.connect(self._on_app_settings,
                                         type=Qt.ConnectionType.QueuedConnection)
        self.panel.set_language(language(app))
        bus.sensors_updated.connect(self._on_sensors, type=Qt.ConnectionType.QueuedConnection)
        for signal in (bus.theme_loaded, bus.orientation_changed):
            signal.connect(self.binding.size_to_canvas, type=Qt.ConnectionType.QueuedConnection)
        selection.changed.connect(self._on_device)
        self._on_device()

    def _device_key(self) -> str:
        """The device this panel edits -- the window's selection."""
        log.debug("SettingsPage._device_key: %s", self._selection.key)
        return self._selection.key

    def _on_device(self, *_key: object) -> None:
        """Another device picked: its canvas, then its settings."""
        log.info("SettingsPage._on_device: %s", self._selection.key)
        self.binding.size_to_canvas()
        self.binding.refresh()

    def _on_settings_event(self, event: object) -> None:
        """Something the panel shows changed -- here, if it is this device's."""
        if ThemeSettings.concerns(event, self._selection.key):
            log.debug("SettingsPage._on_settings_event: %s", type(event).__name__)
            self.binding.refresh()

    def _on_app_settings(self, event: object) -> None:
        """A new language from any UI: the panel's words in it."""
        if isinstance(event, LanguageChanged):
            log.info("SettingsPage._on_app_settings: language %s", event.language)
            self.panel.set_language(event.language)

    def _on_sensors(self, event: SensorsUpdated) -> None:
        """A sensor tick: the element tiles' live values, while on screen."""
        if self.isVisible():
            frame_log.debug("SettingsPage._on_sensors: %d", len(event.readings))
            self.panel.update_metrics(event.readings, event.temp_unit)
