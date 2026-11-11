"""What a module's page is -- one page, which every GUI shows.

A module's ``ui/qt/`` exports ``PAGE``: a widget class that also extends
:class:`ModulePage`, built as ``PAGE(app=bus, look=look)``.  Each GUI hosts
that same page -- the gui as a mode tab beside its theme tabs, qtgui as a
sidebar panel, hudgui as a tab of a device's panel -- and hands it its own
look (:class:`ModuleLook`).  The page talks to the App through the bus it is
given, as every page does; it never reaches a window or another page.

His rule (2026-11-10): a GUI is only a skin.  A module used to write one page
per GUI (``ui/gui/`` PAGE, ``ui/qtgui/`` PANEL), so a GUI that came later --
hudgui -- had none, and the two that existed had drifted apart.  Now a page is
written once, and a new GUI shows every module's page by building a look.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from .tile_grid import TileSkin

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModuleLook:
    """What a GUI hands a module's page: its look, nothing else.  Every field
    is the GUI's own; none has a default, so a GUI says all of it."""
    #: A gallery's tiles.
    tiles: TileSkin
    #: The controls under a gallery -- labels, buttons, sliders (a style sheet).
    controls: str
    #: The name of a tile that cannot be used here (a colour).
    dimmed: str
    #: The line a page says what went wrong on (a style sheet).
    status: str


class ModulePage:
    """A module's page.  Mix into the widget class:
    ``class VisualsPage(BaseThemeBrowser, ModulePage)``.

    Every hook has a default, so a page fills in only what it uses."""

    #: Its tab's images, normal and active -- paths, in the module's own
    #: folder -- for a GUI whose tabs are pictures (the gui's).
    TAB_IMAGE: ClassVar[str] = ""
    TAB_IMAGE_ACTIVE: ClassVar[str] = ""
    #: The tab's tooltip and the page's title, in English; the GUI
    #: translates them (the module's ``i18n.json``).
    TOOLTIP: ClassVar[str] = ""
    TITLE: ClassVar[str] = ""
    #: Its module's own events it follows: the GUI forwards each to
    #: :meth:`on_settings_changed` with the App's own device events.
    EVENTS: ClassVar[tuple[type, ...]] = ()

    def set_device_key(self, key: str) -> None:
        """The LCD the GUI now works on."""
        log.debug("%s.set_device_key: %s", type(self).__name__, key)

    def set_language(self, lang: str) -> None:
        """The GUI's language -- at build time, and on every change.  The
        page translates its own words -- ``tr(text, lang, module=KEY)``, its
        module's ``i18n.json`` first, then TRCC's by reference; ``trcc.ui.
        qt_words`` keeps a page's labels for it.  Its title and tooltip the
        GUI does."""
        log.debug("%s.set_language: %s", type(self).__name__, lang)

    def on_settings_changed(self, event: object) -> None:
        """A device's settings changed, in this GUI or another."""
        log.debug("%s.on_settings_changed: %s", type(self).__name__,
                  type(event).__name__)
