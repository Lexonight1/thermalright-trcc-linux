"""A device panel's module tabs -- each module's page, in hudgui's look.

A module's page is the one every GUI shows (``ui/module_page``): the gui puts
it in a mode tab, qtgui under its device picker, and hudgui here, as a tab of
a device's panel beside the theme tabs -- the room ``TabBar`` keeps for it.
A tab shows while its module is on, in any UI, and its page follows the
device picked, the App's events and the language, as the gui's does.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt

from ...core.commands import ListModules
from ...core.events import LanguageChanged, ModuleChanged
from ...core.i18n import tr
from .._module_faces import module_page
from .settings_look import hud_module_look
from .words import language

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStackedWidget, QWidget

    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection
    from .chrome import TabBar

log = logging.getLogger(__name__)


class ModuleTabs:
    """The module pages of one device panel: built the first time their
    module is on and kept, their tabs hidden while it is off."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 tabs: TabBar, area: QStackedWidget, pages: dict[str, QWidget],
                 leave: Callable[[], None]) -> None:
        log.info("ModuleTabs: built")
        self._app, self._bus, self._selection = app, bus, selection
        self._tabs, self._area, self._pages = tabs, area, pages
        #: Shows the panel's first tab -- when the page on screen goes off.
        self._leave = leave
        self._lang = language(app)
        #: The module pages built, by module key.
        self.built: dict[str, Any] = {}
        bus.app_settings_changed.connect(self._on_app_settings,
                                         type=Qt.ConnectionType.QueuedConnection)
        self.sync()

    def sync(self) -> None:
        """A tab for each module that is on and has a page; the rest hidden --
        and the page on screen, if its module went off, left."""
        on = {m.key for m in self._app.dispatch(ListModules()).modules if m.enabled}
        log.info("ModuleTabs.sync: on %s, built %s", sorted(on), sorted(self.built))
        for key in sorted(on - set(self.built)):
            if (page_class := module_page(key)) is not None:
                self._add(key, page_class)
        for key in self.built:
            self._tabs.tabs[key].setVisible(key in on)
        if self._tabs.current in set(self.built) - on:
            log.info("ModuleTabs.sync: the page on screen is off -- leaving it")
            self._leave()

    def _add(self, key: str, page_class: Any) -> None:
        """Module *key*'s page and its tab, following the device, the App and
        the language."""
        log.info("ModuleTabs._add: %s", key)
        page = page_class(app=self._app, look=hud_module_look())
        for event_type in page.EVENTS:
            self._bus.forward(event_type, "settings_changed")
        self._bus.settings_changed.connect(page.on_settings_changed,
                                           type=Qt.ConnectionType.QueuedConnection)
        self._selection.changed.connect(page.set_device_key)
        page.set_device_key(self._selection.key)
        page.set_language(self._lang)
        tab = self._tabs.add_tab(key, tr(page.TITLE, self._lang, key))
        tab.setToolTip(tr(page.TOOLTIP, self._lang, key))
        self._pages[key] = page
        self._area.addWidget(page)
        self.built[key] = page

    def _on_app_settings(self, event: object) -> None:
        """A module switched in any UI, or a new language."""
        if isinstance(event, ModuleChanged):
            log.info("ModuleTabs._on_app_settings: %s", event.key)
            self.sync()
        elif isinstance(event, LanguageChanged):
            log.info("ModuleTabs._on_app_settings: language %s", event.language)
            self._lang = event.language
            for key, page in self.built.items():
                page.set_language(event.language)
                self._tabs.tabs[key].setText(tr(page.TITLE, event.language, key))
                self._tabs.tabs[key].setToolTip(tr(page.TOOLTIP, event.language, key))
