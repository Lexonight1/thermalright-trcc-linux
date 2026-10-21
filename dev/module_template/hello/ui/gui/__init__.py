"""Hello's gui page -- what a module adds to the gui.

A module's ``ui/gui/`` exports ``PAGE``: a widget class that also extends
``ModulePage``, built as ``PAGE(app=bus)``.  The window gives it a mode tab
while the module is on.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from trcc.core.ports import CommandBus
from trcc.ui.gui.module_page import ModulePage

from ...core import Greet

log = logging.getLogger(__name__)

#: The module's folder -- its icon doubles as its tab.
_HERE = Path(__file__).resolve().parents[2]


class HelloPage(QWidget, ModulePage):
    """A greeting, asked of the module when the page is built."""

    TAB_IMAGE = TAB_IMAGE_ACTIVE = str(_HERE / "icon.svg")
    TOOLTIP = TITLE = "Hello"

    def __init__(self, parent: QWidget | None = None,
                 app: CommandBus | None = None) -> None:
        log.info("HelloPage.__init__")
        super().__init__(parent)
        self.label = QLabel(app.dispatch(Greet()).message if app else "", self)
        QVBoxLayout(self).addWidget(self.label)


PAGE = HelloPage
