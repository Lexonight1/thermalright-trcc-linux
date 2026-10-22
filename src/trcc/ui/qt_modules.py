"""The Modules page's list, for either window.

One tile per module: its symbol on the left, its name, what it does, a
"Guide ›" to its section of the guide, an On/Off switch, and why it cannot
run -- or why a switch was refused, or what switching off could not undo --
when there is something to say.  The window builds the Commands, as the
visual controls' windows do: *listing* makes its ``ListModules``, *switch*
its ``SetModuleEnabled``, *icon* its ``ModuleIcon`` -- the symbol arrives as
bytes the App read, so a window over the socket draws it too.  A switch is sent off the GUI thread: switching a
module off may ask for the password.  The list follows ``ModuleChanged`` --
so a module switched in another UI turns here too, with its note.  Each
window frames it in its own page and style.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap
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
from ..core.ports import CommandBus
from ..core.results import ModuleResult
from .qt_background import dispatch_in_background

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
    """Every module as a tile with its switch, on *app* -- whichever window,
    in-process or over the socket."""

    def __init__(self, app: CommandBus,
                 listing: Callable[[], Command[Any]],
                 switch: Callable[[str, bool], Command[Any]],
                 icon: Callable[[str], Command[Any]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("ModulesView: built")
        self._app = app
        self._listing = listing
        self._switch = switch
        self._icon = icon
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._column = QVBoxLayout(self._body)
        self._column.setSpacing(10)
        self.setWidget(self._body)
        #: The switches by module key -- what a test or a refresh reads.
        self.switches: dict[str, QPushButton] = {}
        #: What the last switch of each module left to say -- why it was
        #: refused, or what switching off could not undo -- shown on its tile
        #: until a switch with nothing to say, never only in a log.
        self.notes: dict[str, str] = {}
        #: A module's title and summary in the window's language -- the gui
        #: sets it (its ``tr``, which holds each module's ``i18n.json``).
        self._translate: Callable[[str], str] = str
        self.refresh()

    def set_translate(self, translate: Callable[[str], str]) -> None:
        """The window's language changed: the tiles again, in it."""
        log.info("ModulesView.set_translate")
        self._translate = translate
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the tiles from what the App says now."""
        modules = self._app.dispatch(self._listing()).modules
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
            log.info("ModulesView.on_event: %s -> %s note=%r", event.key,
                     event.enabled, event.note)
            self._note(event.key, event.note)
            self.refresh()

    def _symbol(self, key: str) -> QPixmap:
        """Module *key*'s symbol at the tile's size, from the bytes the App
        sends -- an SVG drawn at that size, not scaled up.  Null if none."""
        result = self._app.dispatch(self._icon(key))
        if not result.ok:
            log.warning("ModulesView._symbol: %s -- %s", key, result.message)
            return QPixmap()
        buffer = QBuffer()
        buffer.setData(QByteArray(result.image))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        reader.setScaledSize(reader.size().scaled(
            ICON_SIZE, ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        log.debug("ModulesView._symbol: %s %dx%d %s", key, image.width(),
                  image.height(), reader.errorString() if image.isNull() else "ok")
        return QPixmap.fromImage(image)

    def _tile(self, module: ModuleInfo) -> QFrame:
        """One module: name, what it does, why not here, and its switch."""
        log.debug("ModulesView._tile: %s", module.key)
        tile = QFrame()
        tile.setObjectName("moduleTile")
        row = QHBoxLayout(tile)
        if module.icon and not (pixmap := self._symbol(module.key)).isNull():
            symbol = QLabel()
            symbol.setObjectName("moduleIcon")
            symbol.setPixmap(pixmap)
            row.addWidget(symbol, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        name = QLabel(self._translate(module.title))
        name.setObjectName("moduleTitle")
        text.addWidget(name)
        summary = QLabel(self._translate(module.summary))
        summary.setWordWrap(True)
        text.addWidget(summary)
        note = (f"Cannot run here: {module.problem}" if module.problem
                else self._translate(self.notes.get(module.key, "")))
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
        """Ask the App to switch the module -- off the GUI thread, since
        switching off may ask for the password; the list then shows its
        answer."""
        sender = self.sender()
        key = str(sender.property(_KEY_PROPERTY)) if sender is not None else ""
        log.info("ModulesView._on_switch: %s -> %s", key, checked)
        if isinstance(sender, QPushButton):
            sender.setEnabled(False)       # one switch at a time
        dispatch_in_background(self._app, self._switch(key, checked),
                               self._switched,
                               failed=partial(_could_not_switch, key))

    def _switched(self, result: ModuleResult) -> None:
        """The App's answer to a switch: its refusal or its note, on the tile."""
        if result.ok:
            log.info("ModulesView._switched: %s -> %s note=%r", result.key,
                     result.enabled, result.note)
            self._note(result.key, result.note)
        else:
            log.warning("ModulesView._switched: %s refused: %s", result.key,
                        result.message)
            self.notes[result.key] = result.message
        self.refresh()

    def _note(self, key: str, note: str) -> None:
        """*key*'s tile says *note* from now on -- or nothing, for ""."""
        log.debug("ModulesView._note: %s %r", key, note)
        if note:
            self.notes[key] = note
        else:
            self.notes.pop(key, None)

    def _on_guide(self) -> None:
        """Open the module's section of the guide, as the "?" button opens
        the troubleshooting guide."""
        sender = self.sender()
        url = str(sender.property(_GUIDE_PROPERTY)) if sender is not None else ""
        log.info("ModulesView._on_guide: %s", url)
        QDesktopServices.openUrl(QUrl(url))


def _could_not_switch(key: str, message: str) -> ModuleResult:
    """The App went away while a switch waited -- the tile says so."""
    log.warning("_could_not_switch: %s -- %s", key, message)
    return ModuleResult(ok=False, key=key, message=message)
