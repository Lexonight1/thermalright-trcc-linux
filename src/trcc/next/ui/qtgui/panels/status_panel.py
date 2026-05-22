"""StatusPanel — per-device state snapshot + live event feed.

Built for non-technical users to confirm "is my device set up the way
I want?":

* Top: device key picker + Refresh button.
* Middle: a labelled grid showing the LCD's current settings (theme,
  orientation, brightness, mask, time/date format).
* Bottom: rolling log of the last 20 events on the BusBridge — handy
  when a user reports "it stopped working" so they can see exactly
  what fired and when.

Dispatches ``LcdSnapshot`` on Refresh; subscribes to ``FrameSent`` /
``ThemeLoaded`` / ``DeviceConnected`` / ``ErrorOccurred`` / etc on the
bus and prepends new entries to the event list.
"""
from __future__ import annotations

import logging
import time
from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QVBoxLayout,
)

from ....core.commands import LcdSnapshot
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

log = logging.getLogger(__name__)

_MAX_EVENT_LINES = 20


class StatusPanel(BasePanel):
    """Live device state + rolling event log."""

    def _setup_ui(self) -> None:
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd", parent=self,
        )
        self._picker.key_changed.connect(lambda _key: self._on_refresh())

        key_form = QFormLayout()
        key_form.addRow("Device key:", self._picker)

        key_row = QHBoxLayout()
        key_row.addLayout(key_form, stretch=1)

        # ── State group ──
        state_box = QGroupBox("Current state", self)
        state_form = QFormLayout(state_box)
        self._theme_label = QLabel("—", state_box)
        self._orientation_label = QLabel("—", state_box)
        self._brightness_label = QLabel("—", state_box)
        self._mask_label = QLabel("—", state_box)
        self._time_label = QLabel("—", state_box)
        self._date_label = QLabel("—", state_box)
        self._temp_label = QLabel("—", state_box)
        state_form.addRow("Active theme:", self._theme_label)
        state_form.addRow("Orientation:", self._orientation_label)
        state_form.addRow("Brightness:", self._brightness_label)
        state_form.addRow("Mask:", self._mask_label)
        state_form.addRow("Time format:", self._time_label)
        state_form.addRow("Date format:", self._date_label)
        state_form.addRow("Temperature unit:", self._temp_label)

        # ── Event group ──
        event_box = QGroupBox("Recent events", self)
        event_layout = QVBoxLayout(event_box)
        self._events = deque(maxlen=_MAX_EVENT_LINES)
        self._event_list = QListWidget(event_box)
        self._event_list.setSelectionMode(
            QListWidget.SelectionMode.NoSelection,
        )
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self._event_list.setFont(mono)
        event_layout.addWidget(self._event_list)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addLayout(key_row)
        root.addWidget(state_box)
        root.addWidget(event_box, stretch=1)

        # Subscribe to bus events for the rolling log.
        qconn = Qt.ConnectionType.QueuedConnection
        self._bus.device_connected.connect(
            lambda e: self._add_event(f"CONNECT  {e.key}  {e.resolution}"),
            type=qconn,
        )
        self._bus.device_disconnected.connect(
            lambda e: self._add_event(f"DISCONN  {e.key}"),
            type=qconn,
        )
        self._bus.frame_sent.connect(
            lambda e: self._add_event(f"FRAME    {e.key}  {e.bytes_sent} bytes"),
            type=qconn,
        )
        self._bus.theme_loaded.connect(
            lambda e: self._add_event(f"THEME    {e.key}  {e.theme_name}"),
            type=qconn,
        )
        self._bus.error_occurred.connect(
            lambda e: self._add_event(
                f"ERROR    [{e.kind}] {e.message}",
            ),
            type=qconn,
        )

    # ── Refresh ───────────────────────────────────────────────────────

    def _on_refresh(self) -> None:
        key = self._picker.current_key()
        if not key:
            self._theme_label.setText("(pick a device above)")
            return
        result = self.dispatch(LcdSnapshot(key=key))
        if not result.ok:
            self._theme_label.setText(f"(no data: {result.message})")
            return
        self._theme_label.setText(result.current_theme or "—")
        self._orientation_label.setText(f"{result.orientation}°")
        self._brightness_label.setText(f"{result.brightness}%")
        mask_text = result.mask_path or "(none)"
        if result.mask_path and not result.mask_visible:
            mask_text += "  (hidden)"
        self._mask_label.setText(mask_text)
        self._time_label.setText(result.time_format)
        self._date_label.setText(result.date_format)
        self._temp_label.setText(result.temp_unit)

    def _add_event(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"{stamp}  {text}"
        # Prepend (newest on top).
        self._event_list.insertItem(0, line)
        if self._event_list.count() > _MAX_EVENT_LINES:
            self._event_list.takeItem(self._event_list.count() - 1)
