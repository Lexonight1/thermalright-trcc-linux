"""What a module's gui page is: the page one mode tab opens.

A module's ``ui/gui/`` exports ``PAGE``, a widget class that also extends
:class:`ModulePage`.  The window builds it the first time its module is on,
gives it a tab in the next free module slot of the tab row, and hides the tab
while the module is off.  The page talks to the App through the bus it is
given, as every page does -- it never reaches the window or another page.
"""
from __future__ import annotations

import logging
from typing import ClassVar

log = logging.getLogger(__name__)


class ModulePage:
    """A module's page in the theme panel's stack.  Mix into the widget class:
    ``class UCVisuals(BaseThemeBrowser, ModulePage)``.  Built as
    ``PAGE(app=bus)``.

    Every hook has a default, so a page fills in only what it uses."""

    #: Its tab's images, normal and active -- paths, in the module's own folder.
    TAB_IMAGE: ClassVar[str] = ""
    TAB_IMAGE_ACTIVE: ClassVar[str] = ""
    #: The tab's tooltip and the page's title, in English; the window
    #: translates them (the module's ``i18n.json``).
    TOOLTIP: ClassVar[str] = ""
    TITLE: ClassVar[str] = ""

    def set_device_key(self, key: str) -> None:
        """The LCD the window now works on."""
        log.debug("%s.set_device_key: %s", type(self).__name__, key)

    def on_settings_changed(self, event: object) -> None:
        """A device's settings changed, in this window or another."""
        log.debug("%s.on_settings_changed: %s", type(self).__name__,
                  type(event).__name__)
