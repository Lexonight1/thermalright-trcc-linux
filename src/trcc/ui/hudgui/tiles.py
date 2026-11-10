"""A device panel tab of picture tiles -- the one shape Local Themes, Cloud
Masks and Cloud Backgrounds share, written once.

The gui's behaviour, which is what he asked for ("the same way"): tiles of
120 x 140 -- a picture over its name -- as many to a row as fit (the gui's
area is fixed at five; this one follows the window); one click puts that
tile's thing on the panel; the one the panel SHOWS is lit.  The light follows
the APP -- ``LcdSnapshot``, re-read whenever a theme loads or a device's
settings change, from any UI -- never this window's last click.

A tab says only what differs (``TileGrid`` subclasses): its tiles, what a
click dispatches, which tile is current, and an optional header above them.
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.commands import DeviceCanvas, LcdSnapshot
from ...core.results import LcdSnapshotResult
from ..qt_assets import thumbnail_icon
from ..qt_background import dispatch_in_background
from .flow import FlowArea
from .words import words

if TYPE_CHECKING:
    from ...core.commands import Command
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection

log = logging.getLogger(__name__)

#: A tile, as the gui's (``Sizes.THUMB_W`` x ``THUMB_H``), and its picture.
TILE = QSize(120, 140)
PICTURE = 110
#: The space between tiles.
SPACING = 10
#: The property a tile carries: what it names (a path, a cloud id).
_ID = "hud_tile_id"
_TILE_STYLE = ("QToolButton { color: #dfe5ea; background: rgba(255, 255, 255, 12); "
               "border: 2px solid transparent; border-radius: 6px; font-size: 9pt; }"
               "QToolButton:hover { background: rgba(255, 255, 255, 30); }"
               "QToolButton:checked { border-color: #7fd4ff; "
               "background: rgba(127, 212, 255, 40); }")


@dataclass(frozen=True, slots=True)
class Tile:
    """One tile: what it names, its name, its picture -- and whether the
    user may remove it (their own saved theme)."""
    id: str
    name: str
    picture: str = ""
    removable: bool = False


class TileGrid(QWidget):
    """A tab of picture tiles for the picked device; a click puts one on it."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("%s: built", type(self).__name__)
        self._app = app
        self._selection = selection
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        #: What sits above the tiles (a tab's own filter), or None.
        self.header = self._header()
        if self.header is not None:
            column.addWidget(self.header)
        #: The tiles, flowed to the visible width (``flow.FlowArea``).
        self._flow = FlowArea(TILE.width(), SPACING, self)
        column.addWidget(self._flow, stretch=1)
        #: The tiles, by what they name.
        self.tiles: dict[str, QToolButton] = {}
        #: What the tab says when it has no tiles, or why a click failed.
        self.note = QLabel("", self)
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #dfe5ea; font-size: 11pt;")
        column.addWidget(self.note)

        selection.changed.connect(self.refresh)
        bus.data_installed.connect(self.refresh, type=Qt.ConnectionType.QueuedConnection)
        bus.theme_loaded.connect(self._on_device_event,
                                 type=Qt.ConnectionType.QueuedConnection)
        bus.settings_changed.connect(self._on_device_event,
                                     type=Qt.ConnectionType.QueuedConnection)
        self.refresh()

    # ── What a tab says ────────────────────────────────────────────────

    def _header(self) -> QWidget | None:
        """What sits above the tiles -- nothing, unless a tab has a filter."""
        log.debug("%s._header: none", type(self).__name__)
        return None

    @abstractmethod
    def _tiles(self, key: str, size: tuple[int, int]) -> list[Tile]:
        """The tiles for device *key*, whose panel is *size*."""

    @abstractmethod
    def _pick(self, key: str, tile_id: str) -> Command[Any]:
        """What clicking tile *tile_id* dispatches for device *key*."""

    @abstractmethod
    def _current(self, snapshot: LcdSnapshotResult) -> str | None:
        """Which tile the panel shows now, by its id -- None for none."""

    #: Said when there are no tiles: ``{size}`` is filled in.
    EMPTY = "Nothing here for {size} yet."

    # ── The grid ───────────────────────────────────────────────────────

    def refresh(self, *_event: object) -> None:
        """The tiles again: the picked device's, the shown one lit."""
        for tile in self.tiles.values():
            tile.deleteLater()
        self.tiles = {}
        self._flow.place([])        # it must not re-flow the deleted ones
        key = self._selection.key
        say_now = words(self._app)
        if not key:
            self.note.setText(say_now("Pick a device."))
            return
        canvas = self._app.dispatch(DeviceCanvas(key=key))
        if not canvas.ok:
            self.note.setText(say_now("No size known for {key} yet -- connect it first.")
                              .format(key=key))
            return
        size = (canvas.width, canvas.height)
        for tile in self._tiles(key, size):
            self.tiles[tile.id] = self._tile(tile)
        self._flow.place(list(self.tiles.values()))
        log.info("%s.refresh: %s %dx%d -> %d tile(s)", type(self).__name__, key,
                 *size, len(self.tiles))
        self.note.setText("" if self.tiles
                          else say_now(self.EMPTY).format(size=f"{size[0]}x{size[1]}"))
        self._light_current()

    def _tile(self, tile: Tile) -> QToolButton:
        """One tile: its picture over its name."""
        log.debug("%s._tile: %s", type(self).__name__, tile.name)
        button = QToolButton(self._flow.body)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setFixedSize(TILE)
        button.setIcon(thumbnail_icon(Path(tile.picture), PICTURE))
        button.setIconSize(QSize(PICTURE, PICTURE))
        button.setText(tile.name)
        button.setToolTip(tile.name)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_TILE_STYLE)
        button.setProperty(_ID, tile.id)
        button.clicked.connect(self._on_tile)
        return button

    # ── A click, and the light ─────────────────────────────────────────

    def _on_tile(self) -> None:
        """A tile was clicked: its thing goes on the panel -- off the GUI
        thread, since a cloud one downloads first.  The light moves when the
        App says the panel shows it."""
        button = self.sender()
        tile_id = "" if button is None else str(button.property(_ID))
        key = self._selection.key
        log.info("%s._on_tile: %s on %s", type(self).__name__, tile_id, key)
        # Qt lit the clicked tile itself (it is checkable).  Undo that: a
        # tile lights when the App says the panel shows it, so a click that
        # fails never looks like it worked.
        self._light_current()
        if not tile_id or not key:
            return
        self.note.setText("")
        dispatch_in_background(self._app, self._pick(key, tile_id), self._picked)

    def _picked(self, result: Any) -> None:
        """The App's answer to a click: said if it failed; the light, either way."""
        log.info("%s._picked: ok=%s %s", type(self).__name__, result.ok, result.message)
        if not result.ok:
            self.note.setText(result.message)
        self._light_current()

    def _on_device_event(self, event: Any) -> None:
        """A theme loaded, or a device's settings changed -- from any UI:
        light what this device shows now."""
        log.debug("%s._on_device_event: %s", type(self).__name__, type(event).__name__)
        if getattr(event, "key", None) == self._selection.key:
            self._light_current()

    def _light_current(self) -> None:
        """Light the tile the panel shows, as the App says, and no other."""
        key = self._selection.key
        current = self._current(self._app.dispatch(LcdSnapshot(key=key))) if key else None
        log.debug("%s._light_current: %s", type(self).__name__, current)
        for tile_id, button in self.tiles.items():
            button.setChecked(tile_id == current)
