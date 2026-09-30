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
from typing import Any

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

from ....core.commands import DeviceConnectionIssues, LcdSnapshot
from ....core.events import (
    DeviceConnected,
    DeviceDisconnected,
    ErrorOccurred,
    FrameSent,
    ThemeLoaded,
)
from ....core.logs import per_frame
from ..._errors import format_device_error
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

log = logging.getLogger(__name__)
#: FrameSent arrives once per rendered frame — see core.logs.per_frame.
frame_log = per_frame(__name__)

_MAX_EVENT_LINES = 20


class StatusPanel(BasePanel):
    """Live device state + rolling event log."""

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd",
            parent=self, selection=self._selection,
        )
        self._picker.key_changed.connect(self._on_key_changed)

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

        # Subscribe to bus events for the rolling log.  Named slots, not
        # closures: ``feedback_no_lambdas`` — a traceback from inside one of
        # these should name the handler, and five identical
        # ``<lambda>`` frames name nothing.
        qconn = Qt.ConnectionType.QueuedConnection
        self._bus.device_connected.connect(self._on_connected, type=qconn)
        self._bus.device_disconnected.connect(self._on_disconnected, type=qconn)
        self._bus.frame_sent.connect(self._on_frame_sent, type=qconn)
        self._bus.theme_loaded.connect(self._on_theme_loaded, type=qconn)
        self._bus.error_occurred.connect(self._on_error, type=qconn)
        # The labels follow the App, whoever changed it -- they refreshed
        # only on a device pick, so another UI's change never showed here.
        self._bus.settings_changed.connect(self._on_device_event, type=qconn)
        self._bus.app_settings_changed.connect(self._on_app_event, type=qconn)

        # Pull connect failures that fired before this panel subscribed —
        # the same DeviceConnectionIssues query every UI uses (bus-pure).
        for issue in self.dispatch(DeviceConnectionIssues()).issues:
            self._add_event(
                f"ERROR    [connect] {format_device_error(issue)}",
            )
        self._on_refresh()

    # ── Refresh ───────────────────────────────────────────────────────

    def _on_refresh(self) -> None:
        log.info("_on_refresh")
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

    # ── Bus slots ──────────────────────────────────────────────────────
    #
    # One per event rather than one generic formatter: each reads different
    # fields off a different Event type, so a single slot would have to branch
    # on the type it was handed — a logic table the signal already resolved.

    def _on_device_event(self, event: Any) -> None:
        if event.key == self._picker.current_key():
            log.debug("_on_device_event: %s for %s", type(event).__name__,
                      event.key)
            self._on_refresh()

    def _on_app_event(self, event: Any) -> None:
        log.debug("_on_app_event: %s", type(event).__name__)
        self._on_refresh()

    def _on_key_changed(self, key: str) -> None:
        """The window switched device — re-read this one's snapshot."""
        log.debug("_on_key_changed: key=%s", key)
        self._on_refresh()

    def _on_connected(self, event: DeviceConnected) -> None:
        log.debug("_on_connected: key=%s", event.key)
        self._add_event(f"CONNECT  {event.key}  {event.resolution}")

    def _on_disconnected(self, event: DeviceDisconnected) -> None:
        log.debug("_on_disconnected: key=%s", event.key)
        self._add_event(f"DISCONN  {event.key}")

    def _on_frame_sent(self, event: FrameSent) -> None:
        # Per-frame: the shared ``trcc.frame`` family, so it is silent at the
        # default rung and cannot drown the one-shot lines a report is read
        # for.  ``_add_event`` below is the pre-existing per-event line.
        frame_log.debug("_on_frame_sent: key=%s", event.key)
        self._add_event(f"FRAME    {event.key}  {event.bytes_sent} bytes")

    def _on_theme_loaded(self, event: ThemeLoaded) -> None:
        log.debug("_on_theme_loaded: key=%s", event.key)
        self._add_event(f"THEME    {event.key}  {event.theme_name}")
        self._on_device_event(event)

    def _on_error(self, event: ErrorOccurred) -> None:
        log.debug("_on_error: kind=%s", event.kind)
        self._add_event(f"ERROR    [{event.kind}] {format_device_error(event)}")

    def _add_event(self, text: str) -> None:
        log.debug("_add_event: text=%s", text)
        stamp = time.strftime("%H:%M:%S")
        line = f"{stamp}  {text}"
        # Prepend (newest on top).
        self._event_list.insertItem(0, line)
        if self._event_list.count() > _MAX_EVENT_LINES:
            self._event_list.takeItem(self._event_list.count() - 1)
