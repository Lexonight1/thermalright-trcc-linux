"""
PyQt6 UCActivitySidebar - Activity sidebar with live sensor values.

Shows real-time hardware sensor values that can be clicked to add to overlay.
Matches Windows TRCC right-side Activity panel.
"""

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..system_info import get_all_metrics

log = logging.getLogger(__name__)


# Category colors matching Windows TRCC
CATEGORY_COLORS = {
    'cpu': '#32C5FF',
    'gpu': '#44D7B6',
    'memory': '#6DD401',
    'hdd': '#F7B501',
    'network': '#FA6401',
    'fan': '#E02020',
}

# Sensor definitions: category -> [(key_suffix, label, unit, metric_key)]
SENSORS = {
    'cpu': [
        ('temp', 'TEMP', '\u00b0C', 'cpu_temp'),
        ('usage', 'Usage', '%', 'cpu_percent'),
        ('clock', 'Clock', 'MHz', 'cpu_freq'),
        ('power', 'Power', 'W', 'cpu_power'),
    ],
    'gpu': [
        ('temp', 'TEMP', '\u00b0C', 'gpu_temp'),
        ('usage', 'Usage', '%', 'gpu_usage'),
        ('clock', 'Clock', 'MHz', 'gpu_clock'),
        ('power', 'Power', 'W', 'gpu_power'),
    ],
    'memory': [
        ('temp', 'TEMP', '\u00b0C', 'mem_temp'),
        ('usage', 'Usage', '%', 'mem_percent'),
        ('clock', 'Clock', 'MHz', 'mem_clock'),
        ('available', 'Available', 'MB', 'mem_available'),
    ],
    'hdd': [
        ('temp', 'TEMP', '\u00b0C', 'disk_temp'),
        ('activity', 'Activity', '%', 'disk_activity'),
        ('read', 'Read', 'MB/s', 'disk_read'),
        ('write', 'Write', 'MB/s', 'disk_write'),
    ],
    'network': [
        ('upload', 'UP rate', 'KB/s', 'net_up'),
        ('download', 'DL rate', 'KB/s', 'net_down'),
        ('total_up', 'Total UP', 'MB', 'net_total_up'),
        ('total_dl', 'Total DL', 'MB', 'net_total_down'),
    ],
    'fan': [
        ('cpu_fan', 'CPUFAN', 'RPM', 'fan_cpu'),
        ('gpu_fan', 'GPUFAN', 'RPM', 'fan_gpu'),
        ('ssd_fan', 'SSDFAN', 'RPM', 'fan_ssd'),
        ('fan2', 'FAN2', 'RPM', 'fan_sys2'),
    ],
}

# Map sensor keys to overlay element main_count/sub_count
SENSOR_TO_OVERLAY = {
    'cpu_temp': (0, 1), 'cpu_usage': (0, 2), 'cpu_clock': (0, 3), 'cpu_power': (0, 4),
    'gpu_temp': (1, 1), 'gpu_usage': (1, 2), 'gpu_clock': (1, 3), 'gpu_power': (1, 4),
    'memory_temp': (2, 1), 'memory_usage': (2, 2), 'memory_clock': (2, 3), 'memory_available': (2, 4),
    'hdd_temp': (3, 1), 'hdd_activity': (3, 2), 'hdd_read': (3, 3), 'hdd_write': (3, 4),
    'network_upload': (4, 1), 'network_download': (4, 2), 'network_total_up': (4, 3), 'network_total_dl': (4, 4),
    'fan_cpu_fan': (5, 1), 'fan_gpu_fan': (5, 2), 'fan_ssd_fan': (5, 3), 'fan_fan2': (5, 4),
}


class SensorItem(QFrame):
    """Single sensor row — clickable to add to overlay."""

    clicked = pyqtSignal(dict)  # sensor config for overlay

    def __init__(self, category, key_suffix, label, unit, metric_key, color, parent=None):
        super().__init__(parent)
        self.category = category
        self.key_suffix = key_suffix
        self.metric_key = metric_key
        self.unit = unit
        self.color = color

        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 5, 0)
        layout.setSpacing(4)

        # Color indicator
        indicator = QLabel('\u25c6')
        indicator.setFixedWidth(12)
        indicator.setStyleSheet(f"color: {color}; font-size: 6px; background: transparent;")
        layout.addWidget(indicator)

        # Sensor name
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet("color: #AAAAAA; font-size: 9px; background: transparent;")
        name_lbl.setFixedWidth(70)
        layout.addWidget(name_lbl)

        layout.addStretch()

        # Sensor value
        self.value_label = QLabel('--')
        self.value_label.setStyleSheet(f"color: {color}; font-size: 9px; font-weight: bold; background: transparent;")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setFixedWidth(80)
        layout.addWidget(self.value_label)

        # Overlay config for click-to-add
        sensor_key = f"{category}_{key_suffix}"
        main_count, sub_count = SENSOR_TO_OVERLAY.get(sensor_key, (0, 1))
        self._overlay_config = {
            'mode': 0,  # MODE_HARDWARE
            'mode_sub': 0,
            'main_count': main_count,
            'sub_count': sub_count,
            'color': color,
            'x': 100,
            'y': 100,
            'font_name': 'Microsoft YaHei',
            'font_size': 36,
            'font_style': 0,
            'text': '',
        }

    def update_value(self, metrics):
        """Update displayed value from metrics dict."""
        value = metrics.get(self.metric_key)
        if value is not None:
            if isinstance(value, float):
                if value >= 1000:
                    self.value_label.setText(f"{int(value)}{self.unit}")
                else:
                    self.value_label.setText(f"{value:.1f}{self.unit}")
            else:
                self.value_label.setText(f"{value}{self.unit}")
        else:
            self.value_label.setText(f"--{self.unit}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._overlay_config)

    def enterEvent(self, event):
        self.setStyleSheet("background-color: #2A2A2A;")

    def leaveEvent(self, event):
        self.setStyleSheet("")


class UCActivitySidebar(QWidget):
    """Activity sidebar — scrollable list of live hardware sensor values.

    Click a sensor to add it to the overlay grid.
    """

    sensor_clicked = pyqtSignal(dict)  # overlay element config

    def __init__(self, parent=None):
        super().__init__(parent)

        self._sensor_items = []  # all SensorItem widgets
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_values)

        self._setup_ui()

    def _setup_ui(self):
        # Dark background via palette (not stylesheet — children use QPalette)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#1E1E1E'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 5, 0, 0)
        main_layout.setSpacing(0)

        # Title
        title = QLabel("Activity")
        title.setStyleSheet(
            "color: white; font-size: 10px; font-weight: bold; "
            "background: transparent; padding-left: 8px;"
        )
        main_layout.addWidget(title)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 8px; }"
            "QScrollBar::handle:vertical { background: #555; border-radius: 4px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        main_layout.addWidget(scroll)

        # Inner widget
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)

        for category, sensors in SENSORS.items():
            color = CATEGORY_COLORS.get(category, '#FFFFFF')

            # Category header
            header = QLabel(f"  \u25aa {category.upper()}")
            header.setFixedHeight(24)
            header.setStyleSheet(
                f"color: {color}; font-size: 9px; font-weight: bold; "
                f"background-color: #2A2A2A; padding-top: 3px;"
            )
            inner_layout.addWidget(header)

            # Sensor items
            for key_suffix, label, unit, metric_key in sensors:
                item = SensorItem(category, key_suffix, label, unit, metric_key, color)
                item.clicked.connect(self._on_sensor_clicked)
                inner_layout.addWidget(item)
                self._sensor_items.append(item)

        inner_layout.addStretch()
        scroll.setWidget(inner)

    def _on_sensor_clicked(self, config):
        self.sensor_clicked.emit(config)

    def start_updates(self, interval_ms=1000):
        """Start periodic sensor value updates."""
        self._update_values()
        self._update_timer.start(interval_ms)

    def stop_updates(self):
        """Stop sensor value updates."""
        self._update_timer.stop()

    def _update_values(self):
        """Update all sensor values from system_info."""
        try:
            metrics = get_all_metrics()
            for item in self._sensor_items:
                item.update_value(metrics)
        except Exception as e:
            log.error("Activity sidebar update error: %s", e)
