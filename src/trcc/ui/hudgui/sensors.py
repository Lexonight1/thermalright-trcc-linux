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
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.commands import ControlCenterSnapshot, ReadSensors
from ...core.logs import per_frame
from ..presentation.sensor_display import format_sensor_value, group_sensors
from .words import say_late, words

if TYPE_CHECKING:
    from ...core.events import SensorsUpdated
    from ...core.models import SensorInfo
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A card's width.
CARD_WIDTH = 300
_CARD_STYLE = ("QWidget#card { background: rgba(255, 255, 255, 14); "
               "border: 1px solid rgba(255, 255, 255, 30); border-radius: 8px; }"
               "QLabel { color: #dfe5ea; background: transparent; border: none; }")


class SensorsView(QWidget):
    """The cards, side by side, scrolling when they do not fit."""

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
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget "
                                   "{ background: transparent; }")
        column.addWidget(self._scroll)
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
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.values, self._units = {}, {}
        self.cards = {name: self._card(name, rows, body) for name, rows in cards}
        for card in self.cards.values():
            row.addWidget(card, alignment=Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        self._scroll.setWidget(body)        # the old body goes with it
        self._show_values({r.sensor_id: r.value for r in readings},
                          self._app.dispatch(ControlCenterSnapshot()).temp_unit)
        self.note.setText("" if cards else words(self._app)("No sensors found."))

    def _card(self, name: str, readings: list[SensorInfo], parent: QWidget) -> QWidget:
        """One card: its name, then a line per reading -- name, value."""
        log.debug("SensorsView._card: %s (%d)", name, len(readings))
        card = QWidget(parent)
        card.setObjectName("card")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(_CARD_STYLE)
        card.setFixedWidth(CARD_WIDTH)
        grid = QGridLayout(card)
        grid.setContentsMargins(12, 8, 12, 10)
        grid.setVerticalSpacing(3)
        title = QLabel(card)
        say_late(self._app, title, name)       # rebuilt on every refresh
        title.setStyleSheet("font-size: 12pt; font-weight: bold; color: #a9e6ff;")
        grid.addWidget(title, 0, 0, 1, 2)
        for line, reading in enumerate(readings, 1):
            label = QLabel(reading.name, card)
            label.setToolTip(reading.explanation or reading.id)
            value = QLabel("--", card)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(label, line, 0)
            grid.addWidget(value, line, 1)
            self.values[reading.id] = value
            # A temperature's symbol is the App's unit NOW, not the one it
            # was read under: format_sensor_value keeps a "°F" unit as °F.
            self._units[reading.id] = "°C" if reading.unit in ("°C", "°F") else reading.unit
        grid.setColumnStretch(0, 1)
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

