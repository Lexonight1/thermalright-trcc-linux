"""SensorsBox — the live readings, the HDD toggle, and the DRAM slots.

**This is the only box that moves, and it moves on the BUS.**  It used to be
the only box on a timer, at a hardcoded 2 s that ignored the user's
``refresh_interval_s`` entirely: at 10 s it re-read ten times per broadcast,
at 1 s it rendered half as often as data arrived.  ``MetricsLoop`` is the OS
dispatcher — one sweep, one broadcast — and this box now observes it, the way
``ui/gui`` always has.

**Two feeders, one render, because the halves have different lifetimes.**
Identity (id, label, category, unit) comes from ``ReadSensors`` and changes
when hardware does; VALUES change every tick, and the broadcast carries values
only.  So :meth:`refresh_live` reads the catalog on build and on every
view-switch, :meth:`on_sensors_updated` merges each broadcast onto it, and both
end in :meth:`_render`.

The HDD flag and the DRAM slot list are loaded ONCE, in ``_build_ui``, and
deliberately stay out of both: ``ListMemorySlots`` shells out to ``dmidecode``
under a privilege helper, so folding it onto the broadcast would run a root
subprocess every interval for a list that cannot change without opening the
case.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QCheckBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout

from .....core.commands import (
    ControlCenterSnapshot,
    ListMemorySlots,
    ReadSensors,
    SetHddEnabled,
)
from .....core.events import SensorsUpdated
from .....core.models import SensorReading
from ....presentation.sensor_display import apply_live_values
from ._base import SystemBox

log = logging.getLogger(__name__)

#: Height cap on the DRAM list — a few slots, not a scrolling panel.
_MEMORY_LIST_HEIGHT = 90


class SensorsBox(SystemBox):
    """Live hardware readings, with the two settings that shape them."""

    TITLE = "Sensors (live)"
    STRETCH = 1

    def _build_ui(self) -> None:
        log.debug("_build_ui")
        # Identity for every row the broadcast can carry a value for.  Empty
        # until the first read, so a broadcast arriving first renders nothing
        # rather than inventing unlabelled rows.
        self._catalog: list[SensorReading] = []
        layout = QVBoxLayout(self)
        # Whether disk metrics reach sensor broadcasts at all.  Spinning a
        # sleeping disk to read its temperature is a real cost, which is why
        # the toggle exists rather than being always-on.
        self._hdd_check = QCheckBox("Include HDD metrics in broadcasts", self)
        self._hdd_check.toggled.connect(self._on_hdd_toggled)
        layout.addWidget(self._hdd_check)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._list)

        # DRAM identity is per-OS by nature: only Linux enriches with SPD/IMC
        # timings, so an absent field arrives as "" and is rendered "NC" --
        # the convention ``Platform.memory_info`` documents.
        self._memory = QListWidget(self)
        self._memory.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._memory.setMaximumHeight(_MEMORY_LIST_HEIGHT)
        layout.addWidget(QLabel("Memory slots:", self))
        layout.addWidget(self._memory)

        # The switch follows the App, whoever changed it (HddEnabledChanged).
        self._bus.app_settings_changed.connect(
            self._on_app_settings_changed,
            type=Qt.ConnectionType.QueuedConnection)
        self.refresh_hdd()
        self.refresh_memory()
        self.refresh_live()

    def _on_app_settings_changed(self, event: object) -> None:
        log.debug("_on_app_settings_changed: %s", type(event).__name__)
        self.refresh_hdd()

    def refresh_live(self) -> None:
        """Re-read the catalog AND show it.  The explicit path.

        Called on build and on every view-switch (:meth:`showEvent`), never on
        a clock: one dispatch when the user opens the panel, so it populates
        instead of showing whatever was on screen when it last left.
        """
        log.debug("refresh_live")
        r = self.dispatch(ReadSensors())
        self._catalog = list(r.readings)
        self._render(r.readings)

    def on_sensors_updated(self, event: SensorsUpdated) -> None:
        """Render one broadcast.  Wired by :class:`SystemPanel`.

        **The visibility gate is here and not on the host**, because a Qt
        signal fires whether or not anyone is looking — unlike the timer this
        replaces, which ``BasePanel.hideEvent`` suspended.  Without it,
        retiring the timer would quietly undo the hidden-panel fix.

        Per-broadcast, so DEBUG.
        """
        if not self.isVisible():
            log.debug("on_sensors_updated: skipped — box is off screen")
            return
        log.debug("on_sensors_updated: %d value(s), temp_unit=%s",
                  event.reading_count, event.temp_unit)
        self._render(apply_live_values(
            self._catalog, event.readings, temp_unit=event.temp_unit))

    def showEvent(self, event: QShowEvent) -> None:
        """Repopulate the moment the panel comes back on screen."""
        log.debug("showEvent: back on screen — re-reading the catalog")
        super().showEvent(event)
        self.refresh_live()

    def _render(self, readings: Sequence[SensorReading]) -> None:
        """The ONE writer of the readings list — both feeders end here."""
        log.debug("_render: %d reading(s)", len(readings))
        self._list.clear()
        for reading in readings:
            self._list.addItem(QListWidgetItem(
                f"{reading.sensor_id:30}  "
                f"{reading.value:>10.2f} {reading.unit:<6}  "
                f"({reading.category})",
            ))

    def refresh_hdd(self) -> None:
        """Show the PERSISTED flag, not whatever the widget last showed.

        ``blockSignals`` because ``setChecked`` emits ``toggled``, and an
        unguarded load would dispatch a write on every refresh -- the setting
        would then be whatever the UI happened to render, not what the user
        chose.  Same shape as ``MaintenanceBox.refresh``.
        """
        log.debug("refresh_hdd")
        snap = self.dispatch(ControlCenterSnapshot())
        log.info("refresh_hdd: enabled=%s", snap.hdd_enabled)
        self._hdd_check.blockSignals(True)
        self._hdd_check.setChecked(bool(snap.hdd_enabled))
        self._hdd_check.blockSignals(False)

    def refresh_memory(self) -> None:
        """List the DRAM slots.  Absent per-OS fields render as NC.

        NOT on the tick — see the module docstring.
        """
        log.debug("refresh_memory")
        r = self.dispatch(ListMemorySlots())
        log.info("refresh_memory: %d slot(s)", len(r.slots))
        self._memory.clear()
        for slot in r.slots:
            parts = [slot.locator or "NC", slot.size or "NC",
                     slot.speed or "NC", slot.manufacturer or "NC"]
            self._memory.addItem(QListWidgetItem("  ".join(parts)))
        if not r.slots:
            self._memory.addItem(QListWidgetItem("No DRAM slots reported"))

    def _on_hdd_toggled(self, checked: bool) -> None:
        log.info("_on_hdd_toggled: enabled=%s", checked)
        r = self.dispatch(SetHddEnabled(enabled=checked))
        if not r.ok:
            log.warning("_on_hdd_toggled: refused — %s", r.message)
