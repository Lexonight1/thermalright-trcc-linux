#!/usr/bin/env python3
"""
LED control panel (FormLED equivalent).

Full LED control UI matching Windows FormLED layout:
- Left side: UCScreenLED device preview (536x536)
- Right side: Mode buttons, RGB controls, presets, brightness
- Bottom: Zone selection buttons (for multi-zone devices)

Layout coordinates from FormLED.cs InitializeComponent / FormLED.resx.
"""

from typing import List, Tuple

try:
    from PyQt6.QtCore import Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QPalette, QPixmap
    from PyQt6.QtWidgets import (
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
    from .assets import Assets
    from .base import set_background_pixmap
    from .uc_screen_led import UCScreenLED


# =========================================================================
# Layout constants (from FormLED.cs / FormLED.resx)
# =========================================================================

# Panel occupies full form area: (180, 0, 1274, 800) relative to main window
PANEL_WIDTH = 1274
PANEL_HEIGHT = 800

# UCScreenLED preview
PREVIEW_X, PREVIEW_Y = 16, 80
PREVIEW_W, PREVIEW_H = 536, 536

# Mode buttons (6 buttons, arranged horizontally)
MODE_Y = 227
MODE_X_START = 590
MODE_W, MODE_H = 93, 62
MODE_SPACING = 10

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


if PYQT6_AVAILABLE:

    class UCLedControl(QWidget):
        """LED control panel matching Windows FormLED.

        Contains device preview, mode buttons, color picker, brightness,
        and zone selection. Only shown when an LED device is selected.
        """

        # Signals for controller binding
        mode_changed = pyqtSignal(int)              # LEDMode value
        color_changed = pyqtSignal(int, int, int)   # R, G, B
        brightness_changed = pyqtSignal(int)         # 0-100
        global_toggled = pyqtSignal(bool)            # on/off
        segment_clicked = pyqtSignal(int)            # segment index

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setFixedSize(PANEL_WIDTH, PANEL_HEIGHT)

            self._current_mode = 0
            self._zone_count = 1

            self._setup_ui()

        def _setup_ui(self):
            """Create all UI elements."""
            # Dark background
            self.setAutoFillBackground(True)
            palette = self.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
            self.setPalette(palette)

            # -- LED Preview --
            self._preview = UCScreenLED(self)
            self._preview.move(PREVIEW_X, PREVIEW_Y)
            self._preview.segment_clicked.connect(self.segment_clicked.emit)

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
                normal_name = f"D2灯光{i + 1}"
                active_name = f"D2灯光{i + 1}a"
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

            # -- RGB Controls --
            self._rgb_sliders: List[QSlider] = []
            self._rgb_spinboxes: List[QSpinBox] = []
            rgb_labels = ["R", "G", "B"]
            rgb_colors = ["#ff4444", "#44ff44", "#4444ff"]

            for i, (lbl, color) in enumerate(zip(rgb_labels, rgb_colors)):
                y = RGB_Y_START + i * RGB_SPACING

                # Label
                label = QLabel(lbl, self)
                label.setGeometry(RGB_X, y, RGB_LABEL_W, RGB_SLIDER_H)
                label.setStyleSheet(f"color: {color}; font-size: 14px; font-weight: bold;")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)

                # Slider
                slider = QSlider(Qt.Orientation.Horizontal, self)
                slider.setGeometry(
                    RGB_X + RGB_LABEL_W + 5, y, RGB_SLIDER_W - RGB_LABEL_W - RGB_SPINBOX_W - 15, RGB_SLIDER_H
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
                    "color: white; background: #333; border: 1px solid #555; "
                    "border-radius: 3px;"
                )
                spinbox.valueChanged.connect(
                    lambda val, idx=i: self._on_spinbox_changed(idx, val)
                )
                self._rgb_spinboxes.append(spinbox)

            # -- Color preview swatch --
            self._color_swatch = QFrame(self)
            self._color_swatch.setGeometry(RGB_X + RGB_SLIDER_W + 15, RGB_Y_START, 40, RGB_SPACING * 3 - 10)
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
                    "QPushButton { background: #444; color: white; border: 1px solid #666; "
                    "border-radius: 4px; }"
                    "QPushButton:checked { background: #2196F3; border: 1px solid #42A5F5; }"
                )
                btn.setVisible(False)
                self._zone_buttons.append(btn)

            # -- Status label --
            self._status = QLabel("", self)
            self._status.setGeometry(STATUS_X, STATUS_Y, STATUS_W, 24)
            self._status.setStyleSheet("color: #aaa; font-size: 12px;")

        def initialize(self, style_id: int, segment_count: int,
                       zone_count: int = 1, lang: str = 'en') -> None:
            """Configure for a specific LED device style.

            Args:
                style_id: LED device style (1-12).
                segment_count: Number of LED segments.
                zone_count: Number of independent zones.
                lang: Language code for localized background.
            """
            self._zone_count = zone_count
            self._preview.set_style(style_id, segment_count)

            # Load device preview background
            from ..led_device import LED_STYLES
            style = LED_STYLES.get(style_id)
            if style:
                preview_pixmap = Assets.get(style.preview_image)
                if preview_pixmap:
                    self._preview.set_background(QPixmap(preview_pixmap))

                # Set panel background
                bg_base = style.background_base
                bg_name = f"{bg_base}{lang}" if lang and lang != '' else bg_base
                bg_path = Assets.get(bg_name) or Assets.get(bg_base)
                if bg_path:
                    set_background_pixmap(self, bg_name)

                self._title.setText(f"RGB LED Control — {style.model_name}")

            # Show/hide zone buttons
            for i, btn in enumerate(self._zone_buttons):
                btn.setVisible(i < zone_count and zone_count > 1)
            if zone_count > 1 and self._zone_buttons:
                self._zone_buttons[0].setChecked(True)

        def set_led_colors(self, colors: List[Tuple[int, int, int]]) -> None:
            """Update LED preview from controller tick."""
            self._preview.set_colors(colors)

        def set_status(self, text: str) -> None:
            """Update status text."""
            self._status.setText(text)

        def set_language(self, lang: str) -> None:
            """Apply localized background."""
            from ..led_device import LED_STYLES
            style = LED_STYLES.get(self._preview._style_id)
            if style:
                bg_base = style.background_base
                bg_name = f"{bg_base}{lang}" if lang else bg_base
                bg_path = Assets.get(bg_name) or Assets.get(bg_base)
                if bg_path:
                    set_background_pixmap(self, bg_name)

        # -- Internal handlers --

        def _on_mode_clicked(self, index: int):
            """Handle mode button click."""
            self._current_mode = index
            for i, btn in enumerate(self._mode_buttons):
                btn.setChecked(i == index)
            self.mode_changed.emit(index)

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
            self.color_changed.emit(r, g, b)

        def _set_color(self, r: int, g: int, b: int):
            """Set color from preset button."""
            for i, val in enumerate([r, g, b]):
                self._rgb_sliders[i].blockSignals(True)
                self._rgb_sliders[i].setValue(val)
                self._rgb_sliders[i].blockSignals(False)
                self._rgb_spinboxes[i].blockSignals(True)
                self._rgb_spinboxes[i].setValue(val)
                self._rgb_spinboxes[i].blockSignals(False)
            self._update_color_swatch()
            self.color_changed.emit(r, g, b)

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

        @staticmethod
        def _mode_button_style(active: bool) -> str:
            """Get stylesheet for mode buttons."""
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
