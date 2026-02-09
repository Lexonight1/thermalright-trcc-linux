#!/usr/bin/env python3
"""
LED control panel (FormLED equivalent).

Full LED control UI matching Windows FormLED layout:
- Left side: Device preview (UCScreenLED circles or UCSevenSegment for HR10)
- Right side: Mode buttons, color wheel, RGB controls, presets, brightness
- Bottom: Zone selection buttons (for multi-zone devices)
- HR10-specific: Drive metrics panel, display selection, circulate mode

Layout coordinates from FormLED.cs InitializeComponent / FormLED.resx.
All LED devices (styles 1-13) use this single panel — matching Windows
FormLED.cs which is one form for all LED device types.
"""

from typing import Dict, List, Optional, Tuple

try:
    from PyQt6.QtCore import QRect, Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QColor, QFont, QPainter, QPalette, QPixmap
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
    from .assets import Assets, load_pixmap
    from .base import set_background_pixmap
    from .uc_color_wheel import UCColorWheel
    from .uc_screen_led import UCScreenLED
    from .uc_seven_segment import UCSevenSegment


# =========================================================================
# Layout constants (from FormLED.cs / FormLED.resx)
# =========================================================================

# Panel occupies full form area: (180, 0, 1274, 800) relative to main window
PANEL_WIDTH = 1274
PANEL_HEIGHT = 800

# UCScreenLED preview (standard devices)
PREVIEW_X, PREVIEW_Y = 16, 80
PREVIEW_W, PREVIEW_H = 536, 536

# 7-segment preview position (HR10 — shorter, same left area)
SEG_PREVIEW_X, SEG_PREVIEW_Y = 30, 100
SEG_PREVIEW_W, SEG_PREVIEW_H = 500, 400

# Mode buttons (6 buttons, arranged horizontally)
MODE_Y = 227
MODE_X_START = 590
MODE_W, MODE_H = 93, 62
MODE_SPACING = 10

# --- Standard layout (no color wheel) ---
# RGB sliders
RGB_X = 590
RGB_Y_START = 340
RGB_SLIDER_W = 400
RGB_SLIDER_H = 24
RGB_SPACING = 35
RGB_LABEL_W = 30
RGB_SPINBOX_W = 55

# Preset color buttons (8 buttons)
PRESET_Y = 470
PRESET_X_START = 590
PRESET_SIZE = 30
PRESET_SPACING = 8

# Brightness slider
BRIGHT_X = 590
BRIGHT_Y = 530
BRIGHT_W = 400

# On/Off button
ONOFF_X = 1050
ONOFF_Y = 530
ONOFF_W, ONOFF_H = 80, 30

# --- HR10 layout (with color wheel — controls shift right) ---
HR10_WHEEL_X, HR10_WHEEL_Y = 565, 295
HR10_WHEEL_W, HR10_WHEEL_H = 300, 280

HR10_RGB_X = 880
HR10_RGB_Y_START = 320
HR10_RGB_SLIDER_W = 300

HR10_PRESET_Y = 470
HR10_PRESET_X_START = 880

HR10_BRIGHT_X = 590
HR10_BRIGHT_Y = 600
HR10_BRIGHT_W = 560

# Temperature color legend (HR10 modes 4-5)
TEMP_LEGEND_X = 590
TEMP_LEGEND_Y = 570
TEMP_LEGEND_W = 560
TEMP_LEGEND_H = 18

# Drive metrics panel (HR10, bottom left below preview)
METRICS_X = 30
METRICS_Y = 540
METRICS_W = 500
METRICS_H = 130

# Display selection buttons (HR10, bottom right)
DISPLAY_SEL_X = 590
DISPLAY_SEL_Y = 710
DISPLAY_SEL_W = 120
DISPLAY_SEL_H = 45
DISPLAY_SEL_SPACING = 8

# Circulate checkbox (HR10)
CIRCULATE_X = 590
CIRCULATE_Y = 670

# Zone buttons (bottom, multi-zone only)
ZONE_Y = 620
ZONE_X_START = 590
ZONE_W, ZONE_H = 130, 40
ZONE_SPACING = 10

# Status label
STATUS_X = 590
STATUS_Y = 700
STATUS_W = 600

# Mode button labels (English)
MODE_LABELS = [
    "Static",
    "Breathing",
    "Colorful",
    "Rainbow",
    "Temp Link",
    "Load Link",
]

# Preset colors (from FormLED.cs buttonC1-C8)
PRESET_COLORS = [
    (255, 0, 42),     # Red-pink
    (255, 110, 0),    # Orange
    (255, 255, 0),    # Yellow
    (0, 255, 0),      # Green
    (0, 255, 255),    # Cyan
    (0, 91, 255),     # Blue
    (214, 0, 255),    # Purple
    (255, 255, 255),  # White
]

# Display selection options (HR10)
DISPLAY_METRICS = [
    ("Temp\n(\u00b0C/\u00b0F)", "temp"),
    ("Activity\n(%)", "activity"),
    ("Read Rate\n(MB/s)", "read"),
    ("Write Rate\n(MB/s)", "write"),
]

# Circulate interval range (seconds)
CIRCULATE_MIN_S = 2
CIRCULATE_MAX_S = 10
CIRCULATE_DEFAULT_S = 5

# Shared stylesheet fragments (used by multiple widgets in this module)
_STYLE_INFO_BG = (
    "background-color: rgba(20, 20, 20, 200); "
    "border: 1px solid #444; border-radius: 6px;"
)
_STYLE_INFO_NAME = "color: #aaa; font-size: 11px;"
_STYLE_INFO_VALUE = "color: white; font-size: 11px; font-weight: bold;"
_STYLE_MUTED_LABEL = "color: #aaa; font-size: 12px;"
_STYLE_CHECKABLE_BTN = (
    "QPushButton { background: #444; color: #aaa; border: 1px solid #666; "
    "border-radius: 4px; font-size: 11px; }"
    "QPushButton:checked { background: #2196F3; color: white; "
    "border: 1px solid #42A5F5; }"
)


if PYQT6_AVAILABLE:

    class UCInfoImage(QWidget):
        """Sensor gauge widget matching Windows UCInfoImage.cs.

        Shows a background label image (P0M{n}.png), a progress bar fill
        (P环H{n}.png) drawn at variable width, and a numeric value overlay.
        Each widget is 240x30 pixels.

        From FormLED.cs: ucInfoImage1-6 display CPU/GPU temp/clock/usage.
        """

        def __init__(self, index: int, parent=None):
            super().__init__(parent)
            self.setFixedSize(240, 30)

            self._index = index  # 1-6
            self._value = 0.0
            self._text = "--"
            self._unit = ""
            self._mode = 1  # 1=temp/percent, 2=MHz/RPM

            # Load assets
            self._bg_pixmap = load_pixmap(f"P0M{index}.png")
            self._bar_pixmap = load_pixmap(f"P环H{index}.png")

        def set_value(self, value: float, text: str, unit: str = "") -> None:
            """Update displayed value.

            Args:
                value: Numeric value (0-100 for temp/percent, 0-5000 for MHz).
                text: Formatted display string (e.g. "65").
                unit: Unit suffix (e.g. "°C", "%", "MHz").
            """
            self._value = value
            self._text = text
            self._unit = unit
            self.update()

        def set_mode(self, mode: int) -> None:
            """Set value scaling mode: 1=temp/percent (0-100), 2=MHz/RPM (0-5000)."""
            self._mode = mode

        def paintEvent(self, event):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

            # Background image
            if self._bg_pixmap and not self._bg_pixmap.isNull():
                p.drawPixmap(0, 0, self._bg_pixmap)

            # Progress bar (x=35, y=22, max width=200, height=3)
            if self._bar_pixmap and not self._bar_pixmap.isNull():
                if self._mode == 1:  # temp or percent: 0-100 → 0-200px
                    bar_w = max(0, min(200, int(self._value * 2)))
                else:  # MHz/RPM: 0-5000 → 0-200px
                    bar_w = max(0, min(200, int(self._value / 25)))
                if bar_w > 0:
                    p.drawPixmap(
                        35, 22, bar_w, 3,
                        self._bar_pixmap,
                        0, 0, bar_w, 3,
                    )

            # Value text overlay
            font = QFont("Segoe UI", 9)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRect(115, 5, 80, 20), Qt.AlignmentFlag.AlignLeft, self._text)
            p.setPen(QColor(180, 180, 180))
            p.drawText(QRect(170, 5, 60, 20), Qt.AlignmentFlag.AlignLeft, self._unit)

            p.end()

    class UCLedControl(QWidget):
        """LED control panel matching Windows FormLED.

        Contains device preview, mode buttons, color wheel, color picker,
        brightness, and zone selection. Handles all LED device styles
        including HR10 (style 13) with 7-segment preview and drive metrics.
        """

        # Signals for controller binding
        mode_changed = pyqtSignal(int)              # LEDMode value
        color_changed = pyqtSignal(int, int, int)   # R, G, B
        brightness_changed = pyqtSignal(int)         # 0-100
        global_toggled = pyqtSignal(bool)            # on/off
        segment_clicked = pyqtSignal(int)            # segment index
        # HR10-specific signals
        display_metric_changed = pyqtSignal(str)     # "temp", "activity", ...
        circulate_toggled = pyqtSignal(bool)
        # Zone signals
        zone_selected = pyqtSignal(int)              # zone index (0-based)
        sync_all_changed = pyqtSignal(bool)          # sync mode toggled
        # LC2 clock signals (style 9)
        clock_format_changed = pyqtSignal(bool)      # True = 24h
        week_start_changed = pyqtSignal(bool)        # True = Sunday
        # Temperature unit signal
        temp_unit_changed = pyqtSignal(str)          # "C" or "F"

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setFixedSize(PANEL_WIDTH, PANEL_HEIGHT)

            self._current_mode = 0
            self._zone_count = 1
            self._style_id = 0
            self._is_hr10 = False
            self._lang = 'en'

            # Zone state
            self._selected_zone = 0
            self._sync_all = False

            # HR10 state
            self._current_metric = "temp"
            self._metrics: Dict[str, float] = {}
            self._temp_unit = "\u00b0C"

            # LC2 clock state (style 9)
            self._is_timer_24h = True
            self._is_week_sunday = False

            # Circulate timer (HR10)
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

            # -- LED Preview (standard: circles) --
            self._preview = UCScreenLED(self)
            self._preview.move(PREVIEW_X, PREVIEW_Y)
            self._preview.segment_clicked.connect(self.segment_clicked.emit)

            # -- 7-Segment Preview (HR10 — hidden by default) --
            self._seg_display = UCSevenSegment(self)
            self._seg_display.move(SEG_PREVIEW_X, SEG_PREVIEW_Y)
            self._seg_display.set_value("---", "\u00b0C")
            self._seg_display.setVisible(False)

            # -- Title label --
            self._title = QLabel("RGB LED Control", self)
            self._title.setGeometry(PREVIEW_X, 20, 536, 40)
            self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._title.setStyleSheet(
                "color: white; font-size: 20px; font-weight: bold;"
            )

            # -- Mode buttons --
            self._mode_buttons: List[QPushButton] = []
            for i, label in enumerate(MODE_LABELS):
                btn = QPushButton(label, self)
                x = MODE_X_START + i * (MODE_W + MODE_SPACING)
                btn.setGeometry(x, MODE_Y, MODE_W, MODE_H)
                btn.setCheckable(True)
                btn.setStyleSheet(self._mode_button_style(False))
                btn.clicked.connect(lambda checked, idx=i: self._on_mode_clicked(idx))

                # Try to load mode button image
                normal_name = f"D2\u706f\u51491{i + 1}"
                active_name = f"D2\u706f\u51491{i + 1}a"
                normal_path = Assets.get(normal_name)
                active_path = Assets.get(active_name)
                if normal_path and active_path:
                    btn.setText("")  # Clear text, use images
                    btn.setStyleSheet(
                        f"QPushButton {{ border: none; "
                        f"background-image: url({normal_path}); "
                        f"background-repeat: no-repeat; }}"
                        f"QPushButton:checked {{ "
                        f"background-image: url({active_path}); }}"
                    )

                self._mode_buttons.append(btn)

            # Set initial mode
            if self._mode_buttons:
                self._mode_buttons[0].setChecked(True)

            # -- Color Wheel (shown for HR10, hidden for others) --
            self._color_wheel = UCColorWheel(self)
            self._color_wheel.setGeometry(HR10_WHEEL_X, HR10_WHEEL_Y,
                                          HR10_WHEEL_W, HR10_WHEEL_H)
            self._color_wheel.hue_changed.connect(self._on_hue_changed)
            self._color_wheel.setVisible(False)

            # -- RGB Controls --
            self._rgb_sliders: List[QSlider] = []
            self._rgb_spinboxes: List[QSpinBox] = []
            self._rgb_labels: List[QLabel] = []
            rgb_label_texts = ["R", "G", "B"]
            rgb_colors = ["#ff4444", "#44ff44", "#4444ff"]

            for i, (lbl, color) in enumerate(zip(rgb_label_texts, rgb_colors)):
                y = RGB_Y_START + i * RGB_SPACING

                # Label
                label = QLabel(lbl, self)
                label.setGeometry(RGB_X, y, RGB_LABEL_W, RGB_SLIDER_H)
                label.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._rgb_labels.append(label)

                # Slider
                slider = QSlider(Qt.Orientation.Horizontal, self)
                slider.setGeometry(
                    RGB_X + RGB_LABEL_W + 5, y,
                    RGB_SLIDER_W - RGB_LABEL_W - RGB_SPINBOX_W - 15,
                    RGB_SLIDER_H
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
                    RGB_X + RGB_SLIDER_W - RGB_SPINBOX_W, y,
                    RGB_SPINBOX_W, RGB_SLIDER_H
                )
                spinbox.setRange(0, 255)
                spinbox.setValue(255 if i == 0 else 0)
                spinbox.setStyleSheet(
                    "color: white; background: #333; border: 1px solid #555; "
                    "border-radius: 3px;"
                )
                spinbox.valueChanged.connect(
                    lambda val, idx=i: self._on_spinbox_changed(idx, val)
                )
                self._rgb_spinboxes.append(spinbox)

            # -- Color preview swatch --
            self._color_swatch = QFrame(self)
            self._color_swatch.setGeometry(
                RGB_X + RGB_SLIDER_W + 15, RGB_Y_START,
                40, RGB_SPACING * 3 - 10
            )
            self._update_color_swatch()

            # -- Preset color buttons --
            self._preset_buttons: List[QPushButton] = []
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
                self._preset_buttons.append(btn)

            # -- Temperature color legend (HR10 modes 4-5, hidden by default) --
            self._temp_legend = QLabel(self)
            self._temp_legend.setGeometry(
                TEMP_LEGEND_X, TEMP_LEGEND_Y, TEMP_LEGEND_W, TEMP_LEGEND_H
            )
            self._temp_legend.setStyleSheet(
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 #00CCFF, stop:0.33 #00FF00, stop:0.55 #FFFF00, "
                "stop:0.78 #FF8800, stop:1 #FF0000); "
                "border-radius: 3px;"
            )
            self._temp_legend.setVisible(False)

            self._temp_legend_labels = QLabel(
                "<30\u00b0         <50\u00b0         <70\u00b0         <90\u00b0         >90\u00b0", self
            )
            self._temp_legend_labels.setGeometry(
                TEMP_LEGEND_X, TEMP_LEGEND_Y + TEMP_LEGEND_H + 2,
                TEMP_LEGEND_W, 14
            )
            self._temp_legend_labels.setStyleSheet(
                "color: #aaa; font-size: 10px; background: transparent;"
            )
            self._temp_legend_labels.setVisible(False)

            # -- Brightness --
            self._bright_label = QLabel("Brightness", self)
            self._bright_label.setGeometry(BRIGHT_X, BRIGHT_Y - 20, 100, 18)
            self._bright_label.setStyleSheet("color: #aaa; font-size: 12px;")

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

            # -- On/Off toggle --
            self._onoff_btn = QPushButton("ON", self)
            self._onoff_btn.setGeometry(ONOFF_X, ONOFF_Y, ONOFF_W, ONOFF_H)
            self._onoff_btn.setCheckable(True)
            self._onoff_btn.setChecked(True)
            self._onoff_btn.setStyleSheet(
                "QPushButton { background: #4CAF50; color: white; border: none; "
                "border-radius: 4px; font-weight: bold; }"
                "QPushButton:checked { background: #4CAF50; }"
                "QPushButton:!checked { background: #666; }"
            )
            self._onoff_btn.clicked.connect(self._on_toggle_clicked)

            # -- Zone buttons (hidden by default, shown for multi-zone devices) --
            self._zone_buttons: List[QPushButton] = []
            for i in range(4):
                btn = QPushButton(f"Zone {i + 1}", self)
                x = ZONE_X_START + i * (ZONE_W + ZONE_SPACING)
                btn.setGeometry(x, ZONE_Y, ZONE_W, ZONE_H)
                btn.setCheckable(True)
                btn.setStyleSheet(
                    "QPushButton { background: #444; color: white; "
                    "border: 1px solid #666; border-radius: 4px; }"
                    "QPushButton:checked { background: #2196F3; "
                    "border: 1px solid #42A5F5; }"
                )
                btn.clicked.connect(
                    lambda checked, idx=i: self._on_zone_clicked(idx)
                )
                btn.setVisible(False)
                self._zone_buttons.append(btn)

            # Sync All checkbox (Windows "buttonLB" = LunBo mode)
            self._sync_cb = QCheckBox("Sync All", self)
            self._sync_cb.setGeometry(
                ZONE_X_START + 4 * (ZONE_W + ZONE_SPACING), ZONE_Y + 5,
                100, 30
            )
            self._sync_cb.setStyleSheet(
                "QCheckBox { color: #aaa; font-size: 12px; }"
                "QCheckBox::indicator { width: 14px; height: 14px; }"
                "QCheckBox::indicator:unchecked { border: 1px solid #666; "
                "background: #333; }"
                "QCheckBox::indicator:checked { border: 1px solid #4CAF50; "
                "background: #4CAF50; }"
            )
            self._sync_cb.toggled.connect(self._on_sync_toggled)
            self._sync_cb.setVisible(False)

            # ============================================================
            # HR10-specific widgets (hidden by default)
            # ============================================================

            # -- Drive Metrics Panel (bottom left, below preview) --
            self._metrics_bg = QFrame(self)
            self._metrics_bg.setGeometry(METRICS_X, METRICS_Y, METRICS_W, METRICS_H)
            self._metrics_bg.setStyleSheet(
                "background-color: rgba(20, 20, 20, 200); "
                "border: 1px solid #444; border-radius: 6px;"
            )
            self._metrics_bg.setVisible(False)

            self._metric_labels: Dict[str, QLabel] = {}
            self._metric_name_labels: List[QLabel] = []
            metric_defs = [
                ("Drive Temp:", "disk_temp", "-- \u00b0C"),
                ("Total Activity:", "disk_activity", "-- %"),
                ("Read Rate:", "disk_read", "-- MB/s"),
                ("Write Rate:", "disk_write", "-- MB/s"),
            ]
            for i, (label_text, key, default) in enumerate(metric_defs):
                y = METRICS_Y + 12 + i * 28

                name_label = QLabel(label_text, self)
                name_label.setGeometry(METRICS_X + 15, y, 140, 24)
                name_label.setStyleSheet("color: #aaa; font-size: 13px;")
                name_label.setVisible(False)
                self._metric_name_labels.append(name_label)

                value_label = QLabel(default, self)
                value_label.setGeometry(METRICS_X + 160, y, 120, 24)
                value_label.setStyleSheet(
                    "color: white; font-size: 13px; font-weight: bold;"
                )
                value_label.setVisible(False)
                self._metric_labels[key] = value_label

            # NVMe device label
            self._nvme_label = QLabel("", self)
            self._nvme_label.setGeometry(METRICS_X + 200, METRICS_Y + 10, 280, 20)
            self._nvme_label.setStyleSheet("color: #666; font-size: 11px;")
            self._nvme_label.setVisible(False)

            # -- Display Selection section --
            self._ds_label = QLabel("Display selection", self)
            self._ds_label.setGeometry(DISPLAY_SEL_X, DISPLAY_SEL_Y - 30, 200, 20)
            self._ds_label.setStyleSheet("color: #aaa; font-size: 12px;")
            self._ds_label.setVisible(False)

            # Circulate checkbox
            self._circulate_cb = QCheckBox("Circulate", self)
            self._circulate_cb.setGeometry(CIRCULATE_X, CIRCULATE_Y, 100, 20)
            self._circulate_cb.setStyleSheet(
                "QCheckBox { color: #aaa; font-size: 12px; }"
                "QCheckBox::indicator { width: 14px; height: 14px; }"
                "QCheckBox::indicator:unchecked { border: 1px solid #666; background: #333; }"
                "QCheckBox::indicator:checked { border: 1px solid #4CAF50; background: #4CAF50; }"
            )
            self._circulate_cb.toggled.connect(self._on_circulate_toggled)
            self._circulate_cb.setVisible(False)

            # Circulate interval spinbox
            self._circulate_interval_label = QLabel("Interval:", self)
            self._circulate_interval_label.setGeometry(CIRCULATE_X + 110, CIRCULATE_Y, 55, 20)
            self._circulate_interval_label.setStyleSheet("color: #888; font-size: 11px;")
            self._circulate_interval_label.setVisible(False)

            self._circulate_interval = QSpinBox(self)
            self._circulate_interval.setGeometry(CIRCULATE_X + 170, CIRCULATE_Y - 2, 45, 22)
            self._circulate_interval.setRange(CIRCULATE_MIN_S, CIRCULATE_MAX_S)
            self._circulate_interval.setValue(CIRCULATE_DEFAULT_S)
            self._circulate_interval.setSuffix("s")
            self._circulate_interval.setStyleSheet(
                "color: white; background: #333; border: 1px solid #555; "
                "border-radius: 3px; font-size: 11px;"
            )
            self._circulate_interval.valueChanged.connect(
                self._on_circulate_interval_changed
            )
            self._circulate_interval.setVisible(False)

            # Display selection buttons
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
                btn.setVisible(False)
                self._display_buttons.append(btn)

            # ============================================================
            # LC2 clock widgets (style 9 — hidden by default)
            # ============================================================

            self._lc2_label = QLabel("Clock Format:", self)
            self._lc2_label.setGeometry(590, 620, 100, 20)
            self._lc2_label.setStyleSheet(_STYLE_MUTED_LABEL)
            self._lc2_label.setVisible(False)

            self._btn_24h = QPushButton("24H", self)
            self._btn_24h.setGeometry(700, 618, 55, 26)
            self._btn_24h.setCheckable(True)
            self._btn_24h.setChecked(True)
            self._btn_24h.setStyleSheet(self._mode_button_style(False))
            self._btn_24h.clicked.connect(lambda: self._set_clock_format(True))
            self._btn_24h.setVisible(False)

            self._btn_12h = QPushButton("12H", self)
            self._btn_12h.setGeometry(760, 618, 55, 26)
            self._btn_12h.setCheckable(True)
            self._btn_12h.setStyleSheet(self._mode_button_style(False))
            self._btn_12h.clicked.connect(lambda: self._set_clock_format(False))
            self._btn_12h.setVisible(False)

            self._week_label = QLabel("Week Start:", self)
            self._week_label.setGeometry(840, 620, 80, 20)
            self._week_label.setStyleSheet(_STYLE_MUTED_LABEL)
            self._week_label.setVisible(False)

            self._btn_sun = QPushButton("Sun", self)
            self._btn_sun.setGeometry(930, 618, 55, 26)
            self._btn_sun.setCheckable(True)
            self._btn_sun.setStyleSheet(self._mode_button_style(False))
            self._btn_sun.clicked.connect(lambda: self._set_week_start(True))
            self._btn_sun.setVisible(False)

            self._btn_mon = QPushButton("Mon", self)
            self._btn_mon.setGeometry(990, 618, 55, 26)
            self._btn_mon.setCheckable(True)
            self._btn_mon.setChecked(True)
            self._btn_mon.setStyleSheet(self._mode_button_style(False))
            self._btn_mon.clicked.connect(lambda: self._set_week_start(False))
            self._btn_mon.setVisible(False)

            # ============================================================
            # UCInfoImage sensor gauges (styles 1-3, 5-8, 11)
            # Matches Windows FormLED ucInfoImage1-6 layout.
            # ============================================================

            # 6 UCInfoImage widgets: CPU temp/clock/usage, GPU temp/clock/usage
            # Windows layout: col1 x=16, col2 x=276, rows y=659/707/755
            INFO_DEFS = [
                (1, "cpu_temp", 1),   # M1 CPU Temp (mode=1: temp/percent)
                (2, "cpu_clock", 2),  # M2 CPU Clock (mode=2: MHz)
                (3, "cpu_usage", 1),  # M3 CPU Usage (mode=1: percent)
                (4, "gpu_temp", 1),   # M4 GPU Temp
                (5, "gpu_clock", 2),  # M5 GPU Clock
                (6, "gpu_usage", 1),  # M6 GPU Usage
            ]
            self._info_images: Dict[str, UCInfoImage] = {}
            for idx, (img_num, key, mode) in enumerate(INFO_DEFS):
                col = idx // 3
                row = idx % 3
                x = 16 + col * 260
                y = 659 + row * 36
                widget = UCInfoImage(img_num, self)
                widget.setGeometry(x, y, 240, 30)
                widget.set_mode(mode)
                widget.setVisible(False)
                self._info_images[key] = widget

            # °C/°F toggle buttons (near sensor widgets)
            self._btn_celsius = QPushButton("\u00b0C", self)
            self._btn_celsius.setGeometry(16, 770, 40, 24)
            self._btn_celsius.setCheckable(True)
            self._btn_celsius.setChecked(True)
            self._btn_celsius.setStyleSheet(_STYLE_CHECKABLE_BTN)
            self._btn_celsius.clicked.connect(lambda: self._set_temp_unit_btn(False))
            self._btn_celsius.setVisible(False)

            self._btn_fahrenheit = QPushButton("\u00b0F", self)
            self._btn_fahrenheit.setGeometry(60, 770, 40, 24)
            self._btn_fahrenheit.setCheckable(True)
            self._btn_fahrenheit.setStyleSheet(_STYLE_CHECKABLE_BTN)
            self._btn_fahrenheit.clicked.connect(lambda: self._set_temp_unit_btn(True))
            self._btn_fahrenheit.setVisible(False)

            # ============================================================
            # LC1 memory info panel (style 4 — hidden by default)
            # ============================================================

            mem_defs = [
                ("Mem Temp:", "mem_temp", "-- \u00b0C"),
                ("Mem Clock:", "mem_clock", "-- MHz"),
                ("Mem Used:", "mem_used", "-- %"),
            ]
            self._mem_bg, self._mem_labels, self._mem_name_labels = (
                self._create_info_panel(
                    (30, 640, 500, 80), mem_defs,
                    lambda i, _: (45, 648 + i * 24, 150, 648 + i * 24),
                )
            )

            # ============================================================
            # LF11 disk info panel (style 10 — hidden by default)
            # ============================================================

            disk_defs = [
                ("Disk Temp:", "lf11_disk_temp", "-- \u00b0C"),
                ("Disk Usage:", "lf11_disk_usage", "-- %"),
                ("Read Rate:", "lf11_disk_read", "-- MB/s"),
                ("Write Rate:", "lf11_disk_write", "-- MB/s"),
            ]
            self._disk_bg, self._disk_labels, self._disk_name_labels = (
                self._create_info_panel(
                    (30, 640, 500, 80), disk_defs,
                    lambda i, _: (
                        45 + (i // 2) * 250, 652 + (i % 2) * 28,
                        45 + (i // 2) * 250 + 105, 652 + (i % 2) * 28,
                    ),
                )
            )

            # -- Status label --
            self._status = QLabel("", self)
            self._status.setGeometry(STATUS_X, STATUS_Y, STATUS_W, 24)
            self._status.setStyleSheet(_STYLE_MUTED_LABEL)

        # ================================================================
        # Public API
        # ================================================================

        def initialize(self, style_id: int, segment_count: int,
                       zone_count: int = 1, lang: str = 'en') -> None:
            """Configure for a specific LED device style.

            Args:
                style_id: LED device style (1-13).
                segment_count: Number of LED segments.
                zone_count: Number of independent zones.
                lang: Language code for localized background.
            """
            self._style_id = style_id
            self._zone_count = zone_count
            self._lang = lang
            self._is_hr10 = (style_id == 13)

            # Toggle preview widgets
            self._preview.setVisible(not self._is_hr10)
            self._seg_display.setVisible(self._is_hr10)

            if not self._is_hr10:
                self._preview.set_style(style_id, segment_count)

            # Load device preview background (standard preview)
            from ..led_device import LED_STYLES
            style = LED_STYLES.get(style_id)
            if style:
                if not self._is_hr10:
                    preview_pixmap = Assets.get(style.preview_image)
                    if preview_pixmap:
                        self._preview.set_background(QPixmap(preview_pixmap))

                # Set panel background
                bg_base = style.background_base
                bg_name = f"{bg_base}{lang}" if lang and lang != '' else bg_base
                bg_path = Assets.get(bg_name) or Assets.get(bg_base)
                if bg_path:
                    set_background_pixmap(self, bg_name)

                self._title.setText(f"RGB LED Control \u2014 {style.model_name}")

            # Layout shift for HR10 (color wheel takes space)
            self._apply_layout()

            # Show/hide HR10-specific controls
            self._set_hr10_visibility(self._is_hr10)

            # Show/hide zone buttons + sync checkbox
            for i, btn in enumerate(self._zone_buttons):
                btn.setVisible(i < zone_count and zone_count > 1)
            self._sync_cb.setVisible(zone_count > 1)
            self._selected_zone = 0
            self._sync_all = False
            self._sync_cb.setChecked(False)
            if zone_count > 1 and self._zone_buttons:
                self._zone_buttons[0].setChecked(True)

            # Show/hide device-specific info panels (mutually exclusive)
            is_lc2 = (style_id == 9)
            is_lc1 = (style_id == 4)
            is_lf11 = (style_id == 10)
            show_sensors = style_id in (1, 2, 3, 5, 6, 7, 8, 11)

            self._set_lc2_visibility(is_lc2)
            self._set_sensor_visibility(show_sensors)
            self._set_mem_visibility(is_lc1)
            self._set_disk_visibility(is_lf11)

            # Initialize HR10 display
            if self._is_hr10:
                r = self._rgb_sliders[0].value()
                g = self._rgb_sliders[1].value()
                b = self._rgb_sliders[2].value()
                self._seg_display.set_color(r, g, b)
                if self._display_buttons:
                    self._display_buttons[0].setChecked(True)

        def set_led_colors(self, colors: List[Tuple[int, int, int]]) -> None:
            """Update LED preview from controller tick."""
            if self._is_hr10:
                # Use the first LED color to tint the 7-segment display
                if colors:
                    r, g, b = colors[0]
                    self._seg_display.set_color(r, g, b)
            else:
                self._preview.set_colors(colors)

        def set_status(self, text: str) -> None:
            """Update status text."""
            self._status.setText(text)

        def set_language(self, lang: str) -> None:
            """Apply localized background."""
            self._lang = lang
            from ..led_device import LED_STYLES
            style = LED_STYLES.get(self._style_id)
            if style:
                bg_base = style.background_base
                bg_name = f"{bg_base}{lang}" if lang else bg_base
                bg_path = Assets.get(bg_name) or Assets.get(bg_base)
                if bg_path:
                    set_background_pixmap(self, bg_name)

        def set_temp_unit(self, unit_int: int) -> None:
            """Set temperature unit from app settings.

            Args:
                unit_int: 0 = °C, 1 = °F (matches app config).
            """
            self._temp_unit = "\u00b0F" if unit_int == 1 else "\u00b0C"
            # Sync °C/°F button visuals
            self._btn_celsius.setChecked(unit_int == 0)
            self._btn_fahrenheit.setChecked(unit_int == 1)
            if self._is_hr10:
                self._update_display_value()

        def update_drive_metrics(self, metrics: Dict[str, float]) -> None:
            """Update live drive metrics from system_info polling (HR10).

            Expected keys: disk_temp, disk_activity, disk_read, disk_write.
            """
            self._metrics = metrics

            if 'disk_temp' in metrics:
                temp = metrics['disk_temp']
                if self._temp_unit == "\u00b0F":
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

            self._update_display_value()

        def get_display_value(self) -> Tuple[str, str]:
            """Return (value_text, unit_text) for the current 7-segment display.

            Used by the controller to push the display text to the LED
            hardware without reaching into private widget attributes.
            """
            return self._seg_display.get_display_text()

        @property
        def is_hr10(self) -> bool:
            """Whether the panel is currently showing an HR10 device."""
            return self._is_hr10

        # ================================================================
        # Layout management
        # ================================================================

        def _apply_layout(self):
            """Reposition RGB/preset/brightness controls based on style."""
            if self._is_hr10:
                # Color wheel visible — shift controls right
                rgb_x = HR10_RGB_X
                slider_w = HR10_RGB_SLIDER_W
                preset_x = HR10_PRESET_X_START
                bright_x = HR10_BRIGHT_X
                bright_y = HR10_BRIGHT_Y
                bright_w = HR10_BRIGHT_W
                status_y = 760
            else:
                rgb_x = RGB_X
                slider_w = RGB_SLIDER_W
                preset_x = PRESET_X_START
                bright_x = BRIGHT_X
                bright_y = BRIGHT_Y
                bright_w = BRIGHT_W
                status_y = STATUS_Y

            rgb_y = HR10_RGB_Y_START if self._is_hr10 else RGB_Y_START

            for i in range(3):
                y = rgb_y + i * RGB_SPACING
                self._rgb_labels[i].setGeometry(rgb_x, y, RGB_LABEL_W, RGB_SLIDER_H)
                self._rgb_sliders[i].setGeometry(
                    rgb_x + RGB_LABEL_W + 5, y,
                    slider_w - RGB_LABEL_W - RGB_SPINBOX_W - 15,
                    RGB_SLIDER_H
                )
                self._rgb_spinboxes[i].setGeometry(
                    rgb_x + slider_w - RGB_SPINBOX_W, y,
                    RGB_SPINBOX_W, RGB_SLIDER_H
                )

            self._color_swatch.setGeometry(
                rgb_x + slider_w + 15, rgb_y, 40, RGB_SPACING * 3 - 10
            )

            for i, btn in enumerate(self._preset_buttons):
                x = preset_x + i * (PRESET_SIZE + PRESET_SPACING)
                preset_y = HR10_PRESET_Y if self._is_hr10 else PRESET_Y
                btn.setGeometry(x, preset_y, PRESET_SIZE, PRESET_SIZE)

            self._bright_label.setGeometry(bright_x, bright_y - 20, 100, 18)
            self._brightness_slider.setGeometry(bright_x, bright_y, bright_w, 24)
            self._brightness_label.setGeometry(
                bright_x + bright_w + 10, bright_y, 50, 24
            )

            if not self._is_hr10:
                self._onoff_btn.setGeometry(ONOFF_X, ONOFF_Y, ONOFF_W, ONOFF_H)
                self._onoff_btn.setVisible(True)
            else:
                self._onoff_btn.setVisible(False)

            self._status.setGeometry(STATUS_X, status_y, STATUS_W, 24)

        def _set_hr10_visibility(self, visible: bool):
            """Show/hide all HR10-specific widgets."""
            self._color_wheel.setVisible(visible)
            self._seg_display.setVisible(visible)
            self._metrics_bg.setVisible(visible)
            self._nvme_label.setVisible(visible)
            self._ds_label.setVisible(visible)
            self._circulate_cb.setVisible(visible)
            self._circulate_interval_label.setVisible(visible)
            self._circulate_interval.setVisible(visible)

            for lbl in self._metric_name_labels:
                lbl.setVisible(visible)
            for lbl in self._metric_labels.values():
                lbl.setVisible(visible)
            for btn in self._display_buttons:
                btn.setVisible(visible)

            if not visible:
                self._circulate_timer.stop()
                self._temp_legend.setVisible(False)
                self._temp_legend_labels.setVisible(False)

        # ================================================================
        # Internal handlers
        # ================================================================

        def _on_mode_clicked(self, index: int):
            """Handle mode button click."""
            self._current_mode = index
            for i, btn in enumerate(self._mode_buttons):
                btn.setChecked(i == index)
            if self._is_hr10:
                self._update_mode_visibility()
            self.mode_changed.emit(index)

        def _update_mode_visibility(self):
            """Toggle control visibility based on selected mode (HR10)."""
            mode = self._current_mode
            show_color = mode in (0, 1)
            show_temp_legend = mode in (4, 5)

            self._color_wheel.setVisible(show_color)
            self._color_swatch.setVisible(show_color)
            self._temp_legend.setVisible(show_temp_legend)
            self._temp_legend_labels.setVisible(show_temp_legend)

            for slider in self._rgb_sliders:
                slider.setVisible(show_color)
            for spinbox in self._rgb_spinboxes:
                spinbox.setVisible(show_color)
            for lbl in self._rgb_labels:
                lbl.setVisible(show_color)
            for btn in self._preset_buttons:
                btn.setVisible(show_color)

        def _on_hue_changed(self, hue: int):
            """Handle color wheel hue selection -> update RGB sliders."""
            color = QColor.fromHsv(hue, 255, 255)
            self._set_color(color.red(), color.green(), color.blue())

        def _on_rgb_changed(self):
            """Handle RGB slider change."""
            r = self._rgb_sliders[0].value()
            g = self._rgb_sliders[1].value()
            b = self._rgb_sliders[2].value()
            # Sync spinboxes without triggering their signals
            for i, val in enumerate([r, g, b]):
                self._rgb_spinboxes[i].blockSignals(True)
                self._rgb_spinboxes[i].setValue(val)
                self._rgb_spinboxes[i].blockSignals(False)
            self._update_color_swatch()
            if self._is_hr10:
                self._sync_wheel_from_rgb(r, g, b)
                self._seg_display.set_color(r, g, b)
            self.color_changed.emit(r, g, b)

        def _on_spinbox_changed(self, index: int, value: int):
            """Handle RGB spinbox change."""
            self._rgb_sliders[index].blockSignals(True)
            self._rgb_sliders[index].setValue(value)
            self._rgb_sliders[index].blockSignals(False)
            r = self._rgb_spinboxes[0].value()
            g = self._rgb_spinboxes[1].value()
            b = self._rgb_spinboxes[2].value()
            self._update_color_swatch()
            if self._is_hr10:
                self._sync_wheel_from_rgb(r, g, b)
                self._seg_display.set_color(r, g, b)
            self.color_changed.emit(r, g, b)

        def _set_color(self, r: int, g: int, b: int):
            """Set color from preset button or color wheel."""
            for i, val in enumerate([r, g, b]):
                self._rgb_sliders[i].blockSignals(True)
                self._rgb_sliders[i].setValue(val)
                self._rgb_sliders[i].blockSignals(False)
                self._rgb_spinboxes[i].blockSignals(True)
                self._rgb_spinboxes[i].setValue(val)
                self._rgb_spinboxes[i].blockSignals(False)
            self._update_color_swatch()
            if self._is_hr10:
                self._sync_wheel_from_rgb(r, g, b)
                self._seg_display.set_color(r, g, b)
            self.color_changed.emit(r, g, b)

        def _sync_wheel_from_rgb(self, r: int, g: int, b: int):
            """Update wheel indicator from RGB values without triggering loop."""
            hue = QColor(r, g, b).hsvHue()
            if hue < 0:
                hue = 0  # achromatic
            self._color_wheel.blockSignals(True)
            self._color_wheel.set_hue(hue)
            self._color_wheel.blockSignals(False)

        def _on_toggle_clicked(self):
            """Handle on/off toggle."""
            on = self._onoff_btn.isChecked()
            self._onoff_btn.setText("ON" if on else "OFF")
            self.global_toggled.emit(on)

        def _update_color_swatch(self):
            """Update the color preview swatch."""
            r = self._rgb_sliders[0].value()
            g = self._rgb_sliders[1].value()
            b = self._rgb_sliders[2].value()
            self._color_swatch.setStyleSheet(
                f"background-color: rgb({r},{g},{b}); border: 2px solid #555; "
                f"border-radius: 5px;"
            )

        # -- HR10 display selection --

        def _on_display_selected(self, metric_key: str, button_index: int):
            """Handle display selection button click (HR10)."""
            self._current_metric = metric_key
            for i, btn in enumerate(self._display_buttons):
                btn.setChecked(i == button_index)
            self._update_display_value()
            self.display_metric_changed.emit(metric_key)

        def _update_display_value(self):
            """Update the 7-segment display with the current metric value (HR10)."""
            if not self._is_hr10:
                return

            metric = self._current_metric
            if metric == "temp":
                val = self._metrics.get('disk_temp')
                if val is not None:
                    if self._temp_unit == "\u00b0F":
                        val = val * 9 / 5 + 32
                    self._seg_display.set_value(f"{val:.0f}", self._temp_unit)
                else:
                    self._seg_display.set_value("---", self._temp_unit)
            elif metric == "activity":
                val = self._metrics.get('disk_activity')
                if val is not None:
                    self._seg_display.set_value(f"{val:.0f}", "%")
                else:
                    self._seg_display.set_value("---", "%")
            elif metric == "read":
                val = self._metrics.get('disk_read')
                if val is not None:
                    self._seg_display.set_value(f"{val:.0f}", "MB/s")
                else:
                    self._seg_display.set_value("---", "MB/s")
            elif metric == "write":
                val = self._metrics.get('disk_write')
                if val is not None:
                    self._seg_display.set_value(f"{val:.0f}", "MB/s")
                else:
                    self._seg_display.set_value("---", "MB/s")

        # -- HR10 circulate --

        def _on_circulate_toggled(self, enabled: bool):
            if enabled:
                interval_ms = self._circulate_interval.value() * 1000
                self._circulate_timer.start(interval_ms)
                self._circulate_index = 0
            else:
                self._circulate_timer.stop()
            self.circulate_toggled.emit(enabled)

        def _on_circulate_interval_changed(self, value: int):
            if self._circulate_timer.isActive():
                self._circulate_timer.start(value * 1000)

        def _on_circulate_tick(self):
            self._circulate_index = (self._circulate_index + 1) % len(DISPLAY_METRICS)
            _label, key = DISPLAY_METRICS[self._circulate_index]
            self._on_display_selected(key, self._circulate_index)

        # -- Zone selection --

        def _on_zone_clicked(self, zone_index: int):
            """Handle zone button click."""
            if self._sync_all:
                return
            self._selected_zone = zone_index
            for i, btn in enumerate(self._zone_buttons):
                btn.setChecked(i == zone_index)
            self.zone_selected.emit(zone_index)

        def _on_sync_toggled(self, sync: bool):
            """Handle sync all checkbox toggle."""
            self._sync_all = sync
            if sync:
                for i in range(self._zone_count):
                    if i < len(self._zone_buttons):
                        self._zone_buttons[i].setChecked(True)
            else:
                for i, btn in enumerate(self._zone_buttons):
                    btn.setChecked(i == self._selected_zone)
            self.sync_all_changed.emit(sync)

        def load_zone_state(self, zone_index: int, mode: int,
                            color: tuple, brightness: int):
            """Load a zone's state into the UI controls."""
            for slider in self._rgb_sliders:
                slider.blockSignals(True)
            for spinbox in self._rgb_spinboxes:
                spinbox.blockSignals(True)
            self._brightness_slider.blockSignals(True)

            r, g, b = color
            self._rgb_sliders[0].setValue(r)
            self._rgb_sliders[1].setValue(g)
            self._rgb_sliders[2].setValue(b)
            for i, val in enumerate([r, g, b]):
                self._rgb_spinboxes[i].setValue(val)

            self._brightness_slider.setValue(brightness)
            self._brightness_label.setText(f"{brightness}%")

            for i, btn in enumerate(self._mode_buttons):
                btn.setChecked(i == mode)
            self._current_mode = mode

            for slider in self._rgb_sliders:
                slider.blockSignals(False)
            for spinbox in self._rgb_spinboxes:
                spinbox.blockSignals(False)
            self._brightness_slider.blockSignals(False)

            self._update_color_swatch()

        @property
        def selected_zone(self) -> int:
            return self._selected_zone

        @property
        def sync_all(self) -> bool:
            return self._sync_all

        # -- LC2 clock handlers --

        def _set_clock_format(self, is_24h: bool):
            self._is_timer_24h = is_24h
            self._btn_24h.setChecked(is_24h)
            self._btn_12h.setChecked(not is_24h)
            self.clock_format_changed.emit(is_24h)

        def _set_week_start(self, is_sunday: bool):
            self._is_week_sunday = is_sunday
            self._btn_sun.setChecked(is_sunday)
            self._btn_mon.setChecked(not is_sunday)
            self.week_start_changed.emit(is_sunday)

        def _set_temp_unit_btn(self, is_fahrenheit: bool):
            """Handle °C/°F toggle button click."""
            self._btn_celsius.setChecked(not is_fahrenheit)
            self._btn_fahrenheit.setChecked(is_fahrenheit)
            self._temp_unit = "\u00b0F" if is_fahrenheit else "\u00b0C"
            self.temp_unit_changed.emit("F" if is_fahrenheit else "C")

        # -- Info panel factory --

        def _create_info_panel(self, bg_rect, defs, layout_fn):
            """Create a labeled info panel (used for mem/disk panels).

            Args:
                bg_rect: (x, y, w, h) for background QFrame.
                defs: List of (label_text, key, default_value) tuples.
                layout_fn: (index, total) -> (name_x, name_y, val_x, val_y)

            Returns:
                (bg_frame, value_labels_dict, name_labels_list)
            """
            bg = QFrame(self)
            bg.setGeometry(*bg_rect)
            bg.setStyleSheet(_STYLE_INFO_BG)
            bg.setVisible(False)

            val_labels: Dict[str, QLabel] = {}
            name_labels: List[QLabel] = []
            for i, (label_text, key, default) in enumerate(defs):
                name_x, name_y, val_x, val_y = layout_fn(i, len(defs))

                name_lbl = QLabel(label_text, self)
                name_lbl.setGeometry(name_x, name_y, 100, 20)
                name_lbl.setStyleSheet(_STYLE_INFO_NAME)
                name_lbl.setVisible(False)
                name_labels.append(name_lbl)

                val_lbl = QLabel(default, self)
                val_lbl.setGeometry(val_x, val_y, 120, 20)
                val_lbl.setStyleSheet(_STYLE_INFO_VALUE)
                val_lbl.setVisible(False)
                val_labels[key] = val_lbl

            return bg, val_labels, name_labels

        # -- Visibility helpers --

        def _set_lc2_visibility(self, visible: bool):
            """Show/hide LC2 clock widgets."""
            self._lc2_label.setVisible(visible)
            self._btn_24h.setVisible(visible)
            self._btn_12h.setVisible(visible)
            self._week_label.setVisible(visible)
            self._btn_sun.setVisible(visible)
            self._btn_mon.setVisible(visible)

        def _set_sensor_visibility(self, visible: bool):
            """Show/hide UCInfoImage sensor gauges and °C/°F buttons."""
            for widget in self._info_images.values():
                widget.setVisible(visible)
            self._btn_celsius.setVisible(visible)
            self._btn_fahrenheit.setVisible(visible)

        def _set_info_panel_visibility(self, bg, val_labels, name_labels,
                                       visible: bool):
            """Show/hide a labeled info panel created by _create_info_panel."""
            bg.setVisible(visible)
            for lbl in name_labels:
                lbl.setVisible(visible)
            for lbl in val_labels.values():
                lbl.setVisible(visible)

        def _set_mem_visibility(self, visible: bool):
            """Show/hide LC1 memory info labels."""
            self._set_info_panel_visibility(
                self._mem_bg, self._mem_labels, self._mem_name_labels, visible)

        def _set_disk_visibility(self, visible: bool):
            """Show/hide LF11 disk info labels."""
            self._set_info_panel_visibility(
                self._disk_bg, self._disk_labels, self._disk_name_labels,
                visible)

        # -- Sensor/memory/disk update methods --

        def update_sensor_metrics(self, metrics: Dict[str, float]):
            """Update UCInfoImage sensor gauges (for non-HR10 styles)."""
            unit = self._temp_unit
            if 'cpu_temp' in metrics:
                t = metrics['cpu_temp']
                if unit == "\u00b0F":
                    t = t * 9 / 5 + 32
                self._info_images['cpu_temp'].set_value(t, f"{t:.0f}", unit)
            if 'cpu_freq' in metrics:
                f = metrics['cpu_freq']
                self._info_images['cpu_clock'].set_value(f, f"{f:.0f}", "MHz")
            if 'cpu_percent' in metrics:
                v = metrics['cpu_percent']
                self._info_images['cpu_usage'].set_value(v, f"{v:.0f}", "%")
            if 'gpu_temp' in metrics:
                t = metrics['gpu_temp']
                if unit == "\u00b0F":
                    t = t * 9 / 5 + 32
                self._info_images['gpu_temp'].set_value(t, f"{t:.0f}", unit)
            if 'gpu_clock' in metrics:
                f = metrics['gpu_clock']
                self._info_images['gpu_clock'].set_value(f, f"{f:.0f}", "MHz")
            if 'gpu_usage' in metrics:
                v = metrics['gpu_usage']
                self._info_images['gpu_usage'].set_value(v, f"{v:.0f}", "%")

        def update_memory_metrics(self, metrics: Dict[str, float]):
            """Update memory info labels (LC1 style 4)."""
            if 'mem_temp' in metrics:
                self._mem_labels['mem_temp'].setText(
                    f"{metrics['mem_temp']:.0f} \u00b0C")
            if 'mem_clock' in metrics:
                self._mem_labels['mem_clock'].setText(
                    f"{metrics['mem_clock']:.0f} MHz")
            if 'mem_percent' in metrics:
                self._mem_labels['mem_used'].setText(
                    f"{metrics['mem_percent']:.1f}%")

        def update_lf11_disk_metrics(self, metrics: Dict[str, float]):
            """Update disk info labels (LF11 style 10)."""
            if 'disk_temp' in metrics:
                self._disk_labels['lf11_disk_temp'].setText(
                    f"{metrics['disk_temp']:.0f} \u00b0C")
            if 'disk_activity' in metrics:
                self._disk_labels['lf11_disk_usage'].setText(
                    f"{metrics['disk_activity']:.0f}%")
            if 'disk_read' in metrics:
                self._disk_labels['lf11_disk_read'].setText(
                    f"{metrics['disk_read']:.1f} MB/s")
            if 'disk_write' in metrics:
                self._disk_labels['lf11_disk_write'].setText(
                    f"{metrics['disk_write']:.1f} MB/s")

        # ================================================================
        # Styles
        # ================================================================

        @staticmethod
        def _mode_button_style(active: bool) -> str:
            if active:
                return (
                    "QPushButton { background: #2196F3; color: white; "
                    "border: 2px solid #42A5F5; border-radius: 6px; "
                    "font-weight: bold; font-size: 11px; }"
                )
            return (
                "QPushButton { background: #444; color: white; "
                "border: 1px solid #666; border-radius: 6px; "
                "font-size: 11px; }"
                "QPushButton:checked { background: #2196F3; "
                "border: 2px solid #42A5F5; font-weight: bold; }"
                "QPushButton:hover { background: #555; }"
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
