"""DisplayPanel — orientation, brightness, theme load."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ....app import App
from ....core.commands import LoadTheme, SetBrightness, SetOrientation


class DisplayPanel(QWidget):
    """Per-device display controls (orientation / brightness / theme)."""

    def __init__(self, app: App, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app

        self._key_edit = QLineEdit()
        self._key_edit.setPlaceholderText("0402:3922")

        self._orientation = QComboBox()
        for deg in (0, 90, 180, 270):
            self._orientation.addItem(f"{deg}°", userData=deg)

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

        self._theme_path = QLineEdit()
        self._theme_path.setReadOnly(True)
        self._theme_browse = QPushButton("Browse…")
        self._theme_browse.clicked.connect(self._on_browse_theme)

        theme_row = QHBoxLayout()
        theme_row.addWidget(self._theme_path, stretch=1)
        theme_row.addWidget(self._theme_browse)

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._on_apply)

        self._status = QLabel("")

        form = QFormLayout()
        form.addRow("Device key:", self._key_edit)
        form.addRow("Orientation:", self._orientation)
        form.addRow("Brightness:", brightness_row)
        form.addRow("Theme:", theme_row)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self._apply_btn)
        root.addWidget(self._status)
        root.addStretch(1)

    # ── Actions ───────────────────────────────────────────────────────

    def _on_browse_theme(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select theme directory", "",
        )
        if path:
            self._theme_path.setText(path)

    def _on_apply(self) -> None:
        key = self._key_edit.text().strip()
        if not key:
            self._status.setText("Enter a device key (e.g. 0402:3922).")
            return

        messages = []

        r_orient = self._app.dispatch(SetOrientation(
            key=key,
            degrees=int(self._orientation.currentData()),
        ))
        messages.append(r_orient.message)

        r_bright = self._app.dispatch(SetBrightness(
            key=key, percent=self._brightness.value(),
        ))
        messages.append(r_bright.message)

        theme_path = self._theme_path.text().strip()
        if theme_path:
            r_theme = self._app.dispatch(LoadTheme(
                key=key, path=Path(theme_path),
            ))
            messages.append(r_theme.message)

        self._status.setText("  |  ".join(messages))
