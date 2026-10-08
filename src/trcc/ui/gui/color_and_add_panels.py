"""Color picker and add element panels — right-side overlay editors.

ColorPickerPanel: hue strip + colour square, RGB, XY position, font, eyedropper.
AddElementPanel: Create new overlay elements by type with category/metric selection.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from ...core.logs import per_frame
from ...core.models import (
    RECENT_COLOR_DEFAULT,
    RECENT_COLOR_SLOTS,
    OverlayElementConfig,
    OverlayMode,
)
from .assets import Assets
from .base import set_background_pixmap
from .constants import Colors, Layout, Sizes, Styles

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Values a widget carries so its slot is a named method rather than a closure
#: over a loop variable — ``feedback_no_lambdas``.
_SWATCH_PROPERTY = "trcc_swatch_rgb"
_MODE_PROPERTY = "trcc_overlay_mode"


# ── The C#'s inline picker: UCColorB (hue strip) drives UCColorC (square) ──

#: The square's gradient sits 4 px inside its control on every side
#: (``UCColorC``: ``MyBitmap = new Bitmap(Width - 8, Height - 8)`` at (4, 4)).
_SQUARE_INSET = 4

Rgb = tuple[int, int, int]


def hue_strip_rgb(center_x: int, width: int, thumb_w: int) -> Rgb:
    """The colour under the hue strip's thumb -- ``UCColorB_Color``
    (``UCColorB.cs:87-132``): six linear segments R->Y->G->C->B->M->R,
    in the C#'s integer arithmetic."""
    seg = max(1, (width - thumb_w) // 6)
    x = center_x - thumb_w // 2
    log.debug("hue_strip_rgb: x=%d segment=%d", x, seg)
    part, x = divmod(x, seg) if x < seg * 5 else (5, x - seg * 5)
    up, down = 255 * x // seg, 255 - 255 * x // seg
    return ((255, up, 0), (down, 255, 0), (0, 255, up),
            (0, down, 255), (up, 0, 255), (255, 0, down))[part]


def color_square_rgb(base: Rgb, x: int, y: int, w: int, h: int) -> Rgb:
    """Pixel (*x*, *y*) of the *w* x *h* square for hue *base* --
    ``UCColorC.ColorToBitmap``: toward white across the top row, then each
    column toward black, truncating to int at each step as the C# does."""
    log.debug("color_square_rgb: base=%s at (%d,%d) of %dx%d", base, x, y, w, h)
    fx, fy = x / max(1, w - 1), y / max(1, h - 1)
    r, g, b = (int(c * (1.0 - fx) + 255 * fx) for c in base)
    return int(r * (1.0 - fy)), int(g * (1.0 - fy)), int(b * (1.0 - fy))


class _HueStrip(QWidget):
    """The hue strip (``UCColorB``).  The rainbow is in the panel's
    background art; this draws only the thumb and turns a drag into a hue."""

    hue_picked = Signal(int, int, int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._thumb = Assets.load_pixmap('color_panel_slider_thumb.png')
        self._thumb_w = self._thumb.width() if not self._thumb.isNull() else 8
        self._center_x = self._thumb_w // 2
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        log.debug("_HueStrip.__init__: thumb %dpx", self._thumb_w)

    def paintEvent(self, event) -> None:
        frame_log.debug("_HueStrip.paintEvent: x=%d", self._center_x)
        if not self._thumb.isNull():
            QPainter(self).drawPixmap(self._center_x - self._thumb_w // 2, 0,
                                      self._thumb)

    def mousePressEvent(self, event) -> None:
        log.debug("_HueStrip.mousePressEvent: x=%d", event.position().x())
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick(int(event.position().x()))

    def mouseMoveEvent(self, event) -> None:
        frame_log.debug("_HueStrip.mouseMoveEvent: x=%d", event.position().x())
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(int(event.position().x()))

    def _pick(self, x: int) -> None:
        half = self._thumb_w // 2
        self._center_x = max(half, min(self.width() - half, x))
        self.update()
        rgb = hue_strip_rgb(self._center_x, self.width(), self._thumb_w)
        log.debug("_HueStrip._pick: x=%d -> %s", self._center_x, rgb)
        self.hue_picked.emit(*rgb)


class _ColorSquare(QWidget):
    """The colour square (``UCColorC``): the chosen hue toward white across,
    toward black down; a press or drag picks the pixel under the ring."""

    color_picked = Signal(int, int, int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._base: Rgb = (255, 0, 0)
        self._ring = Assets.load_pixmap('color_panel_selector_ring.png')
        self._pos = QPoint(_SQUARE_INSET, _SQUARE_INSET)
        self.setCursor(Qt.CursorShape.CrossCursor)
        log.debug("_ColorSquare.__init__")

    def set_base(self, r: int, g: int, b: int) -> None:
        """A new hue: redraw, ring back to the corner (``SetUCColorC``)."""
        log.debug("_ColorSquare.set_base: %s -> (%d,%d,%d)", self._base, r, g, b)
        self._base = (r, g, b)
        self._pos = QPoint(_SQUARE_INSET, _SQUARE_INSET)
        self.update()

    def paintEvent(self, event) -> None:
        frame_log.debug("_ColorSquare.paintEvent: base=%s", self._base)
        p = QPainter(self)
        area = self.rect().adjusted(_SQUARE_INSET, _SQUARE_INSET,
                                    -_SQUARE_INSET, -_SQUARE_INSET)
        across = QLinearGradient(area.topLeft(), area.topRight())
        across.setColorAt(0.0, QColor(*self._base))
        across.setColorAt(1.0, QColor(255, 255, 255))
        p.fillRect(area, across)
        down = QLinearGradient(area.topLeft(), area.bottomLeft())
        down.setColorAt(0.0, QColor(0, 0, 0, 0))
        down.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.fillRect(area, down)
        if not self._ring.isNull():
            p.drawPixmap(self._pos.x() - self._ring.width() // 2,
                         self._pos.y() - self._ring.height() // 2, self._ring)

    def mousePressEvent(self, event) -> None:
        log.debug("_ColorSquare.mousePressEvent: %s", event.position())
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick(event.position().toPoint())

    def mouseMoveEvent(self, event) -> None:
        frame_log.debug("_ColorSquare.mouseMoveEvent: %s", event.position())
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick(event.position().toPoint())

    def _pick(self, at: QPoint) -> None:
        w = self.width() - 2 * _SQUARE_INSET
        h = self.height() - 2 * _SQUARE_INSET
        x = max(_SQUARE_INSET, min(w + _SQUARE_INSET - 1, at.x()))
        y = max(_SQUARE_INSET, min(h + _SQUARE_INSET - 1, at.y()))
        self._pos = QPoint(x, y)
        self.update()
        rgb = color_square_rgb(self._base, x - _SQUARE_INSET,
                               y - _SQUARE_INSET, w, h)
        log.debug("_ColorSquare._pick: (%d,%d) -> %s", x, y, rgb)
        self.color_picked.emit(*rgb)


class ColorPickerPanel(QFrame):
    """Color and position editor (matches UCXiTongXianShiColor 230x374)."""

    color_changed = Signal(int, int, int)
    #: The colour an edit ENDED on, once, when the user moves to another
    #: element -- the C#'s UCXiTongXianShiBackupColorSave.  Not per drag step.
    edit_finished = Signal(int, int, int)
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
        #: The C#'s myColorChange: set by the strip, the square, typed RGB and
        #: the eyedropper; NOT by a preset or recent swatch.
        self._edited = False
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

        # The C#'s inline picker: a hue strip that drives a colour square.
        # This was a click target that opened a modal QColorDialog instead --
        # the first commit's stand-in, never revisited.
        self.color_square = _ColorSquare(self)
        self.color_square.setGeometry(*Layout.COLOR_AREA)
        self.color_square.color_picked.connect(self._on_square_picked)
        self.hue_strip = _HueStrip(self)
        self.hue_strip.setGeometry(*Layout.COLOR_HUE)
        self.hue_strip.hue_picked.connect(self._on_hue_picked)

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
            btn.setProperty(_SWATCH_PROPERTY, (r, g, b))
            btn.clicked.connect(self._on_swatch_clicked)

        # Recent colours (the C#'s button1..11): the App keeps the row per
        # device; set_recent_colors shows it.  Created transparent and never
        # filled or connected until 2026-10-08.
        self._history_btns = []
        for i in range(RECENT_COLOR_SLOTS):
            btn = QPushButton(self)
            btn.setGeometry(
                Layout.COLOR_SWATCH_X0 + i * Layout.COLOR_SWATCH_DX,
                Layout.COLOR_SWATCH_HISTORY_Y,
                Layout.COLOR_SWATCH_SIZE, Layout.COLOR_SWATCH_SIZE
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self._on_swatch_clicked)
            self._history_btns.append(btn)
        self.set_recent_colors([RECENT_COLOR_DEFAULT] * RECENT_COLOR_SLOTS)

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

    def _on_hue_picked(self, r, g, b):
        """The hue strip: a new hue for the square, and the colour itself."""
        log.info("ColorPickerPanel._on_hue_picked: (%d,%d,%d)", r, g, b)
        self._apply_color(r, g, b)

    def _on_square_picked(self, r, g, b):
        """The square: the colour only -- its hue stays (``ChangedTextBoxOnly``)."""
        log.info("ColorPickerPanel._on_square_picked: (%d,%d,%d)", r, g, b)
        self._apply_color(r, g, b, rebase=False)

    def _on_rgb_changed(self):
        try:
            r = max(0, min(255, int(self.r_input.text())))
            g = max(0, min(255, int(self.g_input.text())))
            b = max(0, min(255, int(self.b_input.text())))
        except ValueError:
            log.warning(
                "ColorPickerPanel._on_rgb_changed: parse failed for "
                "r=%r g=%r b=%r — dropped",
                self.r_input.text(), self.g_input.text(), self.b_input.text(),
            )
            return
        log.info("ColorPickerPanel._on_rgb_changed: (%d,%d,%d)", r, g, b)
        self._apply_color(r, g, b)

    def _on_swatch_clicked(self) -> None:
        """A preset swatch was pressed — read its colour off the button."""
        button = self.sender()
        rgb = None if button is None else button.property(_SWATCH_PROPERTY)
        log.debug("_on_swatch_clicked: rgb=%s", rgb)
        if rgb is not None:
            self._set_color_from_swatch(*rgb)

    def _set_color_from_swatch(self, r, g, b):
        log.info("ColorPickerPanel._set_color_from_swatch: (%d,%d,%d)",
                 r, g, b)
        self._apply_color(r, g, b, edited=False)

    def _apply_color(self, r, g, b, *, rebase=True, edited=True):
        log.debug("ColorPickerPanel._apply_color: r=%d, g=%d, b=%d edited=%s",
                  r, g, b, edited)
        self._edited = self._edited or edited
        self.set_color(r, g, b, rebase=rebase)
        self.color_changed.emit(r, g, b)

    def set_recent_colors(self, colors):
        """Show the device's recent row (newest first) on the 11 swatches."""
        log.debug("ColorPickerPanel.set_recent_colors: %s", list(colors)[:3])
        for btn, (r, g, b) in zip(self._history_btns, colors, strict=False):
            btn.setStyleSheet(
                f"QPushButton {{ background-color: rgb({r},{g},{b}); "
                f"border: none; }}"
                f"QPushButton:hover {{ border: 1px solid white; }}")
            btn.setProperty(_SWATCH_PROPERTY, (r, g, b))

    def end_edit(self):
        """The user moved on: report the colour an edit ended on, once."""
        log.debug("ColorPickerPanel.end_edit: edited=%s", self._edited)
        if self._edited:
            self._edited = False
            c = self._current_color
            self.edit_finished.emit(c.red(), c.green(), c.blue())

    def _on_position_changed(self):
        x, y = self.x_spin.value(), self.y_spin.value()
        log.info("ColorPickerPanel._on_position_changed: (%d,%d)", x, y)
        self.position_changed.emit(x, y)

    def set_color(self, r, g, b, *, rebase=True):
        """Show (r, g, b); *rebase* also makes it the square's hue, as every
        C# path but a pick in the square itself does (``SetUCColorC``)."""
        log.debug("ColorPickerPanel.set_color: (%d,%d,%d) rebase=%s",
                  r, g, b, rebase)
        self._current_color = QColor(r, g, b)
        self.r_input.setText(str(r))
        self.g_input.setText(str(g))
        self.b_input.setText(str(b))
        if rebase:
            self.color_square.set_base(r, g, b)

    def set_color_hex(self, hex_color):
        """Set color from hex string like '#FF0000'."""
        log.debug("ColorPickerPanel.set_color_hex: %s", hex_color)
        c = QColor(hex_color)
        if c.isValid():
            self.set_color(c.red(), c.green(), c.blue())
        else:
            log.warning(
                "ColorPickerPanel.set_color_hex: invalid hex %r", hex_color,
            )

    def set_position(self, x, y):
        log.debug("ColorPickerPanel.set_position: (%d,%d)", x, y)
        self.x_spin.blockSignals(True)
        self.y_spin.blockSignals(True)
        self.x_spin.setValue(x)
        self.y_spin.setValue(y)
        self.x_spin.blockSignals(False)
        self.y_spin.blockSignals(False)

    def _pick_font(self):
        """Open font dialog (matches Windows FontDialog in UCXiTongXianShiColor)."""
        log.info("ColorPickerPanel._pick_font: opening QFontDialog")
        from PySide6.QtWidgets import QDialog, QFontDialog
        current = QFont(self._current_font_name, self._current_font_size)
        # An instance and exec(), never the static QFontDialog.getFont: that
        # one holds the GIL for as long as the dialog is open (measured, 2
        # ticks of a background thread in 1.6 s; every other static dialog we
        # use runs ~150).  It froze the event-stream reader, the App evicted
        # the stalled window, and its preview stopped for good (#301).
        dialog = QFontDialog(current, self)
        dialog.setWindowTitle("Pick Font")
        ok = dialog.exec() == QDialog.DialogCode.Accepted
        font = dialog.selectedFont()
        if ok:
            self._current_font_name = font.family()
            self._current_font_size = font.pointSize()
            # C# Font.Style: 0=Regular, 1=Bold, 2=Italic, 3=BoldItalic
            self._current_font_style = 1 if font.bold() else 0
            log.info(
                "ColorPickerPanel._pick_font: picked %s size=%d bold=%s",
                font.family(), font.pointSize(), font.bold(),
            )
            self.font_btn.setText(font.family())
            self.font_size_spin.blockSignals(True)
            self.font_size_spin.setValue(font.pointSize())
            self.font_size_spin.blockSignals(False)
            self.font_changed.emit(font.family(), font.pointSize(),
                                   self._current_font_style)
        else:
            log.info("ColorPickerPanel._pick_font: dialog cancelled")

    def _on_font_size_changed(self, size: int):
        """Handle font size spinbox change independently."""
        log.info("ColorPickerPanel._on_font_size_changed: %d -> %d",
                 self._current_font_size, size)
        self._current_font_size = size
        self.font_changed.emit(self._current_font_name, size,
                               self._current_font_style)

    def set_font_display(self, font_name, font_size, font_style=0):
        log.debug(
            "ColorPickerPanel.set_font_display: name=%r size=%d style=%d",
            font_name, font_size, font_style,
        )
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
            btn.setProperty(_MODE_PROPERTY, mode)
            btn.clicked.connect(self._on_type_button)
            y += Layout.ADD_BTN_DY

    def _on_type_button(self) -> None:
        """An add-element button was pressed — read its mode off the button."""
        button = self.sender()
        mode = None if button is None else button.property(_MODE_PROPERTY)
        log.debug("_on_type_button: mode=%s", mode)
        if mode is not None:
            self._on_type_clicked(mode)

    def _on_type_clicked(self, mode: OverlayMode):
        log.info("AddElementPanel._on_type_clicked: mode=%s", mode.name)
        if mode == OverlayMode.HARDWARE:
            # Show activity sidebar for hardware sensor selection
            # (Windows: hardware metrics listed as separate section in add panel)
            self.hardware_requested.emit()
            return

        cfg = OverlayElementConfig(mode=mode)
        self.element_added.emit(cfg)
