"""The Audio Visualizer's page -- one page, which every GUI shows.

The gui's audio visuals tab was the blueprint (his rule, 2026-11-10: a GUI is
only a skin): the same grid and tiles as the theme tabs (``ui/tile_grid``),
one tile per visual (and per preset), each the App's picture of what that
visual draws for noise (``VisualTile``), so a tile looks the same whatever is
playing.  One click plays it on the LCD the GUI works on; picking any other
background ends it.  Under the tiles, where it plays -- the whole panel, or a
box in a corner over the theme (``SetInset``) -- and the playing visual's
sliders.  The box used to be a button and a window on the Background panel,
where the visual could not be picked (his ask, 2026-11-10: "maybe it should
be in the module").

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

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from trcc.core.commands import ClearInset, ClearModuleSource, LcdSnapshot, SetInset
from trcc.core.events import BackgroundChanged
from trcc.core.i18n import tr
from trcc.core.models import InsetCorner, InsetSize
from trcc.core.results import LcdSnapshotResult, Result
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
#: The module whose box this page sets.
_MODULE = "visualizer"
#: How each corner and each size is said.
_CORNERS = {InsetCorner.TOP_LEFT: "Top left", InsetCorner.TOP_RIGHT: "Top right",
            InsetCorner.BOTTOM_LEFT: "Bottom left",
            InsetCorner.BOTTOM_RIGHT: "Bottom right"}
_SIZES = {InsetSize.SMALL: "Small", InsetSize.MEDIUM: "Medium",
          InsetSize.LARGE: "Large"}
#: Where the visual plays, and how each is said -- in the row's order.
_OFF, _BACKGROUND, _BOX = "off", "background", "box"
_WHERE = {_OFF: "Off", _BACKGROUND: "Background", _BOX: "Box"}
#: The property a where-button carries: which it is.
_WHERE_PROPERTY = "visualizer_where"
#: The line that says no visual was ever picked -- the page's own hint.
_PICK_ONE = "Pick an audio visual above -- its sliders show here."
#: Where a pick keeps its English, to say again in another language.
_ENGLISH = Qt.ItemDataRole.UserRole + 1


def _no_visuals(message: str) -> VisualsResult:
    """The answer when the App could not be asked -- a ``VisualsResult``, so
    the page reads it like any other (a plain ``Result`` has no ``playing``:
    an App closed mid-ask raised AttributeError on the GUI thread, 2026-11-10)."""
    log.debug("_no_visuals: %s", message)
    return VisualsResult(ok=False, message=message)


def _no_snapshot(message: str) -> LcdSnapshotResult:
    """The App could not be asked where the box is: a snapshot that says so."""
    log.debug("_no_snapshot: %s", message)
    return LcdSnapshotResult(ok=False, message=message)


class PlacementRow(QWidget):
    """Where the visual plays -- Off, Background (the whole panel) or Box (a
    corner of it, over the theme, which plays on under it) -- one of three,
    lit; the box's corner and size, picked while it is the box.  Says what the
    App holds (:meth:`light`); a choice the USER makes emits :attr:`chosen`.

    Three, not a switch: he asked for "box or background or off" (2026-11-10)
    once a box switch's off put the visual back over the background."""

    chosen = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.debug("PlacementRow.__init__")
        self.buttons = QButtonGroup(self)
        self.buttons.setExclusive(True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for where, english in _WHERE.items():
            button = QPushButton(english, self)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty(_WHERE_PROPERTY, where)
            self.buttons.addButton(button)
            row.addWidget(button)
        self.buttons.buttonClicked.connect(self._on_changed)
        self.box_corner = self._combo(tuple((word, c.value) for c, word in _CORNERS.items()))
        self.box_size = self._combo(tuple((word, z.value) for z, word in _SIZES.items()))
        row.addWidget(self.box_corner)
        row.addWidget(self.box_size)
        row.addStretch(1)
        self.light(_OFF, InsetCorner.BOTTOM_RIGHT.value, InsetSize.MEDIUM.value)

    def _combo(self, items: tuple[tuple[str, str], ...]) -> QComboBox:
        """A pick of ``(English, value)`` items -- ``activated`` is the
        user's alone, never a :meth:`light`."""
        log.debug("PlacementRow._combo: %d item(s)", len(items))
        combo = QComboBox(self)
        for english, value in items:
            combo.addItem(english, value)
            combo.setItemData(combo.count() - 1, english, _ENGLISH)
        combo.activated.connect(self._on_changed)
        return combo

    def _on_changed(self, *_qt_args: object) -> None:
        log.info("PlacementRow._on_changed: %s", self.choice())
        self._enable()
        self.chosen.emit()

    def _enable(self) -> None:
        """A corner and a size only for the box."""
        boxed = self.where() == _BOX
        log.debug("PlacementRow._enable: boxed=%s", boxed)
        self.box_corner.setEnabled(boxed)
        self.box_size.setEnabled(boxed)

    def where(self) -> str:
        """The lit one -- ``off``, ``background`` or ``box``."""
        button = self.buttons.checkedButton()
        found = str(button.property(_WHERE_PROPERTY)) if button is not None else _OFF
        log.debug("PlacementRow.where: %s", found)
        return found

    def light(self, where: str, corner: str, size: str) -> None:
        """What the App holds: *where* the visual plays, the box at *corner*
        and *size*."""
        log.debug("PlacementRow.light: %s %s %s", where, corner, size)
        for button in self.buttons.buttons():
            button.setChecked(button.property(_WHERE_PROPERTY) == where)
        self.box_corner.setCurrentIndex(max(0, self.box_corner.findData(corner)))
        self.box_size.setCurrentIndex(max(0, self.box_size.findData(size)))
        self._enable()

    def choice(self) -> tuple[str, str, str]:
        """(where, the box's corner, its size) as set."""
        found = (self.where(), str(self.box_corner.currentData()),
                 str(self.box_size.currentData()))
        log.debug("PlacementRow.choice: %s", found)
        return found

    def set_translate(self, translate: Callable[[str], str]) -> None:
        """Its words, through *translate*."""
        log.debug("PlacementRow.set_translate")
        for button in self.buttons.buttons():
            button.setText(translate(_WHERE[str(button.property(_WHERE_PROPERTY))]))
        for combo in (self.box_corner, self.box_size):
            for i in range(combo.count()):
                combo.setItemText(i, translate(combo.itemData(i, _ENGLISH)))


class Placement(QObject):
    """Where an LCD's visual plays, between the row and the App: what the App
    says lights the row -- its box, else the whole panel while the visual
    draws, else off -- and the user's pick is sent.  A QObject the page owns,
    so an answer that arrives after the page is gone is dropped
    (``qt_background``)."""

    def __init__(self, app: CommandBus, row: PlacementRow,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        log.debug("Placement.__init__")
        self._app, self._row = app, row
        #: What the App last said: the visual draws on the LCD; it is in a
        #: box, at this corner and size.
        self._playing = self._boxed = False
        self._corner, self._size = InsetCorner.BOTTOM_RIGHT.value, InsetSize.MEDIUM.value

    def ask(self, key: str) -> None:
        """Where LCD *key*'s visual is boxed -- the App's box, whichever UI
        set it."""
        if not key:
            log.debug("Placement.ask: no LCD")
            return
        log.debug("Placement.ask: %s", key)
        dispatch_in_background(self._app, LcdSnapshot(key=key), self._on_read,
                               failed=_no_snapshot)

    def _on_read(self, snap: LcdSnapshotResult) -> None:
        if not snap.ok:
            log.debug("Placement._on_read: %s", snap.message)
            return
        log.debug("Placement._on_read: %r %s %s", snap.inset_module,
                  snap.inset_corner, snap.inset_size)
        self._boxed = snap.inset_module == _MODULE
        self._corner = snap.inset_corner or InsetCorner.BOTTOM_RIGHT.value
        self._size = snap.inset_size or InsetSize.MEDIUM.value
        self._light()

    def playing(self, drawing: bool) -> None:
        """Whether the visual draws on the LCD now (``ListVisuals.playing``)."""
        log.debug("Placement.playing: %s", drawing)
        self._playing = drawing
        self._light()

    def _light(self) -> None:
        where = _BOX if self._boxed else _BACKGROUND if self._playing else _OFF
        log.debug("Placement._light: %s", where)
        self._row.light(where, self._corner, self._size)

    def send(self, key: str, translate: Callable[[str], str]) -> Result:
        """The row's pick for LCD *key*: Off -- the box closes, or the
        background ends, and the theme shows as it was; Background -- out of
        its box, the panel's visual (its last, if none draws) fills it; Box --
        in a corner over the theme, moved there or waiting for a tile."""
        where, corner, size = self._row.choice()
        boxed = self._app.dispatch(LcdSnapshot(key=key)).inset_module == _MODULE
        log.info("Placement.send: %s %s %s %s (boxed=%s)", key, where, corner, size,
                 boxed)
        match where:
            case "box":
                return self._app.dispatch(SetInset(key=key, module=_MODULE,
                                                   corner=corner, size=size))
            case "background":
                return self._to_background(key, boxed, translate)
            case _:
                return self._app.dispatch(ClearInset(key=key) if boxed
                                          else ClearModuleSource(key=key, module=_MODULE))

    def _to_background(self, key: str, boxed: bool,
                       translate: Callable[[str], str]) -> Result:
        """The visual on the whole panel: out of its box, or -- drawing
        nowhere -- the one the panel chose last.  Never chosen: say so."""
        listed = self._app.dispatch(ListVisuals(key=key))
        visual, preset = ((listed.playing, listed.preset) if listed.playing
                          else (listed.saved, listed.saved_preset))
        log.info("Placement._to_background: %s %r (boxed=%s)", visual, preset, boxed)
        if not visual:
            return Result(ok=False, message=translate(_PICK_ONE))
        if listed.playing and not boxed:
            return listed                               # already the background
        if boxed and not (cleared := self._app.dispatch(ClearInset(key=key))).ok:
            return cleared
        return self._app.dispatch(PlayVisual(key=key, visual=visual, preset=preset))


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
        self._placement = PlacementRow(self)
        self._placement.setStyleSheet(self._look.controls)
        self._placement.chosen.connect(self._on_placement)
        self._where = Placement(self._app, self._placement, self)
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
        self._placement.set_translate(self._tr)
        self._fit_controls()

    def set_device_key(self, key: str) -> None:
        """The LCD the GUI works on -- its visual's controls show."""
        log.info("VisualsPage.set_device_key: %s -> %s", self._key, key)
        self._key = key
        self._say("")
        if self._listed:
            self._ask_playing()
        self._where.ask(key)

    def on_settings_changed(self, event: object) -> None:
        """Another UI -- or this one -- changed the LCD's background, or its
        visual started or stopped hearing sound: show what it plays now."""
        if (isinstance(event, (BackgroundChanged, VisualHearingChanged))
                and event.key == self._key and self._listed):
            log.debug("VisualsPage.on_settings_changed: %s", event.key)
            self._ask_playing()
            self._where.ask(self._key)

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
                               self._on_listed, failed=_no_visuals)

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
                               self._on_playing, failed=_no_visuals)

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
        if listed.ok:
            self._where.playing(bool(listed.playing))

    def _on_placement(self) -> None:
        """The user picked where the visual plays (``Placement.send``): what
        went wrong shows here, and the row and the tiles show the App again."""
        log.info("VisualsPage._on_placement: %s", self._key)
        if not self._key:
            self._say(self._tr("Pick an LCD first"))
            return
        result = self._where.send(self._key, self._tr)
        if not result.ok:
            log.warning("VisualsPage._on_placement: %s", result.message)
        self._say("" if result.ok else result.message)
        self._where.ask(self._key)
        self._ask_playing()

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
        # Where it plays, just above the sliders.
        row = self._placement.sizeHint().height()
        placed = top - 8 - row
        self._placement.setGeometry(_MARGIN, placed, BROWSER_W - 2 * _MARGIN, row)
        self.scroll_area.setGeometry(0, _TOP, BROWSER_W, placed - 12 - _TOP)
        log.debug("VisualsPage._fit_controls: settings %d px, placement %d px, "
                  "previews %d px", height, row, placed - 12 - _TOP)

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
