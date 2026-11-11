"""The Audio Visualizer's page -- one page, which every GUI shows.

The gui's audio visuals tab was the blueprint (his rule, 2026-11-10: a GUI is
only a skin): the same grid and tiles as the theme tabs (``ui/tile_grid``),
one tile per visual (and per preset), each the App's picture of what that
visual draws for noise (``VisualTile``), so a tile looks the same whatever is
playing.  One click plays it on the LCD the GUI works on; picking any other
background ends it.  Under the tiles, the playing visual's sliders.

Each GUI hosts this page and hands it its look (``ModuleLook``); what it does
is written here once.  It replaced a gui page and a qtgui panel that had
drifted apart -- the gui never showed a refused Play, qtgui spoke only
English, played on a double-click and kept a stale selection.

The tiles are drawn by the App, one at a time and off the GUI thread, so the
window never waits on a visual.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

from trcc.core.events import BackgroundChanged
from trcc.core.i18n import tr
from trcc.ui.module_page import ModuleLook, ModulePage
from trcc.ui.qt_background import dispatch_in_background
from trcc.ui.tile_grid import BROWSER_W, TILE_IMAGE, BaseThemeBrowser, BaseThumbnail

from ...core.commands import (
    ListVisuals,
    PlayVisual,
    SetVisualCenter,
    SetVisualPicture,
    SetVisualSettings,
    VisualTile,
)
from ...core.events import VisualHearingChanged
from ...core.results import VisualsResult
from ..presentation.visual_gallery import VisualItem, gallery_items
from ..visual_controls import VisualControls
from ..visual_tiles import VisualTiles

if TYPE_CHECKING:
    from PySide6.QtGui import QShowEvent
    from PySide6.QtWidgets import QWidget

    from trcc.core.ports import CommandBus

log = logging.getLogger(__name__)

#: The module's own images, its tab among them.
_ASSETS = Path(__file__).resolve().parents[2] / "assets"

#: The split within the 732x652 page: the previews scroll on top, the
#: selected visual's settings sit below at the height THEY need (a hint
#: alone, four sliders, six) -- the previews get everything else.
_TOP, _BOTTOM, _MARGIN = 50, 640, 24
#: What went wrong, right of the title the GUI draws at the top left.
_STATUS = (190, 24, 518, 22)


class VisualThumbnail(BaseThumbnail):
    """A visual's tile: its name until the App's picture of it arrives.  A
    visual that cannot play here says why in its tooltip, its name dimmed;
    a click still asks, and the App's refusal reaches the user."""

    def __init__(self, item_info: VisualItem, look: ModuleLook,
                 parent: QWidget | None = None) -> None:
        super().__init__(item_info, look.tiles, parent)
        log.debug("VisualThumbnail.__init__: %s problem=%r", item_info.name,
                  item_info.problem)
        if item_info.problem:
            self.setToolTip(item_info.problem)
            self.name_label.setStyleSheet(
                f"{self.name_label.styleSheet()} color: {look.dimmed};")

    def _get_display_name(self, info: VisualItem) -> str:
        log.debug("VisualThumbnail._get_display_name: %s", info.name)
        return info.name

    def _get_image_path(self, info: VisualItem) -> str | None:
        """None: the picture comes from the App, not a file."""
        log.debug("VisualThumbnail._get_image_path: %s draws its own", info.name)
        return None

    def show_tile(self, png: bytes) -> None:
        """The App's picture of this visual, in place of the placeholder."""
        image = QImage.fromData(png)
        if image.isNull():
            log.warning("VisualThumbnail.show_tile: %s -- not a picture "
                        "(%d bytes)", self.item_info.name, len(png))
            return
        log.debug("VisualThumbnail.show_tile: %s", self.item_info.name)
        self.thumb_label.setPixmap(QPixmap.fromImage(image.scaled(
            TILE_IMAGE, TILE_IMAGE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)))


class VisualsPage(BaseThemeBrowser, ModulePage):
    """The Audio visuals gallery, and the controls of the visual the LCD
    plays.  A clicked :class:`VisualItem` (``theme_selected``) plays on it."""

    TAB_IMAGE = str(_ASSETS / "tab.png")
    TAB_IMAGE_ACTIVE = str(_ASSETS / "tab_active.png")
    TOOLTIP = TITLE = "Audio visuals"
    EVENTS = (VisualHearingChanged,)

    def __init__(self, parent: QWidget | None = None, *, app: CommandBus,
                 look: ModuleLook) -> None:
        log.info("VisualsPage.__init__")
        self._app = app
        self._look = look
        self._tr: Callable[[str], str] = str
        self._key = ""
        self._listed = False
        self._tiles = VisualTiles(app, self._tile_query, self._show_tile)
        super().__init__(parent, skin=look.tiles)

    def _setup_ui(self) -> None:
        """The gallery's grid, the settings below it, and the status line."""
        log.debug("VisualsPage._setup_ui")
        super()._setup_ui()
        self.scroll_area.setGeometry(0, _TOP, BROWSER_W, _BOTTOM - _TOP)
        self._controls = VisualControls(self._app, self._sliders_command,
                                        self._center_command,
                                        self._picture_command, self)
        self._controls.setStyleSheet(self._look.controls)
        self._status = QLabel(self)
        self._status.setGeometry(*_STATUS)
        self._status.setAlignment(Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
        self._status.setStyleSheet(self._look.status)
        self._status.hide()
        self._fit_controls()
        self.theme_selected.connect(self._on_play)

    def _say(self, message: str) -> None:
        """*message* on the status line -- hidden when there is none."""
        log.debug("VisualsPage._say: %r", message)
        self._status.setText(message)
        self._status.setVisible(bool(message))

    def _on_play(self, item: VisualItem) -> None:
        """A tile was clicked: it plays on the LCD -- the App plays it;
        picking any other background ends it.  A refusal shows here.

        No ``settings``: a click starts the visual at its default sliders --
        one click, as the user asked (2026-10-12) -- and the sliders below
        then move it live (``SetVisualSettings``)."""
        log.info("VisualsPage._on_play: %s %s %r", self._key, item.visual,
                 item.preset)
        if not self._key:
            log.warning("VisualsPage._on_play: no LCD to play on")
            self._say(self._tr("Pick an LCD first"))
            return
        result = self._app.dispatch(PlayVisual(key=self._key, visual=item.visual,
                                               preset=item.preset))
        if not result.ok:
            log.warning("VisualsPage._on_play: %s", result.message)
        self._say("" if result.ok else result.message)

    def set_language(self, lang: str) -> None:
        """Its words, and its sliders', in the GUI's language."""
        log.info("VisualsPage.set_language: %s", lang)
        self._tr = partial(tr, lang=lang, module="visualizer")
        self._controls.set_translate(self._tr)
        self._fit_controls()

    def set_device_key(self, key: str) -> None:
        """The LCD the GUI works on -- its visual's controls show."""
        log.info("VisualsPage.set_device_key: %s -> %s", self._key, key)
        self._key = key
        self._say("")
        if self._listed:
            self._ask_playing()

    def on_settings_changed(self, event: object) -> None:
        """Another UI -- or this one -- changed the LCD's background, or its
        visual started or stopped hearing sound: show what it plays now."""
        if (isinstance(event, (BackgroundChanged, VisualHearingChanged))
                and event.key == self._key and self._listed):
            log.debug("VisualsPage.on_settings_changed: %s", event.key)
            self._ask_playing()

    def _create_thumbnail(self, item_info: VisualItem) -> VisualThumbnail:
        log.debug("VisualsPage._create_thumbnail: %s", item_info.name)
        return VisualThumbnail(item_info, self._look)

    def _no_items_message(self) -> str:
        log.debug("VisualsPage._no_items_message")
        return self._tr("No audio visuals on this computer")

    def showEvent(self, event: QShowEvent) -> None:
        """The gallery is listed the first time it is shown."""
        log.debug("VisualsPage.showEvent: listed=%s", self._listed)
        super().showEvent(event)
        if not self._listed:
            self.refresh()

    def refresh(self) -> None:
        """Ask the App for the visuals; the tiles follow, one at a time."""
        log.info("VisualsPage.refresh")
        self._listed = True
        dispatch_in_background(self._app, ListVisuals(key=self._key),
                               self._on_listed)

    def _on_listed(self, listed: VisualsResult) -> None:
        log.info("VisualsPage._on_listed: ok=%s %s", listed.ok, listed.message)
        self._clear_grid()
        items = gallery_items(listed) if listed.ok else []
        self._populate_grid(items)
        self._tiles.fetch(items)
        self._say("" if listed.ok else listed.message)
        self._on_playing(listed)

    def _ask_playing(self) -> None:
        """What the LCD plays -- for the controls and the tile."""
        log.debug("VisualsPage._ask_playing: %s", self._key)
        dispatch_in_background(self._app, ListVisuals(key=self._key),
                               self._on_playing)

    def _on_playing(self, listed: VisualsResult) -> None:
        """Its tile selected -- none when nothing plays -- its controls shown."""
        log.debug("VisualsPage._on_playing: %s %r", listed.playing, listed.preset)
        playing = next((w.item_info for w in self.item_widgets
                        if isinstance(w, VisualThumbnail)
                        and (w.item_info.visual, w.item_info.preset)
                        == (listed.playing, listed.preset)), None)
        self._select_item(playing)
        self._controls.show_for(listed)
        self._fit_controls()

    def _fit_controls(self) -> None:
        """The settings at the height they need, at the bottom; the
        previews above take the rest."""
        controls = self._controls
        # Styled first (a GUI's padding makes rows taller), then laid out
        # afresh, THEN measured -- else the height is too short.
        controls.ensurePolished()
        if (layout := controls.layout()) is not None:
            layout.invalidate()
            layout.activate()
        height = controls.sizeHint().height()
        top = _BOTTOM - height
        controls.setGeometry(_MARGIN, top, BROWSER_W - 2 * _MARGIN, height)
        self.scroll_area.setGeometry(0, _TOP, BROWSER_W, top - 12 - _TOP)
        log.debug("VisualsPage._fit_controls: settings %d px, previews %d px",
                  height, top - 12 - _TOP)

    def _sliders_command(self, values: dict[str, float]) -> SetVisualSettings:
        """The Command for new slider values, for the LCD."""
        log.debug("VisualsPage._sliders_command: %s %s", self._key, values)
        return SetVisualSettings(key=self._key, settings=values)

    def _picture_command(self, shown: bool | None,
                         fire: float | None) -> SetVisualPicture:
        """The Command for the centre picture on or off, and its fire."""
        log.debug("VisualsPage._picture_command: shown=%s fire=%s", shown, fire)
        return SetVisualPicture(shown=shown, fire=fire)

    def _center_command(self, path: str) -> SetVisualCenter:
        """The Command for a new centre picture ("" for none)."""
        log.debug("VisualsPage._center_command: %r", path)
        return SetVisualCenter(path=path)

    def _tile_query(self, item: VisualItem) -> VisualTile:
        """The Query for *item*'s tile, at the grid's tile size."""
        log.debug("VisualsPage._tile_query: %s", item.name)
        return VisualTile(visual=item.visual, preset=item.preset, size=TILE_IMAGE)

    def _show_tile(self, item: VisualItem, png: bytes) -> None:
        """A tile arrived from the App: onto its thumbnail."""
        log.debug("VisualsPage._show_tile: %s", item.name)
        for widget in self.item_widgets:
            if isinstance(widget, VisualThumbnail) and widget.item_info == item:
                widget.show_tile(png)
