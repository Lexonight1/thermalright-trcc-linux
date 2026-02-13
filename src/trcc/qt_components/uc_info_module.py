"""
PyQt6 UCInfoModule - Compact hardware sensor display bar.

Shows 4 sensor boxes (CPU temp, GPU temp, CPU%, GPU%) above the preview.
Matches Windows TRCC Information Module functionality.
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..system_info import get_all_metrics

# Default sensors: (metric_key, label, color)
DEFAULT_SENSORS = [
    ('cpu_temp', 'CPU Temp', '#FF6B6B'),
    ('gpu_temp', 'GPU Temp', '#4ECDC4'),
    ('cpu_percent', 'CPU %', '#45B7D1'),
    ('gpu_usage', 'GPU %', '#96CEB4'),
]


class SensorBox(QFrame):
    """Single sensor display box with name and value."""

    def __init__(self, label, color, parent=None):
        super().__init__(parent)
        self.color = color
        self.metric_key = ''

        self.setStyleSheet(
            "SensorBox { background-color: #2B2B2B; border: 1px solid #444; border-radius: 3px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self.name_label = QLabel(label)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet(
            "color: #888; font-size: 8pt; background: transparent; border: none;"
        )
        layout.addWidget(self.name_label)

        self.value_label = QLabel("--")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setStyleSheet(
            f"color: {color}; font-size: 16pt; font-weight: bold;"
            " background: transparent; border: none;"
        )
        layout.addWidget(self.value_label)


class UCInfoModule(QWidget):
    """Compact 4-sensor display bar.

    Shows CPU temp, GPU temp, CPU usage, GPU usage in a horizontal row.
    Positioned above the preview (16, 16, 500, 70) when sensor mode is active.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._temp_unit = '\u00b0C'
        self._sensor_boxes = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_values)

        # Dark background via palette
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#1E1E1E'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        for key, label, color in DEFAULT_SENSORS:
            box = SensorBox(label, color)
            box.metric_key = key
            layout.addWidget(box)
            self._sensor_boxes[key] = box

    def set_temp_unit(self, unit):
        """Set temperature display unit ('°C' or '°F')."""
        self._temp_unit = unit
        self._update_values()

    def start_updates(self, interval_ms=1000):
        """Start periodic sensor updates."""
        self._update_values()
        self._timer.start(interval_ms)

    def stop_updates(self):
        """Stop sensor updates."""
        self._timer.stop()

    def _update_values(self):
        """Update all sensor values from system_info."""
        try:
            metrics = get_all_metrics()
            for key, box in self._sensor_boxes.items():
                value = metrics.get(key)
                if value is not None and isinstance(value, (int, float)):
                    if 'temp' in key:
                        if self._temp_unit == '\u00b0F':
                            value = value * 9 / 5 + 32
                        box.value_label.setText(f"{int(value)}{self._temp_unit}")
                    elif 'usage' in key or 'percent' in key:
                        box.value_label.setText(f"{int(value)}%")
                    else:
                        box.value_label.setText(str(int(value)))
                else:
                    box.value_label.setText("--")
        except Exception:
            pass
