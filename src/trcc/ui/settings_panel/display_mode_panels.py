"""Display mode toggle panels — bottom-row controls.

DisplayModePanel: Generic toggle + action buttons for background/mask/video modes.
MaskPanel: Mask overlay with X/Y position inputs and visibility toggle.
ScreenCastPanel: Screen capture with X/Y/W/H coordinate inputs and aspect locking.
DataTablePanel: Context-sensitive format controls (C/F, 12H/24H, date format, text).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QIntValidator,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ...core.geometry import lock_region_to_panel
from ...core.logs import per_frame
from ...core.models import ACTION_ICON_IMAGES, InsetCorner, InsetSize, OverlayMode
from ..qt_words import say_tip
from .settings_layout import (
    ACTION_BTN_1,
    ACTION_BTN_2,
    ACTION_BTN_3,
    CAST_BTN_ADD_H,
    CAST_BTN_ADD_W,
    CAST_BTN_ADD_X,
    CAST_BTN_ADD_Y,
    CAST_BTN_AUDIO,
    CAST_BTN_BORDER,
    CAST_BTN_SUB_H,
    CAST_BTN_SUB_W,
    CAST_BTN_SUB_X,
    CAST_BTN_SUB_Y,
    CAST_TEXTBOX_H,
    CAST_TEXTBOX_W,
    CAST_TEXTBOX_X,
    CAST_TEXTBOX_Y,
    DATA_TABLE_H,
    DATA_TABLE_W,
    DATE_SWITCH,
    DISPLAY_MODE_H,
    DISPLAY_MODE_W,
    FORMAT_SWITCH,
    MASK_ACTION_BTN_1,
    MASK_ACTION_BTN_2,
    MASK_BTN_ADD_X,
    MASK_BTN_ADD_Y,
    MASK_BTN_EYE,
    MASK_BTN_SUB_X,
    MASK_BTN_SUB_Y,
    MASK_TEXTBOX_X,
    MASK_TEXTBOX_Y,
    MODE_TITLE,
    TEXT_INPUT,
    TOGGLE_MASK,
)
from .settings_skin import SettingsSkin

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Values a widget carries so its slot can be a named method rather than a
#: closure over a loop variable — ``feedback_no_lambdas``.
_ACTION_PROPERTY = "trcc_action"
_ENTRY_PROPERTY = "trcc_entry"
_DELTA_PROPERTY = "trcc_delta"
_AXIS_PROPERTY = "trcc_axis"


class ChoiceSwitch(QWidget):
    """Choices side by side, the chosen one highlighted -- the overlay
    editor's °C | °F, 12H | 24H, — | <unit> and its four date formats.
    Clicking another picks it."""

    #: The choice picked, by its place from the left.
    picked = Signal(int)

    def __init__(self, labels: Sequence[str], parent: QWidget | None = None, *,
                 skin: SettingsSkin) -> None:
        super().__init__(parent)
        log.debug("ChoiceSwitch: %s", " | ".join(labels))
        self._skin = skin
        self._labels = tuple(labels)
        self._chosen = 0
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_labels(self, *labels: str) -> None:
        """New words for the choices -- a reading's own unit."""
        log.debug("ChoiceSwitch.set_labels: %s", " | ".join(labels))
        self._labels = labels
        self.update()

    def set_choice(self, index: int) -> None:
        """Show choice *index* as the chosen one -- sends nothing."""
        log.debug("ChoiceSwitch.set_choice: %s", self._labels[index])
        self._chosen = index
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        index = min(len(self._labels) - 1,
                    int(event.position().x() * len(self._labels) / self.width()))
        log.info("ChoiceSwitch: %s clicked", self._labels[index])
        if index != self._chosen:
            self.set_choice(index)
            self.picked.emit(index)

    def paintEvent(self, event: QPaintEvent) -> None:
        frame_log.debug("ChoiceSwitch.paintEvent: %s chosen",
                        self._labels[self._chosen])
        w, h, n = self.width(), self.height(), len(self._labels)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill = QPainterPath()
        pill.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.fillPath(pill, QColor(self._skin.switch_bg))
        p.setClipPath(pill)
        p.fillRect(QRectF(w * self._chosen / n, 0, w / n, h), QColor(self._skin.switch_on))
        p.setClipping(False)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        for i, label in enumerate(self._labels):
            p.setPen(QColor(self._skin.switch_on_text if i == self._chosen
                            else self._skin.switch_off_text))
            p.drawText(QRect(w * i // n, 0, w // n, h), Qt.AlignmentFlag.AlignCenter, label)
        p.end()


class DataTablePanel(QFrame):
    """Data selection table (matches UCXiTongXianShiTable 230x54).

    Windows shows different controls depending on the selected element mode:
    - Hardware (mode 0): a temperature gets the °C | °F switch -- the one
      temperature unit, as the About panel's radios set it; any other reading
      the — | <its unit> switch, the C#'s button0 (show/hide the unit glyph,
      myModeSub 0↔1) in the same look
    - Time    (mode 1): the 12H | 24H switch
    - Weekday (mode 2): no controls
    - Date    (mode 3): the Y/M/D | D/M/Y | M/D | D/M switch
    - Custom  (mode 4): textBox1 — custom text input
    """

    format_changed = Signal(int, int)  # mode, mode_sub
    text_changed = Signal(str)
    #: "C" or "F": the temperature unit, picked on a temperature element.
    temp_unit_changed = Signal(str)

    def __init__(self, parent=None, *, skin: SettingsSkin):
        super().__init__(parent)
        self._skin = skin
        self.setFixedSize(DATA_TABLE_W, DATA_TABLE_H)

        skin.background(self, skin.table_bg, DATA_TABLE_W, DATA_TABLE_H,
                        fallback_style=f"background-color: {skin.panel_bg}; border-radius: 5px;")

        # — | <unit> (mode 0, any reading but a temperature) -- the C#'s
        # button0, its unit switch: myModeSub 1 draws the number + unit, 0 the
        # bare number (the unit baked into the theme art).  Windows: (80, 15)
        # 70x24, drawn as P单位开关 / P单位开关a; the same switch as °C | °F now.
        self.unit_switch = ChoiceSwitch(("—", "%"), self, skin=skin)
        self.unit_switch.setGeometry(*FORMAT_SWITCH)
        say_tip(self.unit_switch, "Show the unit after the value")
        self.unit_switch.picked.connect(self._on_unit_picked)
        self.unit_switch.setVisible(False)

        # A temperature's °C | °F -- the one temperature unit (SetTempUnit),
        # the About panel's radios and this switch always the same answer.
        # Legacy had it; the cutover left it inert, then it was gone.
        self.temp_switch = ChoiceSwitch(("°C", "°F"), self, skin=skin)
        self.temp_switch.setGeometry(*FORMAT_SWITCH)
        say_tip(self.temp_switch, "Temperature unit")
        self.temp_switch.picked.connect(self._on_temp_picked)
        self.temp_switch.setVisible(False)
        self._temp_unit = "C"

        # 12H | 24H (mode 1: time) -- myModeSub 1 = 12H, 2 = 24H.
        self.time_switch = ChoiceSwitch(("12H", "24H"), self, skin=skin)
        self.time_switch.setGeometry(*FORMAT_SWITCH)
        say_tip(self.time_switch, "Time format (12h/24h)")
        self.time_switch.picked.connect(self._on_time_picked)
        self.time_switch.setVisible(False)

        # The four date formats (mode 3: date) -- myModeSub 1..4, the C#'s
        # button3 cycling P YMD / DMY / MD / DM, all four shown at once now.
        self.date_switch = ChoiceSwitch(("Y/M/D", "D/M/Y", "M/D", "D/M"), self, skin=skin)
        self.date_switch.setGeometry(*DATE_SWITCH)
        say_tip(self.date_switch, "Date format")
        self.date_switch.picked.connect(self._on_date_picked)
        self.date_switch.setVisible(False)

        # textBox1 — custom text input (mode 4: custom)
        # Windows: (15, 15) 200x22
        self.text_input = QLineEdit(self)
        self.text_input.setGeometry(*TEXT_INPUT)
        self.text_input.setStyleSheet(self._skin.input_field)
        self.text_input.setPlaceholderText("Text/Value")
        say_tip(self.text_input, "Custom text")
        self.text_input.setMaxLength(100)
        self.text_input.editingFinished.connect(self._on_text_committed)
        self.text_input.setVisible(False)

        self._current_mode = -1
        self._mode_sub = 0

    def _hide_all(self):
        self.unit_switch.setVisible(False)
        self.temp_switch.setVisible(False)
        self.time_switch.setVisible(False)
        self.date_switch.setVisible(False)
        self.text_input.setVisible(False)

    def show_temp_unit(self, unit: str) -> None:
        """The App's temperature unit, changed here or in any UI -- shown on
        the switch; sends nothing."""
        log.debug("show_temp_unit: %s", unit)
        self._temp_unit = unit
        self.temp_switch.set_choice(unit == "F")

    def _on_text_committed(self) -> None:
        """The custom-text field lost focus or was committed."""
        text = self.text_input.text()
        log.info("_on_text_committed: text=%r", text)
        self.text_changed.emit(text)

    def set_mode(self, mode, mode_sub=0, temperature: bool = False, unit: str = ""):
        """Show the control for the selected element: a *temperature* gets
        the °C | °F switch, any other reading — | its *unit*."""
        log.debug("set_mode: mode=%s mode_sub=%s temperature=%s unit=%r", mode,
                  mode_sub, temperature, unit)
        self._current_mode = mode
        self._mode_sub = mode_sub
        self._hide_all()

        match mode:
            case OverlayMode.HARDWARE if temperature:
                self.temp_switch.set_choice(self._temp_unit == "F")
                self.temp_switch.setVisible(True)
            case OverlayMode.HARDWARE:
                self.unit_switch.set_labels("—", unit or "?")
                self.unit_switch.set_choice(bool(self._mode_sub))
                self.unit_switch.setVisible(True)
            case OverlayMode.TIME:
                self.time_switch.set_choice(self._mode_sub != 1)
                self.time_switch.setVisible(True)
            case OverlayMode.WEEKDAY:
                pass  # No controls
            case OverlayMode.DATE:
                if self._mode_sub == 0:
                    self._mode_sub = 1  # Default to YMD
                self.date_switch.set_choice(self._mode_sub - 1)
                self.date_switch.setVisible(True)
            case OverlayMode.CUSTOM:
                self.text_input.setVisible(True)

    def _on_unit_picked(self, side: int) -> None:
        """The bare number (mode_sub 0) or number + unit (1): this element's."""
        self._mode_sub = side
        log.info("_on_unit_picked: mode_sub=%s", side)
        self.format_changed.emit(self._current_mode, self._mode_sub)

    def _on_temp_picked(self, side: int) -> None:
        """°C or °F picked: the App's one temperature unit."""
        unit = "F" if side else "C"
        log.info("_on_temp_picked: %s", unit)
        self._temp_unit = unit
        self.temp_unit_changed.emit(unit)

    def _on_time_picked(self, side: int) -> None:
        """12H (mode_sub 1) or 24H (mode_sub 2) picked: this clock's own."""
        self._mode_sub = 2 if side else 1
        log.info("_on_time_picked: mode_sub=%s", self._mode_sub)
        self.format_changed.emit(self._current_mode, self._mode_sub)

    def _on_date_picked(self, index: int) -> None:
        """One of the four date formats picked (mode_sub 1..4): this clock's."""
        self._mode_sub = index + 1
        log.info("_on_date_picked: mode_sub=%s", self._mode_sub)
        self.format_changed.emit(self._current_mode, self._mode_sub)


class DisplayModePanel(QFrame):
    """Display mode toggle panel (351x100).

    Background image (localized P01) provides labels.
    Controls are invisible click targets over baked-in text.
    """

    mode_changed = Signal(str, bool)
    action_requested = Signal(str)

    def __init__(self, mode_id, actions: list[str] | None = None, parent=None, *,
                 skin: SettingsSkin):
        super().__init__(parent)
        self._skin = skin
        self.mode_id = mode_id
        self.actions: list[str] = actions or []

        self.setFixedSize(DISPLAY_MODE_W, DISPLAY_MODE_H)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(skin.panel_bg))
        self.setPalette(palette)
        skin.background(self, skin.mode_bg)

        self._setup_ui()

    # Tooltip text for action buttons
    _TOOLTIP_MAP = {
        "Image": "Load image from file",
        "Video": "Load video/GIF from file",
        "Load": "Load mask overlay",
        "Clear": "Clear mask",
        "VideoLoad": "Load video for playback",
        "VideoUrl": "Play a web video or live stream (http, https, rtsp)",
        "GIF": "Load animated GIF",
        "Network": "Network stream",
        "Settings": "Settings",
        "Upload": "Upload custom mask",
        "Box": "A module's picture in a box over the theme",
    }

    # Tooltip text for toggle buttons by mode
    _TOGGLE_TOOLTIP = {
        "background": "Enable background display",
        "screencast": "Enable screen capture",
        "video": "Enable video playback",
        "mask": "Toggle mask overlay",
    }

    def _setup_ui(self):
        # Toggle button — small slider for all panels
        self.toggle_btn = QPushButton(self)
        self.toggle_btn.setGeometry(*TOGGLE_MASK)
        on_px = self._skin.pixmap(self._skin.toggle_on, 36, 18)
        off_px = self._skin.pixmap(self._skin.toggle_off, 36, 18)

        self.toggle_btn.setCheckable(True)
        if not on_px.isNull() and not off_px.isNull():
            icon = QIcon()
            icon.addPixmap(on_px, QIcon.Mode.Normal, QIcon.State.On)
            icon.addPixmap(off_px, QIcon.Mode.Normal, QIcon.State.Off)
            self.toggle_btn.setIcon(icon)
            self.toggle_btn.setIconSize(self.toggle_btn.size())
        self.toggle_btn.setStyleSheet(self._skin.flat_button)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        say_tip(self.toggle_btn, self._TOGGLE_TOOLTIP.get(self.mode_id, "Toggle"))
        self.toggle_btn.clicked.connect(self._on_toggle)

        # Title label next to toggle
        self._title_lbl = QLabel(self.mode_id.title(), self)
        self._title_lbl.setGeometry(*MODE_TITLE)
        self._title_lbl.setStyleSheet(self._skin.mode_title)

        # Action buttons with icon images
        _ICON_MAP = ACTION_ICON_IMAGES
        self._action_buttons: list[QPushButton] = []
        action_positions = [ACTION_BTN_1, ACTION_BTN_2,
                            ACTION_BTN_3]
        for i, action_name in enumerate(self.actions):
            if i >= len(action_positions):
                break
            btn = QPushButton(self)
            btn.setGeometry(*action_positions[i])
            if (icon_name := _ICON_MAP.get(action_name)):
                btn.setStyleSheet(self._skin.flat_button_hover)
                px = self._skin.pixmap(icon_name, 40, 40)
                if not px.isNull():
                    btn.setIcon(QIcon(px))
                    btn.setIconSize(btn.size())
            else:
                # An action of ours with no Windows icon says what it is.
                log.debug("_setup_ui: %s has no icon -- a text button", action_name)
                btn.setText(action_name)
                btn.setStyleSheet(self._skin.action_text_button)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            say_tip(btn, self._TOOLTIP_MAP.get(action_name, action_name))
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

    def _make_entry(self, x, y, w, h):
        """Create a numeric coordinate entry field (0-9999)."""
        entry = QLineEdit(self)
        entry.setGeometry(x, y, w, h)
        entry.setText("0")
        entry.setAlignment(Qt.AlignmentFlag.AlignRight)
        entry.setStyleSheet(self._skin.entry)
        entry.setValidator(QIntValidator(0, 9999, entry))
        return entry

    def _make_pm_btn(self, x, y, w, h, delta, entry):
        """Create a +/- button for a coordinate."""
        btn = QPushButton(self)
        btn.setGeometry(x, y, w, h)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        img_name = self._skin.plus if delta > 0 else self._skin.minus
        pix = self._skin.pixmap(img_name, w, h)
        if not pix.isNull():
            btn.setIcon(QIcon(pix))
            btn.setIconSize(btn.size())
            btn.setStyleSheet(self._skin.flat_button)
        else:
            btn.setText("+" if delta > 0 else "-")
            btn.setStyleSheet(self._skin.stepper_fallback)
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
            self._skin.background(self, scaled)


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
    _TEXTBOX_X = MASK_TEXTBOX_X
    _TEXTBOX_Y = MASK_TEXTBOX_Y

    # +/- button positions
    _BTN_ADD_X = MASK_BTN_ADD_X
    _BTN_SUB_X = MASK_BTN_SUB_X
    _BTN_ADD_Y = MASK_BTN_ADD_Y
    _BTN_SUB_Y = MASK_BTN_SUB_Y

    # Eye toggle position
    _BTN_EYE = MASK_BTN_EYE

    def __init__(self, parent=None, *, skin: SettingsSkin):
        super().__init__("mask", ["Load", "Upload"], parent, skin=skin)
        # Reposition action buttons to align with left-side text labels
        if len(self._action_buttons) >= 1:
            self._action_buttons[0].setGeometry(*MASK_ACTION_BTN_1)  # Load/Masks
        if len(self._action_buttons) >= 2:
            self._action_buttons[1].setGeometry(*MASK_ACTION_BTN_2)  # Upload
        self._updating = False
        self._mask_visible = True
        self._setup_mask_ui()

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
        say_tip(self.eye_btn, "Toggle mask visibility")
        self.eye_btn.clicked.connect(self._on_eye_toggle)
        self._update_eye_icon()

    def _make_label(self, text, x, y):
        """Create a small coordinate label."""
        lbl = QLabel(text, self)
        lbl.setGeometry(x, y, 10, 16)
        lbl.setStyleSheet(self._skin.mode_label)
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
        img = self._skin.border_active if self._mask_visible else self._skin.border
        pix = self._skin.pixmap(img, 24, 16)
        if not pix.isNull():
            self.eye_btn.setIcon(QIcon(pix))
            self.eye_btn.setIconSize(self.eye_btn.size())
            self.eye_btn.setStyleSheet(self._skin.flat_button)
        else:
            self.eye_btn.setText("V" if self._mask_visible else "H")
            self.eye_btn.setStyleSheet(self._skin.flag_on_fallback if self._mask_visible
                                       else self._skin.flag_off_fallback)

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
        """Show ``mask_visible`` -- on the eye AND the switch, one fact both
        dispatch (``SetMaskVisible``), as the C#'s one ``myMbxs``."""
        log.debug("set_mask_visible: %s", visible)
        self._mask_visible = visible
        self._update_eye_icon()
        self.set_enabled(visible)


class ScreenCastPanel(DisplayModePanel):
    """Screen cast panel with X/Y/W/H coordinate inputs.

    Extends DisplayModePanel with coordinate entry fields, +/- buttons,
    border toggle, and aspect ratio locking.

    Matches Windows UCTouPingXianShi layout within 351x100.  The region and
    the border flag are the App's (``LcdSnapshot.screencast_rect``); the panel
    shows them and says when the user changed one.
    """

    #: A finished edit -- Enter, focus leaving a field, or a +/- click -- of
    #: the region on screen, ``(x, y, w, h)``.  Not per keystroke: a half-typed
    #: "3" of "320" is not a region anyone asked for.
    region_edited = Signal(int, int, int, int)
    #: The border button: True hides the on-screen frame (the C#'s ``myYcbk``).
    border_toggled = Signal(bool)
    audio_toggled = Signal(bool)

    # Positions from Windows UCTouPingXianShi.cs
    _TEXTBOX_X = CAST_TEXTBOX_X
    _TEXTBOX_Y = CAST_TEXTBOX_Y
    _TEXTBOX_W = CAST_TEXTBOX_W
    _TEXTBOX_H = CAST_TEXTBOX_H

    _BTN_ADD_X = CAST_BTN_ADD_X
    _BTN_SUB_X = CAST_BTN_SUB_X
    _BTN_ADD_Y = CAST_BTN_ADD_Y
    _BTN_SUB_Y = CAST_BTN_SUB_Y
    _BTN_ADD_W = CAST_BTN_ADD_W
    _BTN_SUB_W = CAST_BTN_SUB_W
    _BTN_ADD_H = CAST_BTN_ADD_H
    _BTN_SUB_H = CAST_BTN_SUB_H

    _BTN_BORDER = CAST_BTN_BORDER
    _BTN_AUDIO = CAST_BTN_AUDIO


    def __init__(self, parent=None, *, skin: SettingsSkin):
        super().__init__("screencast", [], parent, skin=skin)
        self._updating = False
        self._hide_border = True
        self._aspect_lock = True
        self._shown: tuple[int, int, int, int] = (0, 0, 0, 0)
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
            field.editingFinished.connect(self._commit)

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
        say_tip(self.border_btn, "Toggle capture border")
        self.border_btn.clicked.connect(self._on_border_toggle)
        self._update_border_icon()

        # Audio visualization toggle button
        self._audio_on = False
        self.audio_btn = QPushButton(self)
        self.audio_btn.setGeometry(*self._BTN_AUDIO)
        self.audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        say_tip(self.audio_btn, "Toggle mic audio visualization")
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

        if self._aspect_lock and which in ('w', 'h'):
            # ONE derivation, shared with qtgui -- core.geometry, in either
            # direction.  The typed edge leads; only the other field is
            # rewritten, so the cursor in the one being typed stays put.  No
            # device yet (None) or a 0x0 answer leaves the region alone.
            _, _, w, h = lock_region_to_panel(
                self._resolution, *self.values(),
                keep="width" if which == 'w' else "height")
            follower, value = ((self.entry_h, h) if which == 'w'
                               else (self.entry_w, w))
            self._updating = True
            follower.setText(str(value))
            self._updating = False
            log.debug("_on_coord_changed: aspect lock %s=%d on %s -> %sx%s",
                      which, val, self._resolution, w, h)

    def values(self) -> tuple[int, int, int, int]:
        """The region the fields show, ``(x, y, w, h)`` -- what Start sends.

        The entries' ``QIntValidator(0, 9999)`` admits digits only, so an
        empty field is the one non-number and reads as 0.
        """
        x, y, w, h = (int(e.text() or '0') for e in
                      (self.entry_x, self.entry_y, self.entry_w, self.entry_h))
        log.debug("values: x=%d y=%d w=%d h=%d", x, y, w, h)
        return x, y, w, h

    def _increment(self, entry, delta):
        """A +/- click is a finished edit, as in the C# (one delegate per click)."""
        log.debug("_increment: %s %+d", entry.property(_AXIS_PROPERTY), delta)
        super()._increment(entry, delta)
        self._commit()

    def _commit(self) -> None:
        """Emit ``region_edited`` when the fields differ from what was shown.

        ``editingFinished`` also fires when focus merely leaves a field, so an
        unchanged region sends nothing.
        """
        region = self.values()
        log.debug("_commit: %s (shown %s)", region, self._shown)
        if region != self._shown:
            self._shown = region
            self.region_edited.emit(*region)

    def _on_border_toggle(self):
        log.info("_on_border_toggle: hide_border=%s→%s",
                 self._hide_border, not self._hide_border)
        self._hide_border = not self._hide_border
        self._update_border_icon()
        self.border_toggled.emit(self._hide_border)

    def _update_border_icon(self):
        # The C#'s ``P显示边框A`` is the image for ``myYcbk`` True -- hidden.
        img = self._skin.border_active if self._hide_border else self._skin.border
        pix = self._skin.pixmap(img, 24, 16)
        if not pix.isNull():
            self.border_btn.setIcon(QIcon(pix))
            self.border_btn.setIconSize(self.border_btn.size())
            self.border_btn.setStyleSheet(self._skin.flat_button)
        else:
            self.border_btn.setText("B" if self._hide_border else "b")
            self.border_btn.setStyleSheet(self._skin.flag_on_fallback if self._hide_border
                                          else self._skin.flag_off_fallback)

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
        self._shown = self.values()

    def set_resolution(self, width: int, height: int) -> None:
        """The canvas a cast fills (``LcdSnapshot.screencast_canvas``) -- the
        aspect the region locks to."""
        log.debug("set_resolution: %s -> %dx%d", self._resolution, width, height)
        self._resolution = (width, height)

    def set_aspect_lock(self, enabled):
        self._aspect_lock = enabled

    def set_hide_border(self, hide: bool) -> None:
        """Show the App's border flag without emitting ``border_toggled``."""
        log.debug("set_hide_border: %s -> %s", self._hide_border, hide)
        self._hide_border = hide
        self._update_border_icon()

    @property
    def audio_on(self) -> bool:
        """The mic button's state -- what Start sends as ``audio``."""
        log.debug("audio_on: %s", self._audio_on)
        return self._audio_on

    def set_audio(self, on: bool) -> None:
        """Show a cast's microphone state without emitting ``audio_toggled``."""
        log.info("set_audio: %s -> %s", self._audio_on, on)
        self._audio_on = on
        self._update_audio_icon()

    def _on_audio_toggle(self):
        log.info("_on_audio_toggle")
        self._audio_on = not self._audio_on
        self._update_audio_icon()
        self.audio_toggled.emit(self._audio_on)

    def _update_audio_icon(self):
        self.audio_btn.setText("🎤" if self._audio_on else "🔇")
        self.audio_btn.setStyleSheet(self._skin.sound_on if self._audio_on
                                     else self._skin.sound_off)


class InsetDialog(QDialog):
    """The Background panel's Box: a module's picture in a box over the
    theme -- which module (or Off), the corner, the size.  Opened on what
    the App holds; it sends nothing itself (:meth:`choice`)."""

    def __init__(self, modules: Sequence[tuple[str, str]], module: str,
                 corner: str, size: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        log.info("InsetDialog: %d module(s), now %r %s %s", len(modules), module,
                 corner, size)
        self.setWindowTitle("Module picture in a box")
        self._module = self._combo((("", "Off"), *modules), module)
        self._corner = self._combo(tuple(
            (c.value, c.value.replace("-", " ").capitalize()) for c in InsetCorner),
            corner)
        self._size = self._combo(tuple((z.value, z.value.capitalize())
                                       for z in InsetSize), size)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("Module:", self._module)
        form.addRow("Corner:", self._corner)
        form.addRow("Size:", self._size)
        form.addRow(buttons)

    def _combo(self, items: Sequence[tuple[str, str]], current: str) -> QComboBox:
        """A combo of ``(value, label)`` items, on *current*."""
        combo = QComboBox(self)
        for value, label in items:
            combo.addItem(label, userData=value)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        log.debug("InsetDialog._combo: %d item(s), on %r", len(items), current)
        return combo

    def choice(self) -> tuple[str, str, str]:
        """The module ("" for Off), the corner and the size chosen."""
        chosen = (str(self._module.currentData()), str(self._corner.currentData()),
                  str(self._size.currentData()))
        log.info("InsetDialog.choice: %s", chosen)
        return chosen
