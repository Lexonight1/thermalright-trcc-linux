"""The RGB panel's RAM-lighting row: the shared view, this skin's Commands."""
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


class RamAccessControl(RamAccessRow):
    """On the RGB panel's lights card, in the native style."""

    def __init__(self, app: CommandBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("RamAccessControl.__init__")
        self._app = app
        self.refresh()

    def _status(self) -> RamLightingResult:
        log.debug("RamAccessControl._status")
        return self._app.dispatch(RamLighting())

    def _switch(self, enabled: bool) -> Command[Any]:
        log.info("RamAccessControl._switch: %s", enabled)
        return SetRamLighting(enabled=enabled)
