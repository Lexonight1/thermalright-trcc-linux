"""The sensors panel must read the bus, not a clock of its own.

The vision is *every UI over the universal bus*.  Measured 2026-09-19 on the
mock fleet, qtgui's System panel was the one hole in it:

    VISIBLE  interval= 1.0s  ReadSensors 8 = 1.00/s   SensorsUpdated 8 = 1.00/s
    VISIBLE  interval=10.0s  ReadSensors 8 = 1.00/s   SensorsUpdated 0 = 0.00/s

The poll rate does not move.  ``MetricsLoop`` is the OS dispatcher — one sweep,
one broadcast, at ``refresh_interval_s`` — and ``ui/gui`` observes it
(``trcc_app.py:439``) while qtgui hardcoded ``_SENSOR_REFRESH_MS = 2000`` and
re-derived everything itself.  So at a 10 s interval the panel polled ten times
per broadcast, and at 1 s it rendered half as often as data arrived, showing
readings a second stale while fresh ones were published.

**Why "connect the signal, delete the timer" was not the whole fix.** The
broadcast carries VALUES only (``SensorsUpdated.readings`` is
``{sensor_id: value}``); the panel renders id · value · unit · (category), and
unit/category exist only on ``SensorReading``, which ``ReadSensors`` builds.
A bare connection blanks two of the four columns.  So identity comes from the
Query — on build and on every view-switch — and values come from the bus,
merged by ``sensor_display.apply_live_values``.

**Two pollers, not one.**  The System panel holds the sensors box AND a
``SensorPickerWidget`` embedded in ``DashboardBox``, each on its own 2 s timer
— which is why the measured rate above is 1.00/s and not 0.50/s.  Both are
gated here, and the picker's half is driven through the REAL panel: it is a
GRANDCHILD (picker < DashboardBox < SystemPanel), and whether a grandchild
sees a view-switch is a question for the toolkit, not for a plan.

Not a CPU story, and this file will not pretend otherwise: one ``ReadSensors``
costs 0.165 ms.  It is a cadence-correctness story.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QStackedWidget

from tests.mock_platform import MockPlatform
from trcc.app import App
from trcc.core.events import SensorsUpdated
from trcc.ui.bus_bridge import BusBridge
from trcc.ui.qtgui.panels.system_panel import SystemPanel

_SPECS = [{"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100}]

#: A reading every platform's mock enumerator advertises, so the merge has a
#: row to land on.  Its catalog unit is °C, which is what makes the °F case
#: below a real question rather than a tautology.
_CPU_TEMP = "cpu:temp"


class _Harness:
    """A real ``SystemPanel`` in a real stack, with every dispatch counted."""

    def __init__(self, qtbot, tmp_path: Path) -> None:
        self.app = App(MockPlatform(_SPECS, tmp_path, host_sensors=False))
        self.counts: Counter[str] = Counter()
        real = self.app.dispatch

        def counting(command: Any) -> Any:
            self.counts[type(command).__name__] += 1
            return real(command)

        # Instance attribute, so only THIS app is instrumented and the count is
        # what the widgets actually asked for.
        self.app.dispatch = counting          # type: ignore[method-assign]

        self.bus = BusBridge(self.app.events)
        self.stack = QStackedWidget()
        self.elsewhere = QLabel("another panel")
        self.panel = SystemPanel(self.app, self.bus)
        self.stack.addWidget(self.elsewhere)
        self.stack.addWidget(self.panel)
        qtbot.addWidget(self.stack)
        self.stack.show()
        qtbot.waitExposed(self.stack)
        self.stack.setCurrentWidget(self.panel)
        qtbot.wait(20)
        self.qtbot = qtbot

    def broadcast(self, value: float, *, temp_unit: str = "C") -> None:
        """Publish one ``SensorsUpdated`` and let the queued signal land."""
        self.app.events.publish(SensorsUpdated(
            reading_count=1,
            readings={_CPU_TEMP: value},
            temp_unit=temp_unit,
        ))
        self.qtbot.wait(50)

    def row(self, sensor_id: str) -> str:
        """The rendered line for *sensor_id*, or "" when it is not shown."""
        listing = self.panel._sensors._list
        for i in range(listing.count()):
            text = listing.item(i).text()
            if text.split()[0] == sensor_id:
                return text
        return ""

    @property
    def picker(self) -> Any:
        """The picker embedded in the dashboard box — a GRANDCHILD."""
        return self.panel._dash._picker

    def picked_row(self, sensor_id: str) -> str:
        """The picker's rendered line for *sensor_id*, or ""."""
        listing = self.picker._sensor_list
        for i in range(listing.count()):
            item = listing.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == sensor_id:
                return item.text()
        return ""

    def show_panel(self) -> None:
        self.stack.setCurrentWidget(self.panel)
        self.qtbot.wait(50)

    def hide_panel(self) -> None:
        self.stack.setCurrentWidget(self.elsewhere)
        self.qtbot.wait(50)


def test_a_broadcast_updates_the_list_without_polling(qtbot, tmp_path) -> None:
    """The bus is the cadence: a broadcast lands, and nothing is re-read.

    MUTATION CHECK: drop the ``sensors_updated`` connection in
    ``SystemPanel._setup_ui`` and this fails with the row still showing the
    value the build-time read left behind.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        h.counts.clear()
        h.broadcast(61.5)

        assert "61.50" in h.row(_CPU_TEMP), (
            f"the broadcast never reached the list — row is {h.row(_CPU_TEMP)!r}"
        )
        assert h.counts["ReadSensors"] == 0, (
            "the panel re-polled ReadSensors to render a broadcast it was "
            "already handed — that is the private cadence this fixes"
        )
    finally:
        h.app.close()


def test_the_panel_schedules_nothing_periodic(qtbot, tmp_path) -> None:
    """No private clock at all — the broadcast IS the clock.

    ``SystemPanel`` owned the one timer in its subtree, and ``refresh_live``
    was what it fired.  With the panel on the bus there is nothing left to
    schedule, and a timer reappearing here means a second cadence for one
    datum.

    MUTATION CHECK: restore ``start_periodic_updates(2000, ...)`` and this
    fails.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        assert not h.panel._updates.is_active, (
            "the System panel is ticking on its own clock again — sensors "
            "arrive on SensorsUpdated at the user's refresh_interval_s"
        )
    finally:
        h.app.close()


def test_a_hidden_panel_ignores_the_broadcast(qtbot, tmp_path) -> None:
    """Phase 1's win, kept: a signal fires whether or not anyone is looking.

    The timer this replaces was suspended by ``BasePanel.hideEvent``.  A bus
    connection has no such property, so the gate moves INTO the slot — and
    without it, retiring the timer would silently undo the hidden-panel fix
    landed hours earlier.

    MUTATION CHECK: remove the ``isVisible()`` guard from
    ``SensorsBox.on_sensors_updated`` and this fails.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        h.hide_panel()
        before = h.row(_CPU_TEMP)
        h.counts.clear()
        h.broadcast(99.0)

        assert h.row(_CPU_TEMP) == before, (
            "a hidden panel rebuilt its list for a broadcast nobody can see"
        )
    finally:
        h.app.close()


def test_a_view_switch_repopulates_immediately(qtbot, tmp_path) -> None:
    """Opening the panel must not show stale readings until the next tick.

    Measured before the change: switching to the System panel dispatched
    ZERO commands, so at the 2 s default the user read stale values for up to
    two seconds, and at 10 s for ten.  ``ui/gui`` has had this
    view-switch immediate-populate path all along (``_fan_out_metrics``).

    MUTATION CHECK: delete ``SensorsBox.showEvent`` and this fails with no
    ReadSensors dispatched on the switch.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        h.hide_panel()
        h.counts.clear()
        h.show_panel()

        assert h.counts["ReadSensors"] >= 1, (
            "coming back to the panel showed whatever was on screen when it "
            "left — identity and values both come from the Query on a switch"
        )
        assert h.row(_CPU_TEMP), "the list is empty after a view-switch"
    finally:
        h.app.close()


def test_the_broadcasts_unit_reaches_the_row(qtbot, tmp_path) -> None:
    """A °F broadcast must not be labelled with the catalog's °C.

    The catalog is cached per view; the broadcast self-describes its unit
    (``SensorsUpdated.temp_unit`` exists for exactly this).  Without it the
    panel renders "140.90 °C" for a Fahrenheit reading until the user
    navigates away and back.

    MUTATION CHECK: pass the catalog's unit straight through in
    ``apply_live_values`` and this fails.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        h.broadcast(140.9, temp_unit="F")
        row = h.row(_CPU_TEMP)

        assert "°F" in row, f"row kept the catalog's unit: {row!r}"
    finally:
        h.app.close()


# ── the second poller: the picker embedded in the dashboard box ──────────


def test_the_picker_renders_a_broadcast_without_polling(qtbot, tmp_path) -> None:
    """The picker's live preview is the same datum on the same bus.

    It is a GRANDCHILD of the panel, which is why it is driven here through the
    real ``SystemPanel`` rather than standalone: the whole question is whether
    the widget sees what the stack does.

    MUTATION CHECK: drop the connection in ``SensorPickerWidget.__init__`` and
    this fails with the row still showing the build-time value.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        h.counts.clear()
        h.broadcast(63.5)

        assert "63.5" in h.picked_row(_CPU_TEMP), (
            f"the broadcast never reached the picker — row is "
            f"{h.picked_row(_CPU_TEMP)!r}"
        )
        assert h.counts["ReadSensors"] == 0, (
            "the picker re-polled to render values it was handed"
        )
    finally:
        h.app.close()


def test_the_picker_schedules_nothing_periodic(qtbot, tmp_path) -> None:
    """The last ``ReadSensors`` timer in qtgui.

    Phase 1 gated this widget on ``isVisible()`` so a hidden host stopped it;
    what remained was that a VISIBLE one polled every 2 s whatever the user set
    ``refresh_interval_s`` to — a third independent cadence for one datum.

    MUTATION CHECK: give it a ``PeriodicUpdater`` again and this fails.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        assert not hasattr(h.picker, "_updates"), (
            "the sensor picker is back on a private clock — sensors arrive on "
            "SensorsUpdated at the cadence the user configured"
        )
    finally:
        h.app.close()


def test_a_hidden_picker_ignores_the_broadcast(qtbot, tmp_path) -> None:
    """Phase 1's measurement, kept: 0.52/s with every host off screen.

    MUTATION CHECK: remove the ``isVisible()`` guard from
    ``SensorPickerWidget._on_sensors_updated`` and this fails.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        h.hide_panel()
        before = h.picked_row(_CPU_TEMP)
        h.broadcast(99.0)

        assert h.picked_row(_CPU_TEMP) == before, (
            "a picker nobody can see rebuilt its whole list for a broadcast"
        )
    finally:
        h.app.close()


def test_a_grandchild_picker_repopulates_on_a_view_switch(qtbot, tmp_path) -> None:
    """Driven, not assumed: does a grandchild see the stack's switch?

    ``SensorsBox`` is a direct child of the panel; this widget is a level
    deeper, and Phase 1 already found one hook that a grandchild never
    receives (which is why its visibility gate is a runtime check and not a
    hide event).  If the toolkit does not deliver this one either, the picker
    would open showing values from whenever it was last on screen.

    Counted on the PICKER, not on the App: the sensors box refreshes on the
    same switch, so an app-wide ``ReadSensors`` count passes this test whether
    or not the picker ever wakes up.  It did, on the first run of this gate.

    MUTATION CHECK: delete ``SensorPickerWidget.showEvent`` and this fails.
    """
    h = _Harness(qtbot, tmp_path)
    try:
        h.hide_panel()
        refreshes: list[int] = []
        real = h.picker._refresh

        def spy() -> None:
            refreshes.append(1)
            real()

        h.picker._refresh = spy           # type: ignore[method-assign]
        h.show_panel()

        assert refreshes, (
            "the picker came back on screen still showing what it had when it "
            "left — identity and values both come from the Query on a switch"
        )
        assert h.picked_row(_CPU_TEMP), "the picker is empty after a switch"
    finally:
        h.app.close()
