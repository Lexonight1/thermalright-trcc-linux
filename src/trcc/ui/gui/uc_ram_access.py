"""The settings page's RAM-lighting row: the shared view, this skin's Commands."""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QWidget

from ...core.commands import RamLighting, SetRamLighting
from ...core.ports import CommandBus
from ...core.results import RamLightingResult
from ..qt_ram_access import RamAccessRow

log = logging.getLogger(__name__)


class UCRamAccess(RamAccessRow):
    """Under the "Corsair RGB RAM follows" checkbox, in the page's colours."""

    def __init__(self, app: CommandBus, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            text_style="color: #B4964F; font-size: 9pt; background: transparent;",
            button_style=("background-color: black; color: #B4964F; "
                          "border: 1px solid #B4964F; padding: 2px 8px;"))
        log.debug("UCRamAccess.__init__")
        self._app = app
        self.refresh()

    def _status(self) -> RamLightingResult:
        log.debug("UCRamAccess._status")
        return self._app.dispatch(RamLighting())

    def _switch(self, enabled: bool) -> RamLightingResult:
        log.info("UCRamAccess._switch: %s", enabled)
        return self._app.dispatch(SetRamLighting(enabled=enabled))
