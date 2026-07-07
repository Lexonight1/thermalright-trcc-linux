#!/usr/bin/env python3
"""
LED control panel (FormLED equivalent).

Full LED control UI matching Windows FormLED layout:
- Left side: Device preview (UCScreenLED segment rectangles)
- Right side: Mode buttons, color wheel, RGB controls, presets, brightness
- Bottom: Zone selection buttons (for multi-zone devices)

Layout coordinates from FormLED.cs InitializeComponent / FormLED.resx.
All LED devices (styles 1-12) use this single panel — matching Windows
FormLED.cs which is one form for all LED device types.
"""

import logging

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIntValidator, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QSpinBox,
    QWidget,
)

from ...core.i18n import tr
from ...core.led_models import (
    LED_MODE_LABELS,
    LED_PRESET_ASSETS,
    LED_SELECT_ALL_STYLES,
    PRESET_COLORS,
)
from ...core.models import HardwareMetrics
from ..presentation.led_display import LedSelector, led_display_for
from ..presentation.led_metrics_format import (
    clock_fields,
    format_disk_labels,
    format_memory_labels,
    format_sensor_gauges,
)
from ..presentation.led_panel import led_panel_for
from ..presentation.led_zone_model import LedZoneModel
from .assets import Assets
from .base import set_background_pixmap
from .uc_color_wheel import UCColorWheel
from .uc_screen_led import UCScreenLED

log = logging.getLogger(__name__)

# =========================================================================
# Layout constants (from FormLED.cs InitializeComponent, ClientSize 1274x800)
#
# C# uses FlatStyle.Flat + BorderSize=0 + BorderColor=transparent on ALL
# buttons.  The background PNG IS the UI; Qt widgets are transparent
# hitboxes placed at exact pixel coordinates on top.
# =========================================================================

PANEL_WIDTH = 1274
PANEL_HEIGHT = 800

# UCScreenLED preview — C#: ucScreenLED1 at (36, 128) 460x460
PREVIEW_X, PREVIEW_Y = 36, 128
PREVIEW_W, PREVIEW_H = 460, 460

# Mode buttons — C#: buttonDSCL..FZLD at (590..1105, 227) each 93x62
MODE_Y = 227
MODE_X_START = 590
MODE_W, MODE_H = 93, 62
MODE_SPACING = 10

# Color wheel — C#: ucColorA1 at (617, 335) 216x216, shown for ALL styles
# Background image: color_wheel_knob (216x216 rainbow ring)
WHEEL_X, WHEEL_Y = 617, 335
WHEEL_W, WHEEL_H = 216, 216

# RGB controls — C#: textBoxR/G/B at (926, 333/363/393) 47x19
#                     ucScrollAR/G/B at (976, 333/363/393) 190x20
# The background image provides R/G/B letter labels; no Qt labels needed.
RGB_SPINBOX_X = 926
RGB_Y_START = 333
RGB_SLIDER_X = 976
RGB_SLIDER_W = 190
RGB_SLIDER_H = 20
RGB_SPACING = 30
RGB_SPINBOX_W = 47

# Preset color buttons — C#: buttonC1-C8, exact X positions from InitializeComponent
PRESET_X_POSITIONS = [901, 935, 970, 1004, 1039, 1073, 1108, 1142]
PRESET_Y = 444
PRESET_SIZE = 24
# Preset asset names from core/models.py
PRESET_ASSETS = LED_PRESET_ASSETS

# Brightness slider — C#: ucScrollA at (976, 537) 190x20
BRIGHT_X = 976
BRIGHT_Y = 537
BRIGHT_W = 190

# Coalesce a brightness drag into a single device write: `valueChanged`
# fires per drag-tick, and each emit dispatches a SetLedBrightness down the
# wire where it interleaves with the segment-number refresh and corrupts the
# display (#202).  Send only after the slider has been still this long.
_BRIGHTNESS_DEBOUNCE_MS = 150

# °C/°F buttons — C#: buttonC at (699, 144) 14x14, buttonF at (759, 144)
TEMP_BTN_Y = 144
TEMP_BTN_C_X = 699
TEMP_BTN_F_X = 759
TEMP_BTN_SIZE = 14

# Temperature color legend (modes 5-6: temp/load reactive)
TEMP_LEGEND_X = 590
TEMP_LEGEND_Y = 480
TEMP_LEGEND_W = 560
TEMP_LEGEND_H = 18

# Zone buttons — C#: button1-4 at (590/748/902/1058, 707) 140x50
# FormLEDInit repositions button5/6 and buttonN1-4 to button1.Top (=707)
# for ALL styles, so every variant ends up at Y=707.
ZONE_Y = 707
ZONE_X_POSITIONS = [590, 748, 902, 1058]
ZONE_W, ZONE_H = 140, 50

# Status label
STATUS_X = 590
STATUS_Y = 770
STATUS_W = 600

# Mode button labels from core/models.py
MODE_LABELS = LED_MODE_LABELS

# Shared stylesheet fragments (used by multiple widgets in this module)
_STYLE_MUTED_LABEL = "color: #aaa; font-size: 12px;"

# Flat transparent button — matches C# FlatStyle.Flat + BorderSize=0.
# Background PNG has the button visual; Qt widget is an invisible hitbox.
_STYLE_FLAT_BTN = (
    "QPushButton { background: transparent; border: none; color: transparent; }"
    "QPushButton:hover { background: rgba(255, 255, 255, 20); }"
    "QPushButton:pressed { background: rgba(255, 255, 255, 40); }"
)
_STYLE_FLAT_CHECKABLE_BTN = (
    "QPushButton { background: transparent; border: none; color: transparent; }"
    "QPushButton:checked { background: rgba(33, 150, 243, 60); }"
    "QPushButton:hover { background: rgba(255, 255, 255, 20); }"
)
_STYLE_CHECKABLE_BTN = (
    "QPushButton { background: transparent; color: #aaa; border: none; "
    "font-size: 11px; }"
    "QPushButton:checked { background: rgba(33, 150, 243, 60); color: white; }"
    "QPushButton:hover { background: rgba(255, 255, 255, 20); }"
)

# LED styles whose display-button art was cleared, so the buttons get tr() text
# overlays (full i18n): AX120(1)+PA120(2) [led_zone_mode_1-4], AK120(3)+LF8(5)+
# LF12(6)+LF15(11) [led_zone_mode_5-6], LC1(4)+LF11(10) [panel bg].  CZ1(8) keeps
# its baked bg labels (not cleared); LF10(7) is colour-zones (no metric text).
# Magic Qube(13) has no baked-label art (generic segment bg) → overlay the
# tr() page labels on the plain button frame, like the LC1/LF11 group.
_OVERLAY_BUTTON_STYLES = frozenset({1, 2, 3, 4, 5, 6, 10, 11, 13})

# Button labels for styles whose model carries none (PA120 is ZONE → page_labels
# must stay () per the model invariant, but its zones *are* the 4 metrics).
_ZONE_BUTTON_LABELS: dict[int, tuple[str, ...]] = {
    2: ("CPU (°C/°F)", "CPU (%)", "GPU (°C/°F)", "GPU (%)"),   # PA120
}


def _checkbox_image_style() -> str:
    """Build stylesheet for checkbox radio-style buttons (°C/°F, 24H/12H, etc.)."""
    normal = Assets.get(Assets.CHECKBOX_OFF)
    active = Assets.get(Assets.CHECKBOX_ON)
    if normal and active:
        return (
            f"QPushButton {{ border: none; "
            f"background-image: url({normal}); "
            f"background-repeat: no-repeat; }}"
            f"QPushButton:checked {{ "
            f"background-image: url({active}); }}"
        )
    return _STYLE_FLAT_CHECKABLE_BTN


class UCInfoImage(QWidget):
    """Sensor gauge widget matching Windows UCInfoImage.cs.

    Shows a background label image (led_meter_bg_{n}.png), a progress bar fill
    (led_meter_bar_{n}.png) drawn at variable width, and a numeric value overlay.
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
        self._bg_pixmap = Assets.load_pixmap(f"led_meter_bg_{index}.png")
        self._bar_pixmap = Assets.load_pixmap(f"led_meter_bar_{index}.png")

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
    including all LED device styles (1-12).
    """

    # Signals for controller binding
    mode_changed = Signal(int)              # LEDMode value
    color_changed = Signal(int, int, int)   # R, G, B
    brightness_changed = Signal(int)         # 0-100
    global_toggled = Signal(bool)            # on/off (from color wheel center button)
    segment_clicked = Signal(int)            # segment index
    # Zone signals
    zone_selected = Signal(int)              # zone index (0-based)
    zone_toggled = Signal(int, bool)         # zone index, on/off
    carousel_changed = Signal(bool)          # carousel mode toggled
    carousel_zones_changed = Signal(object)  # full enabled mask (list[bool])
    carousel_interval_changed = Signal(int)  # interval in seconds
    # LC2 clock signals (style 9)
    clock_format_changed = Signal(bool)      # True = 24h
    week_start_changed = Signal(bool)        # True = Sunday
    # Temperature unit signal
    temp_unit_changed = Signal(str)          # "C" or "F"
    # Disk selector (LF11 style 10)
    disk_index_changed = Signal(int)         # disk index (0-based)
    # DDR multiplier (LC1 style 4)
    memory_ratio_changed = Signal(int)       # 1, 2, or 4
    # Test mode
    test_mode_changed = Signal(bool)         # test mode toggled
    # Metric source for temp-/load-linked LED color modes ("cpu" / "gpu")
    temp_source_changed = Signal(str)
    load_source_changed = Signal(str)

    # Header drag area — C# FormLED uses delegate cmds 241/242/243 for
    # MouseDown/Move/Up so the user can drag the window from the header.
    # The tan header area extends from the top of the panel down to just
    # above the UCScreenLED preview (Y=128) and mode buttons (Y=227).
    _DRAG_MAX_Y = 200

    def __init__(self, parent=None, language: str = "en"):
        super().__init__(parent)
        self.setFixedSize(PANEL_WIDTH, PANEL_HEIGHT)

        # Current app language — injected by the window (the composition root)
        # so the panel never reaches into _boot for global settings.  Kept
        # current via set_language() on every language change.
        self._language = language

        self._current_mode = 0
        self._zone_count = 1
        self._style_id = 0

        # Zone/carousel interaction state lives in a toolkit-free model;
        # this panel mirrors model.enabled onto its buttons.
        self._zones = LedZoneModel()

        # Window drag state (C# delegate cmds 241/242/243)
        self._drag_pos = None

        # Temp unit for display
        self._temp_unit = "\u00b0C"

        # LC2 clock state (style 9)
        self._is_timer_24h = True
        self._is_week_sunday = False

        self._get_memory_info = None
        self._get_disk_info = None

        self._setup_ui()

    def set_hardware_fns(self, get_memory_info, get_disk_info) -> None:
        """Inject platform hardware callables (DI from ControllerBuilder)."""
        self._get_memory_info = get_memory_info
        self._get_disk_info = get_disk_info

    def _setup_ui(self):
        """Create all UI elements."""
        # Dark background
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
        self.setPalette(palette)

        # Shared checkbox style (used by °C/°F, 24H/12H, Sun/Mon buttons)
        _cb_style = _checkbox_image_style()

        # -- LED Preview (standard: circles) --
        self._preview = UCScreenLED(self)
        self._preview.move(PREVIEW_X, PREVIEW_Y)
        self._preview.segment_clicked.connect(self.segment_clicked.emit)

        # -- Title label (hidden when background is loaded — bg has device name) --
        self._title = QLabel("RGB LED Control", self)
        self._title.setGeometry(PREVIEW_X, 20, PREVIEW_W, 40)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet(
            "color: white; font-size: 20px; font-weight: bold;"
        )
        self._title.setVisible(False)

        # -- Dev fingerprint (top-left): the device's handshake as a copy-paste
        # -- ready mock_gui arg string, so a reported device can be reproduced
        # -- without hand-typing PM/SUB.  Selectable text; harmless on a real run.
        self._fingerprint_label = QLabel("", self)
        self._fingerprint_label.setGeometry(8, 2, 460, 18)
        self._fingerprint_label.setStyleSheet(
            "color: #7A7A7A; font-family: monospace; font-size: 11px;"
            " background: transparent;"
        )
        self._fingerprint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )

        # -- Mode buttons (text rendered via i18n, not baked into PNG) --
        lang = self._language
        self._mode_buttons: list[QPushButton] = []
        for i, label_key in enumerate(MODE_LABELS):
            label = tr(label_key, lang)
            btn = QPushButton(label, self)
            x = MODE_X_START + i * (MODE_W + MODE_SPACING)
            btn.setGeometry(x, MODE_Y, MODE_W, MODE_H)
            btn.setCheckable(True)
            btn.setStyleSheet(self._mode_button_style(False))
            btn.clicked.connect(lambda checked, idx=i: self._on_mode_clicked(idx))
            btn.setToolTip(label)
            self._mode_buttons.append(btn)

        # Set initial mode
        if self._mode_buttons:
            self._mode_buttons[0].setChecked(True)

        # -- Color Wheel (C# ucColorA1 — shown for ALL styles) --
        # Includes center on/off button matching C# UCColorA.buttonDSHX
        self._color_wheel = UCColorWheel(self)
        self._color_wheel.setGeometry(WHEEL_X, WHEEL_Y, WHEEL_W, WHEEL_H)
        self._color_wheel.hue_changed.connect(self._on_hue_changed)
        self._color_wheel.onoff_changed.connect(self._on_wheel_onoff)

        # -- RGB Controls (C# ucScrollAR/G/B + textBoxR/G/B) --
        # Background PNG provides R/G/B letter labels — no Qt labels needed.
        self._rgb_sliders: list[QSlider] = []
        self._rgb_spinboxes: list[QSpinBox] = []
        self._rgb_labels: list[QLabel] = []  # kept empty for compat
        rgb_colors = ["#ff4444", "#44ff44", "#4444ff"]

        for i, color in enumerate(rgb_colors):
            y = RGB_Y_START + i * RGB_SPACING

            # Spinbox (left of slider — C# textBoxR/G/B)
            spinbox = QSpinBox(self)
            spinbox.setGeometry(RGB_SPINBOX_X, y, RGB_SPINBOX_W, RGB_SLIDER_H)
            spinbox.setRange(0, 255)
            spinbox.setValue(255 if i == 0 else 0)
            spinbox.setStyleSheet(
                "color: white; background: rgba(40, 40, 40, 180); "
                "border: none; font-size: 11px;"
            )
            spinbox.valueChanged.connect(
                lambda val, idx=i: self._on_spinbox_changed(idx, val)
            )
            self._rgb_spinboxes.append(spinbox)

            # Slider (right of spinbox — C# ucScrollAR/G/B)
            slider = QSlider(Qt.Orientation.Horizontal, self)
            slider.setGeometry(RGB_SLIDER_X, y, RGB_SLIDER_W, RGB_SLIDER_H)
            slider.setRange(0, 255)
            slider.setValue(255 if i == 0 else 0)
            slider.setStyleSheet(
                f"QSlider::groove:horizontal {{ background: transparent; "
                f"height: 8px; }}"
                f"QSlider::handle:horizontal {{ background: {color}; "
                f"width: 14px; margin: -3px 0; border-radius: 7px; }}"
                f"QSlider::sub-page:horizontal {{ background: {color}; "
                f"border-radius: 4px; opacity: 0.7; }}"
            )
            slider.valueChanged.connect(self._on_rgb_changed)
            self._rgb_sliders.append(slider)

        # -- Color preview swatch (hidden — color wheel serves this role) --
        self._color_swatch = QFrame(self)
        self._color_swatch.setGeometry(0, 0, 1, 1)
        self._color_swatch.setVisible(False)

        # -- Preset color buttons (C# buttonC1-C8 with D3* image assets) --
        self._preset_buttons: list[QPushButton] = []
        for i, (r, g, b) in enumerate(PRESET_COLORS):
            btn = QPushButton(self)
            x = PRESET_X_POSITIONS[i]
            btn.setGeometry(x, PRESET_Y, PRESET_SIZE, PRESET_SIZE)
            if (asset_path := Assets.get(PRESET_ASSETS[i])):
                btn.setStyleSheet(
                    f"QPushButton {{ border: none; "
                    f"background-image: url({asset_path}); "
                    f"background-repeat: no-repeat; }}"
                    f"QPushButton:hover {{ border: 1px solid white; "
                    f"border-radius: {PRESET_SIZE // 2}px; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ "
                    f"background-color: rgb({r},{g},{b}); "
                    f"border: 1px solid #555; "
                    f"border-radius: {PRESET_SIZE // 2}px; }}"
                    f"QPushButton:hover {{ border: 2px solid white; }}"
                )
            btn.setFlat(True)
            btn.clicked.connect(
                lambda checked, cr=r, cg=g, cb=b: self._set_color(cr, cg, cb)
            )
            self._preset_buttons.append(btn)

        # -- Temperature color legend (modes 5-6, hidden by default) --
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

        # -- Brightness (C# ucScrollA at (976, 537) — bg has label/icon) --
        self._bright_label = QLabel(self)
        self._bright_label.setVisible(False)  # Background has brightness icon

        self._brightness_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._brightness_slider.setGeometry(BRIGHT_X, BRIGHT_Y, BRIGHT_W, 20)
        self._brightness_slider.setRange(0, 100)
        self._brightness_slider.setValue(100)
        self._brightness_slider.setStyleSheet(
            "QSlider::groove:horizontal { background: transparent; "
            "height: 8px; }"
            "QSlider::handle:horizontal { background: #fff; width: 14px; "
            "margin: -3px 0; border-radius: 7px; }"
            "QSlider::sub-page:horizontal { background: rgba(170, 170, 170, "
            "180); border-radius: 4px; }"
        )
        self._brightness_slider.setToolTip("LED brightness")

        self._brightness_label = QLabel("100%", self)
        self._brightness_label.setGeometry(
            BRIGHT_X + BRIGHT_W + 5, BRIGHT_Y, 40, 20)
        self._brightness_label.setStyleSheet(
            "color: white; font-size: 11px; background: transparent;")

        # Single-shot debounce — the label tracks the slider live, but the
        # device write fires only once the slider settles (#202).
        self._brightness_debounce = QTimer(self)
        self._brightness_debounce.setSingleShot(True)
        self._brightness_debounce.setInterval(_BRIGHTNESS_DEBOUNCE_MS)
        self._brightness_debounce.timeout.connect(self._emit_brightness)
        self._brightness_slider.valueChanged.connect(self._on_brightness_slider_moved)

        # -- Test mode checkbox (C# checkBox1 at (36, 78)) --
        self._test_cb = QCheckBox("", self)
        self._test_cb.setGeometry(36, 78, 20, 20)
        self._test_cb.setStyleSheet(
            "QCheckBox::indicator { width: 14px; height: 14px; }"
            "QCheckBox::indicator:unchecked { border: 1px solid #666; "
            "background: transparent; }"
            "QCheckBox::indicator:checked { border: 1px solid #FF9800; "
            "background: #FF9800; }"
        )
        self._test_cb.setToolTip("LED test mode — cycles white/red/green/blue")
        self._test_cb.toggled.connect(
            lambda on: self.test_mode_changed.emit(on))
        self._test_cb.setVisible(False)  # C# checkBox1.Visible = false

        # -- Zone buttons (C# button1-4/5-6/N1-4 — images swapped per style) --
        self._zone_buttons: list[QPushButton] = []
        for i in range(4):
            btn = QPushButton(self)
            x = ZONE_X_POSITIONS[i] if i < len(ZONE_X_POSITIONS) else 590
            btn.setGeometry(x, ZONE_Y, ZONE_W, ZONE_H)
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setToolTip(f"Select zone {i + 1}")
            btn.setStyleSheet(_STYLE_FLAT_CHECKABLE_BTN)
            btn.clicked.connect(
                lambda checked, idx=i: self._on_zone_clicked(idx)
            )
            btn.setVisible(False)
            self._zone_buttons.append(btn)

        # -- Bottom section labels (i18n text, not baked into PNG) --
        self._display_selection_label = QLabel(
            tr('Display Selection', lang), self)
        self._display_selection_label.setGeometry(590, 668, 140, 20)
        self._display_selection_label.setStyleSheet(
            "color: #ccc; font-size: 12px; background: transparent;")
        self._display_selection_label.setVisible(False)

        self._circulate_label = QLabel(
            tr('Circulate', lang), self)
        self._circulate_label.setGeometry(760, 668, 80, 20)
        self._circulate_label.setStyleSheet(
            "color: #ccc; font-size: 12px; background: transparent;")
        self._circulate_label.setVisible(False)

        # Carousel toggle button (C# buttonLB at (739, 680), 14x14)
        self._carousel_btn = QPushButton(self)
        self._carousel_btn.setGeometry(739, 680, 14, 14)
        self._carousel_btn.setCheckable(True)
        self._carousel_btn.setFlat(True)
        _cb_normal = Assets.get(Assets.CHECKBOX_OFF)
        _cb_active = Assets.get(Assets.CHECKBOX_ON)
        if _cb_normal and _cb_active:
            self._carousel_btn.setStyleSheet(
                f"QPushButton {{ border: none; "
                f"background-image: url({_cb_normal}); "
                f"background-repeat: no-repeat; }}"
                f"QPushButton:checked {{ "
                f"background-image: url({_cb_active}); }}"
            )
        self._carousel_btn.setToolTip("Cycle through selected zones")
        self._carousel_btn.toggled.connect(self._on_sync_toggled)
        self._carousel_btn.setVisible(False)

        # Carousel interval input (C# textBoxTimer at (843, 678), 36x16)
        # Background PNG has "⏱ ___ S" baked in — just the number input
        self._carousel_interval = QLineEdit(self)
        self._carousel_interval.setGeometry(843, 678, 36, 16)
        self._carousel_interval.setText("2")
        self._carousel_interval.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._carousel_interval.setValidator(QIntValidator(1, 60, self))
        self._carousel_interval.setStyleSheet(
            "QLineEdit { background: rgb(67, 67, 67); color: white; "
            "border: none; font-size: 11px; }")
        self._carousel_interval.setToolTip("Carousel rotation interval (seconds)")
        self._carousel_interval.textChanged.connect(
            self._on_carousel_interval_changed)
        self._carousel_interval.setVisible(False)

        # ============================================================
        # LC2 clock widgets (style 9 — hidden by default)
        # ============================================================

        # LC2 clock buttons — C#: 14x14 checkboxes at exact positions.
        # Field labels (headers + per-button) are baked into the C# DLC2 art;
        # we overlay them as tr() text (source: D0LC2en).  LC2 text is light,
        # unlike the gold memory/disk panels.  ``_lc2_text_labels`` collects
        # every LC2 overlay (tr-key) for visibility + language refresh.
        _lc2_lbl_style = (
            "color: rgb(220, 220, 220); font-size: 13px;"
            " background: transparent;")
        self._lc2_text_labels: list[tuple[QLabel, str]] = []

        def _lc2_text(key: str, x: int, y: int, w: int, h: int) -> None:
            lbl = QLabel(tr(key, self._language), self)
            lbl.setGeometry(x, y, w, h)
            lbl.setStyleSheet(_lc2_lbl_style)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter)
            lbl.setVisible(False)
            self._lc2_text_labels.append((lbl, key))

        # "Time format" header + 24H/12H beside their buttons (x=592).
        self._lc2_label = QLabel(tr('Time format', self._language), self)
        self._lc2_label.setGeometry(560, 686, 150, 20)
        self._lc2_label.setStyleSheet(_lc2_lbl_style)
        self._lc2_label.setVisible(False)
        self._lc2_text_labels.append((self._lc2_label, 'Time format'))
        _lc2_text("24H", 612, 709, 60, 18)
        _lc2_text("12H", 612, 727, 60, 18)

        self._btn_24h = QPushButton(self)
        self._btn_24h.setGeometry(592, 711, 14, 14)
        self._btn_24h.setCheckable(True)
        self._btn_24h.setChecked(True)
        self._btn_24h.setFlat(True)
        self._btn_24h.setStyleSheet(_cb_style)
        self._btn_24h.setToolTip("24-hour format")
        self._btn_24h.clicked.connect(lambda: self._set_clock_format(True))
        self._btn_24h.setVisible(False)

        self._btn_12h = QPushButton(self)
        self._btn_12h.setGeometry(592, 729, 14, 14)
        self._btn_12h.setCheckable(True)
        self._btn_12h.setFlat(True)
        self._btn_12h.setStyleSheet(_cb_style)
        self._btn_12h.setToolTip("12-hour format")
        self._btn_12h.clicked.connect(lambda: self._set_clock_format(False))
        self._btn_12h.setVisible(False)

        # "First day of the week" header + Monday/Sunday beside their buttons
        # (x=741).  The C# en art labels the second box "Tuesday" (a typo —
        # the function is isWeekSun, i.e. Sunday); we render the correct value.
        self._week_label = QLabel(
            tr('First day of the week', self._language), self)
        self._week_label.setGeometry(709, 686, 200, 20)
        self._week_label.setStyleSheet(_lc2_lbl_style)
        self._week_label.setVisible(False)
        self._lc2_text_labels.append(
            (self._week_label, 'First day of the week'))
        _lc2_text("Monday", 761, 709, 90, 18)
        _lc2_text("Sunday", 761, 727, 90, 18)

        self._btn_sun = QPushButton(self)
        self._btn_sun.setGeometry(741, 729, 14, 14)
        self._btn_sun.setCheckable(True)
        self._btn_sun.setFlat(True)
        self._btn_sun.setStyleSheet(_cb_style)
        self._btn_sun.setToolTip("Week starts on Sunday")
        self._btn_sun.clicked.connect(lambda: self._set_week_start(True))
        self._btn_sun.setVisible(False)

        self._btn_mon = QPushButton(self)
        self._btn_mon.setGeometry(741, 711, 14, 14)
        self._btn_mon.setCheckable(True)
        self._btn_mon.setChecked(True)
        self._btn_mon.setFlat(True)
        self._btn_mon.setStyleSheet(_cb_style)
        self._btn_mon.setToolTip("Week starts on Monday")
        self._btn_mon.clicked.connect(self._on_mon_clicked)
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
        self._info_images: dict[str, UCInfoImage] = {}
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

        # °C/°F toggle buttons — C#: buttonC at (699, 144) 14x14, buttonF at (759, 144)
        # Uses checkbox images (unchecked/checked) like C#.
        self._btn_celsius = QPushButton(self)
        self._btn_celsius.setGeometry(
            TEMP_BTN_C_X, TEMP_BTN_Y, TEMP_BTN_SIZE, TEMP_BTN_SIZE)
        self._btn_celsius.setCheckable(True)
        self._btn_celsius.setChecked(True)
        self._btn_celsius.setFlat(True)
        self._btn_celsius.setStyleSheet(_cb_style)
        self._btn_celsius.setToolTip("Celsius")
        self._btn_celsius.clicked.connect(self._on_celsius_clicked)
        self._btn_celsius.setVisible(False)

        self._btn_fahrenheit = QPushButton(self)
        self._btn_fahrenheit.setGeometry(
            TEMP_BTN_F_X, TEMP_BTN_Y, TEMP_BTN_SIZE, TEMP_BTN_SIZE)
        self._btn_fahrenheit.setCheckable(True)
        self._btn_fahrenheit.setFlat(True)
        self._btn_fahrenheit.setStyleSheet(_cb_style)
        self._btn_fahrenheit.setToolTip("Fahrenheit")
        self._btn_fahrenheit.clicked.connect(self._on_fahrenheit_clicked)
        self._btn_fahrenheit.setVisible(False)

        # ============================================================
        # LC1 memory info panel (style 4 — C# UCLEDMemoryInfo)
        # Panel at (13, 656) 506x132, transparent bg (bg PNG has labels)
        # ============================================================
        _mem_lbl_style = (
            "color: rgb(180, 150, 83); font-size: 13px;"
            " background: transparent;")

        self._mem_bg = QFrame(self)
        self._mem_bg.setGeometry(13, 656, 506, 132)
        self._mem_bg.setStyleSheet("background: transparent; border: none;")
        self._mem_bg.setVisible(False)

        # C# label positions: internal (x, y) → absolute (13+x, 656+y)
        # label1=temp  label2=speed(MT/s)@136  label2_1=clock(MHz)@311
        # label3=used(GB)  label4=ratio(X)
        # label5-10=timings (tCAS/tRCD/tRP/tRAS/tRC/tRFC)
        self._mem_labels: dict[str, QLabel] = {}
        _mem_layout = [
            ("mem_temp",    136, 15, 166, 23),
            # speed + clock sit in the gaps between the name labels (Memory
            # Speed@10-134 | Memory Clock@200-310 | Ratio@362) so they don't
            # collide once they carry real values instead of "NC".
            ("mem_mts",     136, 35,  60, 23),
            ("mem_clock",   292, 35,  58, 23),
            ("mem_used",    136, 54, 166, 23),
            ("mem_ratio",   136, 74, 166, 23),
            # values sit a few px right of each term name (Tcas@141, Trcd@201,
            # Trp@261, Tras@321, Trc@381, Trfc@441) so "Tcas 30" reads with a
            # gap instead of "Tcas30" mashed together.
            ("mem_tcas",    177, 94,  38, 23),
            ("mem_trcd",    236, 94,  38, 23),
            ("mem_trp",     291, 94,  38, 23),
            ("mem_tras",    354, 94,  38, 23),
            ("mem_trc",     409, 94,  38, 23),
            ("mem_trfc",    472, 94,  38, 23),
        ]
        for key, ix, iy, w, h in _mem_layout:
            lbl = QLabel("NC", self)
            lbl.setGeometry(13 + ix, 656 + iy, w, h)
            lbl.setStyleSheet(_mem_lbl_style)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter)
            lbl.setVisible(False)
            self._mem_labels[key] = lbl
        self._mem_slots: list[dict] = []

        # Field-NAME labels.  The C# bakes these into the UCLEDMemoryInfo
        # background art; our LED panel background is label-less here (the
        # names were missing, not baked), so we overlay them as tr() text at
        # the C# value-label anchors (the name sits left of its value).
        # Transparent background (the baked panel text is erased from the art),
        # font/colour match the C# (Microsoft YaHei, RGB 180,150,83 — the same
        # as the value-label style).
        _mem_name_style = (
            "color: rgb(180, 150, 83); font-size: 13px;"
            " background: transparent;")
        self._mem_name_labels: list[tuple[QLabel, str]] = []
        _mem_name_layout = [
            ("SPD Hub Temp",    10, 15, 124, 23),
            ("Memory Speed",    10, 35, 124, 23),
            ("Memory Clock",   198, 35,  90, 23),
            ("Ratio",          362, 35,  66, 23),
            ("Memory Used",     10, 54, 124, 23),
            ("Clock Ratio",     10, 74, 124, 23),
            ("Memory Timings",  10, 94, 130, 23),
            # The 6 timing terms (deleted from the art at these exact x's;
            # they pair with the value labels at x=169/228/283/346/401/464).
            ("Tcas",           141, 94,  34, 23),
            ("Trcd",           201, 94,  34, 23),
            ("Trp",            261, 94,  34, 23),
            ("Tras",           321, 94,  34, 23),
            ("Trc",            381, 94,  34, 23),
            ("Trfc",           441, 94,  34, 23),
        ]
        for key, ix, iy, w, h in _mem_name_layout:
            lbl = QLabel(tr(key, self._language), self)
            lbl.setGeometry(13 + ix, 656 + iy, w, h)
            lbl.setStyleSheet(_mem_name_style)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter)
            lbl.setVisible(False)
            self._mem_name_labels.append((lbl, key))
        # "(Only DDR5)" — opaque, positioned over the baked "(位DDR5)" so the
        # panel-coloured background hides the Chinese and shows the English.
        self._mem_ddr5_label = QLabel(
            f"({tr('Only DDR5', self._language)})", self)
        self._mem_ddr5_label.setGeometry(13 + 189, 656 + 16, 130, 22)
        self._mem_ddr5_label.setStyleSheet(_mem_name_style)
        self._mem_ddr5_label.setAlignment(Qt.AlignmentFlag.AlignLeft
                                          | Qt.AlignmentFlag.AlignVCenter)
        self._mem_ddr5_label.setVisible(False)

        # DDR multiplier combo (C# ucComboBoxB at internal 430,35)
        self._ddr_combo = QComboBox(self)
        self._ddr_combo.setGeometry(13 + 430, 656 + 35, 58, 20)
        self._ddr_combo.addItem("\u00d71", 1)
        self._ddr_combo.addItem("\u00d72", 2)
        self._ddr_combo.addItem("\u00d74", 4)
        self._ddr_combo.setCurrentIndex(1)  # Default: ×2 (DDR)
        self._ddr_combo.setStyleSheet(
            "QComboBox { background: #333; color: rgb(180, 150, 83); "
            "border: 1px solid #555; font-size: 11px; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView { background: #333; "
            "color: rgb(180, 150, 83); selection-background-color: #555; }")
        self._ddr_combo.currentIndexChanged.connect(self._on_ddr_changed)
        self._ddr_combo.setVisible(False)
        self._memory_ratio = 2

        # ============================================================
        # LF11 disk info panel (style 10 — C# UCLEDHarddiskInfo)
        # Panel at (13, 656) 506x132, transparent bg (bg PNG has labels)
        # ============================================================

        self._disk_bg = QFrame(self)
        self._disk_bg.setGeometry(13, 656, 506, 132)
        self._disk_bg.setStyleSheet("background: transparent; border: none;")
        self._disk_bg.setVisible(False)

        # C# label positions: internal (x, y) → absolute (13+x, 656+y)
        # label1=temp  label2=health%  label3=read  label4=write
        self._disk_labels: dict[str, QLabel] = {}
        _disk_layout = [
            ("lf11_disk_temp",  170, 21, 166, 23),
            ("lf11_disk_usage", 170, 43, 166, 23),
            ("lf11_disk_read",  170, 66, 166, 23),
            ("lf11_disk_write", 170, 88, 166, 23),
        ]
        for key, ix, iy, w, h in _disk_layout:
            lbl = QLabel("NC", self)
            lbl.setGeometry(13 + ix, 656 + iy, w, h)
            lbl.setStyleSheet(_mem_lbl_style)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter)
            lbl.setVisible(False)
            self._disk_labels[key] = lbl
        self._disk_slots: list[dict] = []

        # Field-NAME labels (C# bakes these into UCLEDHarddiskInfo art; our
        # background is blank here).  Overlay tr() text left of each value
        # (values at internal x=170; C# rows y=21/43/66/88).  Source text:
        # D0LF11en ("Drive Temp / Total Activity / Read Rate / Write Rate").
        _disk_name_style = (
            "color: rgb(180, 150, 83); font-size: 13px;"
            " background: transparent;")
        self._disk_name_labels: list[tuple[QLabel, str]] = []
        _disk_name_layout = [
            ("Drive Temp",     10, 21, 155, 23),
            ("Total Activity", 10, 43, 155, 23),
            ("Read Rate",      10, 66, 155, 23),
            ("Write Rate",     10, 88, 155, 23),
        ]
        for key, ix, iy, w, h in _disk_name_layout:
            lbl = QLabel(tr(key, self._language), self)
            lbl.setGeometry(13 + ix, 656 + iy, w, h)
            lbl.setStyleSheet(_disk_name_style)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter)
            lbl.setVisible(False)
            self._disk_name_labels.append((lbl, key))

        # Disk selector combo (C# ucComboBoxC at internal 304,21)
        self._disk_selector = QComboBox(self)
        self._disk_selector.setGeometry(13 + 304, 656 + 21, 180, 24)
        self._disk_selector.setStyleSheet(
            "QComboBox { background: #333; color: rgb(180, 150, 83); "
            "border: 1px solid #555; font-size: 11px; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView { background: #333; "
            "color: rgb(180, 150, 83); selection-background-color: #555; }")
        self._disk_selector.currentIndexChanged.connect(self._on_disk_selected)
        self._disk_selector.setVisible(False)

        # -- Status label --
        self._status = QLabel("", self)
        self._status.setGeometry(STATUS_X, STATUS_Y, STATUS_W, 24)
        self._status.setStyleSheet(_STYLE_MUTED_LABEL)

    # ================================================================
    # Public API
    # ================================================================

    def initialize(self, style_id: int, segment_count: int,
                   zone_count: int = 1,
                   model: str = '') -> None:
        """Configure for a specific LED device style.

        Args:
            style_id: LED device style (1-12).
            segment_count: Number of LED segments.
            zone_count: Number of independent zones.
            model: Device model name (for PM-specific preview image).
        """
        self._style_id = style_id
        self._zone_count = zone_count
        self._model = model

        self._preview.setVisible(True)
        self._preview.set_style(style_id, segment_count)

        # Load device preview background (PM-specific or style default)
        from ...core.led_models import LED_STYLES, STYLE_BY_LEGACY_ID, PmRegistry
        style = LED_STYLES[STYLE_BY_LEGACY_ID[style_id]]

        # Resolve preview: check PmRegistry for model-specific image,
        # fall back to style default (Windows: FormLEDInit per-NO)
        preview_name = style.preview_image
        if model:
            for _pm, entry in PmRegistry._REGISTRY.items():
                if entry.model_name == model and entry.preview_image:
                    preview_name = entry.preview_image
                    break
        if (preview_pixmap := Assets.get(preview_name)):
            self._preview.set_overlay(QPixmap(preview_pixmap))

        # Set panel background (localized with fallback)
        bg_name = Assets.get_localized(style.background_base, self._language)
        if Assets.get(bg_name):
            set_background_pixmap(self, bg_name)

        self._title.setText(f"RGB LED Control \u2014 {style.model_name}")

        self._apply_layout()

        # Apply zone button images for this style, then show/hide
        self._apply_zone_images(style_id)
        for i, btn in enumerate(self._zone_buttons):
            btn.setVisible(i < zone_count and zone_count > 1)
        self._is_select_all_style = style_id in LED_SELECT_ALL_STYLES
        # Carousel + interval + selector-row visibility follow the C# selector
        # (verified against FormLEDInit's buttonLB/textBoxTimer Hide() calls):
        #   * carousel toggle (buttonLB) + the selector row show for any style
        #     WITH a selector — hidden only for NONE (LC2 clock / LF13 solid);
        #   * the rotation-interval box (textBoxTimer) shows ONLY for PAGE styles
        #     (timed metric-page rotation) — never for the ZONE "select all"
        #     styles (PA120/LF10) or NONE.
        selector = led_display_for(style_id).selector
        has_selector = selector is not LedSelector.NONE
        is_page = selector is LedSelector.PAGE
        self._carousel_btn.setVisible(has_selector)
        self._carousel_btn.setToolTip(
            "Select all zones" if self._is_select_all_style
            else "Cycle through selected pages"
        )
        self._carousel_interval.setVisible(is_page)
        self._display_selection_label.setVisible(has_selector)
        self._circulate_label.setVisible(has_selector)
        self._zones.configure(zone_count, self._is_select_all_style)
        self._carousel_btn.setChecked(False)
        if zone_count > 1:
            self._sync_zone_buttons()

        # Selector-button labels from the display model (C#-sourced).  On PAGE
        # styles the "Display Selection" buttons pick which metric the single
        # display shows — tooltip each with its metric so the selector is
        # discoverable (this is what users were missing, clicking the read-only
        # gauges instead).  ZONE styles (PA120/LF10) keep the generic tooltip.
        self._apply_selector_labels(style_id)

        # Section visibility from the toolkit-free panel model (C#-sourced via
        # audit_csharp's FormLEDInit extraction) — one contract, no scattered
        # ``style_id in (...)`` checks.  Sections are mutually exclusive: the
        # memory/disk panels replace the sensor gauges; LC2 adds the clock.
        panel = led_panel_for(style_id)
        self._set_sensor_visibility(panel.show_sensor_gauges)
        self._set_mem_visibility(panel.show_memory_panel)
        self._set_disk_visibility(panel.show_disk_panel)
        self._set_lc2_visibility(panel.show_clock_panel)

        # Populate static hardware identity info (once per device switch)
        if panel.show_memory_panel:
            self._populate_memory_identity()
        elif panel.show_disk_panel:
            self._populate_disk_identity()

    def _apply_selector_labels(self, style_id: int) -> None:
        """Label the selector buttons with the metric each page shows.

        PAGE styles (C# LunBo) → the buttons carry the metric *text* (the C#
        bakes it into the panel art; we render it as tr() so it stays
        translatable + matches the now-label-less background).  ZONE styles
        are image-based (per-zone colour), so their text is cleared.
        """
        disp = led_display_for(style_id)
        # PA120 is ZONE (page_labels==()) but its zone buttons carry metric
        # labels — fall back to the override for those.
        labels = _ZONE_BUTTON_LABELS.get(style_id, disp.page_labels)
        is_overlay = style_id in _OVERLAY_BUTTON_STYLES
        log.info("_apply_selector_labels: style=%d overlay=%s labels=%s",
                 style_id, is_overlay, labels)
        for i, btn in enumerate(self._zone_buttons):
            label = tr(labels[i], self._language) if i < len(labels) else ""
            if is_overlay:
                # Buttons whose art was cleared → overlay text, two-line like
                # the C# baked labels ("CPU" / "(°C/°F)").
                btn.setToolTip(label)
                btn.setText(label.replace(" (", "\n(", 1))
            elif disp.selector is LedSelector.PAGE:
                # Not cleared (e.g. CZ1) — the bg/image still carries the label;
                # tooltip it for discoverability but set no text (would double).
                btn.setToolTip(label)
                btn.setText("")
            else:
                btn.setText("")          # colour-zone / NONE: no metric text
        if disp.selector is LedSelector.PAGE:
            self._display_selection_label.setToolTip(
                "Pick which metric the LED display shows")

    def set_led_colors(self, colors: list[tuple[int, int, int]]) -> None:
        """Update LED preview from controller tick."""
        self._preview.set_colors(colors)

    def set_status(self, text: str) -> None:
        """Update status text."""
        self._status.setText(text)

    def set_device_fingerprint(self, text: str) -> None:
        """Show the device's handshake as a copy-paste-ready mock_gui arg line."""
        log.info("set_device_fingerprint: %s", text)
        self._fingerprint_label.setText(text)

    def set_language(self, lang: str) -> None:
        """Update the current language (injected by the window) and re-apply
        localized background + text.  Replaces the old _boot reach-in."""
        log.debug("set_language: %s", lang)
        self._language = lang
        self.apply_localized_background()

    def apply_localized_background(self) -> None:
        """Re-apply localized background and text labels for current lang."""
        from ...core.led_models import LED_STYLES, STYLE_BY_LEGACY_ID
        if self._style_id not in STYLE_BY_LEGACY_ID:
            # No LED device bound yet — _style_id is the init sentinel 0.
            # Language change still fires this; skip until a device sets it.
            log.debug(
                "apply_localized_background: no LED device bound "
                "(_style_id=%d) — skipping",
                self._style_id,
            )
            return
        lang = self._language
        style = LED_STYLES[STYLE_BY_LEGACY_ID[self._style_id]]
        bg_name = Assets.get_localized(style.background_base, lang)
        if Assets.get(bg_name):
            set_background_pixmap(self, bg_name)
        # Refresh mode button + section label text
        for i, label_key in enumerate(MODE_LABELS):
            if i < len(self._mode_buttons):
                text = tr(label_key, lang)
                self._mode_buttons[i].setText(text)
                self._mode_buttons[i].setToolTip(text)
        self._display_selection_label.setText(tr('Display Selection', lang))
        self._circulate_label.setText(tr('Circulate', lang))
        # Memory-panel field names (LC1) follow the language too.
        for lbl, key in self._mem_name_labels:
            lbl.setText(tr(key, lang))
        self._mem_ddr5_label.setText(f"({tr('Only DDR5', lang)})")
        # Disk-panel field names (LF11).
        for lbl, key in self._disk_name_labels:
            lbl.setText(tr(key, lang))
        # LC2 clock/week labels.
        for lbl, key in self._lc2_text_labels:
            lbl.setText(tr(key, lang))

    def set_temp_unit(self, unit_int: int) -> None:
        """Set temperature unit from app settings.

        Args:
            unit_int: 0 = °C, 1 = °F (matches app config).
        """
        self._temp_unit = "\u00b0F" if unit_int == 1 else "\u00b0C"
        # Sync °C/°F button visuals
        self._btn_celsius.setChecked(unit_int == 0)
        self._btn_fahrenheit.setChecked(unit_int == 1)

    # ================================================================
    # Layout management
    # ================================================================

    def _apply_zone_images(self, style_id: int) -> None:
        """Swap zone button images to match the C# button variant for this style.

        C# has 3 separate sets of zone buttons with different images:
        - button1-4 (led_zone_mode_1-4): styles 1, 2
        - button5-6 (led_zone_mode_5-6): styles 3, 5, 6, 11
        - buttonN1-4 (led_zone_btn_1-4): styles 4, 7, 8, 10
        """
        from ...core.led_models import LED_STYLES, STYLE_BY_LEGACY_ID
        if not (assets := LED_STYLES[STYLE_BY_LEGACY_ID[style_id]].zone_assets):
            return
        for i, btn in enumerate(self._zone_buttons):
            if i < len(assets):
                normal_name, active_name = assets[i]
                normal_path = Assets.get(normal_name)
                active_path = Assets.get(active_name)
                if normal_path and active_path:
                    # Only the bg-label styles (LC1/LF11) carry tr() TEXT over an
                    # empty frame — give them a visible colour + shift the text
                    # into the dark area right of the neon symbol (C# centred it
                    # ~88px from the left; background-origin:border keeps the
                    # frame image pinned left).  Other styles bake the label into
                    # the image, so they keep the plain frame style untouched.
                    if style_id in _OVERLAY_BUTTON_STYLES:
                        # Not active (normal png) → grey text; active (_active
                        # png, checked) → white text — matching the baked look.
                        btn.setStyleSheet(
                            f"QPushButton {{ border: none;"
                            f" color: rgb(160,160,160); font-size: 11px;"
                            f" padding-left: 36px; background-origin: border;"
                            f" background-image: url({normal_path}); "
                            f"background-repeat: no-repeat; }}"
                            f"QPushButton:checked {{ color: white;"
                            f" background-image: url({active_path}); }}"
                        )
                    else:
                        btn.setStyleSheet(
                            f"QPushButton {{ border: none; "
                            f"background-image: url({normal_path}); "
                            f"background-repeat: no-repeat; }}"
                            f"QPushButton:checked {{ "
                            f"background-image: url({active_path}); }}"
                        )
                else:
                    btn.setStyleSheet(_STYLE_FLAT_CHECKABLE_BTN)

    def _apply_layout(self):
        """Reposition controls.  All backgrounds include a color wheel area,
        so the layout is unified: wheel left, RGB/presets/brightness right.
        """
        # Brightness
        self._brightness_slider.setGeometry(BRIGHT_X, BRIGHT_Y, BRIGHT_W, 20)
        self._brightness_label.setGeometry(
            BRIGHT_X + BRIGHT_W + 5, BRIGHT_Y, 40, 20)

        self._status.setGeometry(STATUS_X, STATUS_Y, STATUS_W, 24)

    # ================================================================
    # Internal handlers
    # ================================================================

    def _on_mode_clicked(self, index: int):
        """Handle mode button click."""
        log.debug("_on_mode_clicked: index=%s", index)
        self._current_mode = index
        for i, btn in enumerate(self._mode_buttons):
            btn.setChecked(i == index)
        self._preview.set_led_mode(index)
        self.mode_changed.emit(index)

    def _on_hue_changed(self, hue: int):
        """Handle color wheel hue selection -> update RGB sliders."""
        log.debug("_on_hue_changed: hue=%s", hue)
        color = QColor.fromHsv(hue, 255, 255)
        self._set_color(color.red(), color.green(), color.blue())

    def _on_brightness_slider_moved(self, value: int) -> None:
        """Track the slider live in the label, (re)arm the debounce so a drag
        coalesces into one device write instead of flooding the wire (#202)."""
        self._brightness_label.setText(f"{value}%")
        self._brightness_debounce.start()

    def _emit_brightness(self) -> None:
        """Debounce elapsed — push the settled brightness to the device once."""
        value = self._brightness_slider.value()
        log.info("_emit_brightness: brightness settled at %d%%", value)
        self.brightness_changed.emit(value)

    def _on_rgb_changed(self):
        """Handle RGB slider change."""
        log.debug("_on_rgb_changed: r=%s g=%s b=%s", self._rgb_sliders[0].value(), self._rgb_sliders[1].value(), self._rgb_sliders[2].value())
        r = self._rgb_sliders[0].value()
        g = self._rgb_sliders[1].value()
        b = self._rgb_sliders[2].value()
        for i, val in enumerate([r, g, b]):
            self._rgb_spinboxes[i].blockSignals(True)
            self._rgb_spinboxes[i].setValue(val)
            self._rgb_spinboxes[i].blockSignals(False)
        self._sync_wheel_from_rgb(r, g, b)
        self.color_changed.emit(r, g, b)

    def _on_spinbox_changed(self, index: int, value: int):
        """Handle RGB spinbox change."""
        log.debug("_on_spinbox_changed: index=%s value=%s", index, value)
        self._rgb_sliders[index].blockSignals(True)
        self._rgb_sliders[index].setValue(value)
        self._rgb_sliders[index].blockSignals(False)
        r = self._rgb_spinboxes[0].value()
        g = self._rgb_spinboxes[1].value()
        b = self._rgb_spinboxes[2].value()
        self._sync_wheel_from_rgb(r, g, b)
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
        self._sync_wheel_from_rgb(r, g, b)
        self.color_changed.emit(r, g, b)

    def _sync_wheel_from_rgb(self, r: int, g: int, b: int):
        """Update wheel indicator from RGB values without triggering loop."""
        hue = QColor(r, g, b).hsvHue()
        if hue < 0:
            hue = 0  # achromatic
        self._color_wheel.blockSignals(True)
        self._color_wheel.set_hue(hue)
        self._color_wheel.blockSignals(False)

    def _on_wheel_onoff(self, val: int):
        """Handle color wheel center on/off toggle (C# ucColor2Delegate)."""
        log.debug("_on_wheel_onoff: val=%s (on=%s)", val, val == 1)
        self.global_toggled.emit(val == 1)

    # -- Zone selection --

    def _sync_zone_buttons(self) -> None:
        """Mirror the zone model's display flags onto the buttons.

        Uses ``display_enabled`` so a select-all style shows every button active
        while its overlay is on, without touching the persisted mask.  Buttons
        fire ``clicked`` (not ``toggled``) into ``_on_zone_clicked``, so
        ``setChecked`` here never re-enters the handler.
        """
        enabled = self._zones.display_enabled
        for i, btn in enumerate(self._zone_buttons):
            btn.setChecked(enabled[i] if i < len(enabled) else False)

    def _on_zone_clicked(self, zone_index: int):
        """Handle zone button click — the model owns the rule, the View mirrors.

        Select all (styles 2/7): all stay enabled, clicks ignored.
        Carousel ON: multi-select (toggle in/out, last zone protected).
        Carousel OFF: radio-select (one zone at a time).
        """
        log.debug("_on_zone_clicked: zone_index=%s carousel=%s",
                  zone_index, self._zones.carousel)
        emit = self._zones.click_zone(zone_index)
        self._sync_zone_buttons()
        if emit is None:
            return
        if emit.kind == "zone_selected":
            self.zone_selected.emit(emit.zone)
        elif emit.kind == "carousel_zone":
            # The model owns the full enabled mask — publish it whole so the
            # carousel rotates exactly the toggled pages/zones (one command,
            # no per-index reconstruction handler-side).
            self.carousel_zones_changed.emit(self._zones.enabled)

    def _on_sync_toggled(self, carousel: bool):
        """Handle carousel/select-all checkbox toggle (C# buttonLB_Click).

        Styles 2/7: "Select all" — all zone buttons checked, no interval.
        Other styles: "Circulate" — multi-select zones with timer interval.
        The model recomputes enabled flags; the View owns interval visibility.
        """
        log.debug("_on_sync_toggled: carousel=%s", carousel)
        self._zones.toggle_carousel(carousel)
        if self._is_select_all_style:
            self._carousel_interval.setVisible(False)
        else:
            self._carousel_interval.setVisible(carousel and self._zone_count > 1)
        self._sync_zone_buttons()
        self.carousel_changed.emit(carousel)
        # toggle_carousel recomputed the enabled mask (on → keep current
        # multi-select; off → collapse to the selected one); persist it so the
        # carousel seeds with at least the selected page instead of an empty
        # mask (which would leave it stuck on page 0).
        self.carousel_zones_changed.emit(self._zones.enabled)

    def _on_carousel_interval_changed(self, text: str = ""):
        """Handle carousel interval input change (C# textBoxTimer_TextChanged)."""
        log.debug("_on_carousel_interval_changed: text=%r", text)
        if not text:
            text = self._carousel_interval.text()
        if text.isdigit() and int(text) > 0:
            self.carousel_interval_changed.emit(int(text))

    def load_zone_state(self, zone_index: int, mode: int,
                        color: tuple, brightness: int,
                        on: bool = True):
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

        # Sync color wheel indicator + on/off button with zone state
        self._sync_wheel_from_rgb(r, g, b)
        self._color_wheel.set_onoff(1 if on else 0)

    def load_sync_state(self, enabled: bool, zones: list[bool],
                        interval_secs: int) -> None:
        """Restore carousel/sync UI state from device config.

        Called by LEDHandler._sync_ui_from_state() on device activation.
        Uses blockSignals to prevent round-tripping back through handler.
        """
        self._carousel_btn.blockSignals(True)
        self._carousel_btn.setChecked(enabled)
        self._carousel_btn.blockSignals(False)
        self._zones.load_sync(enabled, zones)
        self._sync_zone_buttons()

        # Show interval for circulate styles (not select-all)
        if not self._is_select_all_style:
            self._carousel_interval.setVisible(enabled and self._zone_count > 1)
            self._carousel_interval.blockSignals(True)
            self._carousel_interval.setText(str(interval_secs))
            self._carousel_interval.blockSignals(False)

    @property
    def selected_zone(self) -> int:
        return self._zones.selected

    @property
    def carousel_mode(self) -> bool:
        return self._zones.carousel

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

    def _on_mon_clicked(self) -> None:
        log.info("_on_mon_clicked")
        self._set_week_start(False)

    def _on_celsius_clicked(self) -> None:
        log.info("_on_celsius_clicked")
        self._set_temp_unit_btn(False)

    def _on_fahrenheit_clicked(self) -> None:
        log.info("_on_fahrenheit_clicked")
        self._set_temp_unit_btn(True)

    def _set_temp_unit_btn(self, is_fahrenheit: bool):
        """Handle °C/°F toggle button click."""
        self._btn_celsius.setChecked(not is_fahrenheit)
        self._btn_fahrenheit.setChecked(is_fahrenheit)
        self._temp_unit = "\u00b0F" if is_fahrenheit else "\u00b0C"
        self.temp_unit_changed.emit("F" if is_fahrenheit else "C")

    # -- Visibility helpers --

    def _set_lc2_visibility(self, visible: bool):
        """Show/hide LC2 clock widgets."""
        self._btn_24h.setVisible(visible)
        self._btn_12h.setVisible(visible)
        self._btn_sun.setVisible(visible)
        self._btn_mon.setVisible(visible)
        for lbl, _ in self._lc2_text_labels:
            lbl.setVisible(visible)

    def _set_sensor_visibility(self, visible: bool):
        """Show/hide UCInfoImage sensor gauges.

        Note: °C/°F buttons (buttonC/buttonF) are permanently hidden in C#
        (Visible=false in InitializeComponent, never changed). Temp unit is
        controlled via the app settings, not per-panel buttons.
        """
        for widget in self._info_images.values():
            widget.setVisible(visible)

    def _set_mem_visibility(self, visible: bool):
        """Show/hide LC1 memory info panel (C# ucledMemoryInfo1)."""
        self._mem_bg.setVisible(visible)
        for lbl in self._mem_labels.values():
            lbl.setVisible(visible)
        for lbl, _ in self._mem_name_labels:
            lbl.setVisible(visible)
        self._mem_ddr5_label.setVisible(visible)
        self._ddr_combo.setVisible(visible)

    def _set_disk_visibility(self, visible: bool):
        """Show/hide LF11 disk info panel (C# ucledHarddiskInfo1)."""
        self._disk_bg.setVisible(visible)
        for lbl in self._disk_labels.values():
            lbl.setVisible(visible)
        for lbl, _ in self._disk_name_labels:
            lbl.setVisible(visible)
        self._disk_selector.setVisible(visible)

    # -- Sensor/memory/disk update methods --

    def update_metrics(self, metrics: HardwareMetrics) -> None:
        """Observer callback — dispatch metrics to visible sub-widgets.

        Single entry point: caller doesn't need to know which style
        uses which update method. Panel owns the routing.
        """
        log.debug("update_metrics")
        if self._style_id not in (4, 10):
            self.update_sensor_metrics(metrics)
        if self._style_id == 4:
            self.update_memory_metrics(metrics)
        elif self._style_id == 10:
            self.update_lf11_disk_metrics(metrics)
        elif self._style_id == 9:
            self._update_clock()

    def _update_clock(self) -> None:
        """LC2 clock display — reads own timer state, no external args."""
        import datetime
        fields = clock_fields(
            datetime.datetime.now(), self._is_timer_24h, self._is_week_sunday,
        )
        self._preview.set_timer(*fields)

    def update_sensor_metrics(self, metrics: HardwareMetrics) -> None:
        """Update UCInfoImage sensor gauges."""
        gauges = format_sensor_gauges(metrics, self._temp_unit)
        # Per-tick → DEBUG, but log the RESOLVED values actually pushed to the
        # gauges (not just intent) so "M1-M6 show --" is traceable to either a
        # dead metrics object or a dead widget. (CLAUDE.md: log resolved values.)
        log.debug("update_sensor_metrics: %s",
                  {k: t for k, (_v, t, _u) in gauges.items()})
        for key, (value, text, unit) in gauges.items():
            self._info_images[key].set_value(value, text, unit)

    def update_memory_metrics(self, metrics: HardwareMetrics) -> None:
        """Update memory info labels (LC1 style 4, C# UCLEDMemoryInfo)."""
        log.debug("update_memory_metrics")
        for key, text in format_memory_labels(
            metrics, self._temp_unit, self._memory_ratio,
        ).items():
            self._mem_labels[key].setText(text)

    def _populate_memory_identity(self) -> None:
        """Populate memory timing labels from DRAM SPD info."""
        try:
            if self._get_memory_info is None:
                return
            self._mem_slots = self._get_memory_info()
            if self._mem_slots:
                s = self._mem_slots[0]
                for key in ('mem_tcas', 'mem_trcd', 'mem_trp',
                            'mem_tras', 'mem_trc', 'mem_trfc'):
                    field = key.replace('mem_t', 't').replace('mem_', '')
                    val = s.get(field, '')
                    self._mem_labels[key].setText(str(val) if val else "NC")
        except Exception as e:
            # Memory probe surface is wide (subprocess + WMI + parser) — fall safe.
            log.debug("uc_led_control: memory identity populate failed: %s", e)
            self._mem_slots = []

    def _on_ddr_changed(self, index: int) -> None:
        """Handle DDR multiplier combo selection."""
        log.debug("_on_ddr_changed: index=%s", index)
        ratio = self._ddr_combo.itemData(index)
        if ratio and isinstance(ratio, int):
            self._memory_ratio = ratio
            self.memory_ratio_changed.emit(ratio)

    def set_memory_ratio(self, ratio: int) -> None:
        """Set DDR combo from saved state (without emitting signal)."""
        self._memory_ratio = ratio
        idx = {1: 0, 2: 1, 4: 2}.get(ratio, 1)
        self._ddr_combo.blockSignals(True)
        self._ddr_combo.setCurrentIndex(idx)
        self._ddr_combo.blockSignals(False)

    def _populate_disk_identity(self) -> None:
        """Populate disk selector dropdown (C# ucComboBoxC)."""
        try:
            if self._get_disk_info is None:
                return
            self._disk_slots = self._get_disk_info()

            self._disk_selector.blockSignals(True)
            self._disk_selector.clear()
            for d in self._disk_slots:
                name = d.get('name', d.get('model', '?'))
                # C# shows name up to '(' character
                if '(' in name:
                    name = name[:name.index('(') - 1]
                self._disk_selector.addItem(name)
            self._disk_selector.blockSignals(False)
        except Exception as e:
            # Disk probe surface is wide (subprocess + WMI + parser) — fall safe.
            log.debug("uc_led_control: disk identity populate failed: %s", e)
            self._disk_slots = []

    def _on_disk_selected(self, idx: int) -> None:
        """Handle disk selector change — emit signal."""
        log.debug("_on_disk_selected: idx=%s", idx)
        self.disk_index_changed.emit(idx)

    def update_lf11_disk_metrics(self, metrics: HardwareMetrics) -> None:
        """Update disk info labels (LF11 style 10, C# UCLEDHarddiskInfo)."""
        log.debug("update_lf11_disk_metrics")
        for key, text in format_disk_labels(metrics, self._temp_unit).items():
            self._disk_labels[key].setText(text)

    # ================================================================
    # Metric-source context menu (temp-/load-linked LED color)
    # ================================================================

    def contextMenuEvent(self, event) -> None:
        """Right-click → pick the CPU/GPU source for temp-/load-linked
        LED color modes.  A context menu keeps the pixel-perfect FormLED
        layout untouched (no new buttons to place)."""
        menu = QMenu(self)
        temp_menu = menu.addMenu("Temperature source")
        temp_cpu = temp_menu.addAction("CPU")
        temp_gpu = temp_menu.addAction("GPU")
        load_menu = menu.addMenu("Load source")
        load_cpu = load_menu.addAction("CPU")
        load_gpu = load_menu.addAction("GPU")
        chosen = menu.exec(event.globalPos())
        if chosen is temp_cpu:
            self.temp_source_changed.emit("cpu")
        elif chosen is temp_gpu:
            self.temp_source_changed.emit("gpu")
        elif chosen is load_cpu:
            self.load_source_changed.emit("cpu")
        elif chosen is load_gpu:
            self.load_source_changed.emit("gpu")

    # ================================================================
    # Window drag (C# delegate cmds 241/242/243)
    # ================================================================

    def mousePressEvent(self, event):
        """Start window drag from header area (C# FormLED_MouseDown)."""
        if (event.button() == Qt.MouseButton.LeftButton
                and event.position().y() < self._DRAG_MAX_Y):
            window = self.window()
            self._drag_pos = (
                event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Move window while dragging (C# FormLED_MouseMove)."""
        if self._drag_pos is not None:
            window = self.window()
            window.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """End window drag (C# FormLED_MouseUp)."""
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ================================================================
    # Styles
    # ================================================================

    @staticmethod
    def _mode_button_style(active: bool) -> str:
        # Text-based mode buttons — rendered over blank background PNG.
        if active:
            return (
                "QPushButton { background: rgba(33, 150, 243, 60); "
                "color: white; border: none; font-size: 10px; }"
            )
        return (
            "QPushButton { background: transparent; color: #ccc; "
            "border: none; font-size: 10px; }"
            "QPushButton:checked { background: rgba(33, 150, 243, 60); "
            "color: white; }"
            "QPushButton:hover { background: rgba(255, 255, 255, 20); }"
        )

