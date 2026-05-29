"""
PyQt6 UCInfoModule - Compact hardware sensor display bar.

Shows 4 sensor boxes (CPU temp, GPU temp, CPU%, GPU%) above the preview.
Matches Windows TRCC Information Module functionality.
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

log = logging.getLogger(__name__)

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
        log.info("SensorBox.__init__: label=%r color=%s", label, color)

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
        self._sensor_boxes: dict = {}
        log.info("UCInfoModule.__init__: default_sensors=%d", len(DEFAULT_SENSORS))

        # Dark background via palette
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#1E1E1E'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self._setup_ui()

    def _setup_ui(self):
        log.info("UCInfoModule._setup_ui: building %d sensor boxes",
                 len(DEFAULT_SENSORS))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        for key, label, color in DEFAULT_SENSORS:
            box = SensorBox(label, color)
            box.metric_key = key
            layout.addWidget(box)
            self._sensor_boxes[key] = box

    def set_temp_unit(self, unit):
        """Set temperature display unit (0=Celsius, 1=Fahrenheit)."""
        new = '\u00b0F' if unit else '\u00b0C'
        log.info("UCInfoModule.set_temp_unit: %r -> %r (unit=%s)",
                 self._temp_unit, new, unit)
        self._temp_unit = new

    def update_from_metrics(self, metrics) -> None:
        """Render from the unified Topic.METRICS broadcast."""
        log.debug("update_from_metrics")
        self._apply_metrics(metrics)

    def stop_updates(self) -> None:
        """No-op — retained for cleanup compatibility."""
        log.info("UCInfoModule.stop_updates: no-op (Topic.METRICS observer)")

    def _apply_metrics(self, metrics) -> None:
        """Apply metrics data to sensor display boxes."""
        rendered: list[str] = []
        missing: list[str] = []
        for key, box in self._sensor_boxes.items():
            value = getattr(metrics, key, None)
            if value is not None and isinstance(value, (int, float)):
                if 'temp' in key:
                    txt = f"{int(value)}{self._temp_unit}"
                elif 'usage' in key or 'percent' in key:
                    txt = f"{int(value)}%"
                else:
                    txt = str(int(value))
                box.value_label.setText(txt)
                rendered.append(f"{key}={txt}")
            else:
                box.value_label.setText("--")
                missing.append(key)
        if missing:
            # Warn loudly — a readings/canonical-field mismatch is silent
            # otherwise.  Include a sample of available keys so reporters
            # can spot a publisher that's only exposing raw sensor IDs.
            available = sorted(getattr(metrics, 'readings', {}) or {})[:10]
            log.warning(
                "UCInfoModule._apply_metrics: %d/%d canonical keys missing=%s "
                "(available_sample=%s)",
                len(missing), len(self._sensor_boxes), missing, available,
            )
        log.debug(
            "UCInfoModule._apply_metrics: rendered=%s",
            "; ".join(rendered) or "<none>",
        )
