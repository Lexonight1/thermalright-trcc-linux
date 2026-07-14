"""CloudThemeBrowser — browse + download Thermalright's hosted catalog.

User flow:
* Pick a category from the dropdown (or "All" for everything).
* Pick a theme from the list (id + category shown).
* Pick a device + click Apply → download + load.

Downloads happen in the foreground so the user sees the status update
immediately ("Downloading a001…").  No background threads — keeps
errors visible.  A long-running download will pin the UI; if/when that
matters we move to a worker thread.

Error messages are designed for non-technical readers: instead of
``HttpFetchError: GET ... → URL error``, the user sees "Couldn't reach
the theme server.  Check your internet connection."
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ....core.commands import ListCloudThemes, LoadCloudTheme
from ..assets import thumbnail_icon
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

log = logging.getLogger(__name__)

_ALL_CATEGORIES = "all"


class CloudThemeBrowser(BasePanel):
    """Browse + apply themes from Thermalright's cloud catalog."""

    def _setup_ui(self) -> None:
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd", parent=self,
        )

        self._category = QComboBox(self)
        self._category.addItem("All categories", userData=_ALL_CATEGORIES)
        self._category.currentIndexChanged.connect(self._on_category_changed)

        key_form = QFormLayout()
        key_form.addRow("Device key:", self._picker)
        key_form.addRow("Category:", self._category)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(lambda _item: self._on_apply())
        # Thumbnail grid (parity with the gui skin): each catalog entry shows
        # its extracted preview PNG (data/web/{w}{h}/<id>.png).
        self._list.setViewMode(QListWidget.ViewMode.IconMode)
        self._list.setIconSize(QSize(96, 96))
        self._list.setGridSize(QSize(124, 140))
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setMovement(QListWidget.Movement.Static)
        self._list.setSpacing(6)
        self._list.setWordWrap(True)
        # Re-fill on device change so previews resolve to the picked device's
        # resolution (the local/mask browsers do the same).
        self._picker.key_changed.connect(self._on_key_changed)

        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(self._on_refresh)

        self._apply_btn = QPushButton("Download + apply", self)
        self._apply_btn.clicked.connect(self._on_apply)

        button_row = QHBoxLayout()
        button_row.addWidget(self._refresh_btn)
        button_row.addWidget(self._apply_btn)
        button_row.addStretch(1)

        self._status = QLabel(
            "Pick a category and refresh to load the catalog.", self,
        )
        self._status.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
        root.addLayout(key_form)
        root.addWidget(self._list, stretch=1)
        root.addLayout(button_row)
        root.addWidget(self._status)

        # Categories are static — populate the dropdown once on open
        # (the catalog list dispatch returns them).
        self._populate_categories()

    # ── Populate ──────────────────────────────────────────────────────

    def _populate_categories(self) -> None:
        """Fill the category dropdown from the catalog (offline-safe)."""
        result = self.dispatch(ListCloudThemes(category=_ALL_CATEGORIES))
        # Categories never change after this — they're a static table.
        existing = {
            self._category.itemData(i) for i in range(self._category.count())
        }
        for cat in result.categories:
            if cat.prefix in existing:
                continue
            self._category.addItem(
                f"{cat.name}  ({cat.count} themes)",
                userData=cat.prefix,
            )
        # Fill the initial list too.
        self._fill_list_from_result(result)

    def _on_refresh(self) -> None:
        log.info("_on_refresh")
        self._on_category_changed()

    def _on_category_changed(self) -> None:
        log.info("_on_category_changed")
        cat = str(self._category.currentData() or _ALL_CATEGORIES)
        result = self.dispatch(ListCloudThemes(category=cat))
        self._fill_list_from_result(result)

    def _on_key_changed(self, _key: str) -> None:
        log.info("_on_key_changed: re-filling cloud list for device resolution")
        self._on_category_changed()

    def _resolution(self) -> tuple[int, int] | None:
        """The picked device's canvas resolution, or None if unresolvable."""
        key = self._picker.current_key()
        if not key:
            return None
        device = self.app.devices.get(key)
        if device is not None:
            if device.profile is not None:
                return device.profile.resolution
            if device.info.native_resolution != (0, 0):
                return device.info.native_resolution
        return None

    def _fill_list_from_result(self, result) -> None:
        self._list.clear()
        # Previews live at data/web/{w}{h}/<id>.png (extracted by the data
        # layer); resolve the dir from the picked device.  No device yet →
        # text-only until one is picked and the list re-fills.
        resolution = self._resolution()
        web_dir = (
            self.app.platform.paths().cloud_theme_dir(*resolution)
            if resolution is not None else None
        )
        for entry in result.themes:
            item = QListWidgetItem(f"{entry.id}\n{entry.category_name}")
            if web_dir is not None:
                item.setIcon(thumbnail_icon(web_dir / f"{entry.id}.png"))
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self._list.addItem(item)
        if result.ok:
            self._status.setText(result.message)
        else:
            self._status.setText(_user_friendly_error(result.message))

    # ── Apply ─────────────────────────────────────────────────────────

    def _on_apply(self) -> None:
        log.info("_on_apply")
        item = self._list.currentItem()
        if item is None:
            self._status.setText("Pick a theme from the list first.")
            return
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first.  Open the Devices panel to scan "
                "if no devices are listed.",
            )
            return
        theme_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._status.setText(
            f"Downloading {theme_id}… this can take a few seconds on the "
            "first download (subsequent loads are cached).",
        )
        self._status.repaint()
        result = self.dispatch(LoadCloudTheme(key=key, theme_id=theme_id))
        if result.ok:
            self._status.setText(result.message)
        else:
            self._status.setText(_user_friendly_error(result.message))


def _user_friendly_error(message: str) -> str:
    """Translate adapter-layer error strings into plain-language hints."""
    lower = message.lower()
    if "url error" in lower or "timed out" in lower:
        return (
            "Couldn't reach the theme server.  Check your internet "
            "connection and try Refresh again."
        )
    if "http 4" in lower:
        return (
            "The server says that theme isn't available right now.  "
            "Try a different theme or refresh the list."
        )
    if "http 5" in lower:
        return (
            "The theme server returned an error.  Try again in a minute."
        )
    return message
