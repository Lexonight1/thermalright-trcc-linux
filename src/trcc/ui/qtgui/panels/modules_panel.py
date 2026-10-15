"""ModulesPanel -- every module, switched on or off (qtgui's Modules page).

The list is ``ui.qt_modules.ModulesView``, shared with the gui's page; a
module switched in any UI turns here too, through ``ModuleChanged``.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout

from ....core.commands import ListModules, SetModuleEnabled
from ...qt_modules import ModulesView
from ..base import BasePanel

log = logging.getLogger(__name__)


class ModulesPanel(BasePanel):
    """The Modules page."""

    def _setup_ui(self) -> None:
        log.debug("ModulesPanel._setup_ui")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Modules", self)
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.view = ModulesView(self.dispatch, ListModules, self._switch, self)
        layout.addWidget(self.view, 1)
        self._bus.app_settings_changed.connect(
            self.view.on_event, type=Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _switch(key: str, enabled: bool) -> SetModuleEnabled:
        """The Command a tile's switch sends."""
        log.debug("ModulesPanel._switch: %s -> %s", key, enabled)
        return SetModuleEnabled(key=key, enabled=enabled)
