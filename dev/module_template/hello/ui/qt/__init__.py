"""Hello's page -- what a module adds to every GUI.

A module's ``ui/qt/`` exports ``PAGE``: a widget class that also extends
``ModulePage``, built as ``PAGE(app=bus, look=look)``.  Every GUI shows this
one page while the module is on -- the gui as a mode tab, qtgui as a sidebar
entry, hudgui as a device tab -- each handing it its own look
(``ModuleLook``); a page draws with that, never with a GUI's own styles.

Its fixed words go through ``trcc.ui.qt_words``: :func:`say` keeps each
label's English, :func:`retranslate` puts them all in the window's language
-- the module's own ``i18n.json`` first, then TRCC's words by reference, so
a word TRCC already translates needs no copy here.
"""
from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from trcc.core.i18n import tr
from trcc.core.ports import CommandBus
from trcc.ui.module_page import ModuleLook, ModulePage
from trcc.ui.qt_words import retranslate, say

from ...core import HelloGreet

log = logging.getLogger(__name__)

#: The module's folder -- its icon doubles as its tab.
_HERE = Path(__file__).resolve().parents[2]


class HelloPage(QWidget, ModulePage):
    """A greeting, asked of the module when the page is built."""

    TAB_IMAGE = TAB_IMAGE_ACTIVE = str(_HERE / "icon.svg")
    TOOLTIP = TITLE = "Hello"

    def __init__(self, parent: QWidget | None = None, *, app: CommandBus,
                 look: ModuleLook) -> None:
        log.info("HelloPage.__init__")
        super().__init__(parent)
        self.setStyleSheet(look.controls)            # the GUI's look, not its own
        layout = QVBoxLayout(self)
        layout.addWidget(say(QLabel(self), "Hello"))
        self.label = QLabel(app.dispatch(HelloGreet()).message, self)
        layout.addWidget(self.label)

    def set_language(self, lang: str) -> None:
        """Its fixed words in the window's language -- its own first."""
        log.info("HelloPage.set_language: %s", lang)
        retranslate(self, partial(tr, lang=lang, module="hello"))


PAGE = HelloPage
