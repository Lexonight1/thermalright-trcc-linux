#!/usr/bin/env python3
"""
HR10 2280 Pro Digital control panel.

Dedicated panel for the HR10 NVMe SSD heatsink with ARGB 7-segment display.
Matches the Windows TRCC FormLED layout when HR10 is detected (style 13).

Key differences from the generic UCLedControl:
- 7-segment display preview instead of circular LED dots
- Live NVMe drive metrics (temp, activity, read/write rates)
- Display selection buttons (choose what the 7-segment display shows)
- HR10-specific mode labels (Monochrome, Mono Breathe, etc.)
- Circulate mode to auto-cycle through metrics

Layout coordinates from Windows FormLED.cs InitializeComponent / FormLED.resx.
"""

from typing import Dict, List, Optional, Tuple

try:
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QColor, QPalette
    from PyQt6.QtWidgets import (
        QCheckBox,
        QFrame,
        QLabel,
        QPushButton,
        QSlider,
        QSpinBox,
        QWidget,
    )
    PYQT6_AVAILABLE = True
except ImportError:
    PYQT6_AVAILABLE = False

if PYQT6_AVAILABLE:
    from .uc_seven_segment import UCSevenSegment


# =========================================================================
# Layout constants (from Windows FormLED.cs for HR10)
# =========================================================================

PANEL_WIDTH = 1274
PANEL_HEIGHT = 800

# 7-segment display preview (left side)
DISPLAY_X, DISPLAY_Y = 30, 100
DISPLAY_W, DISPLAY_H = 500, 400

# Mode buttons (6 buttons, same as generic but different labels)
MODE_Y = 120
MODE_X_START = 570
MODE_W, MODE_H = 103, 50
MODE_SPACING = 8

# RGB sliders (right side, below mode buttons)
RGB_X = 570
RGB_Y_START = 340
RGB_SLIDER_W = 400
RGB_SLIDER_H = 24
RGB_SPACING = 35
RGB_LABEL_W = 30
RGB_SPINBOX_W = 55

# Preset color buttons
PRESET_Y = 460
PRESET_X_START = 570
PRESET_SIZE = 30
PRESET_SPACING = 8

# Brightness slider
BRIGHT_X = 570
BRIGHT_Y = 520
BRIGHT_W = 400

# Drive metrics panel (bottom left)
METRICS_X = 30
METRICS_Y = 540
METRICS_W = 500
METRICS_H = 130

# Display selection buttons (bottom right)
DISPLAY_SEL_Y = 700
DISPLAY_SEL_X = 570
DISPLAY_SEL_W = 120
DISPLAY_SEL_H = 45
DISPLAY_SEL_SPACING = 8

# Circulate checkbox
CIRCULATE_X = 570
CIRCULATE_Y = 660

# Status label
STATUS_X = 570
STATUS_Y = 760

# HR10-specific mode labels (from Windows FormLED)
HR10_MODE_LABELS = [
    "Monochrome",
    "Mono\nBreathe",
    "Color\nCycle",
    "Rainbow",
    "According\nto Temp",
    "According\nto Load",
]

# Display selection options
DISPLAY_METRICS = [
    ("Temp\n(°C/°F)", "temp"),
    ("Activity\n(%)", "activity"),
    ("Read Rate\n(MB/s)", "read"),
    ("Write Rate\n(MB/s)", "write"),
]

# Preset colors (from FormLED.cs)
PRESET_COLORS = [
    (255, 0, 42),
    (255, 110, 0),
    (255, 255, 0),
    (0, 255, 0),
    (0, 255, 255),
    (0, 91, 255),
    (214, 0, 255),
    (255, 255, 255),
]

# Circulate interval range (seconds)
CIRCULATE_MIN_S = 2
CIRCULATE_MAX_S = 10
CIRCULATE_DEFAULT_S = 5


if PYQT6_AVAILABLE:

    class UCHr10Control(QWidget):
        """HR10 NVMe heatsink control panel.

        Full replacement for UCLedControl when the HR10 device is detected.
        """

        # Signals for controller binding (same interface as UCLedControl)
        mode_changed = pyqtSignal(int)
        color_changed = pyqtSignal(int, int, int)
        brightness_changed = pyqtSignal(int)
        global_toggled = pyqtSignal(bool)
        # HR10-specific signals
        display_metric_changed = pyqtSignal(str)  # "temp", "activity", "read", "write"
        circulate_toggled = pyqtSignal(bool)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setFixedSize(PANEL_WIDTH, PANEL_HEIGHT)

            self._current_mode = 0
            self._current_metric = "temp"
            self._metrics: Dict[str, float] = {}
            self._temp_unit = "°C"  # Toggled by display selection

            # Circulate timer
            self._circulate_timer = QTimer(self)
            self._circulate_timer.timeout.connect(self._on_circulate_tick)
            self._circulate_index = 0

            self._setup_ui()

        def _setup_ui(self):
            """Create all UI elements."""
            # Dark background
            self.setAutoFillBackground(True)
            palette = self.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
            self.setPalette(palette)

            # -- Title --
            self._title = QLabel("HR10 2280 PRO DIGITAL", self)
            self._title.setGeometry(30, 20, 500, 40)
            self._title.setStyleSheet(
                "color: white; font-size: 20px; font-weight: bold;"
            )

            # -- 7-Segment Display Preview --
            self._display = UCSevenSegment(self)
            self._display.move(DISPLAY_X, DISPLAY_Y)
            self._display.set_value("---", "°C")

            # -- Mode buttons --
            self._mode_buttons: List[QPushButton] = []
            for i, label in enumerate(HR10_MODE_LABELS):
                btn = QPushButton(label, self)
                x = MODE_X_START + i * (MODE_W + MODE_SPACING)
                btn.setGeometry(x, MODE_Y, MODE_W, MODE_H)
                btn.setCheckable(True)
                btn.setStyleSheet(self._mode_button_style())
                btn.clicked.connect(
                    lambda checked, idx=i: self._on_mode_clicked(idx)
                )
                self._mode_buttons.append(btn)

            if self._mode_buttons:
                self._mode_buttons[0].setChecked(True)

            # -- Mode active indicator (green underline for selected) --
            self._mode_indicators: List[QFrame] = []
            for i in range(len(HR10_MODE_LABELS)):
                indicator = QFrame(self)
                x = MODE_X_START + i * (MODE_W + MODE_SPACING)
                indicator.setGeometry(x + 10, MODE_Y + MODE_H + 2, MODE_W - 20, 3)
                indicator.setStyleSheet(
                    "background-color: #4CAF50;" if i == 0 else "background-color: transparent;"
                )
                self._mode_indicators.append(indicator)

            # -- RGB Controls --
            self._rgb_sliders: List[QSlider] = []
            self._rgb_spinboxes: List[QSpinBox] = []
            rgb_labels = ["R", "G", "B"]
            rgb_colors = ["#ff4444", "#44ff44", "#4444ff"]

            for i, (lbl, color) in enumerate(zip(rgb_labels, rgb_colors)):
                y = RGB_Y_START + i * RGB_SPACING

                # Label with colored dot
                dot = QLabel(f"  {lbl}", self)
                dot.setGeometry(RGB_X, y, RGB_LABEL_W, RGB_SLIDER_H)
                dot.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
                dot.setAlignment(Qt.AlignmentFlag.AlignCenter)

                # Slider
                slider = QSlider(Qt.Orientation.Horizontal, self)
                slider.setGeometry(
                    RGB_X + RGB_LABEL_W + 5, y,
                    RGB_SLIDER_W - RGB_LABEL_W - RGB_SPINBOX_W - 15, RGB_SLIDER_H
                )
                slider.setRange(0, 255)
                slider.setValue(255 if i == 0 else 0)
                slider.setStyleSheet(
                    f"QSlider::groove:horizontal {{ background: #333; height: 8px; border-radius: 4px; }}"
                    f"QSlider::handle:horizontal {{ background: {color}; width: 16px; "
                    f"margin: -4px 0; border-radius: 8px; }}"
                    f"QSlider::sub-page:horizontal {{ background: {color}; border-radius: 4px; }}"
                )
                slider.valueChanged.connect(self._on_rgb_changed)
                self._rgb_sliders.append(slider)

                # Spinbox
                spinbox = QSpinBox(self)
                spinbox.setGeometry(
                    RGB_X + RGB_SLIDER_W - RGB_SPINBOX_W, y, RGB_SPINBOX_W, RGB_SLIDER_H
                )
                spinbox.setRange(0, 255)
                spinbox.setValue(255 if i == 0 else 0)
                spinbox.setStyleSheet(
                    "color: white; background: #333; border: 1px solid #555; border-radius: 3px;"
                )
                spinbox.valueChanged.connect(
                    lambda val, idx=i: self._on_spinbox_changed(idx, val)
                )
                self._rgb_spinboxes.append(spinbox)

            # -- Color preview swatch --
            self._color_swatch = QFrame(self)
            self._color_swatch.setGeometry(
                RGB_X + RGB_SLIDER_W + 15, RGB_Y_START, 40, RGB_SPACING * 3 - 10
            )
            self._update_color_swatch()

            # -- Preset color buttons --
            for i, (r, g, b) in enumerate(PRESET_COLORS):
                btn = QPushButton(self)
                x = PRESET_X_START + i * (PRESET_SIZE + PRESET_SPACING)
                btn.setGeometry(x, PRESET_Y, PRESET_SIZE, PRESET_SIZE)
                btn.setStyleSheet(
                    f"QPushButton {{ "
                    f"background-color: rgb({r},{g},{b}); "
                    f"border: 2px solid #555; border-radius: {PRESET_SIZE // 2}px; }}"
                    f"QPushButton:hover {{ border: 2px solid white; }}"
                )
                btn.clicked.connect(
                    lambda checked, cr=r, cg=g, cb=b: self._set_color(cr, cg, cb)
                )

            # -- Brightness --
            bright_label = QLabel("Brightness", self)
            bright_label.setGeometry(BRIGHT_X, BRIGHT_Y - 20, 100, 18)
            bright_label.setStyleSheet("color: #aaa; font-size: 12px;")

            self._brightness_slider = QSlider(Qt.Orientation.Horizontal, self)
            self._brightness_slider.setGeometry(BRIGHT_X, BRIGHT_Y, BRIGHT_W, 24)
            self._brightness_slider.setRange(0, 100)
            self._brightness_slider.setValue(100)
            self._brightness_slider.setStyleSheet(
                "QSlider::groove:horizontal { background: #333; height: 8px; border-radius: 4px; }"
                "QSlider::handle:horizontal { background: #fff; width: 16px; "
                "margin: -4px 0; border-radius: 8px; }"
                "QSlider::sub-page:horizontal { background: #aaa; border-radius: 4px; }"
            )
            self._brightness_slider.valueChanged.connect(self.brightness_changed.emit)

            self._brightness_label = QLabel("100%", self)
            self._brightness_label.setGeometry(BRIGHT_X + BRIGHT_W + 10, BRIGHT_Y, 50, 24)
            self._brightness_label.setStyleSheet("color: white; font-size: 13px;")
            self._brightness_slider.valueChanged.connect(
                lambda v: self._brightness_label.setText(f"{v}%")
            )

            # -- Drive Metrics Panel (bottom left) --
            metrics_bg = QFrame(self)
            metrics_bg.setGeometry(METRICS_X, METRICS_Y, METRICS_W, METRICS_H)
            metrics_bg.setStyleSheet(
                "background-color: rgba(20, 20, 20, 200); "
                "border: 1px solid #444; border-radius: 6px;"
            )

            self._metric_labels: Dict[str, QLabel] = {}
            metric_defs = [
                ("Drive Temp:", "disk_temp", "-- °C"),
                ("Total Activity:", "disk_activity", "-- %"),
                ("Read Rate:", "disk_read", "-- MB/s"),
                ("Write Rate:", "disk_write", "-- MB/s"),
            ]
            for i, (label_text, key, default) in enumerate(metric_defs):
                y = METRICS_Y + 12 + i * 28

                name_label = QLabel(label_text, self)
                name_label.setGeometry(METRICS_X + 15, y, 140, 24)
                name_label.setStyleSheet("color: #aaa; font-size: 13px;")

                value_label = QLabel(default, self)
                value_label.setGeometry(METRICS_X + 160, y, 120, 24)
                value_label.setStyleSheet("color: white; font-size: 13px; font-weight: bold;")
                self._metric_labels[key] = value_label

            # -- NVMe device label (below metrics) --
            self._nvme_label = QLabel("", self)
            self._nvme_label.setGeometry(METRICS_X + 200, METRICS_Y + 10, 280, 20)
            self._nvme_label.setStyleSheet("color: #666; font-size: 11px;")

            # -- Display Selection section label --
            ds_label = QLabel("Display selection", self)
            ds_label.setGeometry(DISPLAY_SEL_X, DISPLAY_SEL_Y - 30, 200, 20)
            ds_label.setStyleSheet("color: #aaa; font-size: 12px;")

            # -- Circulate checkbox --
            self._circulate_cb = QCheckBox("Circulate", self)
            self._circulate_cb.setGeometry(CIRCULATE_X, CIRCULATE_Y, 100, 20)
            self._circulate_cb.setStyleSheet(
                "QCheckBox { color: #aaa; font-size: 12px; }"
                "QCheckBox::indicator { width: 14px; height: 14px; }"
                "QCheckBox::indicator:unchecked { border: 1px solid #666; background: #333; }"
                "QCheckBox::indicator:checked { border: 1px solid #4CAF50; background: #4CAF50; }"
            )
            self._circulate_cb.toggled.connect(self._on_circulate_toggled)

            # Circulate interval spinbox
            interval_label = QLabel("Interval:", self)
            interval_label.setGeometry(CIRCULATE_X + 110, CIRCULATE_Y, 55, 20)
            interval_label.setStyleSheet("color: #888; font-size: 11px;")

            self._circulate_interval = QSpinBox(self)
            self._circulate_interval.setGeometry(CIRCULATE_X + 170, CIRCULATE_Y - 2, 45, 22)
            self._circulate_interval.setRange(CIRCULATE_MIN_S, CIRCULATE_MAX_S)
            self._circulate_interval.setValue(CIRCULATE_DEFAULT_S)
            self._circulate_interval.setSuffix("s")
            self._circulate_interval.setStyleSheet(
                "color: white; background: #333; border: 1px solid #555; "
                "border-radius: 3px; font-size: 11px;"
            )
            self._circulate_interval.valueChanged.connect(self._on_circulate_interval_changed)

            # -- Display Selection Buttons --
            self._display_buttons: List[QPushButton] = []
            for i, (label, metric_key) in enumerate(DISPLAY_METRICS):
                btn = QPushButton(label, self)
                x = DISPLAY_SEL_X + i * (DISPLAY_SEL_W + DISPLAY_SEL_SPACING)
                btn.setGeometry(x, DISPLAY_SEL_Y, DISPLAY_SEL_W, DISPLAY_SEL_H)
                btn.setCheckable(True)
                btn.setStyleSheet(self._display_button_style())
                btn.clicked.connect(
                    lambda checked, key=metric_key, idx=i: self._on_display_selected(key, idx)
                )
                self._display_buttons.append(btn)

            # Select Temp by default
            if self._display_buttons:
                self._display_buttons[0].setChecked(True)

            # -- Status label --
            self._status = QLabel("", self)
            self._status.setGeometry(STATUS_X, STATUS_Y, 600, 24)
            self._status.setStyleSheet("color: #aaa; font-size: 12px;")

        # ================================================================
        # Public API
        # ================================================================

        def initialize(self, style_id: int, segment_count: int,
                       zone_count: int = 1, lang: str = 'en') -> None:
            """Configure for HR10 device."""
            self._title.setText("HR10 2280 PRO DIGITAL")
            # Set initial display preview color from current RGB
            r = self._rgb_sliders[0].value()
            g = self._rgb_sliders[1].value()
            b = self._rgb_sliders[2].value()
            self._display.set_color(r, g, b)

        def set_led_colors(self, colors: List[Tuple[int, int, int]]) -> None:
            """Update display preview from controller tick."""
            # Use the first LED color to tint the 7-segment display
            if colors:
                r, g, b = colors[0]
                self._display.set_color(r, g, b)

        def set_status(self, text: str) -> None:
            """Update status text."""
            self._status.setText(text)

        def update_drive_metrics(self, metrics: Dict[str, float]) -> None:
            """Update live drive metrics from system_info polling.

            Expected keys: disk_temp, disk_activity, disk_read, disk_write.
            """
            self._metrics = metrics

            # Update metric value labels
            if 'disk_temp' in metrics:
                temp = metrics['disk_temp']
                if self._temp_unit == "°F":
                    temp = temp * 9 / 5 + 32
                self._metric_labels['disk_temp'].setText(
                    f"{temp:.0f} {self._temp_unit}"
                )
            if 'disk_activity' in metrics:
                self._metric_labels['disk_activity'].setText(
                    f"{metrics['disk_activity']:.0f}%"
                )
            if 'disk_read' in metrics:
                self._metric_labels['disk_read'].setText(
                    f"{metrics['disk_read']:.1f} MB/s"
                )
            if 'disk_write' in metrics:
                self._metric_labels['disk_write'].setText(
                    f"{metrics['disk_write']:.1f} MB/s"
                )

            # Update 7-segment display preview with selected metric
            self._update_display_value()

        def set_temp_unit(self, unit_int: int) -> None:
            """Set temperature unit from app settings.

            Args:
                unit_int: 0 = °C, 1 = °F (matches app config).
            """
            self._temp_unit = "°F" if unit_int == 1 else "°C"
            self._update_display_value()

        def set_language(self, lang: str) -> None:
            """Apply localized background (no-op for HR10)."""
            pass

        # ================================================================
        # Internal handlers
        # ================================================================

        def _on_mode_clicked(self, index: int):
            self._current_mode = index
            for i, btn in enumerate(self._mode_buttons):
                btn.setChecked(i == index)
            for i, ind in enumerate(self._mode_indicators):
                ind.setStyleSheet(
                    "background-color: #4CAF50;" if i == index
                    else "background-color: transparent;"
                )
            self.mode_changed.emit(index)

        def _on_rgb_changed(self):
            r = self._rgb_sliders[0].value()
            g = self._rgb_sliders[1].value()
            b = self._rgb_sliders[2].value()
            for i, val in enumerate([r, g, b]):
                self._rgb_spinboxes[i].blockSignals(True)
                self._rgb_spinboxes[i].setValue(val)
                self._rgb_spinboxes[i].blockSignals(False)
            self._update_color_swatch()
            self._display.set_color(r, g, b)
            self.color_changed.emit(r, g, b)

        def _on_spinbox_changed(self, index: int, value: int):
            self._rgb_sliders[index].blockSignals(True)
            self._rgb_sliders[index].setValue(value)
            self._rgb_sliders[index].blockSignals(False)
            r = self._rgb_spinboxes[0].value()
            g = self._rgb_spinboxes[1].value()
            b = self._rgb_spinboxes[2].value()
            self._update_color_swatch()
            self._display.set_color(r, g, b)
            self.color_changed.emit(r, g, b)

        def _set_color(self, r: int, g: int, b: int):
            for i, val in enumerate([r, g, b]):
                self._rgb_sliders[i].blockSignals(True)
                self._rgb_sliders[i].setValue(val)
                self._rgb_sliders[i].blockSignals(False)
                self._rgb_spinboxes[i].blockSignals(True)
                self._rgb_spinboxes[i].setValue(val)
                self._rgb_spinboxes[i].blockSignals(False)
            self._update_color_swatch()
            self._display.set_color(r, g, b)
            self.color_changed.emit(r, g, b)

        def _update_color_swatch(self):
            r = self._rgb_sliders[0].value()
            g = self._rgb_sliders[1].value()
            b = self._rgb_sliders[2].value()
            self._color_swatch.setStyleSheet(
                f"background-color: rgb({r},{g},{b}); border: 2px solid #555; "
                f"border-radius: 5px;"
            )

        def _on_display_selected(self, metric_key: str, button_index: int):
            """Handle display selection button click."""
            self._current_metric = metric_key
            for i, btn in enumerate(self._display_buttons):
                btn.setChecked(i == button_index)

            self._update_display_value()
            self.display_metric_changed.emit(metric_key)

        def _update_display_value(self):
            """Update the 7-segment display with the current metric value."""
            metric = self._current_metric

            if metric == "temp":
                val = self._metrics.get('disk_temp')
                if val is not None:
                    if self._temp_unit == "°F":
                        val = val * 9 / 5 + 32
                    self._display.set_value(f"{val:.0f}", self._temp_unit)
                else:
                    self._display.set_value("---", self._temp_unit)

            elif metric == "activity":
                val = self._metrics.get('disk_activity')
                if val is not None:
                    self._display.set_value(f"{val:.0f}", "%")
                else:
                    self._display.set_value("---", "%")

            elif metric == "read":
                val = self._metrics.get('disk_read')
                if val is not None:
                    self._display.set_value(f"{val:.0f}", "MB/s")
                else:
                    self._display.set_value("---", "MB/s")

            elif metric == "write":
                val = self._metrics.get('disk_write')
                if val is not None:
                    self._display.set_value(f"{val:.0f}", "MB/s")
                else:
                    self._display.set_value("---", "MB/s")

        def _on_circulate_toggled(self, enabled: bool):
            """Handle circulate checkbox toggle."""
            if enabled:
                interval_ms = self._circulate_interval.value() * 1000
                self._circulate_timer.start(interval_ms)
                self._circulate_index = 0
            else:
                self._circulate_timer.stop()
            self.circulate_toggled.emit(enabled)

        def _on_circulate_interval_changed(self, value: int):
            """Handle circulate interval change."""
            if self._circulate_timer.isActive():
                self._circulate_timer.start(value * 1000)

        def _on_circulate_tick(self):
            """Advance to next metric in circulate mode."""
            self._circulate_index = (self._circulate_index + 1) % len(DISPLAY_METRICS)
            label, key = DISPLAY_METRICS[self._circulate_index]
            self._on_display_selected(key, self._circulate_index)

        # ================================================================
        # Styles
        # ================================================================

        @staticmethod
        def _mode_button_style() -> str:
            return (
                "QPushButton { background: #3a3a3a; color: #ccc; "
                "border: 1px solid #555; border-radius: 6px; "
                "font-size: 11px; padding: 4px; }"
                "QPushButton:checked { background: #2a2a2a; color: white; "
                "border: 2px solid #4CAF50; font-weight: bold; }"
                "QPushButton:hover { background: #4a4a4a; }"
            )

        @staticmethod
        def _display_button_style() -> str:
            return (
                "QPushButton { background: #3a3a3a; color: #ccc; "
                "border: 1px solid #555; border-radius: 6px; "
                "font-size: 11px; padding: 6px; }"
                "QPushButton:checked { background: #2a2a2a; color: #ff6b6b; "
                "border: 2px solid #ff6b6b; font-weight: bold; }"
                "QPushButton:hover { background: #4a4a4a; }"
            )
