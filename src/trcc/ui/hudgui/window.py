"""hudgui's window: the chrome bar on top, the live panel preview as its body,
and one menu panel at a time dropping down over it -- on a glass background.

His design (2026-11-05): a redesign of the gui, whose live preview is what
makes it usable, in a HUD look.  Frameless -- the chrome bar is the top edge.
The glass is a picture (``assets/glass.png``, ``dev/tools/gen_hud_glass.py``),
not real transparency, so it needs no compositor.

Everything it shows comes from the App over the Command bus, like every UI:
the preview is the shared ``ui.qt_preview.PreviewSurface``, the readout comes
from ``SensorsUpdated``.  Closing it closes this window only -- the App, and
the panels, keep going.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent, QPainter, QPaintEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ...core.commands import ListDevices
from ...core.logs import per_frame
from ..bus_bridge import BusBridge
from ..device_selection import DeviceSelection
from ..qt_assets import Assets
from ..qt_preview import FRAME_EDGE, PreviewSurface
from .chrome import CHROME_HEIGHT, ChromeBar
from .dropdown import DropPanel

if TYPE_CHECKING:
    from ...core.events import SensorsUpdated
    from ...core.ports import CommandBus

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The glass behind everything.
GLASS = Path(__file__).parent / "assets" / "glass.png"
#: The window's first size.
START_SIZE = (1180, 780)
#: A dropped-down panel's share of the body's height.
DROP_SHARE = 0.72


class HudWindow(QWidget):
    """The hudgui window."""

    def __init__(self, app: CommandBus) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        log.info("HudWindow: building")
        self.setWindowTitle("TRCC")
        self.resize(*START_SIZE)
        self._app = app
        self._bus = BusBridge(app.events)
        self._selection = DeviceSelection(self)
        self._glass = QPixmap(str(GLASS))
        self._glass_scaled = QPixmap()
        #: The menu item whose panel is down, or None.
        self.active: str | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.bar = ChromeBar(self)
        column.addWidget(self.bar)
        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 24, 0, 24)
        self.preview = PreviewSurface(app, self._bus, self._selection, Assets.pixmap,
                                      self._body)
        self.preview.setFixedWidth(FRAME_EDGE + 16)
        body.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self._body, stretch=1)
        self.drop = DropPanel(self._body)

        self.bar.menu_clicked.connect(self._on_menu)
        self.bar.minimise_clicked.connect(self.showMinimized)
        self.bar.close_clicked.connect(self.close)
        self._bus.sensors_updated.connect(self._on_sensors,
                                          type=Qt.ConnectionType.QueuedConnection)
        self._bus.frames_resumed.connect(self.preview.refresh)
        self._bus.follow_previews(self.preview)
        self._show_first_lcd()

    def _show_first_lcd(self) -> None:
        """Preview the first attached LCD -- the Devices panel will choose."""
        lcds = [d.key for d in self._app.dispatch(ListDevices()).devices
                if d.kind.lower().endswith("lcd")]
        log.info("HudWindow._show_first_lcd: %s", lcds or "none attached")
        if lcds:
            self._selection.set_key(lcds[0])
        else:
            self.preview.set_placeholder("No LCD attached -- plug one in.")

    # ── The menu: a toggle group ───────────────────────────────────────

    def _on_menu(self, item: str) -> None:
        """A click on *item*: its panel drops down, clearing the last one --
        or, a second click on the same item, pulls it back up."""
        log.info("HudWindow._on_menu: %s (down: %s)", item, self.active)
        if item == self.active:
            self.active = None
            self.drop.pull_up()
        else:
            self.active = item
            self.drop.set_content(_placeholder(item))
            self.drop.drop(item, 0, self._body.width(), self._drop_height())
        self.bar.set_active(self.active)

    def _drop_height(self) -> int:
        """How tall a dropped-down panel is: most of the body."""
        height = int(self._body.height() * DROP_SHARE)
        log.debug("HudWindow._drop_height: %d", height)
        return height

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Esc pulls the open panel up."""
        log.debug("HudWindow.keyPressEvent: %s", event.key())
        if event.key() == Qt.Key.Key_Escape and self.active is not None:
            self._on_menu(self.active)
            return
        super().keyPressEvent(event)

    # ── Live ───────────────────────────────────────────────────────────

    def _on_sensors(self, event: SensorsUpdated) -> None:
        """The bar's readout follows every sensor broadcast."""
        frame_log.debug("HudWindow._on_sensors")
        metrics = event.metrics
        self.bar.show_temps(metrics.cpu_temp, metrics.gpu_temp, event.temp_unit)

    # ── The glass, and the window's life ───────────────────────────────

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Rescale the glass once per size, and keep a dropped panel full width."""
        log.debug("HudWindow.resizeEvent: %s", event.size().toTuple())
        super().resizeEvent(event)
        self._glass_scaled = self._glass.scaled(
            self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        if self.active is not None:
            self.drop.setGeometry(0, 0, self._body.width(), self._drop_height())

    def paintEvent(self, event: QPaintEvent) -> None:
        """The glass, under everything below the bar."""
        frame_log.debug("HudWindow.paintEvent")
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


def _placeholder(item: str) -> QWidget:
    """What a panel shows before its own contents are built."""
    log.debug("_placeholder: %s", item)
    label = QLabel(f"{item} -- coming next.", None)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    label.setStyleSheet("color: #dfe5ea; font-size: 12pt;")
    return label
