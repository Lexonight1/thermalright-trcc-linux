"""
PyQt6 SensorPickerDialog - Sensor selection popup.

Matches Windows TRCC FormSystemInfo (490x800).
Shows a scrollable list of all discovered hardware sensors with radio-button
selection. Each row shows: [checkbox] name value.

Used by the System Info dashboard to let users assign any sensor to any
panel row.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QIcon, QPalette
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..sensor_enumerator import SensorEnumerator, SensorInfo
from .assets import Assets, load_pixmap
from .constants import Styles

# Dialog dimensions (matches Windows FormSystemInfo)
DIALOG_W = 490
DIALOG_H = 800

# Sensor list area
LIST_X = 20
LIST_Y = 50
LIST_W = 450
LIST_H = 742

# Row dimensions
ROW_H = 22
CHECKBOX_X = 8
CHECKBOX_Y = 3
CHECKBOX_SIZE = 14
NAME_X = 30
NAME_W = 300
VALUE_X = 335
VALUE_W = 100


class SensorRow(QWidget):
    """Single sensor row in the picker list (22px tall)."""

    clicked = pyqtSignal(str)  # sensor_id

    def __init__(self, sensor: SensorInfo, parent=None):
        super().__init__(parent)
        self.sensor = sensor
        self._selected = False
        self.setFixedHeight(ROW_H)

        # Load checkbox images
        self._cb_off = load_pixmap('P点选框.png', CHECKBOX_SIZE, CHECKBOX_SIZE)
        self._cb_on = load_pixmap('P点选框A.png', CHECKBOX_SIZE, CHECKBOX_SIZE)

        # Checkbox button
        self._cb = QPushButton(self)
        self._cb.setGeometry(CHECKBOX_X, CHECKBOX_Y, CHECKBOX_SIZE, CHECKBOX_SIZE)
        self._cb.setFlat(True)
        self._cb.setStyleSheet(Styles.FLAT_BUTTON)
        self._cb.setCursor(Qt.CursorShape.PointingHandCursor)
        if not self._cb_off.isNull():
            self._cb.setIcon(QIcon(self._cb_off))
            self._cb.setIconSize(QSize(CHECKBOX_SIZE, CHECKBOX_SIZE))
        self._cb.clicked.connect(lambda: self.clicked.emit(self.sensor.id))

        # Name label
        self._name = QLabel(sensor.name, self)
        self._name.setGeometry(NAME_X, 0, NAME_W, ROW_H)
        self._name.setStyleSheet(
            "color: #D0D0D0; font-size: 10px; background: transparent;"
        )
        self._name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Value label
        self._value = QLabel('--', self)
        self._value.setGeometry(VALUE_X, 0, VALUE_W, ROW_H)
        self._value.setStyleSheet(
            "color: #A0A0A0; font-size: 10px; background: transparent;"
        )
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def set_selected(self, selected: bool):
        """Update checkbox image."""
        self._selected = selected
        px = self._cb_on if selected else self._cb_off
        if not px.isNull():
            self._cb.setIcon(QIcon(px))

    def update_value(self, value: float | None):
        """Update the displayed value."""
        if value is None:
            self._value.setText('--')
        else:
            unit = self.sensor.unit
            if unit == '°C':
                self._value.setText(f"{value:.0f}°C")
            elif unit in ('%', 'RPM', 'W'):
                self._value.setText(f"{value:.0f}{unit}")
            elif unit == 'V':
                self._value.setText(f"{value:.2f}V")
            elif unit in ('MHz',):
                self._value.setText(f"{value:.0f}MHz")
            elif unit in ('MB', 'MB/s', 'KB/s'):
                self._value.setText(f"{value:.1f}{unit}")
            else:
                self._value.setText(f"{value:.1f}")

    def mousePressEvent(self, event):
        self.clicked.emit(self.sensor.id)


class SensorPickerDialog(QDialog):
    """Sensor selection dialog matching Windows FormSystemInfo (490x800)."""

    def __init__(self, enumerator: SensorEnumerator, lang: str = 'en', parent=None):
        super().__init__(parent)
        self._enumerator = enumerator
        self._selected_id: str | None = None
        self._rows: list[SensorRow] = []
        self._result_sensor: SensorInfo | None = None

        self.setFixedSize(DIALOG_W, DIALOG_H)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)

        # Background image
        bg_name = Assets.get_localized('P0系统信息.png', lang)
        bg = load_pixmap(bg_name, DIALOG_W, DIALOG_H)
        if not bg.isNull():
            palette = self.palette()
            palette.setBrush(QPalette.ColorRole.Window, QBrush(bg))
            self.setPalette(palette)
            self.setAutoFillBackground(True)
            self._bg_ref = bg
        else:
            palette = self.palette()
            palette.setColor(QPalette.ColorRole.Window, palette.color(QPalette.ColorRole.Window))
            self.setStyleSheet("background-color: #1A1A2E;")

        # OK button
        self._ok_btn = QPushButton("OK", self)
        self._ok_btn.setGeometry(411, 12, 30, 30)
        self._ok_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #B4964F; border: none; "
            "font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { color: white; }"
        )
        self._ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok_btn.clicked.connect(self._on_ok)

        # Close button
        self._close_btn = QPushButton("✕", self)
        self._close_btn.setGeometry(451, 12, 30, 30)
        self._close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #B4964F; border: none; "
            "font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { color: white; }"
        )
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self.reject)

        # Scrollable sensor list
        self._scroll = QScrollArea(self)
        self._scroll.setGeometry(LIST_X, LIST_Y, LIST_W, LIST_H)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #1A1A2E; width: 12px; }"
            "QScrollBar::handle:vertical { background: #444; border-radius: 4px; min-height: 30px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._scroll.setWidget(self._list_widget)

        # Populate
        self._populate_sensors()

        # Live value update timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_values)
        self._timer.start(1000)
        self._update_values()  # Initial read

    def _populate_sensors(self):
        """Create rows for all discovered sensors, grouped by source."""
        sensors = self._enumerator.get_sensors()

        # Group by source
        groups: dict[str, list[SensorInfo]] = {}
        for s in sensors:
            groups.setdefault(s.source, []).append(s)

        # Source display order and names
        source_labels = {
            'hwmon': 'Hardware Monitor',
            'nvidia': 'NVIDIA GPU',
            'psutil': 'System',
            'rapl': 'Power (RAPL)',
            'computed': 'Computed Rates',
        }

        for source in ('hwmon', 'nvidia', 'psutil', 'rapl', 'computed'):
            group = groups.get(source, [])
            if not group:
                continue

            # Section header
            header = QLabel(source_labels.get(source, source))
            header.setFixedHeight(24)
            header.setStyleSheet(
                "color: #B4964F; font-size: 11px; font-weight: bold; "
                "background: transparent; padding-left: 4px;"
            )
            self._list_layout.addWidget(header)

            for sensor in group:
                row = SensorRow(sensor)
                row.clicked.connect(self._on_row_clicked)
                self._list_layout.addWidget(row)
                self._rows.append(row)

        self._list_layout.addStretch()

    def set_current_sensor(self, sensor_id: str):
        """Pre-select the currently bound sensor."""
        self._selected_id = sensor_id
        for row in self._rows:
            row.set_selected(row.sensor.id == sensor_id)

    def get_selected_sensor(self) -> SensorInfo | None:
        """Return the sensor the user selected, or None if cancelled."""
        return self._result_sensor

    def _on_row_clicked(self, sensor_id: str):
        """Handle radio-button selection (only one sensor selected)."""
        self._selected_id = sensor_id
        for row in self._rows:
            row.set_selected(row.sensor.id == sensor_id)

    def _on_ok(self):
        """Confirm selection."""
        if self._selected_id:
            for row in self._rows:
                if row.sensor.id == self._selected_id:
                    self._result_sensor = row.sensor
                    break
        self.accept()

    def _update_values(self):
        """Update all sensor values in the list."""
        readings = self._enumerator.read_all()
        for row in self._rows:
            row.update_value(readings.get(row.sensor.id))

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
