"""
PyQt6 UCActivitySidebar - Activity sidebar with live sensor values.

Shows real-time hardware sensor values that can be clicked to add to overlay.
Matches Windows TRCC right-side Activity panel.
"""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...core.models import (
    SENSOR_TO_OVERLAY,
    SENSORS,
    OverlayElementConfig,
    OverlayMode,
)

log = logging.getLogger(__name__)

# Category colors for sidebar display — view-local (Qt layer, will be replaced)
CATEGORY_COLORS = {
    'cpu': '#32C5FF',
    'gpu': '#44D7B6',
    'memory': '#6DD401',
    'hdd': '#F7B501',
    'network': '#FA6401',
    'fan': '#E02020',
}


class SensorItem(QFrame):
    """Single sensor row — clickable to add to overlay."""

    clicked = Signal(object)  # OverlayElementConfig

    def __init__(self, category, key_suffix, label, unit, metric_key, color, parent=None):
        super().__init__(parent)
        self.category = category
        self.key_suffix = key_suffix
        self.metric_key = metric_key
        self.unit = unit
        self.color = color
        log.debug(
            "SensorItem.__init__: category=%s key=%s label=%r metric_key=%s",
            category, key_suffix, label, metric_key,
        )

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
        self._overlay_config = OverlayElementConfig(
            mode=OverlayMode.HARDWARE,
            main_count=main_count,
            sub_count=sub_count,
            color=color,
        )

    def update_value(self, metrics):
        """Update displayed value from HardwareMetrics DTO."""
        log.debug("update_value")
        if (value := getattr(metrics, self.metric_key, None)) is not None:
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
            log.info(
                "SensorItem.mousePressEvent: category=%s metric=%s "
                "(emit overlay add)",
                self.category, self.metric_key,
            )
            self.clicked.emit(self._overlay_config)

    def enterEvent(self, event):
        self.setStyleSheet("background-color: #2A2A2A;")

    def leaveEvent(self, event):
        self.setStyleSheet("")


class UCActivitySidebar(QWidget):
    """Activity sidebar — scrollable list of live hardware sensor values.

    Click a sensor to add it to the overlay grid.
    """

    sensor_clicked = Signal(object)  # OverlayElementConfig

    def __init__(self, parent=None):
        super().__init__(parent)

        self._sensor_items: list = []
        log.info("UCActivitySidebar.__init__: building activity sidebar")
        self._setup_ui()

    def _setup_ui(self):
        log.info("UCActivitySidebar._setup_ui: %d categories from SENSORS",
                 len(SENSORS))
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
        log.info(
            "UCActivitySidebar._setup_ui: built %d sensor items",
            len(self._sensor_items),
        )

    def _on_sensor_clicked(self, config):
        log.info(
            "UCActivitySidebar._on_sensor_clicked: re-emitting "
            "OverlayElementConfig (main=%s sub=%s)",
            getattr(config, 'main_count', '?'),
            getattr(config, 'sub_count', '?'),
        )
        self.sensor_clicked.emit(config)

    def update_from_metrics(self, metrics) -> None:
        """Render from the unified Topic.METRICS broadcast."""
        # Per-tick — DEBUG so a default INFO run isn't drowned.
        log.debug(
            "UCActivitySidebar.update_from_metrics: %d items",
            len(self._sensor_items),
        )
        try:
            for item in self._sensor_items:
                item.update_value(metrics)
        except Exception as e:
            log.error("Activity sidebar update error: %s", e)

    def stop_updates(self) -> None:
        """No-op — retained for cleanup compatibility."""
        log.info(
            "UCActivitySidebar.stop_updates: no-op (Topic.METRICS observer)",
        )
