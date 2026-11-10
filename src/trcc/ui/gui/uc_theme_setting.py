"""
PyQt6 UCThemeSetting - Settings container with sub-panels.

Matches Windows TRCC.DCUserControl.UCThemeSetting (732x661)
Contains overlay editor, color picker, and display mode panels.

Windows layout (from UCThemeSetting.resx):
- ucXiTongXianShi1:     (10, 1)   472x430  - Overlay grid
- ucXiTongXianShiColor1: (492, 1)  230x374  - Color picker
- ucXiTongXianShiAdd1:   (492, 1)  230x430  - Add element (stacked)
- ucXiTongXianShiTable1: (492, 376) 230x54  - Data table
- ucMengBanXianShi1:     (10, 441) 351x100  - Mask display toggle
- ucBeiJingXianShi1:     (371, 441) 351x100 - Background display toggle
- ucTouPingXianShi1:     (10, 551) 351x100  - Screen cast toggle
- ucShiPingBoFangQi1:    (371, 551) 351x100 - Video player toggle

Sub-panel classes live in dedicated modules; this file is the thin orchestrator
plus backward-compatible re-exports.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from functools import partial
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget

from ...core.commands import (
    AddOverlayElement,
    Command,
    DeleteOverlayElement,
    UpdateOverlayElement,
)
from ...core.logs import per_frame
from ...core.models import OverlayElementConfig, OverlayMode
from ..presentation.overlay_serialization import config_fields, entries_to_configs
from ..presentation.theme_settings import SettingsView
from .base import BasePanel
from .color_and_add_panels import AddElementPanel, ColorPickerPanel
from .display_mode_panels import (
    DataTablePanel,
    DisplayModePanel,
    MaskPanel,
    ScreenCastPanel,
)

# ============================================================================
# Re-exports — all external imports continue working
# ============================================================================
from .overlay_element import (  # noqa: F401
    CATEGORY_COLORS,
    CATEGORY_NAMES,
    DATE_FORMATS,
    MODE_IMAGES,
    SELECT_IMAGE,
    SUB_METRICS,
    TIME_FORMATS,
    OverlayElementWidget,
)
from .overlay_grid import OverlayGridPanel
from .settings_layout import (
    BG_PANEL,
    DATA_TABLE,
    MASK_PANEL,
    OVERLAY_GRID,
    RIGHT_STACK,
    SCREENCAST_PANEL,
    SETTING_H,
    SETTING_W,
    VIDEO_PANEL,
)
from .settings_skin import SettingsSkin

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

# ============================================================================
# Main settings container
# ============================================================================

class UCThemeSetting(BasePanel):
    """
    Settings container with overlay editor and display mode panels.

    Windows size: 732x661
    Uses absolute positioning matching Windows UCThemeSetting layout.
    """

    CMD_BACKGROUND_TOGGLE = 1
    CMD_BACKGROUND_LOAD_IMAGE = 49
    CMD_BACKGROUND_LOAD_VIDEO = 50
    CMD_SCREENCAST_TOGGLE = 2
    CMD_VIDEO_TOGGLE = 3
    CMD_MASK_TOGGLE = 96
    CMD_MASK_LOAD = 97
    CMD_MASK_UPLOAD = 98
    CMD_MASK_CLOUD = 99  # C# buttonYDMB_Click — navigate to cloud masks panel
    CMD_MASK_POSITION = 100
    CMD_MASK_VISIBILITY = 101
    CMD_VIDEO_LOAD = 10
    CMD_VIDEO_URL = 11     # ours: a web source for the media player
    CMD_OVERLAY_CHANGED = 128
    CMD_COLOR_REMEMBER = 130   # info = (r, g, b) an edit ended on
    CMD_INSET = 140        # ours: a module's picture in a box over the theme
    CMD_EYEDROPPER = 112  # Matches Windows cmd for FormGetColor

    overlay_changed = Signal(dict)
    background_changed = Signal(bool)
    screencast_changed = Signal(bool)
    eyedropper_requested = Signal()  # launch eyedropper color picker
    #: "C" or "F" picked on a temperature element -- the App's one unit.
    temp_unit_changed = Signal(str)

    def __init__(self, parent=None, *, skin: SettingsSkin):
        super().__init__(parent, width=SETTING_W, height=SETTING_H)
        self._skin = skin
        self._setup_ui()

    def update_metrics(self, readings: Any, temp_unit: str) -> None:
        """A metrics tick for the overlay grid's tiles (#301)."""
        frame_log.debug("UCThemeSetting.update_metrics: %d reading(s)",
                        len(readings))
        self.overlay_grid.update_metrics(readings, temp_unit)

    def _setup_ui(self):
        """Build UI with absolute positioning matching Windows."""
        # Overlay grid
        self.overlay_grid = OverlayGridPanel(self, skin=self._skin)
        self.overlay_grid.move(*OVERLAY_GRID)
        self.overlay_grid.element_selected.connect(self._on_element_selected)
        self.overlay_grid.add_requested.connect(self._on_add_requested)
        self.overlay_grid.element_added.connect(self._on_grid_element_added)
        self.overlay_grid.element_deleted.connect(self._on_element_deleted)

        # Right panel stack — Color picker and Add element share this spot
        self.right_stack = QStackedWidget(self)
        self.right_stack.setGeometry(*RIGHT_STACK)

        self.color_panel = ColorPickerPanel(skin=self._skin)
        self.color_panel.color_changed.connect(self._on_color_changed)
        self.color_panel.edit_finished.connect(self._on_color_edit_finished)
        self.color_panel.position_changed.connect(self._on_position_changed)
        self.color_panel.font_changed.connect(self._on_font_changed)
        self.color_panel.eyedropper_requested.connect(self.eyedropper_requested.emit)
        self.right_stack.addWidget(self.color_panel)

        self.add_panel = AddElementPanel(skin=self._skin)
        self.add_panel.element_added.connect(self._on_element_added)
        self.right_stack.addWidget(self.add_panel)

        self.right_stack.setCurrentWidget(self.color_panel)

        # Data table
        self.data_table = DataTablePanel(self, skin=self._skin)
        self.data_table.move(*DATA_TABLE)
        self.data_table.format_changed.connect(self._on_format_changed)
        self.data_table.text_changed.connect(self._on_text_changed)
        self.data_table.temp_unit_changed.connect(self.temp_unit_changed)

        # Display mode panels
        self.mask_panel = MaskPanel(self, skin=self._skin)
        self.mask_panel.move(*MASK_PANEL)
        self.mask_panel.mode_changed.connect(self._on_mode_changed)
        self.mask_panel.action_requested.connect(self._on_action_requested)
        self.mask_panel.mask_position_changed.connect(self._on_mask_position)
        self.mask_panel.mask_visibility_toggled.connect(self._on_mask_visibility)

        self.background_panel = DisplayModePanel("background", ["Image", "Video", "Box"],
                                                 self, skin=self._skin)
        self.background_panel.move(*BG_PANEL)
        self.background_panel.mode_changed.connect(self._on_mode_changed)
        self.background_panel.action_requested.connect(self._on_action_requested)

        self.screencast_panel = ScreenCastPanel(self, skin=self._skin)
        self.screencast_panel.move(*SCREENCAST_PANEL)
        self.screencast_panel.mode_changed.connect(self._on_mode_changed)

        self.video_panel = DisplayModePanel("video", ["VideoLoad", "VideoUrl"], self,
                                            skin=self._skin)
        self.video_panel.move(*VIDEO_PANEL)
        self.video_panel.mode_changed.connect(self._on_mode_changed)
        self.video_panel.action_requested.connect(self._on_action_requested)

    # --- Element selection / editing ---

    def _on_element_selected(self, index, config: OverlayElementConfig):
        """Element was clicked — show its properties in color panel."""
        log.info("_on_element_selected: index=%s id=%s", index, config.id)
        self.right_stack.setCurrentWidget(self.color_panel)
        self._show_element(config)

    def _on_color_edit_finished(self, r: int, g: int, b: int) -> None:
        """The last element's colour edit ended: the App remembers it."""
        log.info("_on_color_edit_finished: (%d,%d,%d)", r, g, b)
        self.invoke_delegate(self.CMD_COLOR_REMEMBER, (r, g, b))

    def _show_element(self, config: OverlayElementConfig) -> None:
        """Put one element's properties on the side panel.  Sends nothing
        but the colour an edit on the previous element ended on -- the C#
        saves it right here, before showing the next (UCThemeSetting.cs:179)."""
        log.debug("_show_element: %s %s at (%d, %d)", config.id,
                  config.mode.name, config.x, config.y)
        self.color_panel.end_edit()
        self.color_panel.set_position(config.x, config.y)
        self.color_panel.set_color_hex(config.color)
        self.color_panel.set_font_display(config.font_name, config.font_size,
                                              config.font_style)
        unit = _unit_of(config)
        self.data_table.set_mode(config.mode, config.mode_sub,
                                 temperature=unit == "°C", unit=unit)
        if config.mode == OverlayMode.CUSTOM:
            self.data_table.text_input.setText(config.text)

    def show_temp_unit(self, unit: str) -> None:
        """The App's temperature unit, from any UI -- shown on the °C | °F
        switch; sends nothing."""
        log.debug("show_temp_unit: %s", unit)
        self.data_table.show_temp_unit(unit)

    def _on_add_requested(self):
        """Empty cell clicked — show add panel."""
        log.info("_on_add_requested")
        self.right_stack.setCurrentWidget(self.add_panel)

    def _on_element_added(self, config):
        """New element type selected from add panel."""
        log.info("_on_element_added")
        self.right_stack.setCurrentWidget(self.color_panel)
        self.overlay_grid.add_element(config)
        # Select the newly added element
        idx = len(self.overlay_grid.get_all_configs()) - 1
        cfg = self.overlay_grid.get_selected_config()
        if cfg:
            self._on_element_selected(idx, cfg)

    def _on_grid_element_added(self, config: OverlayElementConfig) -> None:
        """A cell was added — the App gains that element, under the cell's id."""
        fields = config_fields(config)
        log.info("_on_grid_element_added: %s %s", config.id, config.mode.name)
        if fields is not None:
            self._send_edit(AddOverlayElement, element_id=config.id, **fields)

    def _on_element_deleted(self, config: OverlayElementConfig) -> None:
        """A cell was deleted — the App drops that element, by id."""
        log.info("_on_element_deleted: %s", config.id)
        self.right_stack.setCurrentWidget(self.color_panel)
        self._send_edit(DeleteOverlayElement, element_id=config.id)

    def _send_edit(self, command: Callable[..., Command], **fields: Any) -> None:
        """Hand ONE element edit to the window, which adds the device key.

        The panel is shared by every LCD and knows no key; the active
        handler does (``LCDHandler.on_overlay_edit``).
        """
        log.info("_send_edit: %s %s", command.__name__, fields)
        self.invoke_delegate(self.CMD_OVERLAY_CHANGED, partial(command, **fields))

    def _update_selected(self, require_mode: OverlayMode | None = None, **fields):
        """Update selected overlay element config fields and propagate.

        Single entry point for all element property changes (color, position,
        font, format, text). Guards on require_mode when the update only
        applies to a specific element type.
        """
        idx = self.overlay_grid.get_selected_index()
        cfg = self.overlay_grid.get_selected_config()
        if cfg is None:
            log.info("_update_selected: NO element selected — %s ignored "
                     "(click a box in the grid first)", fields)
            return
        if require_mode is not None and cfg.mode != require_mode:
            log.info("_update_selected: selected element is %s, not %s — "
                     "%s skipped", cfg.mode.name, require_mode.name, fields)
            return
        before = {k: getattr(cfg, k, None) for k in fields}
        was = config_fields(cfg) or {}
        for k, v in fields.items():
            setattr(cfg, k, v)
        # The transition the user actually wants to see in the log, e.g.
        # "element[0] HARDWARE 'CPU' (hw 0/1): color #808080 → #ff0000".
        label = cfg.text or f"hw {cfg.main_count}/{cfg.sub_count}"
        log.info(
            "_update_selected: element[%s] %s %r: %s",
            idx, cfg.mode.name, label,
            ", ".join(f"{k} {before[k]} → {v}" for k, v in fields.items()),
        )
        self.overlay_grid.update_element(idx, cfg)
        # Only what differs: a field this cell cannot represent (a format
        # outside the table, bold AND italic) is never rewritten by an edit
        # to something else.
        changed = {k: v for k, v in (config_fields(cfg) or {}).items()
                   if was.get(k) != v}
        if changed:
            self._send_edit(UpdateOverlayElement, element_id=cfg.id, **changed)

    def _on_color_changed(self, r, g, b):
        log.debug("_on_color_changed: r=%d, g=%d, b=%d", r, g, b)
        self._update_selected(color=f'#{r:02x}{g:02x}{b:02x}')

    def _on_position_changed(self, x, y):
        log.debug("_on_position_changed: x=%d, y=%d", x, y)
        self._update_selected(x=x, y=y)

    def _on_font_changed(self, font_name, font_size, font_style):
        log.debug("_on_font_changed: %s %s %s", font_name, font_size, font_style)
        self._update_selected(font_name=font_name, font_size=font_size,
                              font_style=font_style)

    def _on_format_changed(self, mode, mode_sub):
        log.debug("_on_format_changed: mode=%s, mode_sub=%s", mode, mode_sub)
        # The selected element's format and nothing else: the C# keeps it per
        # element (myModeSub) with no global one.  This used to also set a
        # global preference on EVERY device.  HARDWARE's button0 is the same
        # per-element field, the unit switch.
        self._update_selected(require_mode=mode, mode_sub=mode_sub)

    def _on_text_changed(self, text):
        log.info("_on_text_changed: text=%s", text)
        self._update_selected(require_mode=OverlayMode.CUSTOM, text=text)

    # --- Display mode panels ---

    def _on_mode_changed(self, mode_id, enabled):
        log.info("_on_mode_changed: mode_id=%s enabled=%s", mode_id, enabled)
        if mode_id == "background":
            if enabled:
                self.screencast_panel.set_enabled(False)
                self.video_panel.set_enabled(False)
            self.background_changed.emit(enabled)
            self.invoke_delegate(self.CMD_BACKGROUND_TOGGLE, enabled)
        elif mode_id == "screencast":
            if enabled:
                self.background_panel.set_enabled(False)
                self.video_panel.set_enabled(False)
            self.screencast_changed.emit(enabled)
            self.invoke_delegate(self.CMD_SCREENCAST_TOGGLE, enabled)
        elif mode_id == "video":
            if enabled:
                self.background_panel.set_enabled(False)
                self.screencast_panel.set_enabled(False)
            self.invoke_delegate(self.CMD_VIDEO_TOGGLE, enabled)
        elif mode_id == "mask":
            self.invoke_delegate(self.CMD_MASK_TOGGLE, enabled)

    def _on_mask_position(self, x, y):
        """Forward mask position change to main app."""
        log.info("_on_mask_position: x=%s y=%s", x, y)
        self.invoke_delegate(self.CMD_MASK_POSITION, (x, y))

    def _on_mask_visibility(self, visible):
        """Forward mask visibility toggle to main app."""
        log.info("_on_mask_visibility: visible=%s", visible)
        self.invoke_delegate(self.CMD_MASK_VISIBILITY, visible)

    def _on_action_requested(self, action_name):
        log.info("_on_action_requested: action_name=%s", action_name)
        action_map = {
            "Image": self.CMD_BACKGROUND_LOAD_IMAGE,
            "Video": self.CMD_BACKGROUND_LOAD_VIDEO,
            "Load": self.CMD_MASK_LOAD,
            "Upload": self.CMD_MASK_UPLOAD,
            "VideoLoad": self.CMD_VIDEO_LOAD,
            "VideoUrl": self.CMD_VIDEO_URL,
            "Box": self.CMD_INSET,
        }
        cmd = action_map.get(action_name)
        if cmd:
            self.invoke_delegate(cmd)

    # --- Public API ---

    def get_all_configs(self):
        return self.overlay_grid.get_all_configs()

    def load_configs(self, configs):
        """Show a layout; a still-selected element re-shows its properties,
        which another UI may just have changed."""
        log.debug("load_configs: %d cell(s)", len(configs))
        self.overlay_grid.load_configs(configs)
        if (selected := self.overlay_grid.get_selected_config()) is not None:
            self._show_element(selected)

    def set_overlay_enabled(self, enabled: bool):
        self.overlay_grid.set_overlay_enabled(enabled)

    def set_canvas(self, width: int, height: int) -> None:
        """The size elements are placed on; bounds the X / Y inputs."""
        log.info("UCThemeSetting.set_canvas: %dx%d", width, height)
        self.color_panel.set_position_range(width, height)

    def set_mask_position(self, x: int, y: int):
        """Update mask panel X/Y fields (e.g. after mask load or drag)."""
        self.mask_panel.set_position(x, y)

    def set_mask_visible(self, visible: bool):
        """Update mask panel eye toggle state."""
        self.mask_panel.set_mask_visible(visible)

    def show(self, view: SettingsView) -> None:
        """Draw *view* -- everything the App holds for the panel on screen.
        Sends nothing.  The one way this panel is filled: every GUI's Settings
        panel draws the same :class:`SettingsView`."""
        log.info("UCThemeSetting.show: %s overlay=%s %d element(s)", view.key,
                 view.overlay_on, len(view.elements))
        self.set_overlay_enabled(view.overlay_on)
        self.load_configs(entries_to_configs(view.elements))
        self.set_mask_visible(view.mask_visible)
        self.set_mask_position(*view.mask_position)
        self.show_sources(view.source, view.background_drawn)
        cast = self.screencast_panel
        if view.cast_canvas is not None:
            cast.set_resolution(*view.cast_canvas)
        if view.cast_rect is not None:
            x, y, w, h = view.cast_rect
            cast.set_values(x=x, y=y, w=w, h=h)
        cast.set_hide_border(view.cast_hide_border)
        if view.cast_audio is not None:     # off a cast it is this window's choice
            cast.set_audio(view.cast_audio)
        self.color_panel.set_recent_colors(view.recent_colours)
        self.show_temp_unit(view.temp_unit)

    def show_sources(self, source: str, background_drawn: bool) -> None:
        """Show which display source is on the panel, sending nothing.

        The C#'s one UI-mode choice (``FormCZTV.ThemeSetting``): background,
        screencast or video player, one at a time.  *source* is the App's
        ``LcdSnapshot.display_source``; the background switch is also off
        when no background is drawn (``background_mode == 'transparent'``).
        """
        log.info("show_sources: %s background_drawn=%s", source, background_drawn)
        self.background_panel.set_enabled(source == "background" and background_drawn)
        self.screencast_panel.set_enabled(source == "screencast")
        self.video_panel.set_enabled(source == "media")


def _unit_of(config: OverlayElementConfig) -> str:
    """A hardware element's unit -- what its format draws after the value:
    ``°C`` (a temperature, the one the renderer turns into °F), ``%``,
    ``MHz``, ``RPM`` ... -- "" for none or for another kind of element."""
    fields = config_fields(config) if config.mode is OverlayMode.HARDWARE else None
    unit = re.sub(r"\{[^}]*\}", "", str(fields.get("format", ""))).strip() if fields else ""
    log.debug("_unit_of: %s -> %r", config.id, unit)
    return unit
