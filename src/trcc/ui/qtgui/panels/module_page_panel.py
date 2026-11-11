"""A module's page in qtgui -- the page every GUI shows, under qtgui's
device picker.

qtgui's panels each pick their LCD with a ``DevicePickerWidget``; the page
itself (``ui/qt/``'s ``PAGE``) is the module's one page, in qtgui's look --
the native one, as every qtgui panel is drawn.
"""
from __future__ import annotations

import logging
from functools import cache
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout, QVBoxLayout

from ...module_page import ModuleLook
from ...tile_grid import TileSkin
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from ....core.ports import CommandBus
    from ...bus_bridge import BusBridge
    from ...device_selection import DeviceSelection

log = logging.getLogger(__name__)


def _selected(cls_name: str) -> str:
    """A lit tile: the system's highlight round it."""
    log.debug("qtgui _selected: %s", cls_name)
    return (f"{cls_name} {{ border: 2px solid palette(highlight); "
            f"border-radius: 4px; }}")


def _normal(cls_name: str) -> str:
    """A tile at rest; the system's mid tone round it under the pointer."""
    log.debug("qtgui _normal: %s", cls_name)
    return (f"{cls_name} {{ border: 1px solid transparent; border-radius: 4px; }}"
            f"{cls_name}:hover {{ border: 1px solid palette(mid); }}")


def _not_local(cls_name: str) -> str:
    """A tile not yet downloaded: dashed."""
    log.debug("qtgui _not_local: %s", cls_name)
    return (f"{cls_name} {{ border: 1px dashed palette(mid); "
            f"border-radius: 4px; }}")


@cache
def qtgui_module_look() -> ModuleLook:
    """qtgui's look for a module's page: the system's own."""
    log.debug("qtgui_module_look: built")
    return ModuleLook(
        tiles=TileSkin(image="border: 1px solid palette(mid);", name="",
                       scroll_area="QScrollArea { border: none; }",
                       selected=_selected, normal=_normal, not_local=_not_local,
                       placeholder=(60, 60, 64), placeholder_text=(160, 160, 160),
                       empty_text="palette(placeholder-text)"),
        controls="",
        dimmed="palette(placeholder-text)",
        status="",
    )



class ModulePagePanel(BasePanel):
    """qtgui's panel for a module's page: the device picker, then the page."""

    def __init__(self, page_class: Any, app: CommandBus, bus: BusBridge,
                 parent: QWidget | None = None, *,
                 selection: DeviceSelection | None = None) -> None:
        log.info("ModulePagePanel: %s", page_class.__name__)
        self._page_class = page_class
        super().__init__(app, bus, parent, selection=selection)

    def _setup_ui(self) -> None:
        log.debug("ModulePagePanel._setup_ui")
        self._picker = DevicePickerWidget(self._app, self._bus, kind_filter="lcd",
                                          parent=self, selection=self._selection)
        self.page = self._page_class(app=self._app, look=qtgui_module_look())
        form = QFormLayout()
        form.addRow(self._picker.LABEL, self._picker)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
        root.addLayout(form)
        # A page has its own size (the grid's 732 x 652): top-left, the
        # room below and beside it left empty, never round it.
        root.addWidget(self.page, stretch=1, alignment=Qt.AlignmentFlag.AlignTop
                       | Qt.AlignmentFlag.AlignLeft)
        for event_type in self.page.EVENTS:
            self._bus.forward(event_type, "settings_changed")
        self._bus.settings_changed.connect(
            self.page.on_settings_changed, type=Qt.ConnectionType.QueuedConnection)
        self._picker.key_changed.connect(self.page.set_device_key)
        self.page.set_device_key(self._picker.current_key() or "")
