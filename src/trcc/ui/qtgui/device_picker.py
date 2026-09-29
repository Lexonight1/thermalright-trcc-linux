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

from ...core.commands import DiscoverDevices, ListDevices
from .device_selection import DeviceSelection

if TYPE_CHECKING:
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge

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
        app: CommandBus,
        bus: BusBridge | None = None,
        *,
        kind_filter: str | None = None,
        parent: QWidget | None = None,
        selection: DeviceSelection | None = None,
    ) -> None:
        log.debug("__init__: app=%s bus=%s selection=%s", app, bus, selection)
        super().__init__(parent)
        self._app = app
        self._bus = bus
        self._kind_filter = kind_filter  # "lcd" / "led" / None
        self._selection = selection
        self._build()
        self._populate_from_app()
        if selection is not None:
            self._bind(selection)
        if bus is not None:
            # Refresh dropdown when devices are attached/detached from
            # any UI — events arrive on the Qt thread thanks to the
            # bridge's queued connection setup.
            for signal in (bus.device_connected, bus.device_disconnected):
                signal.connect(self._on_fleet_changed,
                               type=Qt.ConnectionType.QueuedConnection)

    def _on_fleet_changed(self, event: object) -> None:
        """A device attached or detached anywhere — rebuild the dropdown."""
        log.debug("_on_fleet_changed: %s", type(event).__name__)
        self._populate_from_app()

    def _bind(self, selection: DeviceSelection) -> None:
        """Make this combo a VIEW of the window's shared selection.

        Both directions, which is what was missing: a user pick updates the
        selection, and a pick made in any other panel updates this combo.
        ``set_key`` blocks signals, so the round trip cannot loop, and
        ``DeviceSelection.set_key`` no-ops on an unchanged key.

        Seeding runs whichever way has information.  The FIRST picker built
        finds an empty selection and seeds it with its own sensible default
        (the first attached device); every later picker finds that key and
        adopts it instead of defaulting to index 0 -- which is precisely the
        divergence this replaces.
        """
        log.info("_bind: selection.key=%r combo=%r",
                 selection.key, self.current_key())
        if selection.key:
            self.set_key(selection.key)
        else:
            selection.set_key(self.current_key())
        self.key_changed.connect(selection.set_key)
        selection.changed.connect(self.set_key)

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
        log.debug("current_key")
        data = self._combo.currentData()
        if data:
            return str(data).strip()
        return self._combo.currentText().strip()

    def set_key(self, key: str) -> None:
        """Set the visible key without emitting :sig:`key_changed`."""
        log.debug("set_key: key=%s", key)
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
        log.debug("_build")
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
        """Rebuild the dropdown from ``app.devices``, preserving choice.

        Devices attach *asynchronously* — usually after the panels that host
        this picker have already built and run their initial refresh against an
        empty ``app.devices``.  When a device then appears (or the selection
        otherwise changes as a result of the rebuild), we emit
        :sig:`key_changed` so dependent panels (theme / mask / cloud browsers,
        preview) re-fetch their lists.  Without this the dropdown would show a
        device while every selection grid stayed empty.  The rebuild itself is
        done under blocked signals so the per-widget churn doesn't thrash;
        the single deliberate emit at the end carries the real transition.
        """
        previous_key = self.current_key()

        self._combo.blockSignals(True)
        self._combo.clear()
        for entry in self._app.dispatch(ListDevices()).devices:
            if not self._matches_filter(entry):
                continue
            label = f"{entry.key} — {entry.vendor} {entry.product}".strip()
            self._combo.addItem(label, userData=entry.key)

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

        new_key = self.current_key()
        if new_key != previous_key:
            log.info(
                "_populate_from_app: selection %r -> %r — emitting key_changed",
                previous_key, new_key,
            )
            self.key_changed.emit(new_key)

    def _matches_filter(self, entry) -> bool:
        """Optional 'lcd' / 'led' filter — narrow when callers know."""
        log.debug("_matches_filter: entry=%s", entry)
        if self._kind_filter is None:
            return True
        if not entry.kind:
            return True
        return entry.kind.lower().endswith(self._kind_filter.lower())

    def _index_for_key(self, key: str) -> int:
        log.debug("_index_for_key: key=%s", key)
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
