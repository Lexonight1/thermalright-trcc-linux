"""OverlayEditorPanel — manage user-edited overlay elements on a device.

User flow (designed for non-technical readers):
* Pick a device key (free text — Devices panel populates it for them).
* See a table of every user overlay element on that device.
* "Add element…" opens a dialog: pick text / metric / clock, set
  position + color + size + extras, OK.
* Double-click a row (or click Edit) to modify; Delete removes;
  Flash briefly highlights the element on a connected device's screen.

Single-layout model (matches the legacy GUI): the device renders ONE
overlay layout (``resolve_overlay_elements``: user edits > applied mask
> theme), never theme + user stacked.  When you open the editor it adopts
the active theme/mask layout into the editable user layer, so the rows you
see ARE what's on screen — editing one element changes it in place, it
does not add a duplicate on top of the theme.
"""
from __future__ import annotations

import dataclasses
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ....core.commands import (
    AddOverlayElement,
    DeleteOverlayElement,
    FlashOverlayElement,
    LcdSnapshot,
    ResolveOverlay,
    SetOverlayConfig,
    UpdateOverlayElement,
)
from ..base import BasePanel
from ..device_picker import DevicePickerWidget

log = logging.getLogger(__name__)

_TYPES = ("text", "metric", "clock")
_CLOCK_SOURCES = ("time", "weekday", "date")


class OverlayEditorPanel(BasePanel):
    """Manage a device's user-overlay element list."""

    def _setup_ui(self) -> None:
        self._picker = DevicePickerWidget(
            self.app, self._bus, kind_filter="lcd", parent=self,
        )
        self._picker.key_changed.connect(lambda _key: self.refresh())

        self._refresh_btn = QPushButton("Load", self)
        self._refresh_btn.clicked.connect(self.refresh)

        key_row = QHBoxLayout()
        key_form = QFormLayout()
        key_form.addRow("Device key:", self._picker)
        key_row.addLayout(key_form, stretch=1)
        key_row.addWidget(self._refresh_btn)

        self._list = QListWidget(self)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection,
        )
        self._list.itemDoubleClicked.connect(lambda _item: self._on_edit())

        self._add_btn = QPushButton("Add element…", self)
        self._add_btn.clicked.connect(self._on_add)
        self._edit_btn = QPushButton("Edit…", self)
        self._edit_btn.clicked.connect(self._on_edit)
        self._delete_btn = QPushButton("Delete", self)
        self._delete_btn.clicked.connect(self._on_delete)
        self._flash_btn = QPushButton("Flash on screen", self)
        self._flash_btn.clicked.connect(self._on_flash)

        button_row = QHBoxLayout()
        for btn in (self._add_btn, self._edit_btn, self._delete_btn, self._flash_btn):
            button_row.addWidget(btn)
        button_row.addStretch(1)

        self._status = QLabel(
            "Edit the device's overlay layout.  The rows below are exactly "
            "what's on screen — editing one moves/changes it in place.",
            self,
        )
        self._status.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)
        root.addLayout(key_row)
        root.addWidget(self._list, stretch=1)
        root.addLayout(button_row)
        root.addWidget(self._status)

    # ── Refresh ───────────────────────────────────────────────────────

    def refresh(self) -> None:
        log.debug("refresh")
        key = self._key()
        if key is None:
            return
        # Single-layout model: the editable user layer IS the device's whole
        # overlay layout.  If the user hasn't edited yet, adopt the active
        # theme/mask layout into it so the rows shown are what renders and an
        # incremental Add/Edit/Delete operates on the full layout (the render
        # draws the user layer as a REPLACEMENT, not on top of the theme).
        layout = self.dispatch(ResolveOverlay(key=key))
        # ``source`` names the WINNING layer, and that is why the seed guard
        # reads it rather than testing the element list for truthiness.  The
        # two states a truthiness test collapses are exactly the ones #276
        # turned on: a user layer of ``[]`` means "the user emptied it" and
        # must NOT be re-seeded, while no user layer at all reports "theme".
        # Testing ``not elements`` re-seeded the emptied layer from the theme,
        # so the last element a user deleted came straight back.
        if layout.source != "user" and layout.elements:
            log.info("refresh: adopting the active %s layout into the "
                     "editable user layer for %s (%d element(s))",
                     layout.source, key, len(layout.elements))
            self.dispatch(SetOverlayConfig(
                key=key, elements=tuple(self._entry_to_dict(e) for e in layout.elements),
            ))
            layout = self.dispatch(ResolveOverlay(key=key))
        self._list.clear()
        for element in layout.elements:
            text = self._format_element_row(element)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, element.id)
            self._list.addItem(item)
        if not layout.elements:
            self._status.setText(
                f"No overlay layout on {key} yet.  "
                "Click 'Add element…' to start.",
            )
        else:
            self._status.setText(
                f"{len(layout.elements)} element(s) on {key}.",
            )

    @staticmethod
    def _entry_to_dict(entry) -> dict:
        """``OverlayElementEntry`` -> the flat dict ``SetOverlayConfig`` takes.

        ``asdict`` rather than a hand-written mapping: the two shapes already
        agree, and a hand-written one is where a field goes missing silently —
        ``font`` was added to ``to_dict`` once and not read back, so every
        user element lost its font on restart.  Round-tripped in tests for
        text, metric AND clock elements rather than only the one to hand.
        """
        return dataclasses.asdict(entry)

    @staticmethod
    def _format_element_row(element) -> str:
        head = f"[{element.type:6}] ({element.x:>4},{element.y:>4})"
        if element.type == "text":
            payload = repr(element.text)
        elif element.type == "metric":
            payload = element.metric or "(no metric)"
        else:
            payload = element.source
        return f"{head}  {payload}   size={element.size}  {element.color}"

    # ── Actions ───────────────────────────────────────────────────────

    def _key(self) -> str | None:
        key = self._picker.current_key()
        if not key:
            self._status.setText(
                "Pick a device first.  Open the Devices panel to scan "
                "if no devices are listed.",
            )
            return None
        return key

    def _selected_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            self._status.setText("Pick an element from the list first.")
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def _on_add(self) -> None:
        log.info("_on_add")
        key = self._key()
        if key is None:
            return
        dialog = _ElementDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        result = self.dispatch(AddOverlayElement(
            key=key,
            type=values["type"],
            x=values["x"],
            y=values["y"],
            color=values["color"],
            size=values["size"],
            bold=values["bold"],
            italic=values["italic"],
            text=values["text"],
            metric=values["metric"],
            format=values["format"],
            show_unit=values["show_unit"],
            source=values["source"],
        ))
        self._status.setText(result.message)
        if result.ok:
            self.refresh()

    def _on_edit(self) -> None:
        log.info("_on_edit")
        key = self._key()
        eid = self._selected_id()
        if key is None or eid is None:
            return
        layout = self.dispatch(ResolveOverlay(key=key))
        current = next((e for e in layout.elements if e.id == eid), None)
        if current is None:
            self._status.setText(
                f"Element {eid} is no longer present — Load to refresh.",
            )
            return
        dialog = _ElementDialog(self, prefill=current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        result = self.dispatch(UpdateOverlayElement(
            key=key, element_id=eid,
            x=values["x"], y=values["y"],
            color=values["color"], size=values["size"],
            bold=values["bold"], italic=values["italic"],
            text=values["text"], metric=values["metric"],
            format=values["format"], show_unit=values["show_unit"],
            source=values["source"],
        ))
        self._status.setText(result.message)
        if result.ok:
            self.refresh()

    def _on_delete(self) -> None:
        log.info("_on_delete")
        key = self._key()
        eid = self._selected_id()
        if key is None or eid is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete element?",
            f"Remove overlay element {eid}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        result = self.dispatch(
            DeleteOverlayElement(key=key, element_id=eid),
        )
        self._status.setText(result.message)
        if result.ok:
            self.refresh()

    def _on_flash(self) -> None:
        log.info("_on_flash")
        key = self._key()
        eid = self._selected_id()
        if key is None or eid is None:
            return
        # LcdSnapshot guard — flash only makes sense on a connected device.
        snapshot = self.dispatch(LcdSnapshot(key=key))
        if not snapshot.ok:
            self._status.setText(
                f"Connect to {key} first (Devices panel) before flashing.",
            )
            return
        result = self.dispatch(FlashOverlayElement(
            key=key, element_id=eid, duration_ms=1500,
        ))
        self._status.setText(result.message)


# =========================================================================
# Add/edit dialog
# =========================================================================


class _ElementDialog(QDialog):
    """Modal form for adding or editing one overlay element."""

    def __init__(self, parent, *, prefill=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Edit overlay element" if prefill is not None else "Add overlay element",
        )
        self.setModal(True)
        self._prefill = prefill
        self._build()

    def _build(self) -> None:
        self._type = QComboBox(self)
        for kind in _TYPES:
            self._type.addItem(kind.capitalize(), userData=kind)
        self._type.currentIndexChanged.connect(self._refresh_visibility)

        self._x = QSpinBox(self)
        self._x.setRange(-9999, 9999)
        self._y = QSpinBox(self)
        self._y.setRange(-9999, 9999)

        self._size = QSpinBox(self)
        self._size.setRange(8, 200)
        self._size.setValue(16)

        self._color_btn = QPushButton("Pick color…", self)
        self._color_btn.clicked.connect(self._pick_color)
        self._eyedropper_btn = QPushButton("From screen…", self)
        self._eyedropper_btn.setToolTip(
            "Freeze the desktop and click a pixel to use its colour.",
        )
        self._eyedropper_btn.clicked.connect(self._pick_color_from_screen)
        self._color_label = QLabel("#ffffff", self)
        color_row = QHBoxLayout()
        color_row.addWidget(self._color_btn)
        color_row.addWidget(self._eyedropper_btn)
        color_row.addWidget(self._color_label)
        color_row.addStretch(1)
        self._color = "#ffffff"

        self._bold = QComboBox(self)
        self._bold.addItem("Regular", userData=False)
        self._bold.addItem("Bold", userData=True)
        self._italic = QComboBox(self)
        self._italic.addItem("Roman", userData=False)
        self._italic.addItem("Italic", userData=True)

        # Type-specific fields
        self._text = QLineEdit(self)
        self._metric = QLineEdit(self)
        self._metric.setPlaceholderText("e.g. cpu:temp")
        self._pick_metric_btn = QPushButton("Pick…", self)
        self._pick_metric_btn.clicked.connect(self._pick_metric)
        metric_row = QHBoxLayout()
        metric_row.addWidget(self._metric, stretch=1)
        metric_row.addWidget(self._pick_metric_btn)
        self._format = QLineEdit(self)
        self._format.setText("{value:.0f}°C")
        # button0 unit-switch: draw the value with its unit (45°C) vs bare (45).
        self._show_unit = QCheckBox("Show unit (e.g. °C, %, RPM)", self)
        self._show_unit.setChecked(True)
        self._source = QComboBox(self)
        for src in _CLOCK_SOURCES:
            self._source.addItem(src, userData=src)

        form = QFormLayout()
        form.addRow("Type:", self._type)
        form.addRow("X position:", self._x)
        form.addRow("Y position:", self._y)
        form.addRow("Size (px):", self._size)
        form.addRow("Color:", color_row)
        form.addRow("Weight:", self._bold)
        form.addRow("Slant:", self._italic)
        form.addRow("Text:", self._text)
        form.addRow("Metric id:", metric_row)
        form.addRow("Format string:", self._format)
        form.addRow("", self._show_unit)
        form.addRow("Clock source:", self._source)

        self._form = form

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout(self)
        outer.addLayout(form)
        outer.addWidget(buttons)

        if self._prefill is not None:
            self._apply_prefill(self._prefill)
        self._refresh_visibility()

    def _apply_prefill(self, element) -> None:
        index = max(0, list(_TYPES).index(element.type))
        self._type.setCurrentIndex(index)
        self._x.setValue(element.x)
        self._y.setValue(element.y)
        self._size.setValue(element.size)
        self._color = element.color
        self._color_label.setText(element.color)
        self._bold.setCurrentIndex(1 if element.bold else 0)
        self._italic.setCurrentIndex(1 if element.italic else 0)
        self._text.setText(element.text)
        self._metric.setText(element.metric)
        self._format.setText(element.format)
        self._show_unit.setChecked(getattr(element, "show_unit", True))
        try:
            self._source.setCurrentIndex(
                list(_CLOCK_SOURCES).index(element.source),
            )
        except ValueError:
            self._source.setCurrentIndex(0)

    def _refresh_visibility(self) -> None:
        log.debug("_refresh_visibility")
        kind = self._type.currentData()
        # Show only the field group relevant to the chosen type.
        self._set_row_visible(self._text, kind == "text")
        self._set_row_visible(self._metric, kind == "metric")
        self._set_row_visible(self._format, kind == "metric")
        self._set_row_visible(self._show_unit, kind == "metric")
        self._set_row_visible(self._source, kind == "clock")

    def _set_row_visible(self, widget, visible: bool) -> None:
        label = self._form.labelForField(widget)
        widget.setVisible(visible)
        if label is not None:
            label.setVisible(visible)

    def _pick_color(self) -> None:
        from PySide6.QtGui import QColor
        current = QColor(self._color)
        picked = QColorDialog.getColor(current, self, "Pick element color")
        if picked.isValid():
            self._color = picked.name()
            self._color_label.setText(self._color)

    def _pick_color_from_screen(self) -> None:
        """Freeze the desktop and let the user pick a pixel colour."""
        from ...eyedropper import EyedropperOverlay

        overlay = EyedropperOverlay(self)
        overlay.color_picked.connect(self._on_eyedropper_picked)
        overlay.show()

    def _on_eyedropper_picked(self, r: int, g: int, b: int) -> None:
        log.info("_on_eyedropper_picked: r=%s g=%s b=%s", r, g, b)
        self._color = f"#{r:02x}{g:02x}{b:02x}"
        self._color_label.setText(self._color)

    def _pick_metric(self) -> None:
        """Open a modal sensor-picker dialog; commit the chosen sensor id."""
        from ..sensor_picker import SensorPickerWidget

        # Reach back to the parent's app reference — the dialog is a
        # child of OverlayEditorPanel which is a BasePanel.
        parent_panel = self.parent()
        app = getattr(parent_panel, "app", None) or getattr(
            parent_panel, "_app", None,
        )
        if app is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Pick a sensor")
        dialog.setModal(True)
        dialog.resize(560, 420)
        picker = SensorPickerWidget(app, dialog)
        if self._metric.text().strip():
            picker.select_sensor_id(self._metric.text().strip())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(picker, stretch=1)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        picked = picker.selected_sensor()
        if picked is None:
            return
        sensor_id, _label = picked
        self._metric.setText(sensor_id)

    def values(self) -> dict:
        return {
            "type":    self._type.currentData(),
            "x":       int(self._x.value()),
            "y":       int(self._y.value()),
            "size":    int(self._size.value()),
            "color":   self._color,
            "bold":    bool(self._bold.currentData()),
            "italic":  bool(self._italic.currentData()),
            "text":    self._text.text(),
            "metric":  self._metric.text().strip(),
            "format":  self._format.text() or "{value}",
            "show_unit": bool(self._show_unit.isChecked()),
            "source":  self._source.currentData(),
        }
