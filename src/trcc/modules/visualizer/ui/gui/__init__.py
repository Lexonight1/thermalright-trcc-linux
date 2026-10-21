"""The Audio Visualizer's gui page -- the audio bars tab, the first module
tab slot, right of Cloud backgrounds.  Exported as ``PAGE``.

The same grid and tiles as the cloud backgrounds: one tile per visual (and
per preset), each the App's picture of what that visual draws for noise
(``VisualTile``), so a tile looks the same whatever is playing.  Clicking
one makes it the panel's background; picking any other background ends it.

The tiles are drawn by the App, one at a time and off the GUI thread, so
the window never waits on a visual.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from trcc.core.events import BackgroundChanged
from trcc.ui.gui.base import BaseThemeBrowser, BaseThumbnail
from trcc.ui.gui.constants import Colors, Sizes, Styles
from trcc.ui.gui.module_page import ModulePage
from trcc.ui.qt_background import dispatch_in_background

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

    from trcc.core.ports import CommandBus

log = logging.getLogger(__name__)

#: The module's own images, its tab among them.
_ASSETS = Path(__file__).resolve().parents[2] / "assets"


class VisualThumbnail(BaseThumbnail):
    """A visual's tile: its name until the App's picture of it arrives.  A
    visual that cannot play here says why in its tooltip, its name dimmed;
    a click still asks, and the App's refusal reaches the user."""

    def __init__(self, item_info: VisualItem, parent=None) -> None:
        super().__init__(item_info, parent)
        log.debug("VisualThumbnail.__init__: %s problem=%r", item_info.name,
                  item_info.problem)
        if item_info.problem:
            self.setToolTip(item_info.problem)
            self.name_label.setStyleSheet(
                f"{self.name_label.styleSheet()} color: {Colors.EMPTY_TEXT};")

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
            Sizes.THUMB_IMAGE, Sizes.THUMB_IMAGE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)))


#: The user's split, within the 732x652 panel: the previews scroll on top,
#: the selected visual's settings sit below at the height THEY need (a hint
#: alone, four sliders, six) -- the previews get everything else.
_TOP, _BOTTOM, _MARGIN = 50, 640, 24
#: The controls in the classic skin's colours; the groove shows on the dark
#: panel, which the shared slider style leaves transparent.
_CONTROLS_STYLE = (
    f"QLabel {{ color: {Colors.TEXT}; background: transparent; }}"
    f"QPushButton {{ background: {Colors.BUTTON_BG}; color: "
    f"{Colors.BUTTON_TEXT}; border: 1px solid {Colors.HOVER_BORDER}; "
    f"padding: 4px 12px; }}"
    + Styles.SLIDER
    + f"QSlider::groove:horizontal {{ background: {Colors.BUTTON_BG}; "
      f"height: 6px; border-radius: 3px; }}")


class UCVisuals(BaseThemeBrowser, ModulePage):
    """The Audio visuals gallery, and the controls of the visual the active
    panel plays.  A clicked :class:`VisualItem` (``theme_selected``) plays
    on the active panel."""

    TAB_IMAGE = str(_ASSETS / "tab.png")
    TAB_IMAGE_ACTIVE = str(_ASSETS / "tab_active.png")
    TOOLTIP = TITLE = "Audio visuals"
    EVENTS = (VisualHearingChanged,)

    def __init__(self, parent=None, app: CommandBus | None = None) -> None:
        log.info("UCVisuals.__init__: app=%s", app is not None)
        self._app = app
        self._key = ""
        self._listed = False
        self._tiles = (VisualTiles(app, self._tile_query, self._show_tile)
                       if app is not None else None)
        self._controls: VisualControls | None = None
        super().__init__(parent)

    def _setup_ui(self) -> None:
        """The cloud gallery's grid, with the settings below it."""
        log.debug("UCVisuals._setup_ui")
        super()._setup_ui()
        self.scroll_area.setGeometry(0, _TOP, 732, _BOTTOM - _TOP)
        if self._app is not None:
            self._controls = VisualControls(self._app, self._sliders_command,
                                            self._center_command,
                                            self._picture_command, self)
            self._controls.setStyleSheet(_CONTROLS_STYLE)
            self._fit_controls()
        self.theme_selected.connect(self._on_play)

    def _on_play(self, item: VisualItem) -> None:
        """A tile was clicked: it plays on the active panel -- the App plays
        it; picking any other background ends it."""
        log.info("UCVisuals._on_play: %s %s %r", self._key, item.visual,
                 item.preset)
        if self._app is None or not self._key:
            log.warning("UCVisuals._on_play: no panel to play on -- dropped")
            return
        result = self._app.dispatch(PlayVisual(key=self._key, visual=item.visual,
                                               preset=item.preset))
        if not result.ok:
            log.warning("UCVisuals._on_play: %s", result.message)

    def set_device_key(self, key: str) -> None:
        """The panel the window works on -- its visual's controls show."""
        log.info("UCVisuals.set_device_key: %s -> %s", self._key, key)
        self._key = key
        if self._listed:
            self._ask_playing()

    def on_settings_changed(self, event: object) -> None:
        """Another UI -- or this one -- changed the active panel's
        background, or its visual started or stopped hearing sound: show
        what it plays now."""
        if (isinstance(event, (BackgroundChanged, VisualHearingChanged))
                and event.key == self._key and self._listed):
            log.debug("UCVisuals.on_settings_changed: %s", event.key)
            self._ask_playing()

    def _create_thumbnail(self, item_info: VisualItem) -> VisualThumbnail:
        log.debug("UCVisuals._create_thumbnail: %s", item_info.name)
        return VisualThumbnail(item_info)

    def _no_items_message(self) -> str:
        log.debug("UCVisuals._no_items_message")
        return "No audio visuals on this computer"

    def showEvent(self, event: QShowEvent) -> None:
        """The gallery is listed the first time it is shown."""
        log.debug("UCVisuals.showEvent: listed=%s", self._listed)
        super().showEvent(event)
        if not self._listed:
            self.refresh()

    def refresh(self) -> None:
        """Ask the App for the visuals; the tiles follow, one at a time."""
        if self._app is None:
            log.warning("UCVisuals.refresh: no App -- nothing to list")
            return
        log.info("UCVisuals.refresh")
        self._listed = True
        dispatch_in_background(self._app, ListVisuals(key=self._key),
                               self._on_listed)

    def _on_listed(self, listed: VisualsResult) -> None:
        log.info("UCVisuals._on_listed: ok=%s %s", listed.ok, listed.message)
        self._clear_grid()
        items = gallery_items(listed) if listed.ok else []
        self._populate_grid(items)
        if self._tiles is not None:
            self._tiles.fetch(items)
        self._on_playing(listed)

    def _ask_playing(self) -> None:
        """What the active panel plays -- for the controls and the tile."""
        if self._app is None:
            log.debug("UCVisuals._ask_playing: no App")
            return
        log.debug("UCVisuals._ask_playing: %s", self._key)
        dispatch_in_background(self._app, ListVisuals(key=self._key),
                               self._on_playing)

    def _on_playing(self, listed: VisualsResult) -> None:
        """Its tile selected, its controls shown."""
        log.debug("UCVisuals._on_playing: %s %r", listed.playing, listed.preset)
        playing = next((w.item_info for w in self.item_widgets
                        if isinstance(w, VisualThumbnail)
                        and (w.item_info.visual, w.item_info.preset)
                        == (listed.playing, listed.preset)), None)
        self._select_item(playing)
        if self._controls is not None:
            self._controls.show_for(listed)
            self._fit_controls()

    def _fit_controls(self) -> None:
        """The settings at the height they need, at the bottom; the
        previews above take the rest."""
        controls = self._controls
        if controls is None:
            log.debug("UCVisuals._fit_controls: no controls")
            return
        # Styled first (the classic skin's padding makes rows taller), then
        # laid out afresh, THEN measured -- else the height is too short.
        controls.ensurePolished()
        if (layout := controls.layout()) is not None:
            layout.invalidate()
            layout.activate()
        height = controls.sizeHint().height()
        top = _BOTTOM - height
        controls.setGeometry(_MARGIN, top, 732 - 2 * _MARGIN, height)
        self.scroll_area.setGeometry(0, _TOP, 732, top - 12 - _TOP)
        log.debug("UCVisuals._fit_controls: settings %d px, previews %d px",
                  height, top - 12 - _TOP)

    def _sliders_command(self, values: dict[str, float]) -> SetVisualSettings:
        """The Command for new slider values, for the active panel."""
        log.debug("UCVisuals._sliders_command: %s %s", self._key, values)
        return SetVisualSettings(key=self._key, settings=values)

    def _picture_command(self, shown: bool | None,
                         fire: float | None) -> SetVisualPicture:
        """The Command for the centre picture on or off, and its fire."""
        log.debug("UCVisuals._picture_command: shown=%s fire=%s", shown, fire)
        return SetVisualPicture(shown=shown, fire=fire)

    def _center_command(self, path: str) -> SetVisualCenter:
        """The Command for a new centre picture ("" for none)."""
        log.debug("UCVisuals._center_command: %r", path)
        return SetVisualCenter(path=path)

    def _tile_query(self, item: VisualItem) -> VisualTile:
        """The Query for *item*'s tile, at this grid's tile size."""
        log.debug("UCVisuals._tile_query: %s", item.name)
        return VisualTile(visual=item.visual, preset=item.preset,
                          size=Sizes.THUMB_IMAGE)

    def _show_tile(self, item: VisualItem, png: bytes) -> None:
        """A tile arrived from the App: onto its thumbnail."""
        log.debug("UCVisuals._show_tile: %s", item.name)
        for widget in self.item_widgets:
            if isinstance(widget, VisualThumbnail) and widget.item_info == item:
                widget.show_tile(png)


PAGE = UCVisuals
