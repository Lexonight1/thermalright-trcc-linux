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
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QToolButton,
    QWidget,
)

from ...core.commands import (
    ApplyMask,
    DeleteTheme,
    ExportCurrentTheme,
    ImportTheme,
    ListCloudThemes,
    ListMasks,
    ListThemes,
    LoadCloudTheme,
    LoadTheme,
    SaveTheme,
)
from ..qt_words import say, say_tip
from .tiles import TILE, Tile, TileGrid
from .words import words

if TYPE_CHECKING:
    from ...core.commands import Command
    from ...core.ports import CommandBus
    from ...core.results import LcdSnapshotResult
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection

log = logging.getLogger(__name__)

#: The property a tile's ✕ carries: the theme it removes.
_THEME = "hud_theme_path"
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
    user's pick, its defaults).  Above them, as the gui has them: save what
    the panel shows, import a ``.tr``, export what it shows; on each of the
    user's OWN themes a ✕ removes it, after asking."""

    EMPTY = "No themes for {size} yet -- they download on first connect."

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(app, bus, selection, parent)
        log.debug("LocalThemes: re-listing on ThemesChanged too")
        bus.themes_changed.connect(self.refresh, type=Qt.ConnectionType.QueuedConnection)

    def _header(self) -> QWidget:
        """Save as…, Import…, Export…"""
        log.debug("LocalThemes._header")
        bar = QWidget(self)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        for text, slot in (("Save As", self._on_save), ("Import…", self._on_import),
                           ("Export…", self._on_export)):
            button = say(QPushButton(bar), text)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        return bar

    def _tiles(self, key: str, size: tuple[int, int]) -> list[Tile]:
        listed = self._app.dispatch(ListThemes(resolution=size))
        log.debug("LocalThemes._tiles: %d", len(listed.themes))
        return [Tile(_resolved(t.path) or t.path, t.name, t.preview,
                     removable=t.origin == "user") for t in listed.themes]

    def _tile(self, tile: Tile) -> QToolButton:
        """The base tile, and a ✕ on the user's own theme, as the gui's."""
        button = super()._tile(tile)
        log.debug("LocalThemes._tile: %s removable=%s", tile.name, tile.removable)
        if tile.removable:
            cross = QToolButton(button)
            cross.setText("✕")
            say_tip(cross, "Delete this theme")
            cross.setGeometry(TILE.width() - 24, 4, 20, 20)
            cross.setProperty(_THEME, tile.id)
            cross.clicked.connect(self._on_remove)
        return button

    # ── What a person asks -- methods, so a test answers ───────────────

    def ask_name(self) -> str:
        """The name to save the panel's look as, or ""."""
        log.debug("LocalThemes.ask_name")
        say_now = words(self._app)
        name, ok = QInputDialog.getText(self, say_now("Save theme"), say_now("Name:"))
        return name.strip() if ok else ""

    def confirm(self, question: str) -> bool:
        """Yes or no."""
        log.debug("LocalThemes.confirm: %s", question)
        answer = QMessageBox.question(self, words(self._app)("Themes"), question)
        return answer == QMessageBox.StandardButton.Yes

    def choose_archive(self, saving: bool) -> str:
        """A ``.tr`` theme file to write (*saving*) or read, or ""."""
        log.debug("LocalThemes.choose_archive: saving=%s", saving)
        pick = QFileDialog.getSaveFileName if saving else QFileDialog.getOpenFileName
        path, _ = pick(self, words(self._app)("Theme file"), "", "TRCC themes (*.tr)")
        return path

    # ── The actions ────────────────────────────────────────────────────

    def _on_save(self) -> None:
        """Save what the panel shows -- asking first before replacing one."""
        key, name = self._selection.key, self.ask_name()
        log.info("LocalThemes._on_save: %s as %r", key, name)
        if not key or not name:
            return
        result = self._app.dispatch(SaveTheme(key=key, name=name))
        question = words(self._app)("A theme named '{name}' already exists.  Replace it?")
        if not result.ok and getattr(result, "target_exists", False) and self.confirm(
                question.format(name=name)):
            result = self._app.dispatch(SaveTheme(key=key, name=name, overwrite=True))
        self.note.setText("" if result.ok else result.message)

    def _on_import(self) -> None:
        key, path = self._selection.key, self.choose_archive(saving=False)
        log.info("LocalThemes._on_import: %s <- %s", key, path)
        if key and path:
            result = self._app.dispatch(ImportTheme(key=key, archive_path=Path(path)))
            self.note.setText("" if result.ok else result.message)

    def _on_export(self) -> None:
        """What the panel shows, saved or not, to a ``.tr``."""
        key, path = self._selection.key, self.choose_archive(saving=True)
        log.info("LocalThemes._on_export: %s -> %s", key, path)
        if key and path:
            result = self._app.dispatch(ExportCurrentTheme(key=key, archive_path=Path(path)))
            self.note.setText(result.message)

    def _on_remove(self) -> None:
        """A ✕: the user's theme goes, after asking."""
        cross = self.sender()
        path = "" if cross is None else str(cross.property(_THEME))
        log.info("LocalThemes._on_remove: %s", path)
        question = words(self._app)("Delete the theme '{name}'?  This cannot be undone.")
        if path and self.confirm(question.format(name=Path(path).name)):
            result = self._app.dispatch(DeleteTheme(path=Path(path)))
            self.note.setText("" if result.ok else result.message)

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
        pill = say(QPushButton(self.header), name)
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
