"""Color picker and add element panels — right-side overlay editors.

ColorPickerPanel: RGB color picker, XY position, font selector, eyedropper.
AddElementPanel: Create new overlay elements by type with category/metric selection.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QPushButton,
    QSpinBox,
)

from ...core.models import OverlayElementConfig, OverlayMode
from .assets import Assets
from .base import set_background_pixmap
from .constants import Colors, Layout, Sizes, Styles

log = logging.getLogger(__name__)


class ColorPickerPanel(QFrame):
    """Color and position editor (matches UCXiTongXianShiColor 230x374)."""

    color_changed = Signal(int, int, int)
    position_changed = Signal(int, int)
    font_changed = Signal(str, int, int)  # name, size, style (0=Regular, 1=Bold)
    eyedropper_requested = Signal()  # launch eyedropper color picker

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(Sizes.COLOR_PANEL_W, Sizes.COLOR_PANEL_H)

        set_background_pixmap(self, 'settings_overlay_color_bg.png',
            Sizes.COLOR_PANEL_W, Sizes.COLOR_PANEL_H,
            fallback_style=f"background-color: {Colors.PANEL_FALLBACK}; border-radius: 5px;")

        self._current_color = QColor(255, 255, 255)
        self._setup_ui()

    def _setup_ui(self):
        # X coordinate input
        self.x_spin = QSpinBox(self)
        self.x_spin.setGeometry(*Layout.COLOR_X_SPIN)
        self.x_spin.setRange(0, 480)
        self.x_spin.setStyleSheet(Styles.INPUT_FIELD)
        self.x_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.x_spin.setToolTip("X position")
        self.x_spin.valueChanged.connect(self._on_position_changed)

        # Y coordinate input
        self.y_spin = QSpinBox(self)
        self.y_spin.setGeometry(*Layout.COLOR_Y_SPIN)
        self.y_spin.setRange(0, 480)
        self.y_spin.setStyleSheet(Styles.INPUT_FIELD)
        self.y_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.y_spin.setToolTip("Y position")
        self.y_spin.valueChanged.connect(self._on_position_changed)

        # Font picker button (name only)
        self.font_btn = QPushButton(self)
        self.font_btn.setGeometry(*Layout.COLOR_FONT_BTN)
        self.font_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; border: none; "
            f"color: {Colors.TEXT}; font-size: 10px; text-align: left; padding-left: 27px; }}"
        )
        self.font_btn.setText("Microsoft YaHei")
        self.font_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.font_btn.setToolTip("Choose font")
        self.font_btn.clicked.connect(self._pick_font)
        self._current_font_name = "Microsoft YaHei"
        self._current_font_size = 36
        self._current_font_style = 0  # 0=Regular, 1=Bold

        # Font size spinbox (separate adjuster)
        self.font_size_spin = QSpinBox(self)
        self.font_size_spin.setGeometry(*Layout.COLOR_FONT_SIZE_SPIN)
        self.font_size_spin.setRange(6, 200)
        self.font_size_spin.setValue(36)
        self.font_size_spin.setStyleSheet(Styles.INPUT_FIELD)
        self.font_size_spin.setToolTip("Font size")
        self.font_size_spin.valueChanged.connect(self._on_font_size_changed)

        # Color picker area click target
        self.color_area_btn = QPushButton(self)
        self.color_area_btn.setGeometry(*Layout.COLOR_AREA)
        self.color_area_btn.setStyleSheet("background-color: transparent; border: none;")
        self.color_area_btn.setCursor(Qt.CursorShape.CrossCursor)
        self.color_area_btn.setToolTip("Pick color")
        self.color_area_btn.clicked.connect(self._pick_color)

        # RGB input boxes
        self.r_input = QLineEdit("255", self)
        self.r_input.setGeometry(*Layout.COLOR_R)
        self.r_input.setStyleSheet(Styles.RGB_INPUT)
        self.r_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.r_input.setToolTip("Red (0-255)")

        self.g_input = QLineEdit("255", self)
        self.g_input.setGeometry(*Layout.COLOR_G)
        self.g_input.setStyleSheet(Styles.RGB_INPUT)
        self.g_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.g_input.setToolTip("Green (0-255)")

        self.b_input = QLineEdit("255", self)
        self.b_input.setGeometry(*Layout.COLOR_B)
        self.b_input.setStyleSheet(Styles.RGB_INPUT)
        self.b_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.b_input.setToolTip("Blue (0-255)")

        for inp in (self.r_input, self.g_input, self.b_input):
            inp.editingFinished.connect(self._on_rgb_changed)

        # Preset color swatches
        for i, (r, g, b) in enumerate(Colors.PRESET_COLORS):
            btn = QPushButton(self)
            btn.setGeometry(
                Layout.COLOR_SWATCH_X0 + i * Layout.COLOR_SWATCH_DX,
                Layout.COLOR_SWATCH_PRESET_Y,
                Layout.COLOR_SWATCH_SIZE, Layout.COLOR_SWATCH_SIZE
            )
            btn.setStyleSheet(
                f"QPushButton {{ background-color: rgb({r},{g},{b}); border: none; }}"
                f"QPushButton:hover {{ border: 1px solid white; }}"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, cr=r, cg=g, cb=b: self._set_color_from_swatch(cr, cg, cb))

        # History color swatches
        self._history_btns = []
        for i in range(len(Colors.PRESET_COLORS)):
            btn = QPushButton(self)
            btn.setGeometry(
                Layout.COLOR_SWATCH_X0 + i * Layout.COLOR_SWATCH_DX,
                Layout.COLOR_SWATCH_HISTORY_Y,
                Layout.COLOR_SWATCH_SIZE, Layout.COLOR_SWATCH_SIZE
            )
            btn.setStyleSheet("background-color: transparent; border: none;")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._history_btns.append(btn)

        # Eyedropper button (matches Windows buttonGetColor at (12, 276, 48, 48))
        self.eyedropper_btn = QPushButton(self)
        self.eyedropper_btn.setGeometry(*Layout.COLOR_EYEDROPPER)
        eyedrop_pixmap = Assets.load_pixmap('color_panel_eyedropper.png', 48, 48)
        if not eyedrop_pixmap.isNull():
            self.eyedropper_btn.setIcon(QIcon(eyedrop_pixmap))
            self.eyedropper_btn.setIconSize(self.eyedropper_btn.size())
        self.eyedropper_btn.setFlat(True)
        self.eyedropper_btn.setStyleSheet(Styles.ICON_BUTTON_HOVER)
        self.eyedropper_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eyedropper_btn.setToolTip("Pick color from screen")
        self.eyedropper_btn.clicked.connect(self.eyedropper_requested.emit)

    def _pick_color(self):
        from PySide6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self._current_color, self, "Pick Color")
        if color.isValid():
            self._apply_color(color.red(), color.green(), color.blue())

    def _on_rgb_changed(self):
        try:
            r = max(0, min(255, int(self.r_input.text())))
            g = max(0, min(255, int(self.g_input.text())))
            b = max(0, min(255, int(self.b_input.text())))
            self._apply_color(r, g, b)
        except ValueError:
            pass

    def _set_color_from_swatch(self, r, g, b):
        self._apply_color(r, g, b)

    def _apply_color(self, r, g, b):
        log.debug("_apply_color: r=%d, g=%d, b=%d", r, g, b)
        self._current_color = QColor(r, g, b)
        self.r_input.setText(str(r))
        self.g_input.setText(str(g))
        self.b_input.setText(str(b))
        self.color_changed.emit(r, g, b)

    def _on_position_changed(self):
        self.position_changed.emit(self.x_spin.value(), self.y_spin.value())

    def set_color(self, r, g, b):
        self._current_color = QColor(r, g, b)
        self.r_input.setText(str(r))
        self.g_input.setText(str(g))
        self.b_input.setText(str(b))

    def set_color_hex(self, hex_color):
        """Set color from hex string like '#FF0000'."""
        c = QColor(hex_color)
        if c.isValid():
            self.set_color(c.red(), c.green(), c.blue())

    def set_position(self, x, y):
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.x_spin.setValue(x)
        self.y_spin.setValue(y)
        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)

    def _pick_font(self):
        """Open font dialog (matches Windows FontDialog in UCXiTongXianShiColor)."""
        from PySide6.QtWidgets import QFontDialog
        current = QFont(self._current_font_name, self._current_font_size)
        ok, font = QFontDialog.getFont(current, self, "Pick Font")
        if ok:
            self._current_font_name = font.family()
            self._current_font_size = font.pointSize()
            # C# Font.Style: 0=Regular, 1=Bold, 2=Italic, 3=BoldItalic
            self._current_font_style = 1 if font.bold() else 0
            self.font_btn.setText(font.family())
            self.font_size_spin.blockSignals(True)
            self.font_size_spin.setValue(font.pointSize())
            self.font_size_spin.blockSignals(False)
            self.font_changed.emit(font.family(), font.pointSize(),
                                   self._current_font_style)

    def _on_font_size_changed(self, size: int):
        """Handle font size spinbox change independently."""
        self._current_font_size = size
        self.font_changed.emit(self._current_font_name, size,
                               self._current_font_style)

    def set_font_display(self, font_name, font_size, font_style=0):
        self._current_font_name = font_name
        self._current_font_size = font_size
        self._current_font_style = font_style
        self.font_btn.setText(font_name)
        self.font_size_spin.blockSignals(True)
        self.font_size_spin.setValue(font_size)
        self.font_size_spin.blockSignals(False)


class AddElementPanel(QFrame):
    """Add new overlay element panel (matches UCXiTongXianShiAdd 230x430)."""

    element_added = Signal(object)  # OverlayElementConfig
    hardware_requested = Signal()   # Show activity sidebar for hardware pick

    ELEMENT_TYPES = [
        ("Hardware Data", OverlayMode.HARDWARE),
        ("Time", OverlayMode.TIME),
        ("Weekday", OverlayMode.WEEKDAY),
        ("Date", OverlayMode.DATE),
        ("Custom Text", OverlayMode.CUSTOM),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(Sizes.ADD_PANEL_W, Sizes.ADD_PANEL_H)

        set_background_pixmap(self, 'settings_overlay_add_bg.png',
            Sizes.ADD_PANEL_W, Sizes.ADD_PANEL_H,
            fallback_style=f"background-color: {Colors.PANEL_FALLBACK}; border-radius: 5px;")

        self._setup_ui()

    def _setup_ui(self):
        y = Layout.ADD_BTN_Y0
        for name, mode in self.ELEMENT_TYPES:
            btn = QPushButton(name, self)
            btn.setGeometry(Layout.ADD_BTN_X, y, Layout.ADD_BTN_W, Layout.ADD_BTN_H)
            btn.setStyleSheet(Styles.ADD_ELEMENT_BTN)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, m=mode: self._on_type_clicked(m))
            y += Layout.ADD_BTN_DY

    def _on_type_clicked(self, mode: OverlayMode):
        if mode == OverlayMode.HARDWARE:
            # Show activity sidebar for hardware sensor selection
            # (Windows: hardware metrics listed as separate section in add panel)
            self.hardware_requested.emit()
            return

        cfg = OverlayElementConfig(mode=mode)
        self.element_added.emit(cfg)
