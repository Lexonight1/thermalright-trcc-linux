"""DevicePickerWidget — editable combo of attached devices.

Every panel that operates on "a specific device key" used to require
the user to type a four-digit hex pair (e.g. ``0402:3922``).  That's a
hostile UX for everyone except the maintainer.

This widget replaces those QLineEdits with an editable :class:`QComboBox`
that:

* pre-populates with every currently-attached device (key + a friendly
  vendor/product label);
* still lets users type any key — the box is editable, so future
  hardware or pre-staging works;
* exposes a refresh button that dispatches :class:`DiscoverDevices` so
  newly-plugged devices show up without restarting the panel;
* picks up :class:`DeviceConnected` / :class:`DeviceDisconnected` events
  so the dropdown stays in sync when another UI scans for devices.

Emits :sig:`key_changed(str)` whenever the user picks a different key
(programmatic :meth:`set_key` calls don't emit, so panels can sync
state without thrashing).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from ...core.commands import DiscoverDevices

if TYPE_CHECKING:
    from ...app import App
    from .bus_bridge import BusBridge

log = logging.getLogger(__name__)


class DevicePickerWidget(QWidget):
    """Editable combo + refresh button for selecting a device key.

    Two-line widget that drops into a ``QFormLayout`` row like a
    one-line field.  Use :meth:`current_key` to read the active key;
    connect to :sig:`key_changed` to react to user changes.
    """

    key_changed = Signal(str)

    def __init__(
        self,
        app: App,
        bus: BusBridge | None = None,
        *,
        kind_filter: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = app
        self._bus = bus
        self._kind_filter = kind_filter  # "lcd" / "led" / None
        self._build()
        self._populate_from_app()
        if bus is not None:
            # Refresh dropdown when devices are attached/detached from
            # any UI — events arrive on the Qt thread thanks to the
            # bridge's queued connection setup.
            bus.device_connected.connect(
                lambda _evt: self._populate_from_app(),
                type=Qt.ConnectionType.QueuedConnection,
            )
            bus.device_disconnected.connect(
                lambda _evt: self._populate_from_app(),
                type=Qt.ConnectionType.QueuedConnection,
            )

    # ── Public API ───────────────────────────────────────────────────

    def current_key(self) -> str:
        """Return the currently selected / typed device key, stripped.

        Items are ``addItem(label, userData=key)`` with a human label
        ("vid:pid — Vendor Product"), so a SELECTED item carries the key in
        ``currentData()``; the visible ``currentText()`` is just the label.
        Reading ``currentText()`` handed the whole label to every command as
        the key and broke all of them (#176).  Prefer the item's data; fall
        back to the typed text only when nothing is selected (the editable
        "type a raw key" path).
        """
        data = self._combo.currentData()
        if data:
            return str(data).strip()
        return self._combo.currentText().strip()

    def set_key(self, key: str) -> None:
        """Set the visible key without emitting :sig:`key_changed`."""
        self._combo.blockSignals(True)
        # If the key already exists in the dropdown, select it.
        # Otherwise just set the editable text.
        idx = self._index_for_key(key)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        else:
            self._combo.setEditText(key)
        self._combo.blockSignals(False)

    def refresh(self) -> None:
        """Dispatch :class:`DiscoverDevices`, then rebuild the list."""
        log.debug("refresh")
        self._app.dispatch(DiscoverDevices())
        self._populate_from_app()

    # ── UI ───────────────────────────────────────────────────────────

    def _build(self) -> None:
        self._combo = QComboBox(self)
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._combo.setMinimumWidth(220)
        self._combo.currentIndexChanged.connect(self._on_index_changed)
        # editTextChanged fires while the user is typing — emit on
        # finalisation (return key, focus loss) via editingFinished.
        if (line_edit := self._combo.lineEdit()) is not None:
            line_edit.editingFinished.connect(self._on_text_finished)
            line_edit.setPlaceholderText("0402:3922")

        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.setToolTip(
            "Rescan for attached devices.",
        )
        self._refresh_btn.clicked.connect(self.refresh)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._combo, stretch=1)
        row.addWidget(self._refresh_btn)

    # ── Population ───────────────────────────────────────────────────

    def _populate_from_app(self) -> None:
        """Rebuild the dropdown from ``app.devices``, preserving choice."""
        previous_key = self.current_key()

        self._combo.blockSignals(True)
        self._combo.clear()
        for key, device in sorted(self._app.devices.items()):
            if not self._matches_filter(device):
                continue
            label = f"{key} — {device.info.vendor} {device.info.product}".strip()
            self._combo.addItem(label, userData=key)

        # Re-select the previous key (typed or chosen).
        if previous_key:
            idx = self._index_for_key(previous_key)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            else:
                self._combo.setEditText(previous_key)
        elif self._combo.count() > 0:
            # No prior selection but devices are attached — surface the
            # first one as a sensible default so first-run users don't
            # have to know any key.
            self._combo.setCurrentIndex(0)
        self._combo.blockSignals(False)

    def _matches_filter(self, device) -> bool:
        """Optional 'lcd' / 'led' filter — narrow when callers know."""
        if self._kind_filter is None:
            return True
        kind = getattr(device.info, "kind", None)
        if kind is None:
            return True
        return str(kind).lower().endswith(self._kind_filter.lower())

    def _index_for_key(self, key: str) -> int:
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == key:
                return i
            if self._combo.itemText(i).startswith(f"{key} ") \
                    or self._combo.itemText(i) == key:
                return i
        return -1

    # ── Signal plumbing ──────────────────────────────────────────────

    def _on_index_changed(self, _idx: int) -> None:
        log.info("_on_index_changed: _idx=%s", _idx)
        self.key_changed.emit(self.current_key())

    def _on_text_finished(self) -> None:
        # editingFinished fires for both Enter + focus loss.  Emit so
        # panels react to manually-typed keys.
        log.info("_on_text_finished")
        self.key_changed.emit(self.current_key())
