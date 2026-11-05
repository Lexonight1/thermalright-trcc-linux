"""hudgui's words in the App's language.

Fixed labels are set with ``ui.qt_words.say`` (and its kin), which keeps
their English; the window retranslates them all when the language changes.
A line made up at the moment -- "Delete the theme 'Blue'?", "No themes for
320x320 yet" -- is translated when it is made, in the language the App holds
NOW (``ControlCenterSnapshot``), so a change from any UI counts.  The words
are the shared table's (``core.i18n``), the gui's own where it has them.
"""
from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from ...core.commands import ControlCenterSnapshot
from ...core.i18n import tr
from ..qt_words import say

if TYPE_CHECKING:
    from PySide6.QtWidgets import QAbstractButton, QLabel

    from ...core.ports import CommandBus
    from ..qt_words import Tr

log = logging.getLogger(__name__)


def language(app: CommandBus) -> str:
    """The language the App holds now."""
    lang = app.dispatch(ControlCenterSnapshot()).language
    log.debug("language: %s", lang)
    return lang


def words(app: CommandBus) -> Tr:
    """English in, the App's language out."""
    lang = language(app)
    log.debug("words: %s", lang)
    return partial(tr, lang=lang)


def say_late(app: CommandBus, widget: QLabel | QAbstractButton, english: str) -> None:
    """``say`` for a widget made AFTER the window translated itself (a list
    rebuilt on every refresh): its English kept for the next change, its text
    in the App's language now."""
    log.debug("say_late: %r", english)
    say(widget, english)
    widget.setText(words(app)(english))
