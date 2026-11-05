"""hudgui's window: a thin chrome bar above the game, and one menu panel at a
time dropping down from it.

His design (2026-11-05): "so gamers can have a thin bar above their gaming".
At rest the window IS the bar: frameless, always on top, across the top of the
primary screen.  A click on a menu item grows it and drops that item's panel
down; a second click pulls it back up to the bar.  The glass under a dropped
panel is a picture (``assets/glass.png``, ``dev/tools/gen_hud_glass.py``),
not real transparency, so it needs no compositor.

Devices drops down the found devices as their symbols; a picked one opens
its side panel (``devices.py``).  Everything shown comes from the App over the
Command bus, like every UI: the live preview is the shared
``ui.qt_preview.PreviewSurface`` (which renders only while it is on screen --
so at rest, under a game, hudgui renders nothing), the readout comes from
``SensorsUpdated``.  Closing it closes this window
only -- the App, and the panels, keep going.
"""
from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QGuiApplication,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QApplication, QWidget

from ...core.events import LanguageChanged
from ...core.i18n import tr
from ...core.logs import per_frame
from ..bus_bridge import BusBridge
from ..device_selection import DeviceSelection
from ..qt_modules import ModulesView
from ..qt_preview import FRAME_EDGE
from ..qt_words import retranslate
from .about import About
from .chrome import CHROME_HEIGHT, ChromeBar
from .control_center import ControlCenter
from .devices import DevicesView
from .dropdown import DropPanel
from .modules import modules_panel
from .sensors import SensorsView
from .words import language

if TYPE_CHECKING:
    from ...core.events import SensorsUpdated
    from ...core.ports import CommandBus

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The glass under a dropped-down panel.
GLASS = Path(__file__).parent / "assets" / "glass.png"
#: A dropped-down panel's height: a device panel's tab bar, the preview's
#: bezel, and the frame around them.
DROP_HEIGHT = FRAME_EDGE + 120


class HudWindow(QWidget):
    """The hudgui window: the thin bar, and a panel that drops from it."""

    def __init__(self, app: CommandBus) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        log.info("HudWindow: building")
        self.setWindowTitle("TRCC")
        self._app = app
        self._bus = BusBridge(app.events)
        self._selection = DeviceSelection(self)
        self._glass = QPixmap(str(GLASS))
        self._glass_scaled = QPixmap()
        #: The menu item whose panel is down, or None -- just the bar.
        self.active: str | None = None
        #: Each item's panel, made on its first click and kept after.
        self._panels: dict[str, QWidget] = {}

        self.bar = ChromeBar(self)
        self.drop = DropPanel(self)

        self.bar.menu_clicked.connect(self._on_menu)
        self.bar.minimise_clicked.connect(self.showMinimized)
        self.bar.close_clicked.connect(self.close)
        self.drop.closed.connect(self._to_bar)
        self._bus.sensors_updated.connect(self._on_sensors,
                                          type=Qt.ConnectionType.QueuedConnection)
        self._bus.app_settings_changed.connect(self._on_app_settings,
                                               type=Qt.ConnectionType.QueuedConnection)
        #: The language the words are in now.
        self._lang = ""
        self._set_language(language(app))
        self._to_bar()

    def _screen_top(self) -> QRect:
        """The primary screen's usable area -- the bar lies along its top."""
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)
        log.debug("HudWindow._screen_top: %s", area)
        return area

    def _to_bar(self) -> None:
        """Rest as just the bar, across the top of the primary screen."""
        area = self._screen_top()
        log.info("HudWindow._to_bar: %dx%d at (%d, %d)", area.width(), CHROME_HEIGHT,
                 area.x(), area.y())
        self.setGeometry(area.x(), area.y(), area.width(), CHROME_HEIGHT)
        self.bar.setGeometry(0, 0, area.width(), CHROME_HEIGHT)

    def panel(self, item: str) -> QWidget:
        """*item*'s panel -- made on its first click, then the same one."""
        if item not in self._panels:
            log.info("HudWindow.panel: making %s's", item)
            made = self._panels[item] = self._make(item)
            self._translate(made)
        return self._panels[item]

    def _make(self, item: str) -> QWidget:
        """A new panel for menu *item*."""
        log.debug("HudWindow._make: %s", item)
        match item:
            case "Devices":
                devices = DevicesView(self._app, self._bus, self._selection)
                self._bus.frames_resumed.connect(devices.panel.preview.refresh)
                self._bus.follow_previews(devices.panel.preview)
                return devices
            case "Sensors":
                return SensorsView(self._app, self._bus)
            case "Modules":
                return modules_panel(self._app, self._bus)
            case "Control Center":
                return ControlCenter(self._app, self._bus)
            case "About":
                return About(self._app, self._bus)
        raise ValueError(f"no hudgui panel for menu item {item!r}")

    # ── The menu: a toggle group ───────────────────────────────────────

    def _on_menu(self, item: str) -> None:
        """A click on *item*: the window grows and its panel drops down,
        clearing the last one -- or, a second click on the same item, the
        panel pulls up and the window goes back to the bar."""
        log.info("HudWindow._on_menu: %s (down: %s)", item, self.active)
        if item == self.active:
            self.active = None
            self.drop.pull_up()
        else:
            self.active = item
            self.drop.set_content(self.panel(item))
            self.resize(self.width(), CHROME_HEIGHT + DROP_HEIGHT)
            self.drop.drop(tr(item, self._lang), CHROME_HEIGHT, self.width(), DROP_HEIGHT)
        self.bar.set_active(self.active)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Esc pulls the open panel up."""
        log.debug("HudWindow.keyPressEvent: %s", event.key())
        if event.key() == Qt.Key.Key_Escape and self.active is not None:
            self._on_menu(self.active)
            return
        super().keyPressEvent(event)

    # ── The language ───────────────────────────────────────────────────

    def _on_app_settings(self, event: object) -> None:
        """An App setting changed in some UI -- a new language is ours too."""
        log.debug("HudWindow._on_app_settings: %s", type(event).__name__)
        if isinstance(event, LanguageChanged):
            self._set_language(event.language)

    def _set_language(self, lang: str) -> None:
        """Every word on the window -- the bar, each panel made so far, the
        dropped panel's title -- in *lang*."""
        log.info("HudWindow._set_language: %r -> %r", self._lang, lang)
        self._lang = lang
        self._translate(self)
        for panel in self._panels.values():
            self._translate(panel)
        if self.active is not None:
            self.drop.set_title(tr(self.active, lang))

    def _translate(self, widget: QWidget) -> None:
        """*widget*'s fixed words in the window's language -- the shared
        Modules list says its own (``set_language``)."""
        log.debug("HudWindow._translate: %s in %s", type(widget).__name__, self._lang)
        retranslate(widget, partial(tr, lang=self._lang))
        for modules in (widget, *widget.findChildren(ModulesView)):
            if isinstance(modules, ModulesView):
                modules.set_language(self._lang)

    # ── Live ───────────────────────────────────────────────────────────

    def _on_sensors(self, event: SensorsUpdated) -> None:
        """The bar's readout follows every sensor broadcast."""
        frame_log.debug("HudWindow._on_sensors")
        metrics = event.metrics
        self.bar.show_temps(metrics.cpu_temp, metrics.gpu_temp, event.temp_unit)

    # ── The glass, and the window's life ───────────────────────────────

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Rescale the glass once per size; the bar spans the width."""
        log.debug("HudWindow.resizeEvent: %s", event.size().toTuple())
        super().resizeEvent(event)
        self.bar.setGeometry(0, 0, self.width(), CHROME_HEIGHT)
        self._glass_scaled = self._glass.scaled(
            self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)

    def paintEvent(self, event: QPaintEvent) -> None:
        """The glass, under a dropped-down panel."""
        frame_log.debug("HudWindow.paintEvent")
        if self.height() <= CHROME_HEIGHT:
            return
        painter = QPainter(self)
        painter.drawPixmap(0, CHROME_HEIGHT, self._glass_scaled,
                           0, CHROME_HEIGHT, self.width(), self.height() - CHROME_HEIGHT)
        painter.end()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing hudgui closes this window only: the App keeps the panels
        playing.  The event loop is ended so the face's teardown runs."""
        log.info("HudWindow.closeEvent: closing hudgui -- the App keeps going")
        event.accept()
        if (qapp := QApplication.instance()) is not None:
            qapp.quit()

