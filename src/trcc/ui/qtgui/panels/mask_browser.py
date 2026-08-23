"""MaskBrowser — list, apply, position, and toggle visibility of masks.

Masks are PNG / JPG overlays that punch a transparent window through
the device background.  This panel covers the full mask lifecycle:

* **Browse** every mask under ``user_content_dir/masks/``.
* **Upload** a new image — copied into the masks dir + applied.
* **Apply** the selected mask to a chosen device.
* **Position** the mask on the canvas (X / Y in pixels).
* **Show / hide** the mask without un-applying it (handy when
  experimenting with element layouts).

Picks up :class:`MaskApplied` / :class:`MaskPositionChanged` /
:class:`MaskVisibilityChanged` so external mutations (CLI, API)
update the UI without a refresh click.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ....core.commands import (
    ApplyMask,
    LcdSnapshot,
    ListMasks,
    SetMaskPosition,
    SetMaskVisible,
    UploadCustomMask,
)
from ..assets import thumbnail_icon
from ..device_picker import DevicePickerWidget
from ._browser_base import AssetBrowserPanel

log = logging.getLogger(__name__)


class MaskBrowser(AssetBrowserPanel):
    """List + apply + edit position / visibility of masks."""

    def _setup_ui(self) -> None:
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd", parent=self,
        )
        self._picker.key_changed.connect(self._on_key_changed)

        key_form = QFormLayout()
        key_form.addRow("Device key:", self._picker)

        # ── Mask list + actions ─────────────────────────────────────
        # Thumbnail grid: each mask shows its Theme.png preview
        # (falling back to the 01.png overlay).
        self._list = self._build_asset_list()

        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.clicked.connect(self.refresh)

        self._apply_btn = QPushButton("Apply", self)
        self._apply_btn.clicked.connect(self._on_apply)

        self._upload_btn = QPushButton("Upload new mask…", self)
        self._upload_btn.clicked.connect(self._on_upload)

        button_row = QHBoxLayout()
        button_row.addWidget(self._refresh_btn)
        button_row.addWidget(self._apply_btn)
        button_row.addWidget(self._upload_btn)
        button_row.addStretch(1)

        # ── Position + visibility group ─────────────────────────────
        position_box = QGroupBox("Position + visibility", self)
        position_form = QFormLayout(position_box)

        self._x = QSpinBox(position_box)
        self._x.setRange(0, 9999)
        self._x.setSuffix(" px")
        self._x.editingFinished.connect(self._on_position_changed)

        self._y = QSpinBox(position_box)
        self._y.setRange(0, 9999)
        self._y.setSuffix(" px")
        self._y.editingFinished.connect(self._on_position_changed)

        xy_row = QHBoxLayout()
        xy_row.addWidget(QLabel("X:", position_box))
        xy_row.addWidget(self._x)
        xy_row.addSpacing(12)
        xy_row.addWidget(QLabel("Y:", position_box))
        xy_row.addWidget(self._y)
        xy_row.addStretch(1)

        self._visible = QCheckBox("Mask visible", position_box)
        self._visible.setChecked(True)
        self._visible.toggled.connect(self._on_visibility_changed)

        position_form.addRow("Position:", xy_row)
        position_form.addRow(self._visible)

        self._status = QLabel(
            "Masks are images that punch a window through the background.  "
            "Apply one to see only that shape on your device.",
            self,
        )
        self._status.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
        root.addLayout(key_form)
        root.addWidget(self._list, stretch=1)
        root.addLayout(button_row)
        root.addWidget(position_box)
        root.addWidget(self._status)

        # Subscribe to mask-related events so external mutations refresh
        # the position + visibility widgets here.
        self._bus.mask_applied.connect(
            self._on_mask_applied_event,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self._bus.mask_position_changed.connect(
            self._on_mask_position_event,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self._bus.mask_visibility_changed.connect(
            self._on_mask_visibility_event,
            type=Qt.ConnectionType.QueuedConnection,
        )

        self.refresh()

    # ── Public ────────────────────────────────────────────────────────

    def refresh(self) -> None:
        log.debug("refresh")
        self._list.clear()
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first to list masks at its resolution.",
            )
            return
        resolution = self._target_resolution(key)
        if resolution is None:
            return
        result = self.dispatch(ListMasks(resolution=resolution, key=key))
        for entry in result.masks:
            item = QListWidgetItem(thumbnail_icon(Path(entry.preview)), entry.name)
            item.setData(Qt.ItemDataRole.UserRole, entry.path)
            self._list.addItem(item)
        if not result.masks:
            self._status.setText(
                "No masks found.  Click 'Upload new mask…' to add a "
                "PNG / JPG image and apply it to your device.",
            )
        else:
            self._status.setText(result.message)

    # ── Actions ───────────────────────────────────────────────────────

    def _device_key(self) -> str | None:
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first.  Open the Devices panel to scan "
                "if no devices are listed.",
            )
            return None
        return key

    def _on_apply(self) -> None:
        log.info("_on_apply")
        item = self._list.currentItem()
        if item is None:
            self._status.setText("Pick a mask from the list first.")
            return
        key = self._device_key()
        if key is None:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole))
        result = self.dispatch(ApplyMask(key=key, path=Path(path)))
        self._status.setText(result.message)

    def _on_upload(self) -> None:
        log.info("_on_upload")
        key = self._device_key()
        if key is None:
            return
        path_str, _filter = QFileDialog.getOpenFileName(
            self,
            "Pick a mask image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp);;All files (*.*)",
        )
        if not path_str:
            return
        result = self.dispatch(
            UploadCustomMask(key=key, source=Path(path_str)),
        )
        self._status.setText(result.message)
        if result.ok:
            self.refresh()

    def _on_position_changed(self) -> None:
        log.info("_on_position_changed")
        key = self._device_key()
        if key is None:
            return
        result = self.dispatch(SetMaskPosition(
            key=key, x=self._x.value(), y=self._y.value(),
        ))
        self._status.setText(result.message)

    def _on_visibility_changed(self, visible: bool) -> None:
        log.info("_on_visibility_changed: visible=%s", visible)
        key = self._device_key()
        if key is None:
            return
        result = self.dispatch(SetMaskVisible(key=key, visible=visible))
        self._status.setText(result.message)

    def _on_key_changed(self, _new_key: str) -> None:
        """User picked a different device — refresh list + sync widgets."""
        log.info("_on_key_changed: _new_key=%s", _new_key)
        self.refresh()
        self._sync_from_snapshot()

    def _sync_from_snapshot(self) -> None:
        key = self._picker.current_key()
        if not key:
            return
        snap = self.dispatch(LcdSnapshot(key=key))
        if not snap.ok:
            return
        x, y = snap.mask_position or (0, 0)
        self._x.blockSignals(True)
        self._x.setValue(x)
        self._x.blockSignals(False)
        self._y.blockSignals(True)
        self._y.setValue(y)
        self._y.blockSignals(False)
        self._visible.blockSignals(True)
        self._visible.setChecked(snap.mask_visible)
        self._visible.blockSignals(False)

    # ── Bus subscriptions ────────────────────────────────────────────

    def _on_mask_applied_event(self, event) -> None:
        log.info("_on_mask_applied_event")
        if event.key == self._picker.current_key():
            self._sync_from_snapshot()

    def _on_mask_position_event(self, event) -> None:
        log.info("_on_mask_position_event")
        if event.key != self._picker.current_key():
            return
        x, y = event.position or (0, 0)
        self._x.blockSignals(True)
        self._x.setValue(x)
        self._x.blockSignals(False)
        self._y.blockSignals(True)
        self._y.setValue(y)
        self._y.blockSignals(False)

    def _on_mask_visibility_event(self, event) -> None:
        log.info("_on_mask_visibility_event")
        if event.key != self._picker.current_key():
            return
        self._visible.blockSignals(True)
        self._visible.setChecked(event.visible)
        self._visible.blockSignals(False)
