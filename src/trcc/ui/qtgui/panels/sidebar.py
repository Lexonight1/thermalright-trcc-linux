"""ActivitySidebar — the left rail: the devices found, then navigation.

**The device list is the point.**  ``ui/gui``'s left rail IS the device list
(``uc_device.py``, 180x800, "Shows connected LCD devices as clickable
buttons") and choosing there is what every other part of the window then
edits.  qtgui built navigation here instead and demoted the device list to
one destination among thirteen, with a private ``DevicePickerWidget``
repeated in ten panels.

The rail's own docstring used to say it "replaces legacy
``gui/uc_activity_sidebar.py``" — which is not a navigation rail at all, but
the live-sensor list you click to add an overlay element.  The name was taken
from the wrong module, and the thing gui actually keeps on the side never
arrived.

So the rail now carries both: the attached devices at the top (choosing one
drives the window's ``DeviceSelection`` for that device's KIND), and the panel
navigation below it.  Buttons stay data-driven (``_ENTRIES``) so adding a
panel is still one row.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtCore import Qt as _Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ....core.commands import ListDevices
from ..base import BasePanel

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Entry:
    key: str         # MainWindow uses this as the stacked-widget id
    label: str       # display text


# Add panels here as they land — each one corresponds to a setCurrentWidget()
# target on MainWindow.  Order = vertical order in the sidebar.
_ENTRIES: tuple[_Entry, ...] = (
    _Entry("devices",    "Devices"),
    _Entry("display",    "Display"),
    _Entry("preview",    "Preview"),
    _Entry("themes",     "Themes"),
    _Entry("cloud",      "Cloud themes"),
    _Entry("masks",      "Masks"),
    _Entry("overlay",    "Overlay editor"),
    _Entry("screencast", "Screencast"),
    _Entry("config",     "Configuration"),
    _Entry("led",        "LED"),
    _Entry("rgb",        "RGB Lighting"),
    _Entry("status",     "Status"),
    _Entry("system",     "System"),
    _Entry("about",      "About"),
)


#: The panel key a nav button carries, so its slot can be a named method
#: instead of a closure over the loop variable.
_KEY_PROPERTY = "trcc_panel_key"

#: Qt.ItemDataRole.UserRole — the device key behind a rail row.
_KEY_ROLE = 0x0100
#: UserRole + 1 — its kind ("lcd" / "led"), which picks the selection.
_KIND_ROLE = 0x0101


class ActivitySidebar(BasePanel):
    """The left rail: attached devices on top, panel navigation below."""

    selected = Signal(str)
    #: (key, kind) — the user chose a device.  MainWindow routes it to the
    #: DeviceSelection for that kind; the rail never owns the selection.
    device_chosen = Signal(str, str)

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        self.setFixedWidth(200)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 16)
        layout.setSpacing(4)

        title = QLabel("TRCC", self)
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(12)

        # ── The devices found ──────────────────────────────────────────
        devices_label = QLabel("Devices", self)
        devices_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(devices_label)

        self._devices = QListWidget(self)
        self._devices.setMaximumHeight(200)
        # Product names are long ("Winbond Trofeo Vision 9.16 LCD"), and a
        # horizontal scrollbar to read the end of one is a worse answer than
        # eliding it -- the full key + wire + kind is on the tooltip.
        self._devices.setTextElideMode(_Qt.TextElideMode.ElideRight)
        self._devices.setHorizontalScrollBarPolicy(
            _Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._devices.currentItemChanged.connect(self._on_device_row)
        layout.addWidget(self._devices)
        layout.addSpacing(12)

        self.refresh_devices()
        # Attach / detach from ANY UI re-lists the rail.  Queued: the bridge
        # delivers on the Qt thread.
        for signal in (self._bus.device_connected, self._bus.device_disconnected):
            signal.connect(self._on_fleet_changed,
                           type=_Qt.ConnectionType.QueuedConnection)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._buttons: dict[str, QPushButton] = {}
        for entry in _ENTRIES:
            button = QPushButton(entry.label, self)
            button.setCheckable(True)
            button.setMinimumHeight(36)
            # The panel key rides on the button rather than being captured in
            # a closure: ``feedback_no_lambdas`` — every callable gets a real
            # symbol so a traceback, a debugger and a grep all name it.
            button.setProperty(_KEY_PROPERTY, entry.key)
            button.clicked.connect(self._on_nav_clicked)
            self._buttons[entry.key] = button
            self._group.addButton(button)
            layout.addWidget(button)

        layout.addStretch(1)

        # Default selection — first entry checked.
        first = _ENTRIES[0]
        self._buttons[first.key].setChecked(True)

    # ── Devices ────────────────────────────────────────────────────────

    def refresh_devices(self) -> None:
        """Re-list the attached devices, keeping the current choice."""
        previous = self.current_device()
        self._devices.blockSignals(True)
        self._devices.clear()
        for entry in self.dispatch(ListDevices()).devices:
            label = f"{entry.vendor} {entry.product}".strip() or entry.key
            item = QListWidgetItem(label, self._devices)
            item.setToolTip(f"{entry.key} — {entry.wire or '?'} ({entry.kind or '?'})")
            item.setData(_KEY_ROLE, entry.key)
            item.setData(_KIND_ROLE, entry.kind)
        log.info("refresh_devices: %d device(s) on the rail",
                 self._devices.count())
        if previous:
            self._select_row(previous)
        self._devices.blockSignals(False)
        # A first listing has no prior choice; adopt the first row so the
        # window has a device without the user hunting for one.
        if not previous and self._devices.count():
            self._devices.setCurrentRow(0)

    def device_keys(self) -> set[str]:
        """Every device key the rail is showing."""
        keys = {self._devices.item(i).data(_KEY_ROLE)
                for i in range(self._devices.count())}
        log.debug("device_keys -> %s", keys)
        return keys

    def current_device(self) -> str:
        """The key of the highlighted row, or ""."""
        item = self._devices.currentItem()
        key = str(item.data(_KEY_ROLE)) if item is not None else ""
        log.debug("current_device -> %r", key)
        return key

    def choose(self, key: str) -> None:
        """Select *key* on the rail as a user click would."""
        log.info("choose: key=%s", key)
        self._select_row(key)

    def show_device(self, key: str) -> None:
        """Reflect a choice made elsewhere, without re-announcing it."""
        log.debug("show_device: key=%s", key)
        self._devices.blockSignals(True)
        self._select_row(key)
        self._devices.blockSignals(False)

    def _select_row(self, key: str) -> None:
        for i in range(self._devices.count()):
            if self._devices.item(i).data(_KEY_ROLE) == key:
                self._devices.setCurrentRow(i)
                return
        log.debug("_select_row: %r is not on the rail", key)

    def _on_device_row(self, item: object, _previous: object = None) -> None:
        """A row became current — announce (key, kind) for MainWindow."""
        if item is None:
            log.debug("_on_device_row: cleared")
            return
        key = str(item.data(_KEY_ROLE))          # type: ignore[attr-defined]
        kind = str(item.data(_KIND_ROLE) or "")  # type: ignore[attr-defined]
        log.info("_on_device_row: key=%s kind=%s", key, kind)
        self.device_chosen.emit(key, kind)

    # ── Navigation ─────────────────────────────────────────────────────

    def select(self, key: str) -> None:
        """Programmatically check the button for *key* (no signal emitted)."""
        log.debug("select: key=%s", key)
        button = self._buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def _on_nav_clicked(self) -> None:
        """A navigation button was pressed — announce its panel key."""
        button = self.sender()
        key = "" if button is None else str(button.property(_KEY_PROPERTY))
        log.info("_on_nav_clicked: key=%s", key)
        if key:
            self.selected.emit(key)

    def _on_fleet_changed(self, event: object) -> None:
        """A device attached or detached anywhere — re-list the rail."""
        log.debug("_on_fleet_changed: event=%s", type(event).__name__)
        self.refresh_devices()
