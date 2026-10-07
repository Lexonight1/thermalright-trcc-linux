"""
PyQt6 UCActivitySidebar - Activity sidebar with live sensor values.

Shows the sensor dashboard's rows, live; click one to add it to the overlay.
Matches Windows TRCC's right-side Activity panel, which IS the dashboard panel
list, custom panels included.  It used to be a fixed 24-entry catalog, so a
board probe, a voltage or one DIMM the dashboard could show could never be
placed (#223, #259, #310).
"""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...core.models import PanelConfig, percent_only
from ..presentation.sensor_display import activity_rows, format_sensor_value

log = logging.getLogger(__name__)

# Dashboard panel category -> colour.  View-local (Qt layer).
CATEGORY_COLORS = {
    1: '#32C5FF',   # CPU
    2: '#44D7B6',   # GPU
    3: '#6DD401',   # memory
    4: '#F7B501',   # disk
    5: '#FA6401',   # network
    6: '#E02020',   # fan
}
_CUSTOM_COLOR = '#FFFFFF'


class SensorItem(QFrame):
    """Single sensor row -- clickable to add to overlay."""

    clicked = Signal(object)  # OverlayElementConfig

    def __init__(self, label, sensor_id, unit, config, color, parent=None):
        super().__init__(parent)
        self.sensor_id = sensor_id
        self.unit = unit
        self._overlay_config = config
        log.debug("SensorItem.__init__: %s label=%r unit=%s", sensor_id, label, unit)

        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 5, 0)
        layout.setSpacing(4)

        indicator = QLabel('\u25c6')
        indicator.setFixedWidth(12)
        indicator.setStyleSheet(f"color: {color}; font-size: 6px; background: transparent;")
        layout.addWidget(indicator)

        name_lbl = QLabel(label)
        name_lbl.setStyleSheet("color: #AAAAAA; font-size: 9px; background: transparent;")
        name_lbl.setFixedWidth(120)
        layout.addWidget(name_lbl)

        layout.addStretch()

        self.value_label = QLabel('--')
        self.value_label.setStyleSheet(f"color: {color}; font-size: 9px; font-weight: bold; background: transparent;")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setFixedWidth(80)
        layout.addWidget(self.value_label)

    def update_value(self, metrics):
        """Show this row's reading from the broadcast, by sensor id."""
        log.debug("update_value: %s", self.sensor_id)
        readings = getattr(metrics, "readings", {})
        # A GPU fan with a duty percent only shows it as one, never under
        # "RPM" (#145).
        if (duty := percent_only(readings, self.sensor_id)) is not None:
            self.value_label.setText(format_sensor_value(duty, "%"))
        elif (value := readings.get(self.sensor_id)) is not None:
            self.value_label.setText(format_sensor_value(value, self.unit))
        else:
            self.value_label.setText('--')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            log.info("SensorItem.mousePressEvent: %s (emit overlay add)",
                     self.sensor_id)
            self.clicked.emit(self._overlay_config)

    def enterEvent(self, event):
        self.setStyleSheet("background-color: #2A2A2A;")

    def leaveEvent(self, event):
        self.setStyleSheet("")


class UCActivitySidebar(QWidget):
    """Activity sidebar -- the dashboard's rows, live.  Click one to add it to
    the overlay grid.  Filled by :meth:`set_panels` each time it opens."""

    sensor_clicked = Signal(object)  # OverlayElementConfig

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sensor_items: list[SensorItem] = []
        self._inner: QWidget | None = None
        log.info("UCActivitySidebar.__init__: building activity sidebar")
        self._setup_ui()

    def _setup_ui(self):
        log.info("UCActivitySidebar._setup_ui")
        # Dark background via palette (not stylesheet -- children use QPalette)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#1E1E1E'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 5, 0, 0)
        main_layout.setSpacing(0)

        title = QLabel("Activity")
        title.setStyleSheet(
            "color: white; font-size: 10px; font-weight: bold; "
            "background: transparent; padding-left: 8px;"
        )
        main_layout.addWidget(title)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 8px; }"
            "QScrollBar::handle:vertical { background: #555; border-radius: 4px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        main_layout.addWidget(self._scroll)

    def set_panels(self, panels: list[PanelConfig]) -> None:
        """Rebuild the rows from the sensor dashboard (``GetSensorDashboard``)."""
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)
        self._sensor_items = []
        for name, category_id, rows in activity_rows(panels):
            color = CATEGORY_COLORS.get(category_id, _CUSTOM_COLOR)
            header = QLabel(f"  \u25aa {name.upper()}")
            header.setFixedHeight(24)
            header.setStyleSheet(
                f"color: {color}; font-size: 9px; font-weight: bold; "
                f"background-color: #2A2A2A; padding-top: 3px;"
            )
            inner_layout.addWidget(header)
            for label, sensor_id, unit, config in rows:
                config.color = color
                item = SensorItem(label, sensor_id, unit, config, color)
                item.clicked.connect(self._on_sensor_clicked)
                inner_layout.addWidget(item)
                self._sensor_items.append(item)
        inner_layout.addStretch()
        self._scroll.setWidget(inner)
        self._inner = inner
        log.info("UCActivitySidebar.set_panels: %d row(s) from %d panel(s)",
                 len(self._sensor_items), len(panels))

    def _on_sensor_clicked(self, config):
        log.info("UCActivitySidebar._on_sensor_clicked: %s", config.metric)
        self.sensor_clicked.emit(config)

    def update_from_metrics(self, metrics) -> None:
        """Render from the unified Topic.METRICS broadcast."""
        # Per-tick -- DEBUG so a default INFO run isn't drowned.
        log.debug("UCActivitySidebar.update_from_metrics: %d items",
                  len(self._sensor_items))
        try:
            for item in self._sensor_items:
                item.update_value(metrics)
        except Exception as e:
            log.error("Activity sidebar update error: %s", e)

    def stop_updates(self) -> None:
        """No-op -- retained for cleanup compatibility."""
        log.info("UCActivitySidebar.stop_updates: no-op (Topic.METRICS observer)")
