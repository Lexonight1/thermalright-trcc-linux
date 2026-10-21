"""The Modules page: every module, switched on or off.  Opened by Modules on
the Control Center page's top bar, between Control Center and About.  The
list itself is ``ui.qt_modules.ModulesView``, shared with qtgui."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPaintEvent
from PySide6.QtWidgets import QWidget

from ...core.commands import Command, ListModules, SetModuleEnabled
from ...core.logs import per_frame
from ..bus_bridge import BusBridge
from ..qt_modules import ModulesView
from .base import paint_page
from .constants import Colors, Sizes

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Inside the framed body, in this page's coordinates.
_BODY = (40, 110, 1190, 660)
_STYLE = (f"QFrame#moduleTile {{ background: #2a2a30; border: 1px solid {Colors.PAGE_FRAME};"
          " border-radius: 6px; }"
          "QLabel { background: transparent; color: #d0d0d0; font-size: 11pt; }"
          "QLabel#moduleTitle { color: white; font-size: 14pt; font-weight: bold; }"
          f"QLabel#moduleProblem {{ color: {Colors.PAGE_GOLD}; }}"
          "QLabel#modulesEmpty { color: #b3b3b3; font-size: 13pt; }"
          "QPushButton { min-width: 70px; min-height: 32px; color: white;"
          f" background: #3a3a3a; border: 1px solid {Colors.PAGE_FRAME}; border-radius: 4px; }}"
          f"QPushButton:checked {{ background: {Colors.PAGE_GOLD}; color: #3a3a3a; }}"
          # "Guide ›" is a link, not a button: gold text, no box.
          "QPushButton#moduleGuide { min-width: 0; min-height: 0; padding: 0;"
          f" border: none; background: transparent; color: {Colors.PAGE_GOLD}; }}"
          "QPushButton#moduleGuide:hover { text-decoration: underline; }"
          "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")


class UCModules(QWidget):
    """The Modules page: its gold title bar, then the shared list."""

    def __init__(self, dispatch: Callable[[Command[Any]], Any], bus: BusBridge,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("UCModules: built")
        self.setFixedSize(Sizes.FORM_W, Sizes.FORM_H)
        self._title = "Modules"
        self.view = ModulesView(dispatch, ListModules, self._switch, self)
        self.view.setGeometry(*_BODY)
        self.view.setStyleSheet(_STYLE)
        # A module switched in any UI: this page follows.
        bus.app_settings_changed.connect(
            self.view.on_event, type=Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _switch(key: str, enabled: bool) -> SetModuleEnabled:
        """The Command a tile's switch sends."""
        log.debug("UCModules._switch: %s -> %s", key, enabled)
        return SetModuleEnabled(key=key, enabled=enabled)

    def set_title(self, title: str) -> None:
        """The title bar's text -- retranslated with the window's language."""
        log.info("UCModules.set_title: %r", title)
        self._title = title
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        frame_log.debug("UCModules.paintEvent: %s", self._title)
        paint_page(self, self._title)
