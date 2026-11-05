"""hudgui's Sensors panel: every reading the App has, grouped, live.

A card per group -- CPU, GPU, Memory, Disks, Network, Fans, then any other
source (a module's readings) -- each listing its readings by name.  The
names, units and groups come from ``ReadSensors`` once; the values follow
``SensorsUpdated``, the broadcast every UI hears, and only while the panel is
on screen.  A reading the panel has not seen yet (a module switched on, a
disk plugged in) lists everything again.

Clocks are left out: the time and date are not sensors.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
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

from ...core.commands import ReadSensors
from ...core.logs import per_frame

if TYPE_CHECKING:
    from ...core.events import SensorsUpdated
    from ...core.models import SensorReading
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A sensor's source (its id up to the first ``:``) -> the card it goes on,
#: in the order the cards stand.  Any other source gets a card of its own,
#: after these, named after it.
GROUPS = {"cpu": "CPU", "gpu": "GPU", "memory": "Memory", "disk": "Disks",
          "net": "Network", "fan": "Fans"}
#: Sources that are not sensors.
_NOT_SENSORS = frozenset({"time", "date"})
#: A card's width.
CARD_WIDTH = 300
_CARD_STYLE = ("QWidget#card { background: rgba(255, 255, 255, 14); "
               "border: 1px solid rgba(255, 255, 255, 30); border-radius: 8px; }"
               "QLabel { color: #dfe5ea; background: transparent; border: none; }")


def group_of(sensor_id: str) -> str:
    """The card *sensor_id* goes on."""
    source = sensor_id.split(":", 1)[0]
    card = GROUPS.get(source, source.replace("_", " ").title())
    frame_log.debug("group_of: %s -> %s", sensor_id, card)
    return card


def grouped(readings: Iterable[SensorReading]) -> dict[str, list[SensorReading]]:
    """*readings* by card, the known cards first in their order, clocks out."""
    cards: dict[str, list[SensorReading]] = {name: [] for name in GROUPS.values()}
    for reading in readings:
        if reading.sensor_id.split(":", 1)[0] not in _NOT_SENSORS:
            cards.setdefault(group_of(reading.sensor_id), []).append(reading)
    found = {name: rows for name, rows in cards.items() if rows}
    log.debug("grouped: %s", {name: len(rows) for name, rows in found.items()})
    return found


def shown(value: float, unit: str) -> str:
    """*value* in *unit*, as a person reads it: whole numbers from ten up."""
    number = f"{value:.0f}" if abs(value) >= 10 or value == int(value) else f"{value:.1f}"
    joined = f"{number}{unit}" if unit in ("%", "°C", "°F") else f"{number} {unit}".strip()
    frame_log.debug("shown: %s %s -> %s", value, unit, joined)
    return joined


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
        cards = grouped(readings)
        log.info("SensorsView.refresh: %d reading(s) on %d card(s)", len(readings), len(cards))
        body = QWidget()
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.values, self._units = {}, {}
        self.cards = {name: self._card(name, rows, body) for name, rows in cards.items()}
        for card in self.cards.values():
            row.addWidget(card, alignment=Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        self._scroll.setWidget(body)        # the old body goes with it
        self.note.setText("" if cards else "No sensors found.")

    def _card(self, name: str, readings: list[SensorReading], parent: QWidget) -> QWidget:
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
        title = QLabel(name, card)
        title.setStyleSheet("font-size: 12pt; font-weight: bold; color: #a9e6ff;")
        grid.addWidget(title, 0, 0, 1, 2)
        for line, reading in enumerate(readings, 1):
            label = QLabel(reading.label or reading.sensor_id, card)
            label.setToolTip(reading.explanation or reading.sensor_id)
            value = QLabel(shown(reading.value, reading.unit), card)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(label, line, 0)
            grid.addWidget(value, line, 1)
            self.values[reading.sensor_id] = value
            self._units[reading.sensor_id] = reading.unit
        grid.setColumnStretch(0, 1)
        return card

    def _on_sensors(self, event: SensorsUpdated) -> None:
        """The broadcast: new values, while on screen.  A reading not seen
        before lists everything again."""
        if not self.isVisible():
            return
        frame_log.debug("SensorsView._on_sensors: %d", len(event.readings))
        if not event.readings.keys() <= self.values.keys() | _clocks(event.readings):
            log.info("SensorsView._on_sensors: a new reading -- listing again")
            self.refresh()
            return
        temp = f"°{event.temp_unit}"
        for sensor_id, value in event.readings.items():
            if (label := self.values.get(sensor_id)) is not None:
                unit = self._units[sensor_id]
                label.setText(shown(value, temp if unit in ("°C", "°F") else unit))

    def showEvent(self, event: QShowEvent) -> None:
        """Dropped down: fresh values at once, not at the next broadcast."""
        log.debug("SensorsView.showEvent")
        super().showEvent(event)
        self.refresh()


def _clocks(readings: dict[str, float]) -> set[str]:
    """The ids in *readings* that are clocks, not sensors."""
    found = {sid for sid in readings if sid.split(":", 1)[0] in _NOT_SENSORS}
    frame_log.debug("_clocks: %d", len(found))
    return found
