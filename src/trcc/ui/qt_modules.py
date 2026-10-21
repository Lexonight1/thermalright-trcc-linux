"""The Modules page's list, for either window.

One tile per module: its symbol on the left, its name, what it does, a
"Guide ›" to its section of the guide, an On/Off switch, and why it cannot
run -- or why a switch was refused -- when it cannot.  The window builds the Commands, as the
visual controls' windows do: *listing* makes its ``ListModules``, *switch*
its ``SetModuleEnabled``.  The list follows ``ModuleChanged`` -- so a module
switched in another UI turns here too.  Each window frames it in its own page
and style.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.commands import Command
from ..core.events import ModuleChanged
from ..core.models import ModuleInfo

log = logging.getLogger(__name__)

#: What the page says before any feature is a module.
NONE_YET = "No modules yet -- features arrive here as they become modules."
#: The property a switch carries: the module it turns.
_KEY_PROPERTY = "trcc_module_key"
#: The property a "Guide ›" carries: where it opens.
_GUIDE_PROPERTY = "trcc_module_guide"
#: The symbol's size on a tile, in pixels.
ICON_SIZE = 40


class ModulesView(QScrollArea):
    """Every module as a tile with its switch.  *dispatch* runs a Command on
    the App -- whichever window, in-process or over the socket."""

    def __init__(self, dispatch: Callable[[Command[Any]], Any],
                 listing: Callable[[], Command[Any]],
                 switch: Callable[[str, bool], Command[Any]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("ModulesView: built")
        self._dispatch = dispatch
        self._listing = listing
        self._switch = switch
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._column = QVBoxLayout(self._body)
        self._column.setSpacing(10)
        self.setWidget(self._body)
        #: The switches by module key -- what a test or a refresh reads.
        self.switches: dict[str, QPushButton] = {}
        #: Why the last switch of each module was refused -- shown on its
        #: tile until one succeeds, never only in a log.
        self.refused: dict[str, str] = {}
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the tiles from what the App says now."""
        modules = self._dispatch(self._listing()).modules
        log.info("ModulesView.refresh: %s", {m.key: m.enabled for m in modules})
        while self._column.count():
            item = self._column.takeAt(0)
            if item is not None and (widget := item.widget()) is not None:
                # Gone at once: deleteLater waits for the event loop, and the
                # old tile showed under the new one until it ran.
                widget.hide()
                widget.deleteLater()
        self.switches.clear()
        if not modules:
            empty = QLabel(NONE_YET)
            empty.setObjectName("modulesEmpty")
            self._column.addWidget(empty)
        for module in modules:
            self._column.addWidget(self._tile(module))
        self._column.addStretch(1)

    def on_event(self, event: object) -> None:
        """Follow a module switched in any UI."""
        if isinstance(event, ModuleChanged):
            log.info("ModulesView.on_event: %s -> %s", event.key, event.enabled)
            self.refresh()

    def _tile(self, module: ModuleInfo) -> QFrame:
        """One module: name, what it does, why not here, and its switch."""
        log.debug("ModulesView._tile: %s", module.key)
        tile = QFrame()
        tile.setObjectName("moduleTile")
        row = QHBoxLayout(tile)
        if module.icon and not (icon := QIcon(module.icon)).isNull():
            symbol = QLabel()
            symbol.setObjectName("moduleIcon")
            symbol.setPixmap(icon.pixmap(ICON_SIZE, ICON_SIZE))
            row.addWidget(symbol, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        name = QLabel(module.title)
        name.setObjectName("moduleTitle")
        text.addWidget(name)
        summary = QLabel(module.summary)
        summary.setWordWrap(True)
        text.addWidget(summary)
        note = (f"Cannot run here: {module.problem}" if module.problem
                else self.refused.get(module.key, ""))
        if note:
            problem = QLabel(note)
            problem.setObjectName("moduleProblem")
            problem.setWordWrap(True)
            text.addWidget(problem)
        if module.guide:
            guide = QPushButton("Guide ›")
            guide.setObjectName("moduleGuide")
            guide.setFlat(True)
            guide.setCursor(Qt.CursorShape.PointingHandCursor)
            guide.setProperty(_GUIDE_PROPERTY, module.guide)
            guide.clicked.connect(self._on_guide)
            text.addWidget(guide, 0, Qt.AlignmentFlag.AlignLeft)
        row.addLayout(text, 1)
        switch = QPushButton("On" if module.enabled else "Off")
        switch.setCheckable(True)
        switch.setChecked(module.enabled)
        switch.setCursor(Qt.CursorShape.PointingHandCursor)
        switch.setProperty(_KEY_PROPERTY, module.key)
        switch.clicked.connect(self._on_switch)
        row.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.switches[module.key] = switch
        return tile

    def _on_switch(self, checked: bool) -> None:
        """Ask the App to switch the module; the list then shows its answer."""
        sender = self.sender()
        key = str(sender.property(_KEY_PROPERTY)) if sender is not None else ""
        log.info("ModulesView._on_switch: %s -> %s", key, checked)
        result = self._dispatch(self._switch(key, checked))
        if result.ok:
            self.refused.pop(key, None)
        else:
            log.warning("ModulesView._on_switch: %s refused: %s", key,
                        result.message)
            self.refused[key] = result.message
        self.refresh()

    def _on_guide(self) -> None:
        """Open the module's section of the guide, as the "?" button opens
        the troubleshooting guide."""
        sender = self.sender()
        url = str(sender.property(_GUIDE_PROPERTY)) if sender is not None else ""
        log.info("ModulesView._on_guide: %s", url)
        QDesktopServices.openUrl(QUrl(url))
