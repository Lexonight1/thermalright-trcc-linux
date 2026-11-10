"""hudgui's Sensors panel: every reading the App has, grouped, live.

A card per group, in the gui's order and names -- the grouping and the
value format are the gui sensor picker's own (``ui/presentation/
sensor_display``), so both windows show a reading the same way.  The names,
units and groups come from ``ReadSensors`` once; the values follow
``SensorsUpdated``, the broadcast every UI hears, and only while the panel is
on screen.  A reading the panel has not seen yet (a module switched on, a
disk plugged in) lists everything again.  Clocks are not sensors and are
left out.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.commands import ControlCenterSnapshot, ReadSensors
from ...core.logs import per_frame
from ..presentation.sensor_display import format_sensor_value, group_sensors
from .flow import FlowArea
from .words import say_late, words

if TYPE_CHECKING:
    from ...core.events import SensorsUpdated
    from ...core.models import SensorInfo
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A card's width -- one for every card, so they line up in rows; wide enough
#: for a CPU or GPU's full name beside its value.
CARD_WIDTH = 370
#: A card's height -- one for every card, about eleven lines; a card with
#: more scrolls inside itself (his ask, 2026-11-10).
CARD_HEIGHT = 280
#: The gap between cards.
SPACING = 10
_CARD_STYLE = ("QWidget#card { background: rgba(255, 255, 255, 14); "
               "border: 1px solid rgba(255, 255, 255, 30); border-radius: 8px; }"
               "QLabel { color: #dfe5ea; background: transparent; border: none; }"
               "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")


class _Name(QLabel):
    """A reading's name, cut short with "..." to the room beside its value --
    "12th Gen Intel(R) Core(TM) i9-12900K Temperature (Package id 0)" pushed
    the value out of a card.  The whole name is on the tooltip."""

    def __init__(self, name: str, explanation: str, parent: QWidget) -> None:
        super().__init__(name, parent)
        log.debug("_Name: %s", name)
        self._full = name
        self.setToolTip(f"{name}\n{explanation}" if explanation else name)
        # Its width is what the value leaves, never the name's own.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """The room changed: the name cut to it again."""
        super().resizeEvent(event)
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight,
                                              self.width())
        frame_log.debug("_Name.resizeEvent: %d wide -> %r", self.width(), shown)
        if shown != self.text():
            self.setText(shown)


class SensorsView(QWidget):
    """The cards in rows of the visible width, scrolling down when they do not
    fit -- never sideways (his ask, 2026-11-10)."""

    def __init__(self, app: CommandBus, bus: BusBridge,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("SensorsView: built")
        self._app = app
        #: Each reading's value label, and its unit, by sensor id.
        self.values: dict[str, QLabel] = {}
        self._units: dict[str, str] = {}
        #: Every id the App listed, clocks too -- what is not new.
        self._known: set[str] = set()
        #: The cards, by name.
        self.cards: dict[str, QWidget] = {}
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        #: The cards, flowed to the visible width (``flow.FlowArea``).
        self.flow = FlowArea(CARD_WIDTH, SPACING, self)
        column.addWidget(self.flow)
        #: Said when there is nothing to show.
        self.note = QLabel("", self)
        self.note.setStyleSheet("color: #dfe5ea; font-size: 11pt;")
        column.addWidget(self.note)
        bus.sensors_updated.connect(self._on_sensors, type=Qt.ConnectionType.QueuedConnection)
        self.refresh()

    def refresh(self) -> None:
        """Every reading again, from the App: the cards rebuilt."""
        readings = self._app.dispatch(ReadSensors()).readings
        self._known = {reading.sensor_id for reading in readings}
        cards = group_sensors(readings)
        log.info("SensorsView.refresh: %d reading(s) on %d card(s)", len(readings), len(cards))
        for card in self.cards.values():
            card.deleteLater()
        self.values, self._units = {}, {}
        self.cards = {name: self._card(name, rows, self.flow.body) for name, rows in cards}
        self.flow.place(list(self.cards.values()))
        self._show_values({r.sensor_id: r.value for r in readings},
                          self._app.dispatch(ControlCenterSnapshot()).temp_unit)
        self.note.setText("" if cards else words(self._app)("No sensors found."))

    def _card(self, name: str, readings: list[SensorInfo], parent: QWidget) -> QWidget:
        """One card, every card one size: its name, then a line per reading --
        name, value -- scrolling inside the card when there are more lines
        than it holds."""
        log.debug("SensorsView._card: %s (%d)", name, len(readings))
        card = QWidget(parent)
        card.setObjectName("card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(_CARD_STYLE)
        card.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        column = QVBoxLayout(card)
        column.setContentsMargins(12, 8, 12, 10)
        column.setSpacing(4)
        title = QLabel(card)
        say_late(self._app, title, name)       # rebuilt on every refresh
        title.setStyleSheet("font-size: 12pt; font-weight: bold; color: #a9e6ff;")
        column.addWidget(title)
        lines = QScrollArea(card)
        lines.setWidgetResizable(True)
        lines.setFrameShape(QScrollArea.Shape.NoFrame)
        lines.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(3)
        for line, reading in enumerate(readings):
            label = _Name(reading.name, reading.explanation, body)
            value = QLabel("--", body)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(label, line, 0)
            grid.addWidget(value, line, 1)
            self.values[reading.id] = value
            # A temperature's symbol is the App's unit NOW, not the one it
            # was read under: format_sensor_value keeps a "°F" unit as °F.
            self._units[reading.id] = "°C" if reading.unit in ("°C", "°F") else reading.unit
        grid.setColumnStretch(0, 1)
        # Fewer lines than the card holds: they stay at the top, the room goes
        # under them, not between them.
        grid.setRowStretch(len(readings), 1)
        lines.setWidget(body)
        column.addWidget(lines, stretch=1)
        return card

    def _on_sensors(self, event: SensorsUpdated) -> None:
        """The broadcast: new values, while on screen.  A reading not seen
        before lists everything again."""
        if not self.isVisible():
            return
        frame_log.debug("SensorsView._on_sensors: %d", len(event.readings))
        if new := event.readings.keys() - self._known:
            log.info("SensorsView._on_sensors: %d new reading(s) %s -- listing again",
                     len(new), sorted(new)[:5])
            self.refresh()
            # The broadcast also carries keys ReadSensors never lists (the GPU
            # aliases gpu:0:* and gpu:nvidia:0:* on an NVIDIA box): known now,
            # or every broadcast rebuilt every card -- on his box, every 2 s.
            self._known |= event.readings.keys()
            return
        self._show_values(event.readings, event.temp_unit)

    def _show_values(self, values: dict[str, float], temp_unit: str) -> None:
        """*values* on their lines, as the gui's picker writes them."""
        frame_log.debug("SensorsView._show_values: %d in °%s", len(values), temp_unit)
        fahrenheit = int(temp_unit == "F")
        for sensor_id, value in values.items():
            if (label := self.values.get(sensor_id)) is not None:
                label.setText(format_sensor_value(value, self._units[sensor_id], fahrenheit))

    def showEvent(self, event: QShowEvent) -> None:
        """Dropped down: fresh values at once, not at the next broadcast."""
        log.debug("SensorsView.showEvent")
        super().showEvent(event)
        self.refresh()

