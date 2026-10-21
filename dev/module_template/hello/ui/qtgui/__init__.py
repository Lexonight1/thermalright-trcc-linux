"""Hello's qtgui panel -- what a module adds to the qtgui.

A module's ``ui/qtgui/`` exports ``PANEL``: a ``BasePanel`` subclass with a
``LABEL`` for its sidebar entry, built like every panel.  The window shows its
entry while the module is on.
"""
from __future__ import annotations

import logging
from typing import ClassVar

from PySide6.QtWidgets import QLabel, QVBoxLayout

from trcc.ui.qtgui.base import BasePanel

from ...core import Greet

log = logging.getLogger(__name__)


class HelloPanel(BasePanel):
    """A greeting, asked of the module when the panel is built."""

    LABEL: ClassVar[str] = "Hello"

    def _setup_ui(self) -> None:
        log.debug("HelloPanel._setup_ui")
        self.label = QLabel(self.dispatch(Greet()).message, self)
        QVBoxLayout(self).addWidget(self.label)


PANEL = HelloPanel
