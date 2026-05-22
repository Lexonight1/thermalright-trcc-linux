"""LedPanel — push a uniform RGB color to an LED controller."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ....app import App
from ....core.commands import SetLedColors


class LedPanel(QWidget):
    """Set a uniform RGB color across N LEDs on a connected Led device.

    Real LED layouts (per-LED colors, segment displays, effects) land
    with the styles service — Phase 12.  This panel proves the Command
    path works end-to-end.
    """

    def __init__(self, app: App, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._color = QColor(255, 255, 255)

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("0416:8001")

        self._count = QSpinBox()
        self._count.setRange(1, 256)
        self._count.setValue(12)

        self._color_btn = QPushButton("Pick color…")
        self._color_btn.clicked.connect(self._on_pick_color)
        self._color_swatch = QLabel()
        self._color_swatch.setFixedSize(64, 24)
        self._update_swatch()

        color_row = QHBoxLayout()
        color_row.addWidget(self._color_btn)
        color_row.addWidget(self._color_swatch)
        color_row.addStretch(1)

        self._brightness = QSlider(Qt.Orientation.Horizontal)
        self._brightness.setRange(0, 100)
        self._brightness.setValue(100)
        self._brightness_label = QLabel("100%")
        self._brightness.valueChanged.connect(
            lambda v: self._brightness_label.setText(f"{v}%")
        )

        brightness_row = QHBoxLayout()
        brightness_row.addWidget(self._brightness, stretch=1)
        brightness_row.addWidget(self._brightness_label)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._on_apply)

        self._off_btn = QPushButton("All off")
        self._off_btn.clicked.connect(self._on_off)

        button_row = QHBoxLayout()
        button_row.addWidget(self._apply_btn)
        button_row.addWidget(self._off_btn)
        button_row.addStretch(1)

        self._status = QLabel("")

        form = QFormLayout()
        form.addRow("Device key:", self._key_edit)
        form.addRow("LED count:", self._count)
        form.addRow("Color:", color_row)
        form.addRow("Brightness:", brightness_row)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(button_row)
        root.addWidget(self._status)
        root.addStretch(1)

    # ── Helpers ───────────────────────────────────────────────────────

    def _update_swatch(self) -> None:
        self._color_swatch.setStyleSheet(
            f"background-color: {self._color.name()}; border: 1px solid #222"
        )

    def _on_pick_color(self) -> None:
        picked = QColorDialog.getColor(self._color, self, "Pick LED color")
        if picked.isValid():
            self._color = picked
            self._update_swatch()

    def _dispatch(self, global_on: bool) -> None:
        key = self._key_edit.text().strip()
        if not key:
            self._status.setText("Enter a device key (e.g. 0416:8001).")
            return
        r, g, b = self._color.red(), self._color.green(), self._color.blue()
        colors = [(r, g, b)] * self._count.value()
        result = self._app.dispatch(SetLedColors(
            key=key, colors=colors,
            global_on=global_on,
            brightness=self._brightness.value(),
        ))
        self._status.setText(result.message)

    def _on_apply(self) -> None:
        self._dispatch(global_on=True)

    def _on_off(self) -> None:
        self._dispatch(global_on=False)
