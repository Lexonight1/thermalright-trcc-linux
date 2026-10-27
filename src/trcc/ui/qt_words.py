"""A page's fixed words, kept in English and put in the window's language --
for any page, TRCC's own or a module's, in either window.

Every fixed label is set once through :func:`say` (or :func:`say_tip`,
:func:`say_item`), which keeps its English beside it; :func:`retranslate`
walks a widget and puts every one of them in another language -- so a
language change reaches the labels a widget made long ago without each one
being remembered by hand.

The translation is whatever *tr* the page passes: a module's page passes
``partial(tr, lang=lang, module=KEY)`` (``trcc.core.i18n.tr``) -- its own
``i18n.json`` first, then TRCC's words by reference.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QWidget

log = logging.getLogger(__name__)

_W = TypeVar("_W", bound=QLabel | QAbstractButton)
#: English in, the window's language out.
Tr = Callable[[str], str]

#: Where a widget keeps the English it was given.
_EN = "trccEnglish"
_EN_TIP = "trccEnglishTip"
#: Where a picker's item keeps its English.
EN_ROLE = Qt.ItemDataRole.UserRole + 1


def say(widget: _W, text: str) -> _W:
    """Give *widget* the label *text* -- English now, and kept, so
    :func:`retranslate` can say it in another language later."""
    log.debug("say: %s %r", type(widget).__name__, text[:40])
    widget.setProperty(_EN, text)
    widget.setText(text)
    return widget


def say_tip(widget: QWidget, text: str) -> QWidget:
    """*widget*'s tooltip, kept like :func:`say`'s labels."""
    log.debug("say_tip: %s %r", type(widget).__name__, text[:40])
    widget.setProperty(_EN_TIP, text)
    widget.setToolTip(text)
    return widget


def say_item(combo: QComboBox, text: str, data: object, tr: Tr) -> None:
    """A picker item: *text* in the language *tr* speaks now, its English
    kept for the next change."""
    log.debug("say_item: %r", text)
    combo.addItem(tr(text), data)
    combo.setItemData(combo.count() - 1, text, EN_ROLE)


def retranslate(root: QWidget, tr: Tr) -> None:
    """Every label, tooltip and picker item :func:`say` and its kin set
    under *root*, in the language *tr* speaks."""
    widgets = [root, *root.findChildren(QWidget)]
    log.info("retranslate: %s, %d widget(s)", type(root).__name__, len(widgets))
    for widget in widgets:
        text = widget.property(_EN)
        if isinstance(text, str) and isinstance(widget, (QLabel, QAbstractButton)):
            widget.setText(tr(text))
        tip = widget.property(_EN_TIP)
        if isinstance(tip, str):
            widget.setToolTip(tr(tip))
        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                english = widget.itemData(index, EN_ROLE)
                if isinstance(english, str):
                    widget.setItemText(index, tr(english))
