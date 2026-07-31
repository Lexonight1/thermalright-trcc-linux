"""PreviewPanel — live render of what a device is (or would be) showing.

Composes the same pipeline ``RenderAndSend`` does, minus the wire
encode + transport, and shows the result as a QPixmap so the user
sees real overlay output before plugging in any hardware.

Refresh strategy:
* On a 1s timer, re-render if the device has an active theme.
* Subscribes to ``FrameSent`` so a fresh frame on the wire also
  refreshes the preview (catches state changes from other UIs / API).

Honest scope: only LCD devices preview here.  LED devices don't have
a renderable "screen" — for those use the LED control panel.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from ....core.commands import BuildPreview, LcdSnapshot
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


_REFRESH_MS = 1000
_PREVIEW_MAX = 480  # max edge of the preview pixmap in window pixels


class PreviewPanel(BasePanel):
    """Live preview of an LCD device's rendered output."""

    def _setup_ui(self) -> None:
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd", parent=self,
        )
        self._picker.key_changed.connect(lambda _key: self._refresh())

        key_form = QFormLayout()
        key_form.addRow("Device key:", self._picker)

        # Preview pixmap area
        preview_box = QGroupBox("Preview", self)
        preview_layout = QVBoxLayout(preview_box)
        self._preview = QLabel(preview_box)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(_PREVIEW_MAX, _PREVIEW_MAX // 2)
        self._preview.setStyleSheet(
            "background-color: #111; border: 1px solid #333;",
        )
        self._set_placeholder(
            "Pick a device to preview its output.",
        )
        preview_layout.addWidget(self._preview)

        # Current state group
        state_box = QGroupBox("State", self)
        state_form = QFormLayout(state_box)
        self._theme_label = QLabel("—", state_box)
        self._size_label = QLabel("—", state_box)
        self._orientation_label = QLabel("—", state_box)
        self._brightness_label = QLabel("—", state_box)
        state_form.addRow("Theme:", self._theme_label)
        state_form.addRow("Render size:", self._size_label)
        state_form.addRow("Orientation:", self._orientation_label)
        state_form.addRow("Brightness:", self._brightness_label)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addLayout(key_form)
        root.addWidget(preview_box, stretch=1)
        root.addWidget(state_box)

        # Re-render every second so live overlay metrics update.
        self.start_periodic_updates(_REFRESH_MS, self._refresh)

        # Frame-on-wire = re-render so external state changes show up too.
        self._bus.frame_sent.connect(
            lambda e: self._refresh() if e.key == self._picker.current_key() else None,
            type=Qt.ConnectionType.QueuedConnection,
        )

    # ── Render ────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        log.debug("_refresh")
        key = self._picker.current_key()
        if not key:
            return
        snapshot = self.dispatch(LcdSnapshot(key=key))
        if not snapshot.ok:
            self._set_placeholder(
                f"No data for {key}.  "
                "If the device isn't connected, load a theme via the Themes panel.",
            )
            return
        self._theme_label.setText(snapshot.current_theme or "(no theme loaded)")
        self._orientation_label.setText(f"{snapshot.orientation}°")
        self._brightness_label.setText(f"{snapshot.brightness}%")

        # The render every UI shows — one Command, sensors included.
        preview = self.dispatch(BuildPreview(key=key))
        if not preview.ok:
            # Not attached, or the render raised — say which.  "Load a theme"
            # would be a lie for a device that isn't plugged in.
            self._set_placeholder(f"No data for {key} — {preview.message}")
            self._size_label.setText("—")
            return
        if preview.surface is None:
            self._set_placeholder(
                f"Load a theme for {key} to see a live preview here.",
            )
            self._size_label.setText("—")
            return
        pix = _surface_to_pixmap(preview.surface, _PREVIEW_MAX)
        if pix is None:
            self._set_placeholder(
                "Preview surface couldn't be rendered to a pixmap.",
            )
            return
        self._preview.setPixmap(pix)
        self._preview.setText("")
        self._size_label.setText(f"{pix.width()}×{pix.height()}")

    def _set_placeholder(self, text: str) -> None:
        self._preview.clear()
        self._preview.setText(text)
        font = QFont()
        font.setPointSize(11)
        self._preview.setFont(font)
        self._preview.setStyleSheet(
            "background-color: #111; color: #aaa; border: 1px solid #333; "
            "padding: 16px;",
        )
        self._preview.setWordWrap(True)


def _surface_to_pixmap(surface: object, max_edge: int) -> QPixmap | None:
    """QtRenderer surfaces are QImage — convert + scale.

    Falls back to None on unexpected types so the panel surfaces a
    friendly placeholder rather than a crash.
    """
    if not isinstance(surface, QImage):
        return None
    pix = QPixmap.fromImage(surface)
    if pix.width() > max_edge or pix.height() > max_edge:
        pix = pix.scaled(
            max_edge, max_edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pix
