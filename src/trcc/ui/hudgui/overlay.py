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
from functools import partial
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
    QLabel,
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
    ReadSensors,
    UpdateOverlayElement,
)
from ...core.results import OverlayElementEntry
from ..presentation.theme_settings import ElementDrag, SettingsView
from ..qt_words import say, say_item
from .sources import _SettingsTab
from .words import words

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...core.commands._base import Command
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge
    from ..device_selection import DeviceSelection
    from ..qt_words import Tr

log = logging.getLogger(__name__)

#: What the Add buttons make: (button, element type, clock source).
ADDS = (("Text", "text", ""), ("Metric", "metric", ""), ("Time", "clock", "time"),
        ("Weekday", "clock", "weekday"), ("Date", "clock", "date"))
#: The property an Add button carries: its row in ADDS.
_ADD = "hud_overlay_add"
#: The property a form control carries: the element field it edits.
_FIELD = "hud_overlay_field"
_FORM_STYLE = "QLabel, QCheckBox { color: #dfe5ea; }"


def _distance(x: int, y: int, entry: OverlayElementEntry) -> int:
    """How far *entry* is from panel pixel (*x*, *y*), squared."""
    found = (entry.x - x) ** 2 + (entry.y - y) ** 2
    log.debug("_distance: %s from (%d, %d) -> %d", entry.id, x, y, found)
    return found


def describe(entry: OverlayElementEntry, say_now: Tr = str) -> str:
    """An element's line in the list: what it shows, in *say_now*'s words."""
    what = {"text": entry.text or say_now("(empty text)"),
            "metric": entry.metric or say_now("(no reading)"),
            "clock": say_now(entry.source.capitalize())}.get(entry.type, entry.type)
    line = f"{say_now(entry.type.capitalize())}: {what}"
    log.debug("describe: %s -> %s", entry.id, line)
    return line


class OverlayEditor(_SettingsTab):
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
        self.on_switch = say(QCheckBox(self), "Overlay on")
        self.on_switch.toggled.connect(self._on_enabled)
        top.addWidget(self.on_switch)
        top.addStretch(1)
        self.delete = say(QPushButton(self), "Delete")
        self.delete.clicked.connect(self._on_delete)
        top.addWidget(self.delete)
        column.addLayout(top)
        adds = QGridLayout()
        adds.addWidget(say(QLabel(self), "Add"), 0, 0)
        for index, (label, _type, _source) in enumerate(ADDS, 1):
            button = say(QPushButton(self), label)
            button.setProperty(_ADD, index - 1)
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
        #: Moving an element by hand on the preview.
        self.drag = PreviewDrag(self)
        # X / Y are bounded by where elements sit -- the composed canvas -- which
        # changes with the device, its theme or its orientation, never per edit.
        selection.changed.connect(self._size_to_canvas)
        for signal in (bus.theme_loaded, bus.orientation_changed):
            signal.connect(self._size_to_canvas, type=Qt.ConnectionType.QueuedConnection)
        self._size_to_canvas()
        self.show_state()

    def _size_to_canvas(self, *_event: object) -> None:
        """X / Y bounded by the composed canvas the preview draws, as in the gui
        (it was the native ``DeviceCanvas``: wrong on a portrait-composed theme)."""
        key = self._selection.key
        canvas = self._settings.canvas(key) if key else None
        log.debug("OverlayEditor._size_to_canvas: %s -> %s", key, canvas)
        if canvas is not None:
            self.x_box.setRange(0, canvas[0])
            self.y_box.setRange(0, canvas[1])

    def _form(self) -> QFormLayout:
        """The selected element's properties -- each edit sends its field."""
        log.debug("OverlayEditor._form")
        form = QFormLayout()
        self.x_box, self.y_box, self.size_box = QSpinBox(self), QSpinBox(self), QSpinBox(self)
        self.size_box.setRange(4, 200)
        self.colour_box = QPushButton("", self)
        self.font_box = QFontComboBox(self)
        self._narrow(self.font_box)
        self.bold_box = say(QCheckBox(self), "Bold")
        self.italic_box = say(QCheckBox(self), "Italic")
        self.text_box = QLineEdit(self)
        self.metric_box = QComboBox(self)
        self.metric_box.setEditable(True)
        self._narrow(self.metric_box)
        self.format_box = QLineEdit(self)
        self.unit_box = say(QCheckBox(self), "Show the unit")
        self.clock_box = QComboBox(self)
        say_now = words(self._app)
        for source in ("time", "weekday", "date"):
            say_item(self.clock_box, source.capitalize(), source, say_now)
        style = QHBoxLayout()
        style.addWidget(self.bold_box)
        style.addWidget(self.italic_box)
        for label, widget in (("X", self.x_box), ("Y", self.y_box), ("Size", self.size_box),
                              ("Colour", self.colour_box), ("Font", self.font_box)):
            form.addRow(say(QLabel(self), label), widget)
        form.addRow("", style)
        for label, widget in (("Text", self.text_box), ("Reading", self.metric_box),
                              ("Format", self.format_box), ("", self.unit_box),
                              ("Clock", self.clock_box)):
            form.addRow(say(QLabel(self), label), widget)
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

    def _show_view(self, view: SettingsView) -> None:
        """The list and the form from the App's layout; the selection kept."""
        picked = self._picked_id()
        log.info("OverlayEditor._show_view: %s %d element(s), on=%s",
                 view.key, len(view.elements), view.overlay_on)
        self._quiet(self.on_switch, view.overlay_on)
        self._elements = {e.id: e for e in view.elements}
        say_now = words(self._app)
        self.list.blockSignals(True)
        self.list.clear()
        for entry in view.elements:
            item = QListWidgetItem(describe(entry, say_now))
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
            self.clock_box.setCurrentIndex(max(0, self.clock_box.findData(entry.source)))
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
            self._edit_element(partial(UpdateOverlayElement, element_id=element_id, **field))
            self.show_state()

    def _edit_element(self, edit: Callable[..., Command[Any]]) -> Any:
        """One element added, changed or deleted -- the edit every GUI makes
        (``ThemeSettings.edit_element``), so an edit to a switched-off overlay
        switches it on here as it does in the gui.  The edit's answer."""
        log.debug("OverlayEditor._edit_element: overlay on %s", self.on_switch.isChecked())
        return self._settings.edit_element(self._selection.key, edit,
                                           self.on_switch.isChecked()).result

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
        colour = QColorDialog.getColor(QColor(start), self,
                                       words(self._app)("Element colour"))
        return colour.name() if colour.isValid() else ""

    def _on_colour(self) -> None:
        log.info("OverlayEditor._on_colour")
        if colour := self.choose_colour(self.colour_box.text() or "#ffffff"):
            self._edit(color=colour)

    def _select(self, element_id: str) -> None:
        """Select *element_id* in the list (its form follows)."""
        log.debug("OverlayEditor._select: %s", element_id)
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == element_id:
                self.list.setCurrentRow(row)

    # ── On / off, add, delete ──────────────────────────────────────────

    def _on_enabled(self, on: bool) -> None:
        log.info("OverlayEditor._on_enabled: %s", on)
        self._settings.set_overlay(self._selection.key, on)
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
        result = self._edit_element(partial(AddOverlayElement, **fields))
        self.show_state()
        new = getattr(result, "element", None)
        if new is not None:
            self._select(new.id)

    def _on_delete(self) -> None:
        element_id = self._picked_id()
        log.info("OverlayEditor._on_delete: %s", element_id)
        if element_id:
            self._edit_element(partial(DeleteOverlayElement, element_id=element_id))
            self.show_state()


class PreviewDrag:
    """Moving an overlay element by hand on the preview (S3) -- its own
    concern, beside the editor whose element it moves.  The gui's way: a
    press picks up the selected element, or the nearest; it moves as far as
    the pointer, kept on the panel; only its place is sent while it moves,
    and the editor shows the App's layout again when it is let go."""

    def __init__(self, editor: OverlayEditor) -> None:
        log.debug("PreviewDrag: for %s", type(editor).__name__)
        self._editor = editor
        #: What a press picked up: its id, and where it goes as the pointer moves.
        self._grabbed: tuple[str, ElementDrag] | None = None

    def grab_at(self, x: int, y: int) -> None:
        """A press on the preview at panel pixel (*x*, *y*)."""
        editor = self._editor
        entry = editor._elements.get(editor._picked_id())
        if entry is None and editor._elements:
            entry = min(editor._elements.values(), key=partial(_distance, x, y))
        log.info("PreviewDrag.grab_at: (%d, %d) -> %s", x, y, entry and entry.id)
        if entry is None:
            self._grabbed = None
            return
        editor._select(entry.id)
        self._grabbed = (entry.id, ElementDrag(origin=(x, y), start=(entry.x, entry.y),
                                               canvas=(editor.x_box.maximum(),
                                                       editor.y_box.maximum())))

    def drag_to(self, x: int, y: int) -> None:
        """The pointer moved: the element moves as far, kept on the panel."""
        if self._grabbed is None:
            return
        editor = self._editor
        element_id, drag = self._grabbed
        new_x, new_y = drag.to(x, y)
        log.debug("PreviewDrag.drag_to: %s -> (%d, %d)", element_id, new_x, new_y)
        for box, value in ((editor.x_box, new_x), (editor.y_box, new_y)):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)
        editor._edit_element(partial(UpdateOverlayElement, element_id=element_id,
                                     x=new_x, y=new_y))

    def let_go(self) -> None:
        """Let go: the editor shows the App's layout again."""
        log.info("PreviewDrag.let_go: %s", self._grabbed and self._grabbed[0])
        if self._grabbed is not None:
            self._grabbed = None
            self._editor.show_state()
