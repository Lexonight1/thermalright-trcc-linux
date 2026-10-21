"""AudioVisualsPanel -- the audio visuals gallery, next to Cloud themes.

The same gallery as the gui skin's audio bars tab, from the same Query: one tile per
visual (and per preset), each the App's picture of what it draws for noise,
TRCC Pipes first.  Pick a device and double-click a tile -- or Play -- and
it becomes that panel's background; any other background ends it.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from trcc.core.events import BackgroundChanged
from trcc.ui.qt_background import dispatch_in_background
from trcc.ui.qtgui.device_picker import DevicePickerWidget
from trcc.ui.qtgui.panels._browser_base import AssetBrowserPanel

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

log = logging.getLogger(__name__)

#: Tile size asked of the App -- the grid's icon size.
_TILE = 96


class AudioVisualsPanel(AssetBrowserPanel):
    """Pick an audio visual as a panel's background."""

    #: Its entry in the sidebar, just above Modules.
    LABEL: ClassVar[str] = "Audio visuals"

    def _setup_ui(self) -> None:
        log.debug("AudioVisualsPanel._setup_ui")
        self._listed = False
        self._tiles = VisualTiles(self.app, self._tile_query, self._show_tile)
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd",
            parent=self, selection=self._selection,
        )
        form = QFormLayout()
        form.addRow("Device key:", self._picker)

        self._list = self._build_asset_list()
        self._controls = VisualControls(self.app, self._sliders_command,
                                        self._center_command,
                                            self._picture_command, self)
        self._picker.key_changed.connect(self._on_key_changed)
        self._bus.forward(VisualHearingChanged, "settings_changed")
        self._bus.settings_changed.connect(
            self._on_settings_changed, type=Qt.ConnectionType.QueuedConnection)

        self._play_btn = QPushButton("Play on this panel", self)
        self._play_btn.clicked.connect(self._on_apply)
        buttons = QHBoxLayout()
        buttons.addWidget(self._play_btn)
        buttons.addStretch(1)

        self._status = QLabel(
            "What your computer is playing, drawn as the panel's background. "
            "Pick one and press Play.", self)
        self._status.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
        root.addLayout(form)
        root.addWidget(self._list, stretch=1)       # previews on top take the rest;

        root.addLayout(buttons)
        root.addWidget(self._controls)              # ... below, as tall as they need
        root.addWidget(self._status)

    def showEvent(self, event: QShowEvent) -> None:
        """The gallery is listed the first time it is shown."""
        log.debug("AudioVisualsPanel.showEvent: listed=%s", self._listed)
        super().showEvent(event)
        if not self._listed:
            self.refresh()

    def refresh(self) -> None:
        """Ask the App for the visuals; the tiles follow, one at a time."""
        log.info("AudioVisualsPanel.refresh")
        self._listed = True
        dispatch_in_background(
            self.app, ListVisuals(key=self._picker.current_key() or ""),
            self._on_listed)

    def _on_listed(self, listed: VisualsResult) -> None:
        log.info("AudioVisualsPanel._on_listed: ok=%s %s", listed.ok,
                 listed.message)
        self._list.clear()
        items = gallery_items(listed) if listed.ok else []
        for item in items:
            row = QListWidgetItem(item.name)
            row.setData(Qt.ItemDataRole.UserRole, item)
            if item.problem:
                row.setToolTip(item.problem)
                row.setForeground(self.palette().placeholderText())
            self._list.addItem(row)
        if not listed.ok:
            self._status.setText(listed.message)
        self._tiles.fetch(items)
        self._on_playing(listed)

    def _on_key_changed(self, key: str) -> None:
        log.info("AudioVisualsPanel._on_key_changed: %s", key)
        if self._listed:
            self._ask_playing()

    def _on_settings_changed(self, event: object) -> None:
        """Another UI -- or this one -- changed the picked panel's
        background: show what it plays now."""
        if (isinstance(event, (BackgroundChanged, VisualHearingChanged))
                and self._listed
                and event.key == self._picker.current_key()):
            log.debug("AudioVisualsPanel._on_settings_changed: %s", event.key)
            self._ask_playing()

    def _ask_playing(self) -> None:
        """What the picked panel plays -- for the controls and the list."""
        key = self._picker.current_key() or ""
        log.debug("AudioVisualsPanel._ask_playing: %s", key)
        dispatch_in_background(self.app, ListVisuals(key=key), self._on_playing)

    def _on_playing(self, listed: VisualsResult) -> None:
        """Its entry selected, its controls shown."""
        log.debug("AudioVisualsPanel._on_playing: %s %r", listed.playing,
                  listed.preset)
        for i in range(self._list.count()):
            item: VisualItem = self._list.item(i).data(Qt.ItemDataRole.UserRole)
            if (item.visual, item.preset) == (listed.playing, listed.preset):
                self._list.setCurrentRow(i)
        self._controls.show_for(listed)

    def _sliders_command(self, values: dict[str, float]) -> SetVisualSettings:
        """The Command for new slider values, for the picked panel."""
        key = self._picker.current_key() or ""
        log.debug("AudioVisualsPanel._sliders_command: %s %s", key, values)
        return SetVisualSettings(key=key, settings=values)

    def _picture_command(self, shown: bool | None,
                         fire: float | None) -> SetVisualPicture:
        """The Command for the centre picture on or off, and its fire."""
        log.debug("AudioVisualsPanel._picture_command: shown=%s fire=%s", shown, fire)
        return SetVisualPicture(shown=shown, fire=fire)

    def _center_command(self, path: str) -> SetVisualCenter:
        """The Command for a new centre picture ("" for none)."""
        log.debug("AudioVisualsPanel._center_command: %r", path)
        return SetVisualCenter(path=path)

    def _tile_query(self, item: VisualItem) -> VisualTile:
        """The Query for *item*'s tile, at this grid's icon size."""
        log.debug("AudioVisualsPanel._tile_query: %s", item.name)
        return VisualTile(visual=item.visual, preset=item.preset, size=_TILE)

    def _show_tile(self, item: VisualItem, png: bytes) -> None:
        """A tile arrived from the App: onto its entry."""
        log.debug("AudioVisualsPanel._show_tile: %s", item.name)
        image = QImage.fromData(png)
        for i in range(self._list.count()):
            row = self._list.item(i)
            if row.data(Qt.ItemDataRole.UserRole) == item and not image.isNull():
                row.setIcon(QIcon(QPixmap.fromImage(image)))

    def _on_apply(self) -> None:
        """Play the picked visual on the picked panel."""
        log.info("AudioVisualsPanel._on_apply")
        row = self._list.currentItem()
        if row is None:
            self._status.setText("Pick an audio visual first.")
            return
        key = self._device_key()
        if key is None:
            return
        item: VisualItem = row.data(Qt.ItemDataRole.UserRole)
        result = self.dispatch(PlayVisual(key=key, visual=item.visual,
                                          preset=item.preset))
        self._status.setText(result.message)


PANEL = AudioVisualsPanel
