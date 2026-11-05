"""hudgui's overlay editor -- the Settings tab's text, metric and clock elements.

The gui's job, layout first (S2, 2026-11-05): the overlay on or off; add a
Text, a Metric, a Time, a Weekday or a Date; delete one; and for the selected
element its place, size, colour, font -- then its text, its metric (any
reading the App offers, a module's own included) with format and unit, or its
clock.  Dragging an element on the preview is the next step.

The layout is the App's: ``ResolveOverlay`` fills the list and the form, and
they fill again on every device event from any UI.  An edit sends
``UpdateOverlayElement`` with ONLY the field that changed, as the gui's does --
the element by its id; nothing here keeps a copy of the layout to send back.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFontComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.commands import (
    AddOverlayElement,
    DeleteOverlayElement,
    DeviceCanvas,
    EnableOverlay,
    ReadSensors,
    ResolveOverlay,
    UpdateOverlayElement,
)
from ...core.results import OverlayElementEntry
from .sources import _DeviceTab

if TYPE_CHECKING:
    from ...core.ports import CommandBus
    from ...core.results import LcdSnapshotResult
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection

log = logging.getLogger(__name__)

#: What the Add buttons make: (button, element type, clock source).
ADDS = (("Text", "text", ""), ("Metric", "metric", ""), ("Time", "clock", "time"),
        ("Weekday", "clock", "weekday"), ("Date", "clock", "date"))
#: The property an Add button carries: its row in ADDS.
_ADD = "hud_overlay_add"
#: The property a form control carries: the element field it edits.
_FIELD = "hud_overlay_field"
_FORM_STYLE = "QLabel, QCheckBox { color: #dfe5ea; }"


def describe(entry: OverlayElementEntry) -> str:
    """An element's line in the list: what it shows."""
    what = {"text": entry.text or "(empty text)", "metric": entry.metric or "(no reading)",
            "clock": entry.source}.get(entry.type, entry.type)
    line = f"{entry.type.capitalize()}: {what}"
    log.debug("describe: %s -> %s", entry.id, line)
    return line


class OverlayEditor(_DeviceTab):
    """The panel's overlay elements: the list, the selected one's form."""

    def __init__(self, app: CommandBus, bus: BusBridge, selection: DeviceSelection,
                 parent: QWidget | None = None) -> None:
        super().__init__(app, bus, selection, parent)
        log.debug("OverlayEditor: list + form")
        self.setStyleSheet(_FORM_STYLE)
        #: The App's elements, by id -- what the form shows, never sent back.
        self._elements: dict[str, OverlayElementEntry] = {}
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        # Two rows, so it fits the tab: the switch and Delete, then the Adds.
        top = QHBoxLayout()
        self.on_switch = QCheckBox("Overlay on", self)
        self.on_switch.toggled.connect(self._on_enabled)
        top.addWidget(self.on_switch)
        top.addStretch(1)
        self.delete = QPushButton("Delete", self)
        self.delete.clicked.connect(self._on_delete)
        top.addWidget(self.delete)
        column.addLayout(top)
        adds = QGridLayout()
        for index, (label, _type, _source) in enumerate(ADDS):
            button = QPushButton(f"+ {label}", self)
            button.setProperty(_ADD, index)
            button.clicked.connect(self._on_add)
            adds.addWidget(button, index // 3, index % 3)
        column.addLayout(adds)
        row = QHBoxLayout()
        self.list = QListWidget(self)
        self.list.setFixedWidth(170)
        self.list.currentItemChanged.connect(self._on_selected)
        row.addWidget(self.list)
        row.addLayout(self._form(), stretch=1)
        column.addLayout(row)
        self.show_state()

    def _form(self) -> QFormLayout:
        """The selected element's properties -- each edit sends its field."""
        log.debug("OverlayEditor._form")
        form = QFormLayout()
        self.x_box, self.y_box, self.size_box = QSpinBox(self), QSpinBox(self), QSpinBox(self)
        self.size_box.setRange(4, 200)
        self.colour_box = QPushButton("", self)
        self.font_box = QFontComboBox(self)
        self._narrow(self.font_box)
        self.bold_box, self.italic_box = QCheckBox("Bold", self), QCheckBox("Italic", self)
        self.text_box = QLineEdit(self)
        self.metric_box = QComboBox(self)
        self.metric_box.setEditable(True)
        self._narrow(self.metric_box)
        self.format_box = QLineEdit(self)
        self.unit_box = QCheckBox("Show the unit", self)
        self.clock_box = QComboBox(self)
        self.clock_box.addItems(["time", "weekday", "date"])
        style = QHBoxLayout()
        style.addWidget(self.bold_box)
        style.addWidget(self.italic_box)
        for label, widget in (("X", self.x_box), ("Y", self.y_box), ("Size", self.size_box),
                              ("Colour", self.colour_box), ("Font", self.font_box)):
            form.addRow(label, widget)
        form.addRow("", style)
        for label, widget in (("Text", self.text_box), ("Reading", self.metric_box),
                              ("Format", self.format_box), ("", self.unit_box),
                              ("Clock", self.clock_box)):
            form.addRow(label, widget)
        self._form_layout = form
        # Each control carries the field it edits, and edits by its KIND --
        # one handler per kind, not one per field.
        for widget, field in ((self.x_box, "x"), (self.y_box, "y"), (self.size_box, "size"),
                              (self.text_box, "text"), (self.format_box, "format"),
                              (self.bold_box, "bold"), (self.italic_box, "italic"),
                              (self.unit_box, "show_unit"), (self.metric_box, "metric"),
                              (self.clock_box, "source")):
            widget.setProperty(_FIELD, field)
        for spin in (self.x_box, self.y_box, self.size_box):
            spin.editingFinished.connect(self._on_number)
        for line in (self.text_box, self.format_box):
            line.editingFinished.connect(self._on_words)
        for flag in (self.bold_box, self.italic_box, self.unit_box):
            flag.toggled.connect(self._on_flag)
        for choice in (self.metric_box, self.clock_box):
            choice.activated.connect(self._on_choice)
        self.colour_box.clicked.connect(self._on_colour)
        self.font_box.currentFontChanged.connect(self._on_font)
        return form

    @staticmethod
    def _narrow(box: QComboBox) -> None:
        """A picker sized to the tab, not to its longest entry -- the font
        list's and the readings' made the editor wider than the tab."""
        log.debug("OverlayEditor._narrow: %s", type(box).__name__)
        box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(12)

    @property
    def _fields(self) -> tuple[QWidget, ...]:
        """Every form control -- quiet while the App's values go on."""
        log.debug("OverlayEditor._fields")
        return (self.x_box, self.y_box, self.size_box, self.font_box, self.bold_box, self.italic_box,
                self.text_box, self.metric_box, self.format_box, self.unit_box, self.clock_box)

    # ── Showing the App's layout ───────────────────────────────────────

    def _show(self, snapshot: LcdSnapshotResult) -> None:
        """The list and the form from ``ResolveOverlay``; the selection kept."""
        key = self._selection.key
        layout = self._app.dispatch(ResolveOverlay(key=key))
        canvas = self._app.dispatch(DeviceCanvas(key=key))
        picked = self._picked_id()
        log.info("OverlayEditor._show: %s %d element(s) (%s), on=%s", key,
                 len(layout.elements), layout.source, layout.enabled)
        self._quiet(self.on_switch, layout.enabled)
        if canvas.ok:
            self.x_box.setRange(0, canvas.width)
            self.y_box.setRange(0, canvas.height)
        self._elements = {e.id: e for e in layout.elements}
        self.list.blockSignals(True)
        self.list.clear()
        for entry in layout.elements:
            item = QListWidgetItem(describe(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self.list.addItem(item)
            if entry.id == picked:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self._fill_readings()
        self._fill_form(self._elements.get(self._picked_id()))

    def _fill_readings(self) -> None:
        """Every reading the App offers, a module's own included."""
        readings = self._app.dispatch(ReadSensors()).readings
        log.debug("OverlayEditor._fill_readings: %d", len(readings))
        self.metric_box.blockSignals(True)
        self.metric_box.clear()
        for reading in readings:
            self.metric_box.addItem(f"{reading.label or reading.sensor_id}  ({reading.sensor_id})",
                                userData=reading.sensor_id)
        self.metric_box.blockSignals(False)

    def _fill_form(self, entry: OverlayElementEntry | None) -> None:
        """The form from *entry*; only its type's own rows shown."""
        log.debug("OverlayEditor._fill_form: %s", entry and entry.id)
        for widget in self._fields:
            widget.setEnabled(entry is not None)
            widget.blockSignals(True)
        if entry is not None:
            self.x_box.setValue(entry.x)
            self.y_box.setValue(entry.y)
            self.size_box.setValue(entry.size)
            self.colour_box.setText(entry.color)
            self.colour_box.setStyleSheet(f"background: {entry.color};")
            if entry.font:
                self.font_box.setCurrentFont(QFont(entry.font))
            self.bold_box.setChecked(entry.bold)
            self.italic_box.setChecked(entry.italic)
            self.text_box.setText(entry.text)
            index = self.metric_box.findData(entry.metric)
            if index >= 0:
                self.metric_box.setCurrentIndex(index)
            else:
                self.metric_box.setEditText(entry.metric)
            self.format_box.setText(entry.format)
            self.unit_box.setChecked(entry.show_unit)
            self.clock_box.setCurrentText(entry.source)
        for widget in self._fields:
            widget.blockSignals(False)
        kind = entry.type if entry is not None else ""
        for widget, kinds in ((self.text_box, {"text"}), (self.metric_box, {"metric"}),
                              (self.format_box, {"metric", "clock"}), (self.unit_box, {"metric"}),
                              (self.clock_box, {"clock"})):
            self._form_layout.setRowVisible(widget, kind in kinds)

    def _picked_id(self) -> str:
        """The selected element's id, or ""."""
        item = self.list.currentItem()
        found = str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""
        log.debug("OverlayEditor._picked_id: %r", found)
        return found

    def _on_selected(self, *_items: object) -> None:
        log.info("OverlayEditor._on_selected: %s", self._picked_id())
        self._fill_form(self._elements.get(self._picked_id()))

    # ── Edits: each sends its own field ────────────────────────────────

    def _edit(self, **field: Any) -> None:
        """Send the selected element's ONE changed field, then show the App's."""
        element_id = self._picked_id()
        log.info("OverlayEditor._edit: %s %s", element_id, field)
        if element_id:
            self._send(UpdateOverlayElement(key=self._selection.key,
                                            element_id=element_id, **field))
            self.show_state()

    def _field(self) -> str:
        """The field the sending control edits."""
        widget = self.sender()
        found = "" if widget is None else str(widget.property(_FIELD))
        log.debug("OverlayEditor._field: %s", found)
        return found

    def _on_number(self) -> None:
        """A number edited (X, Y, size)."""
        widget = self.sender()
        log.debug("OverlayEditor._on_number")
        if isinstance(widget, QSpinBox):
            self._edit(**{self._field(): widget.value()})

    def _on_words(self) -> None:
        """Words edited (text, format)."""
        widget = self.sender()
        log.debug("OverlayEditor._on_words")
        if isinstance(widget, QLineEdit):
            self._edit(**{self._field(): widget.text()})

    def _on_flag(self, on: bool) -> None:
        """A flag ticked (bold, italic, show the unit)."""
        log.debug("OverlayEditor._on_flag: %s", on)
        self._edit(**{self._field(): on})

    def _on_choice(self, _index: int) -> None:
        """A choice made (the reading, the clock) -- a reading by its id."""
        widget = self.sender()
        log.debug("OverlayEditor._on_choice")
        if isinstance(widget, QComboBox):
            self._edit(**{self._field(): str(widget.currentData() or widget.currentText())})

    def _on_font(self, font: QFont) -> None:
        log.debug("OverlayEditor._on_font")
        self._edit(font=font.family())

    def choose_colour(self, start: str) -> str:
        """A colour the user picks, as #rrggbb, or "" -- a method, so a test picks."""
        log.debug("OverlayEditor.choose_colour: %s", start)
        colour = QColorDialog.getColor(QColor(start), self, "Element colour")
        return colour.name() if colour.isValid() else ""

    def _on_colour(self) -> None:
        log.info("OverlayEditor._on_colour")
        if colour := self.choose_colour(self.colour_box.text() or "#ffffff"):
            self._edit(color=colour)

    # ── On / off, add, delete ──────────────────────────────────────────

    def _on_enabled(self, on: bool) -> None:
        log.info("OverlayEditor._on_enabled: %s", on)
        self._send(EnableOverlay(key=self._selection.key, enabled=on))
        self.show_state()

    def _on_add(self) -> None:
        """Add the button's kind of element, near the top left, selected."""
        button = self.sender()
        label, kind, source = ADDS[int(button.property(_ADD))] if button is not None else ADDS[0]
        log.info("OverlayEditor._on_add: %s", label)
        fields: dict[str, Any] = {"type": kind, "x": 20, "y": 20}
        if kind == "text":
            fields["text"] = "Text"
        elif kind == "clock":
            fields["source"] = source
        result = self._send(AddOverlayElement(key=self._selection.key, **fields))
        self.show_state()
        new = getattr(result, "element", None)
        new_id = new.id if new is not None else ""
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == new_id:
                self.list.setCurrentRow(row)

    def _on_delete(self) -> None:
        element_id = self._picked_id()
        log.info("OverlayEditor._on_delete: %s", element_id)
        if element_id:
            self._send(DeleteOverlayElement(key=self._selection.key, element_id=element_id))
            self.show_state()
