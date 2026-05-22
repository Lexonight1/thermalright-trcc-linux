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

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget

from ...core.models import OverlayElementConfig, OverlayMode
from .base import BasePanel
from .color_and_add_panels import AddElementPanel, ColorPickerPanel
from .constants import Layout, Sizes
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

log = logging.getLogger(__name__)

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
    CMD_OVERLAY_CHANGED = 128
    CMD_EYEDROPPER = 112  # Matches Windows cmd for FormGetColor

    overlay_changed = Signal(dict)
    background_changed = Signal(bool)
    screencast_changed = Signal(bool)
    screencast_params_changed = Signal(int, int, int, int)  # x, y, w, h
    eyedropper_requested = Signal()  # launch eyedropper color picker
    capture_requested = Signal()     # launch screen capture

    def __init__(self, parent=None):
        super().__init__(parent, width=Sizes.SETTING_W, height=Sizes.SETTING_H)
        self._setup_ui()

    def _setup_ui(self):
        """Build UI with absolute positioning matching Windows."""
        # Overlay grid
        self.overlay_grid = OverlayGridPanel(self)
        self.overlay_grid.move(*Layout.OVERLAY_GRID)
        self.overlay_grid.element_selected.connect(self._on_element_selected)
        self.overlay_grid.add_requested.connect(self._on_add_requested)
        self.overlay_grid.element_deleted.connect(self._on_element_deleted)
        self.overlay_grid.elements_changed.connect(self._on_elements_changed)

        # Right panel stack — Color picker and Add element share this spot
        self.right_stack = QStackedWidget(self)
        self.right_stack.setGeometry(*Layout.RIGHT_STACK)

        self.color_panel = ColorPickerPanel()
        self.color_panel.color_changed.connect(self._on_color_changed)
        self.color_panel.position_changed.connect(self._on_position_changed)
        self.color_panel.font_changed.connect(self._on_font_changed)
        self.color_panel.eyedropper_requested.connect(self.eyedropper_requested.emit)
        self.right_stack.addWidget(self.color_panel)

        self.add_panel = AddElementPanel()
        self.add_panel.element_added.connect(self._on_element_added)
        self.right_stack.addWidget(self.add_panel)

        self.right_stack.setCurrentWidget(self.color_panel)

        # Data table
        self.data_table = DataTablePanel(self)
        self.data_table.move(*Layout.DATA_TABLE)
        self.data_table.format_changed.connect(self._on_format_changed)
        self.data_table.text_changed.connect(self._on_text_changed)

        # Display mode panels
        self.mask_panel = MaskPanel(self)
        self.mask_panel.move(*Layout.MASK_PANEL)
        self.mask_panel.mode_changed.connect(self._on_mode_changed)
        self.mask_panel.action_requested.connect(self._on_action_requested)
        self.mask_panel.mask_position_changed.connect(self._on_mask_position)
        self.mask_panel.mask_visibility_toggled.connect(self._on_mask_visibility)

        self.background_panel = DisplayModePanel("background", ["Image", "Video"], self)
        self.background_panel.move(*Layout.BG_PANEL)
        self.background_panel.mode_changed.connect(self._on_mode_changed)
        self.background_panel.action_requested.connect(self._on_action_requested)

        self.screencast_panel = ScreenCastPanel(self)
        self.screencast_panel.move(*Layout.SCREENCAST_PANEL)
        self.screencast_panel.mode_changed.connect(self._on_mode_changed)
        self.screencast_panel.screencast_params_changed.connect(self._on_screencast_params)
        self.screencast_panel.capture_requested.connect(self.capture_requested.emit)

        self.video_panel = DisplayModePanel("video", ["VideoLoad"], self)
        self.video_panel.move(*Layout.VIDEO_PANEL)
        self.video_panel.mode_changed.connect(self._on_mode_changed)
        self.video_panel.action_requested.connect(self._on_action_requested)

    # --- Element selection / editing ---

    def _on_element_selected(self, index, config: OverlayElementConfig):
        """Element was clicked — show its properties in color panel."""
        self.right_stack.setCurrentWidget(self.color_panel)
        self.color_panel.set_position(config.x, config.y)
        self.color_panel.set_color_hex(config.color)
        self.color_panel.set_font_display(config.font_name, config.font_size,
                                              config.font_style)
        self.data_table.set_mode(config.mode, config.mode_sub)
        if config.mode == OverlayMode.CUSTOM:
            self.data_table.text_input.setText(config.text)

    def _on_add_requested(self):
        """Empty cell clicked — show add panel."""
        self.right_stack.setCurrentWidget(self.add_panel)

    def _on_element_added(self, config):
        """New element type selected from add panel."""
        self.right_stack.setCurrentWidget(self.color_panel)
        self.overlay_grid.add_element(config)
        # Select the newly added element
        idx = len(self.overlay_grid.get_all_configs()) - 1
        cfg = self.overlay_grid.get_selected_config()
        if cfg:
            self._on_element_selected(idx, cfg)

    def _on_element_deleted(self, index):
        """Element was deleted."""
        self.right_stack.setCurrentWidget(self.color_panel)

    def _on_elements_changed(self):
        """Any change to elements list — notify parent via delegate."""
        config = self.overlay_grid.to_overlay_config()
        log.debug("_on_elements_changed: %d elements, invoking CMD_OVERLAY_CHANGED",
                  len(config) if config else 0)
        self.invoke_delegate(self.CMD_OVERLAY_CHANGED, config)

    def _update_selected(self, require_mode: OverlayMode | None = None, **fields):
        """Update selected overlay element config fields and propagate.

        Single entry point for all element property changes (color, position,
        font, format, text). Guards on require_mode when the update only
        applies to a specific element type.
        """
        idx = self.overlay_grid.get_selected_index()
        cfg = self.overlay_grid.get_selected_config()
        log.debug("_update_selected: idx=%s, cfg=%s, require_mode=%s, fields=%s",
                  idx, cfg.mode if cfg else None, require_mode, fields)
        if cfg is None:
            return
        if require_mode is not None and cfg.mode != require_mode:
            log.debug("_update_selected: mode mismatch %s != %s, skipping",
                      cfg.mode, require_mode)
            return
        for k, v in fields.items():
            setattr(cfg, k, v)
        self.overlay_grid.update_element(idx, cfg)
        self._on_elements_changed()

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
        self._update_selected(require_mode=mode, mode_sub=mode_sub)
        # Persist format preference so it carries across theme changes
        from ...conf import Settings
        if mode == OverlayMode.TIME:
            Settings.save_format_pref('time_format', mode_sub)
        elif mode == OverlayMode.DATE:
            Settings.save_format_pref('date_format', mode_sub)
        elif mode == OverlayMode.HARDWARE:
            Settings.save_format_pref('temp_unit', mode_sub)

    def _on_text_changed(self, text):
        self._update_selected(require_mode=OverlayMode.CUSTOM, text=text)

    # --- Display mode panels ---

    def _on_mode_changed(self, mode_id, enabled):
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

    def _on_screencast_params(self, x, y, w, h):
        """Forward screencast coordinate changes."""
        self.screencast_params_changed.emit(x, y, w, h)

    def _on_mask_position(self, x, y):
        """Forward mask position change to main app."""
        self.invoke_delegate(self.CMD_MASK_POSITION, (x, y))

    def _on_mask_visibility(self, visible):
        """Forward mask visibility toggle to main app."""
        self.invoke_delegate(self.CMD_MASK_VISIBILITY, visible)

    def _on_action_requested(self, action_name):
        action_map = {
            "Image": self.CMD_BACKGROUND_LOAD_IMAGE,
            "Video": self.CMD_BACKGROUND_LOAD_VIDEO,
            "Load": self.CMD_MASK_LOAD,
            "Upload": self.CMD_MASK_UPLOAD,
            "VideoLoad": self.CMD_VIDEO_LOAD,
        }
        cmd = action_map.get(action_name)
        if cmd:
            self.invoke_delegate(cmd)

    # --- Public API ---

    def get_all_configs(self):
        return self.overlay_grid.get_all_configs()

    def load_configs(self, configs):
        self.overlay_grid.load_configs(configs)

    def to_overlay_config(self):
        return self.overlay_grid.to_overlay_config()

    def load_from_overlay_config(self, overlay_config):
        self.overlay_grid.load_from_overlay_config(overlay_config)

    def set_overlay_enabled(self, enabled: bool):
        self.overlay_grid.set_overlay_enabled(enabled)

    def set_mask_position(self, x: int, y: int):
        """Update mask panel X/Y fields (e.g. after mask load or drag)."""
        self.mask_panel.set_position(x, y)

    def set_mask_visible(self, visible: bool):
        """Update mask panel eye toggle state."""
        self.mask_panel.set_mask_visible(visible)

    def set_resolution(self, width: int, height: int):
        """Delegate resolution to screencast panel."""
        self.screencast_panel.set_resolution(width, height)
