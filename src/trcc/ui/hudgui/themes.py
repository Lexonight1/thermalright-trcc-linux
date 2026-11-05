"""hudgui's Local Themes tab: the device's installed themes as thumbnails.

The gui's behaviour, which is what he asked for ("the same way"): tiles of
120 x 140 -- the theme's picture over its name -- as many to a row as fit
(the gui's area is fixed at five; this one follows the window); one click
loads that theme onto the panel; the theme the panel shows is lit.  The light
follows the APP, never this window's last click, so a theme loaded from any
UI lights here too.

All of it over the Command bus: ``DeviceCanvas`` for the resolution,
``ListThemes`` for the themes, ``LoadTheme`` for a click (a user's pick --
its defaults), ``LcdSnapshot.current_theme`` for which one is showing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QToolButton, QWidget

from ...core.commands import DeviceCanvas, LcdSnapshot, ListThemes, LoadTheme
from ..qt_assets import thumbnail_icon

if TYPE_CHECKING:
    from ...core.events import ThemeLoaded
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection

log = logging.getLogger(__name__)

#: A tile, as the gui's (``Sizes.THUMB_W`` x ``THUMB_H``), and its picture.
TILE = QSize(120, 140)
PICTURE = 110
#: The space between tiles.
SPACING = 10
#: The property a tile carries: its theme's folder.
_PATH = "hud_theme_path"
_TILE_STYLE = ("QToolButton { color: #dfe5ea; background: rgba(255, 255, 255, 12); "
               "border: 2px solid transparent; border-radius: 6px; font-size: 9pt; }"
               "QToolButton:hover { background: rgba(255, 255, 255, 30); }"
               "QToolButton:checked { border-color: #7fd4ff; "
               "background: rgba(127, 212, 255, 40); }")


class ThemeGrid(QScrollArea):
    """The picked device's installed themes; a click loads one."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("ThemeGrid: built")
        self._app = app
        self._selection = selection
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget "
                           "{ background: transparent; }")
        self._body = QWidget()
        self._grid = QGridLayout(self._body)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(SPACING)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._columns = 1
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setWidget(self._body)
        #: The tiles, by theme folder (resolved).
        self.tiles: dict[Path, QToolButton] = {}
        #: What the grid says when it has no tiles.
        self.note = QLabel("", self._body)
        self.note.setStyleSheet("color: #dfe5ea; font-size: 11pt;")

        selection.changed.connect(self.refresh)
        bus.themes_changed.connect(self.refresh, type=Qt.ConnectionType.QueuedConnection)
        bus.data_installed.connect(self.refresh, type=Qt.ConnectionType.QueuedConnection)
        bus.theme_loaded.connect(self._on_theme_loaded,
                                 type=Qt.ConnectionType.QueuedConnection)
        self.refresh()

    def refresh(self, *_event: object) -> None:
        """The tiles again: the picked device's themes, the shown one lit."""
        for tile in self.tiles.values():
            tile.deleteLater()
        self.tiles = {}
        key = self._selection.key
        if not key:
            self._say("Pick a device to see its themes.")
            return
        canvas = self._app.dispatch(DeviceCanvas(key=key))
        if not canvas.ok:
            self._say(f"No size known for {key} yet -- connect it first.")
            return
        listed = self._app.dispatch(ListThemes(resolution=(canvas.width, canvas.height)))
        for theme in listed.themes:
            self._tile(theme.name, Path(theme.path), Path(theme.preview))
        self._lay_out()
        log.info("ThemeGrid.refresh: %s %dx%d -> %d theme(s)", key, canvas.width,
                 canvas.height, len(self.tiles))
        self._say("" if self.tiles else
                  f"No themes for {canvas.width}x{canvas.height} yet -- they "
                  "download on first connect.")
        self._light_current()

    def _say(self, text: str) -> None:
        """The grid's note -- shown only when it has something to say."""
        log.debug("ThemeGrid._say: %r", text)
        self.note.setText(text)
        self.note.setVisible(bool(text))
        if text:
            self._grid.addWidget(self.note, 0, 0, 1, max(1, self._columns))

    def _fit(self) -> int:
        """How many tiles fit across the area now (at least one)."""
        columns = max(1, (self.viewport().width() + SPACING) // (TILE.width() + SPACING))
        log.debug("ThemeGrid._fit: %d px -> %d", self.viewport().width(), columns)
        return columns

    def _lay_out(self) -> None:
        """Place the tiles, in order, as many to a row as fit."""
        self._columns = self._fit()
        log.debug("ThemeGrid._lay_out: %d tile(s), %d to a row", len(self.tiles),
                  self._columns)
        for index, tile in enumerate(self.tiles.values()):
            self._grid.addWidget(tile, index // self._columns, index % self._columns)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """A new width: the tiles flow again, if a different number fit."""
        super().resizeEvent(event)
        if self._fit() != self._columns:
            log.debug("ThemeGrid.resizeEvent: %s -> re-flow", event.size().toTuple())
            self._lay_out()

    def _tile(self, name: str, path: Path, preview: Path) -> QToolButton:
        """One theme: its picture over its name."""
        log.debug("ThemeGrid._tile: %s", name)
        tile = QToolButton(self._body)
        tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        tile.setFixedSize(TILE)
        tile.setIcon(thumbnail_icon(preview, PICTURE))
        tile.setIconSize(QSize(PICTURE, PICTURE))
        tile.setText(name)
        tile.setToolTip(name)
        tile.setCheckable(True)
        tile.setCursor(Qt.CursorShape.PointingHandCursor)
        tile.setStyleSheet(_TILE_STYLE)
        tile.setProperty(_PATH, str(path))
        tile.clicked.connect(self._on_tile)
        self.tiles[path.resolve()] = tile
        return tile

    def _on_tile(self) -> None:
        """A tile was clicked: its theme loads onto the panel -- the light
        moves when the App says it showed it."""
        tile = self.sender()
        path = Path(str(tile.property(_PATH))) if tile is not None else None
        key = self._selection.key
        log.info("ThemeGrid._on_tile: %s on %s", path, key)
        if path is None or not key:
            return
        result = self._app.dispatch(LoadTheme(key=key, path=path))
        if not result.ok:
            log.warning("ThemeGrid._on_tile: %s", result.message)
        self._light_current()

    def _on_theme_loaded(self, event: ThemeLoaded) -> None:
        """Any UI loaded a theme: light it, if it is this device's."""
        log.debug("ThemeGrid._on_theme_loaded: %s %s", event.key, event.theme_name)
        if event.key == self._selection.key:
            self._light_current()

    def _light_current(self) -> None:
        """Light the theme the panel shows, as the App says, and no other."""
        key = self._selection.key
        current = self._app.dispatch(LcdSnapshot(key=key)).current_theme if key else None
        shown = Path(current).resolve() if current else None
        log.debug("ThemeGrid._light_current: %s", shown)
        for path, tile in self.tiles.items():
            tile.setChecked(path == shown)
