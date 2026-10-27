"""What a module's gui view is: a whole page of the window, opened by its own
button in the sidebar -- as RGB Lighting, About and Modules are.

A module's ``ui/gui/`` exports ``VIEW``, a widget class that also extends
:class:`ModuleView` (beside, or instead of, ``PAGE``, the theme panel's tab).
The window builds it the first time its module is on, gives it a button in
the next free sidebar slot, and hides both while the module is off.  The view
reaches the App through the bus it is given and hears it through the window's
bridge -- ``bridge.frame_sent``, ``bridge.forward(MyEvent, "module_event")``,
``bridge.add_preview(widget)`` -- never the window or another page.
"""
from __future__ import annotations

import logging
from typing import ClassVar

log = logging.getLogger(__name__)


class ModuleView:
    """A module's full-window view.  Mix into the widget class:
    ``class UCRgb(QWidget, ModuleView)``.  Built as ``VIEW(app=bus,
    bridge=bus_bridge)``, sized to the window's form.

    Every hook has a default, so a view fills in only what it uses."""

    #: Its sidebar button's text and tooltip, in English; the window
    #: translates them (the module's ``i18n.json``).
    BUTTON_TEXT: ClassVar[str] = ""
    TOOLTIP: ClassVar[str] = ""

    def set_language(self, lang: str) -> None:
        """The window's language -- at build time, and on every change.  The
        view translates its own words -- ``tr(text, lang, module=KEY)``, its
        module's ``i18n.json`` first, then TRCC's by reference; its button the
        window does."""
        log.debug("%s.set_language: %s", type(self).__name__, lang)

    def on_settings_changed(self, event: object) -> None:
        """A device's settings changed, in this window or another."""
        log.debug("%s.on_settings_changed: %s", type(self).__name__,
                  type(event).__name__)
