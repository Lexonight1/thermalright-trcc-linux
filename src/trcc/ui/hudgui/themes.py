"""hudgui's device panel tabs of tiles: Local Themes, Cloud Masks, Cloud
Backgrounds -- each the shared ``TileGrid`` saying only what differs.

The calls are the Command bus's (``doc/REFERENCE_COMMANDS.md``), the look and
behaviour the gui's: one click puts it on the panel; the one shown is lit.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ...core.commands import (
    ApplyMask,
    ListCloudThemes,
    ListMasks,
    ListThemes,
    LoadCloudTheme,
    LoadTheme,
)
from .tiles import Tile, TileGrid

if TYPE_CHECKING:
    from ...core.commands import Command
    from ...core.ports import CommandBus
    from ...core.results import LcdSnapshotResult
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection

log = logging.getLogger(__name__)

#: The property a category button carries: the category it shows.
_CATEGORY = "hud_cloud_category"
_PILL_STYLE = ("QPushButton { color: #dfe5ea; background: rgba(255, 255, 255, 18); "
               "border: 1px solid rgba(255, 255, 255, 40); border-radius: 11px; "
               "padding: 3px 12px; }"
               "QPushButton:checked { color: #031520; background: #a9e6ff; }")


def _resolved(path: str | None) -> str | None:
    """*path* as one spelling of it, so a tile and the App's answer compare."""
    found = str(Path(path).resolve()) if path else None
    log.debug("_resolved: %s -> %s", path, found)
    return found


class LocalThemes(TileGrid):
    """The device's installed themes; a click loads one (``LoadTheme`` -- a
    user's pick, its defaults)."""

    EMPTY = "No themes for {size} yet -- they download on first connect."

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(app, bus, selection, parent)
        log.debug("LocalThemes: re-listing on ThemesChanged too")
        bus.themes_changed.connect(self.refresh, type=Qt.ConnectionType.QueuedConnection)

    def _tiles(self, key: str, size: tuple[int, int]) -> list[Tile]:
        listed = self._app.dispatch(ListThemes(resolution=size))
        log.debug("LocalThemes._tiles: %d", len(listed.themes))
        return [Tile(_resolved(t.path) or t.path, t.name, t.preview) for t in listed.themes]

    def _pick(self, key: str, tile_id: str) -> Command[Any]:
        log.debug("LocalThemes._pick: %s", tile_id)
        return LoadTheme(key=key, path=Path(tile_id))

    def _current(self, snapshot: LcdSnapshotResult) -> str | None:
        log.debug("LocalThemes._current: %s", snapshot.current_theme)
        return _resolved(snapshot.current_theme)


class CloudMasks(TileGrid):
    """The masks for the device's size; a click lays one over the theme
    (``ApplyMask``)."""

    EMPTY = "No masks for {size} yet -- they download on first connect."

    def _tiles(self, key: str, size: tuple[int, int]) -> list[Tile]:
        listed = self._app.dispatch(ListMasks(resolution=size, key=key))
        log.debug("CloudMasks._tiles: %d", len(listed.masks))
        return [Tile(_resolved(m.path) or m.path, m.name, m.preview) for m in listed.masks]

    def _pick(self, key: str, tile_id: str) -> Command[Any]:
        log.debug("CloudMasks._pick: %s", tile_id)
        return ApplyMask(key=key, path=Path(tile_id))

    def _current(self, snapshot: LcdSnapshotResult) -> str | None:
        """The App names the mask's FILE (``.../002a/01.png``); a tile is its
        folder -- so the folder is what is lit."""
        mask = Path(snapshot.mask_path) if snapshot.mask_path else None
        folder = mask.parent if mask is not None and mask.suffix else mask
        log.debug("CloudMasks._current: %s -> %s", snapshot.mask_path, folder)
        return _resolved(str(folder)) if folder is not None else None


class CloudBackgrounds(TileGrid):
    """Thermalright's hosted backgrounds, by category; a click downloads one
    and puts it on the panel (``LoadCloudTheme``)."""

    EMPTY = "No cloud backgrounds listed for {size}."

    def _header(self) -> QWidget:
        """The categories: All, then the catalog's own, one lit."""
        log.debug("CloudBackgrounds._header")
        self._category = "all"
        self._pills: dict[str, QPushButton] = {}
        bar = QWidget(self)
        self._pill_row = QHBoxLayout(bar)
        self._pill_row.setContentsMargins(0, 0, 0, 0)
        self._pill_row.setSpacing(6)
        self._pill_row.addStretch(1)
        return bar

    def _pill(self, prefix: str, name: str) -> None:
        """One category button, before the row's stretch."""
        log.debug("CloudBackgrounds._pill: %s %s", prefix, name)
        pill = QPushButton(name, self.header)
        pill.setCheckable(True)
        pill.setCursor(Qt.CursorShape.PointingHandCursor)
        pill.setStyleSheet(_PILL_STYLE)
        pill.setProperty(_CATEGORY, prefix)
        pill.clicked.connect(self._on_category)
        self._pill_row.insertWidget(self._pill_row.count() - 1, pill)
        self._pills[prefix] = pill

    def _on_category(self) -> None:
        """A category was clicked: its backgrounds only."""
        pill = self.sender()
        self._category = "all" if pill is None else str(pill.property(_CATEGORY))
        log.info("CloudBackgrounds._on_category: %s", self._category)
        self.refresh()

    def _tiles(self, key: str, size: tuple[int, int]) -> list[Tile]:
        listed = self._app.dispatch(ListCloudThemes(category=self._category,
                                                    resolution=size))
        if not self._pills:
            self._pill("all", "All")
            for category in listed.categories:
                self._pill(category.prefix, category.name)
        for prefix, pill in self._pills.items():
            pill.setChecked(prefix == self._category)
        log.debug("CloudBackgrounds._tiles: %s -> %d", self._category, len(listed.themes))
        return [Tile(t.id, t.id, t.preview) for t in listed.themes]

    def _pick(self, key: str, tile_id: str) -> Command[Any]:
        log.debug("CloudBackgrounds._pick: %s", tile_id)
        return LoadCloudTheme(key=key, theme_id=tile_id)

    def _current(self, snapshot: LcdSnapshotResult) -> str | None:
        """A cloud background is the file named for its id."""
        found = Path(snapshot.background_file).stem if snapshot.background_file else None
        log.debug("CloudBackgrounds._current: %s", found)
        return found
