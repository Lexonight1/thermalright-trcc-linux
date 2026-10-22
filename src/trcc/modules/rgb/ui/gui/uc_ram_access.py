"""The RGB page's RAM-lighting row: the shared view, this skin's Commands."""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import QWidget

from trcc.core.commands import Command
from trcc.core.ports import CommandBus
from trcc.modules.rgb.core.commands import RamLighting, SetRamLighting
from trcc.modules.rgb.core.results import RamLightingResult
from trcc.modules.rgb.ui.qt_ram_access import RamAccessRow

log = logging.getLogger(__name__)


class UCRamAccess(RamAccessRow):
    """On the RGB page's lights card, in the page's colours."""

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

    def _switch(self, enabled: bool) -> Command[Any]:
        log.info("UCRamAccess._switch: %s", enabled)
        return SetRamLighting(enabled=enabled)
