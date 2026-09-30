"""Display mode toggle panels — bottom-row controls.

DisplayModePanel: Generic toggle + action buttons for background/mask/video modes.
MaskPanel: Mask overlay with X/Y position inputs and visibility toggle.
ScreenCastPanel: Screen capture with X/Y/W/H coordinate inputs and aspect locking.
DataTablePanel: Context-sensitive format controls (C/F, 12H/24H, date format, text).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QIntValidator, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
)

from ...core.geometry import lock_region_to_panel
from ...core.i18n import tr
from ...core.models import ACTION_ICON_IMAGES, DATE_FORMAT_IMAGES, OverlayMode
from .assets import Assets
from .base import set_background_pixmap
from .constants import Colors, Layout, Sizes, Styles

log = logging.getLogger(__name__)

#: Values a widget carries so its slot can be a named method rather than a
#: closure over a loop variable — ``feedback_no_lambdas``.
_ACTION_PROPERTY = "trcc_action"
_ENTRY_PROPERTY = "trcc_entry"
_DELTA_PROPERTY = "trcc_delta"
_AXIS_PROPERTY = "trcc_axis"


class DataTablePanel(QFrame):
    """Data selection table (matches UCXiTongXianShiTable 230x54).

    Windows shows different controls depending on the selected element mode:
    - Hardware (mode 0): button0 — show/hide the unit glyph (myModeSub 0↔1);
      the global C/F choice is the About panel's celsius/fahrenheit radios
    - Time    (mode 1): button1 — 12H/24H toggle
    - Weekday (mode 2): no controls
    - Date    (mode 3): button3 — date format cycle (YMD→DMY→MD→DM)
    - Custom  (mode 4): textBox1 — custom text input
    """

    format_changed = Signal(int, int)  # mode, mode_sub
    text_changed = Signal(str)

    # Date format images in cycle order (mode_sub 1→2→3→4→1)
    _DATE_IMAGES = DATE_FORMAT_IMAGES

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(Sizes.DATA_TABLE_W, Sizes.DATA_TABLE_H)

        set_background_pixmap(self, 'settings_overlay_table_bg.png',
            Sizes.DATA_TABLE_W, Sizes.DATA_TABLE_H,
            fallback_style=f"background-color: {Colors.PANEL_FALLBACK}; border-radius: 5px;")

        # button0 — show/hide unit glyph (mode 0: hardware).  The C# unit-switch
        # toggles myModeSub: 1 draws the number + unit, 0 the bare number (unit
        # baked into the theme art).  NOT the global C/F choice (About panel).
        # Windows: (80, 15) 70x24
        self.unit_btn = QPushButton(self)
        self.unit_btn.setGeometry(80, 15, 70, 24)
        self.unit_btn.setFlat(True)
        self.unit_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.unit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # _unit_off = bare (mode_sub 0), _unit_on = unit shown (mode_sub 1).
        self._unit_off = Assets.load_pixmap('display_mode_unit_c.png', 70, 24)
        self._unit_on = Assets.load_pixmap('display_mode_unit_f.png', 70, 24)
        self.unit_btn.setToolTip("Show unit (°C/%/MHz/RPM) on the value")
        self.unit_btn.clicked.connect(self._on_unit_clicked)
        self.unit_btn.setVisible(False)

        # button1 — 12H/24H toggle (mode 1: time)
        # Windows: (88, 16) 54x22
        self.time_btn = QPushButton(self)
        self.time_btn.setGeometry(88, 16, 54, 22)
        self.time_btn.setFlat(True)
        self.time_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.time_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._time_12h = Assets.load_pixmap('display_mode_12h.png', 54, 22)
        self._time_24h = Assets.load_pixmap('display_mode_24h.png', 54, 22)
        self.time_btn.setToolTip("Time format (12h/24h)")
        self.time_btn.clicked.connect(self._on_time_clicked)
        self.time_btn.setVisible(False)

        # button3 — date format cycle (mode 3: date)
        # Windows: (88, 16) 54x22
        self.date_btn = QPushButton(self)
        self.date_btn.setGeometry(88, 16, 54, 22)
        self.date_btn.setFlat(True)
        self.date_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.date_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._date_pixmaps = {
            k: Assets.load_pixmap(v, 54, 22) for k, v in self._DATE_IMAGES.items()
        }
        self.date_btn.setToolTip("Date format")
        self.date_btn.clicked.connect(self._on_date_clicked)
        self.date_btn.setVisible(False)

        # textBox1 — custom text input (mode 4: custom)
        # Windows: (15, 15) 200x22
        self.text_input = QLineEdit(self)
        self.text_input.setGeometry(15, 15, 200, 22)
        self.text_input.setStyleSheet(Styles.INPUT_FIELD)
        self.text_input.setPlaceholderText("Text/Value")
        self.text_input.setToolTip("Custom text")
        self.text_input.setMaxLength(100)
        self.text_input.editingFinished.connect(self._on_text_committed)
        self.text_input.setVisible(False)

        self._current_mode = -1
        self._mode_sub = 0

    def _hide_all(self):
        self.unit_btn.setVisible(False)
        self.time_btn.setVisible(False)
        self.date_btn.setVisible(False)
        self.text_input.setVisible(False)

    def _update_unit_image(self):
        px = self._unit_on if self._mode_sub else self._unit_off
        if not px.isNull():
            self.unit_btn.setIcon(QIcon(px))
            self.unit_btn.setIconSize(self.unit_btn.size())

    def _update_time_image(self):
        # mode_sub 1 = 12H (hh:mm AM/PM), else 24H (HH:mm)
        px = self._time_12h if self._mode_sub == 1 else self._time_24h
        if not px.isNull():
            self.time_btn.setIcon(QIcon(px))
            self.time_btn.setIconSize(self.time_btn.size())

    def _update_date_image(self):
        px = self._date_pixmaps.get(self._mode_sub)
        if px and not px.isNull():
            self.date_btn.setIcon(QIcon(px))
            self.date_btn.setIconSize(self.date_btn.size())

    def _on_text_committed(self) -> None:
        """The custom-text field lost focus or was committed."""
        text = self.text_input.text()
        log.info("_on_text_committed: text=%r", text)
        self.text_changed.emit(text)

    def set_mode(self, mode, mode_sub=0):
        """Show the appropriate control for the selected element mode."""
        self._current_mode = mode
        self._mode_sub = mode_sub
        self._hide_all()

        match mode:
            case OverlayMode.HARDWARE:
                self._update_unit_image()
                self.unit_btn.setVisible(True)
            case OverlayMode.TIME:
                self._update_time_image()
                self.time_btn.setVisible(True)
            case OverlayMode.WEEKDAY:
                pass  # No controls
            case OverlayMode.DATE:
                if self._mode_sub == 0:
                    self._mode_sub = 1  # Default to YMD
                self._update_date_image()
                self.date_btn.setVisible(True)
            case OverlayMode.CUSTOM:
                self.text_input.setVisible(True)

    def _on_unit_clicked(self):
        """Toggle show-unit: mode_sub 0 (bare) ↔ 1 (number + unit)."""
        log.debug("_on_unit_clicked: mode_sub=%s→%s", self._mode_sub, 0 if self._mode_sub else 1)
        self._mode_sub = 0 if self._mode_sub else 1
        self._update_unit_image()
        self.format_changed.emit(self._current_mode, self._mode_sub)

    def _on_time_clicked(self):
        """Toggle 12H/24H: mode_sub 1↔2."""
        log.debug("_on_time_clicked: mode_sub=%s→%s", self._mode_sub, 2 if self._mode_sub == 1 else 1)
        self._mode_sub = 2 if self._mode_sub == 1 else 1
        self._update_time_image()
        self.format_changed.emit(self._current_mode, self._mode_sub)

    def _on_date_clicked(self):
        """Cycle date format: 1→2→3→4→1 (YMD→DMY→MD→DM)."""
        log.debug("_on_date_clicked: mode_sub=%s→%s", self._mode_sub, (self._mode_sub % 4) + 1)
        self._mode_sub = (self._mode_sub % 4) + 1
        self._update_date_image()
        self.format_changed.emit(self._current_mode, self._mode_sub)


class DisplayModePanel(QFrame):
    """Display mode toggle panel (351x100).

    Background image (localized P01) provides labels.
    Controls are invisible click targets over baked-in text.
    """

    mode_changed = Signal(str, bool)
    action_requested = Signal(str)

    def __init__(self, mode_id, actions: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.mode_id = mode_id
        self.actions: list[str] = actions or []

        self.setFixedSize(Sizes.DISPLAY_MODE_W, Sizes.DISPLAY_MODE_H)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(Colors.PANEL_FALLBACK))
        self.setPalette(palette)

        self._setup_ui()

    # Tooltip text for action buttons
    _TOOLTIP_MAP = {
        "Image": "Load image from file",
        "Video": "Load video/GIF from file",
        "Load": "Load mask overlay",
        "Clear": "Clear mask",
        "VideoLoad": "Load video for playback",
        "GIF": "Load animated GIF",
        "Network": "Network stream",
        "Settings": "Settings",
        "Upload": "Upload custom mask",
    }

    # Tooltip text for toggle buttons by mode
    _TOGGLE_TOOLTIP = {
        "background": "Enable background display",
        "screencast": "Enable screen capture",
        "video": "Enable video playback",
        "mask": "Toggle mask overlay",
    }

    _TITLE_STYLE = (
        "color: white; font-family: 'Microsoft YaHei'; font-size: 12pt;"
        " background: transparent;"
    )

    def _setup_ui(self):
        # Toggle button — small slider for all panels
        self.toggle_btn = QPushButton(self)
        self.toggle_btn.setGeometry(*Layout.TOGGLE_MASK)
        on_px = Assets.load_pixmap(Assets.TOGGLE_ON, 36, 18)
        off_px = Assets.load_pixmap(Assets.TOGGLE_OFF, 36, 18)

        self.toggle_btn.setCheckable(True)
        if not on_px.isNull() and not off_px.isNull():
            icon = QIcon()
            icon.addPixmap(on_px, QIcon.Mode.Normal, QIcon.State.On)
            icon.addPixmap(off_px, QIcon.Mode.Normal, QIcon.State.Off)
            self.toggle_btn.setIcon(icon)
            self.toggle_btn.setIconSize(self.toggle_btn.size())
        self.toggle_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setToolTip(self._TOGGLE_TOOLTIP.get(self.mode_id, "Toggle"))
        self.toggle_btn.clicked.connect(self._on_toggle)

        # Title label next to toggle
        self._title_lbl = QLabel(self.mode_id.title(), self)
        self._title_lbl.setGeometry(44, 5, 140, 18)
        self._title_lbl.setStyleSheet(self._TITLE_STYLE)

        # Action buttons with icon images
        _ICON_MAP = ACTION_ICON_IMAGES
        self._action_buttons: list[QPushButton] = []
        action_positions = [Layout.ACTION_BTN_1, Layout.ACTION_BTN_2]
        for i, action_name in enumerate(self.actions):
            if i >= len(action_positions):
                break
            btn = QPushButton(self)
            btn.setGeometry(*action_positions[i])
            if (icon_name := _ICON_MAP.get(action_name)):
                px = Assets.load_pixmap(icon_name, 40, 40)
                if not px.isNull():
                    btn.setIcon(QIcon(px))
                    btn.setIconSize(btn.size())
            btn.setStyleSheet(Styles.FLAT_BUTTON_HOVER)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(self._TOOLTIP_MAP.get(action_name, action_name))
            btn.setEnabled(False)  # Disabled until toggle is ON (C# buttonOnOff_Set)
            # The action rides ON the button rather than in a closure over
            # the loop variable — ``feedback_no_lambdas``.
            btn.setProperty(_ACTION_PROPERTY, action_name)
            btn.clicked.connect(self._on_action_clicked)
            self._action_buttons.append(btn)

    def _on_action_clicked(self) -> None:
        """An action button was pressed — read which off the button."""
        button = self.sender()
        action = "" if button is None else str(button.property(_ACTION_PROPERTY))
        log.info("_on_action_clicked: action=%s", action)
        if action:
            self.action_requested.emit(action)

    # Shared by MaskPanel and ScreenCastPanel, which carried byte-identical
    # copies until 2026-09-20 — ``dup_bodies`` clustered all three, and the
    # style constant resolved to the same string in both, so this is a pull-up
    # rather than the `0` case where each subclass supplies its own value.
    _ENTRY_STYLE = (
        "background-color: black; color: #B4964F; border: none;"
        " font-family: 'Microsoft YaHei'; font-size: 9pt;"
    )

    def _make_entry(self, x, y, w, h):
        """Create a numeric coordinate entry field (0-9999)."""
        entry = QLineEdit(self)
        entry.setGeometry(x, y, w, h)
        entry.setText("0")
        entry.setAlignment(Qt.AlignmentFlag.AlignRight)
        entry.setStyleSheet(self._ENTRY_STYLE)
        entry.setValidator(QIntValidator(0, 9999, entry))
        return entry

    def _make_pm_btn(self, x, y, w, h, delta, entry):
        """Create a +/- button for a coordinate."""
        btn = QPushButton(self)
        btn.setGeometry(x, y, w, h)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        img_name = Assets.PLUS if delta > 0 else Assets.MINUS
        pix = Assets.load_pixmap(img_name, w, h)
        if not pix.isNull():
            btn.setIcon(QIcon(pix))
            btn.setIconSize(btn.size())
            btn.setStyleSheet(Styles.FLAT_BUTTON)
        else:
            btn.setText("+" if delta > 0 else "-")
            btn.setStyleSheet(
                "QPushButton { background: #333; color: #888; border: none; font-size: 9px; }"
            )
        btn.setProperty(_ENTRY_PROPERTY, entry)
        btn.setProperty(_DELTA_PROPERTY, delta)
        btn.clicked.connect(self._on_step_clicked)

    def _increment(self, entry, delta):
        """Increment/decrement an entry value, clamped to 0..9999.

        On the BASE: ``MaskPanel`` and ``ScreenCastPanel`` carried
        byte-identical copies (``dup_bodies`` clustered them), and
        ``_on_step_clicked`` needs exactly one of them to resolve against.
        """
        try:
            val = max(0, min(9999, int(entry.text() or '0') + delta))
            entry.setText(str(val))
        except ValueError:
            log.debug("_increment: %r is not a number — ignored", entry.text())

    def _on_step_clicked(self) -> None:
        """A +/- stepper was pressed — read its field and step off the button.

        Lives on the shared base, not on both subclasses: ``_increment`` is
        already a duplicate pair (``dup_bodies`` reports
        ``DisplayModePanel._increment <- MaskPanel, ScreenCastPanel``) and one
        slot resolving through it keeps this from becoming a third copy.
        """
        button = self.sender()
        if button is None:
            return
        entry = button.property(_ENTRY_PROPERTY)
        delta = button.property(_DELTA_PROPERTY)
        log.debug("_on_step_clicked: delta=%s", delta)
        if entry is not None and delta is not None:
            self._increment(entry, int(delta))

    def _on_toggle(self, checked):
        log.debug("_on_toggle: mode_id=%s checked=%s", self.mode_id, checked)
        self._set_actions_enabled(checked)
        self.mode_changed.emit(self.mode_id, checked)

    def _set_actions_enabled(self, enabled: bool):
        """Enable/disable action buttons (C# buttonOnOff_Set pattern)."""
        for btn in self._action_buttons:
            btn.setEnabled(enabled)

    def set_enabled(self, enabled):
        self.toggle_btn.blockSignals(True)
        self.toggle_btn.setChecked(enabled)
        self.toggle_btn.blockSignals(False)
        self._set_actions_enabled(enabled)

    def set_title(self, text: str) -> None:
        """Update the title label text."""
        self._title_lbl.setText(text)

    def set_background_image(self, pixmap):
        """Apply P01 localized background via QPalette (not stylesheet)."""
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.width(), self.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            set_background_pixmap(self, scaled)


class MaskPanel(DisplayModePanel):
    """Mask overlay panel with X/Y position inputs and visibility toggle.

    Extends DisplayModePanel with coordinate entry fields for mask positioning
    and an eye toggle for mask visibility.

    Layout within 351x100:
        [Toggle] [Load] [Upload]     X: [___][+][-]
                                      Y: [___][+][-]  [eye]
    """

    mask_position_changed = Signal(int, int)  # x, y
    mask_visibility_toggled = Signal(bool)

    # X/Y entry positions (right side of panel)
    _TEXTBOX_X = (259, 40, 38, 16)
    _TEXTBOX_Y = (259, 65, 38, 16)

    # +/- button positions
    _BTN_ADD_X = (301, 42, 14, 14)
    _BTN_SUB_X = (319, 42, 14, 14)
    _BTN_ADD_Y = (301, 67, 14, 14)
    _BTN_SUB_Y = (319, 67, 14, 14)

    # Eye toggle position
    _BTN_EYE = (309, 6, 24, 16)

    def __init__(self, parent=None):
        super().__init__("mask", ["Load", "Upload"], parent)
        # Reposition action buttons to align with left-side text labels
        if len(self._action_buttons) >= 1:
            self._action_buttons[0].setGeometry(115, 30, 40, 40)  # Load/Masks
        if len(self._action_buttons) >= 2:
            self._action_buttons[1].setGeometry(175, 30, 40, 40)  # Upload
        self._updating = False
        self._mask_visible = True
        self._setup_mask_ui()

    _LABEL_STYLE = (
        "color: white; font-family: 'Microsoft YaHei'; font-size: 9pt;"
        " background: transparent;"
    )

    def _setup_mask_ui(self):
        """Add X/Y coordinate inputs and eye toggle on top of base panel."""
        self._make_label("X", 247, 40)
        self._make_label("Y", 247, 65)

        self.entry_x = self._make_entry(*self._TEXTBOX_X)
        self.entry_y = self._make_entry(*self._TEXTBOX_Y)

        self.entry_x.textChanged.connect(self._on_position_changed)
        self.entry_y.textChanged.connect(self._on_position_changed)

        # +/- buttons
        self._make_pm_btn(*self._BTN_ADD_X, +1, self.entry_x)
        self._make_pm_btn(*self._BTN_SUB_X, -1, self.entry_x)
        self._make_pm_btn(*self._BTN_ADD_Y, +1, self.entry_y)
        self._make_pm_btn(*self._BTN_SUB_Y, -1, self.entry_y)

        # Eye toggle button
        self.eye_btn = QPushButton(self)
        self.eye_btn.setGeometry(*self._BTN_EYE)
        self.eye_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.eye_btn.setToolTip("Toggle mask visibility")
        self.eye_btn.clicked.connect(self._on_eye_toggle)
        self._update_eye_icon()

    def _make_label(self, text, x, y):
        """Create a small coordinate label."""
        lbl = QLabel(text, self)
        lbl.setGeometry(x, y, 10, 16)
        lbl.setStyleSheet(self._LABEL_STYLE)
        return lbl

    def _on_position_changed(self):
        """Handle X/Y value change."""
        if self._updating:
            return
        log.debug("_on_position_changed: x=%s y=%s", self.entry_x.text(), self.entry_y.text())
        if (position := self._entered_position()) is not None:
            self.mask_position_changed.emit(*position)

    def _entered_position(self) -> tuple[int, int] | None:
        """The X/Y the fields say, an empty field reading as 0; None mid-edit."""
        try:
            return int(self.entry_x.text() or '0'), int(self.entry_y.text() or '0')
        except ValueError:
            log.debug("_entered_position: not a number yet (%r, %r)",
                      self.entry_x.text(), self.entry_y.text())
            return None

    def _on_eye_toggle(self):
        log.debug("_on_eye_toggle: mask_visible=%s→%s", self._mask_visible, not self._mask_visible)
        self._mask_visible = not self._mask_visible
        self._update_eye_icon()
        self.mask_visibility_toggled.emit(self._mask_visible)

    def _update_eye_icon(self):
        img = 'display_mode_border_active.png' if self._mask_visible else 'display_mode_border.png'
        pix = Assets.load_pixmap(img, 24, 16)
        if not pix.isNull():
            self.eye_btn.setIcon(QIcon(pix))
            self.eye_btn.setIconSize(self.eye_btn.size())
            self.eye_btn.setStyleSheet(Styles.FLAT_BUTTON)
        else:
            self.eye_btn.setText("V" if self._mask_visible else "H")
            self.eye_btn.setStyleSheet(
                "QPushButton { background: #00CED1; color: white; border: none; font-size: 8px; }"
                if self._mask_visible else
                "QPushButton { background: #555; color: white; border: none; font-size: 8px; }"
            )

    def set_position(self, x: int, y: int):
        """Set X/Y values without triggering events.

        Left alone when the fields already mean it: the App echoes back every
        position typed here, and rewriting a field the user is typing in turns
        an emptied field into "0" under the cursor.
        """
        log.debug("set_position: (%d,%d) entered=%s", x, y, self._entered_position())
        if self._entered_position() == (x, y):
            return
        self._updating = True
        self.entry_x.setText(str(x))
        self.entry_y.setText(str(y))
        self._updating = False

    def set_mask_visible(self, visible: bool):
        """Set eye toggle state."""
        self._mask_visible = visible
        self._update_eye_icon()

    def apply_language(self, lang: str) -> None:
        """Update title label for current language."""
        self._title_lbl.setText(tr('Layer Mask', lang))


class ScreenCastPanel(DisplayModePanel):
    """Screen cast panel with X/Y/W/H coordinate inputs.

    Extends DisplayModePanel with coordinate entry fields, +/- buttons,
    border toggle, and aspect ratio locking.

    Matches Windows UCTouPingXianShi layout within 351x100.
    """

    screencast_params_changed = Signal(int, int, int, int)  # x, y, w, h
    border_toggled = Signal(bool)
    audio_toggled = Signal(bool)

    # Positions from Windows UCTouPingXianShi.cs
    _TEXTBOX_X = (110, 40, 56, 16)
    _TEXTBOX_Y = (110, 65, 56, 16)
    _TEXTBOX_W = (241, 40, 56, 16)
    _TEXTBOX_H = (241, 65, 56, 16)

    _BTN_ADD_X = (171, 42, 14, 14)
    _BTN_SUB_X = (189, 42, 14, 14)
    _BTN_ADD_Y = (171, 67, 14, 14)
    _BTN_SUB_Y = (189, 67, 14, 14)
    _BTN_ADD_W = (301, 42, 14, 14)
    _BTN_SUB_W = (319, 42, 14, 14)
    _BTN_ADD_H = (301, 67, 14, 14)
    _BTN_SUB_H = (319, 67, 14, 14)

    _BTN_BORDER = (309, 16, 24, 16)
    _BTN_AUDIO = (280, 16, 24, 16)

    capture_requested = Signal()  # launch screen capture

    def __init__(self, parent=None):
        super().__init__("screencast", [], parent)
        self._updating = False
        self._show_border = True
        self._aspect_lock = True
        # None, not (0, 0) — ``DeviceStateResult.resolution`` keeps "we have
        # not asked the hardware yet" distinct from "a 0x0 panel", and a
        # presentation that collapses them cannot tell a user with no device
        # from a user whose device answered nonsense.
        self._resolution: tuple[int, int] | None = None
        self._setup_screencast_ui()

    def _setup_screencast_ui(self):
        """Add coordinate inputs on top of base DisplayModePanel."""
        # X/Y/W/H entries
        self.entry_x = self._make_entry(*self._TEXTBOX_X)
        self.entry_y = self._make_entry(*self._TEXTBOX_Y)
        self.entry_w = self._make_entry(*self._TEXTBOX_W)
        self.entry_h = self._make_entry(*self._TEXTBOX_H)

        for axis, field in (("x", self.entry_x), ("y", self.entry_y),
                            ("w", self.entry_w), ("h", self.entry_h)):
            field.setProperty(_AXIS_PROPERTY, axis)
            field.textChanged.connect(self._on_coord_edited)

        # +/- buttons
        self._make_pm_btn(*self._BTN_ADD_X, +1, self.entry_x)
        self._make_pm_btn(*self._BTN_SUB_X, -1, self.entry_x)
        self._make_pm_btn(*self._BTN_ADD_Y, +1, self.entry_y)
        self._make_pm_btn(*self._BTN_SUB_Y, -1, self.entry_y)
        self._make_pm_btn(*self._BTN_ADD_W, +1, self.entry_w)
        self._make_pm_btn(*self._BTN_SUB_W, -1, self.entry_w)
        self._make_pm_btn(*self._BTN_ADD_H, +1, self.entry_h)
        self._make_pm_btn(*self._BTN_SUB_H, -1, self.entry_h)

        # Border toggle button
        self.border_btn = QPushButton(self)
        self.border_btn.setGeometry(*self._BTN_BORDER)
        self.border_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.border_btn.setToolTip("Toggle capture border")
        self.border_btn.clicked.connect(self._on_border_toggle)
        self._update_border_icon()

        # Audio visualization toggle button
        self._audio_on = False
        self.audio_btn = QPushButton(self)
        self.audio_btn.setGeometry(*self._BTN_AUDIO)
        self.audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.audio_btn.setToolTip("Toggle mic audio visualization")
        self.audio_btn.clicked.connect(self._on_audio_toggle)
        self._update_audio_icon()

    def _on_coord_edited(self) -> None:
        """A coordinate field changed — read which axis off the widget."""
        field = self.sender()
        axis = "" if field is None else str(field.property(_AXIS_PROPERTY))
        log.debug("_on_coord_edited: axis=%s", axis)
        if axis:
            self._on_coord_changed(axis)

    def _on_coord_changed(self, which):
        """Handle coordinate value change with aspect ratio locking."""
        if self._updating:
            return
        log.debug("_on_coord_changed: which=%s val=%s", which, getattr(self, f'entry_{which}', None) and getattr(self, f'entry_{which}').text())
        try:
            val = int(getattr(self, f'entry_{which}').text() or '0')
        except ValueError:
            return

        if self._aspect_lock and which in ('w', 'h') and (
                ratio := self._get_aspect_ratio()):
            # ratio is height/width, so height = width * ratio and
            # width = height / ratio.  These were the wrong way round, which
            # made every non-square panel move the locked edge the WRONG WAY:
            # on an 854x480 panel a width of 201 produced a height of 357
            # where 113 is correct.  Square panels were skipped outright by a
            # ``ratio != 1.0`` guard, so they never locked at all.
            self._updating = True
            if which == 'w':
                # ONE derivation, shared with qtgui — core.geometry.
                *_, locked = lock_region_to_panel(self._resolution, 0, 0, val, 0)
                self.entry_h.setText(str(locked))
            else:
                self.entry_w.setText(str(round(val / ratio)))
            log.debug("_on_coord_changed: aspect lock %s=%d ratio=%.4f -> %sx%s",
                      which, val, ratio,
                      self.entry_w.text(), self.entry_h.text())
            self._updating = False

        self._emit_params()

    def _emit_params(self):
        """Emit all four coordinate values."""
        try:
            x = int(self.entry_x.text() or '0')
            y = int(self.entry_y.text() or '0')
            w = int(self.entry_w.text() or '0')
            h = int(self.entry_h.text() or '0')
            self.screencast_params_changed.emit(x, y, w, h)
        except ValueError:
            pass

    def _get_aspect_ratio(self) -> float | None:
        """The panel's height/width, or ``None`` before a device is known.

        Derived, not tabulated.  A hardcoded resolution->ratio table used to
        live here; every one of its 11 entries was exactly ``height / width``,
        so it was a second copy of geometry this panel is already handed by
        :meth:`set_resolution` — and it had drifted, missing 640x172 entirely
        and falling back to a 0.75 default that is 2.8x wrong for that panel.

        ``None`` carries the same meaning it does on
        ``DeviceStateResult.resolution``, which is where this geometry comes
        from: nobody has told us yet.  A UI that answered 0.75 anyway would be
        constraining the user's region to a panel it has not met.
        """
        if self._resolution is None:
            log.debug("_get_aspect_ratio: no device yet — no aspect lock")
            return None
        width, height = self._resolution
        if width <= 0 or height <= 0:
            log.warning("_get_aspect_ratio: device reported %dx%d — no aspect lock",
                        width, height)
            return None
        return height / width

    def _on_border_toggle(self):
        log.debug("_on_border_toggle: show_border=%s→%s", self._show_border, not self._show_border)
        self._show_border = not self._show_border
        self._update_border_icon()
        self.border_toggled.emit(self._show_border)

    def _update_border_icon(self):
        img = 'display_mode_border_active.png' if self._show_border else 'display_mode_border.png'
        pix = Assets.load_pixmap(img, 24, 16)
        if not pix.isNull():
            self.border_btn.setIcon(QIcon(pix))
            self.border_btn.setIconSize(self.border_btn.size())
            self.border_btn.setStyleSheet(Styles.FLAT_BUTTON)
        else:
            self.border_btn.setText("B" if self._show_border else "b")
            self.border_btn.setStyleSheet(
                "QPushButton { background: #00CED1; color: white; border: none; font-size: 8px; }"
                if self._show_border else
                "QPushButton { background: #555; color: white; border: none; font-size: 8px; }"
            )

    def set_values(self, x=None, y=None, w=None, h=None):
        """Set coordinate values without triggering events."""
        self._updating = True
        if x is not None:
            self.entry_x.setText(str(x))
        if y is not None:
            self.entry_y.setText(str(y))
        if w is not None:
            self.entry_w.setText(str(w))
        if h is not None:
            self.entry_h.setText(str(h))
        self._updating = False

    def set_resolution(self, width: int, height: int) -> None:
        """Set LCD resolution for aspect ratio calculations."""
        self._resolution = (width, height)

    def set_aspect_lock(self, enabled):
        self._aspect_lock = enabled

    def set_border_visible(self, visible):
        self._show_border = visible
        self._update_border_icon()

    def _on_audio_toggle(self):
        log.info("_on_audio_toggle")
        self._audio_on = not self._audio_on
        self._update_audio_icon()
        self.audio_toggled.emit(self._audio_on)

    def _update_audio_icon(self):
        self.audio_btn.setText("🎤" if self._audio_on else "🔇")
        self.audio_btn.setStyleSheet(
            "QPushButton { background: #00CED1; color: white; border: none; font-size: 10px; }"
            if self._audio_on else
            "QPushButton { background: #555; color: white; border: none; font-size: 10px; }"
        )
