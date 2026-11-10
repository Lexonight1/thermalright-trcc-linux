"""The gui's look for the device Settings panel -- its own styles, injected.

The gui keeps the styles it needs: built from ITS ``Colors``, ``Styles`` and
``Assets``, the values the panel has always drawn with (gold on dark, the
Windows app's images).  The panel receives this as a ``SettingsSkin`` and
never imports a gui style itself, so another GUI hands it its own.
"""
from __future__ import annotations

import logging
from functools import cache

from .assets import Assets
from .base import set_background_pixmap
from .constants import Colors, Styles
from .settings_skin import SettingsSkin

log = logging.getLogger(__name__)


@cache
def gui_skin() -> SettingsSkin:
    """The gui's Settings look -- one instance, shared by every panel."""
    log.debug("gui_skin: built")
    return SettingsSkin(
        pixmap=Assets.load_pixmap,
        background=set_background_pixmap,
        toggle_on=Assets.TOGGLE_ON,
        toggle_off=Assets.TOGGLE_OFF,
        plus=Assets.PLUS,
        minus=Assets.MINUS,
        hue_thumb='color_panel_slider_thumb.png',
        square_ring='color_panel_selector_ring.png',
        eyedropper='color_panel_eyedropper.png',
        border='display_mode_border.png',
        border_active='display_mode_border_active.png',
        grid_bg='settings_overlay.png',
        colour_bg='settings_params.png',
        add_bg='settings_overlay_add_bg.png',
        table_bg='settings_overlay_table_bg.png',
        mode_bg='settings_background.png',
        base_bg=Colors.BASE_BG,
        panel_bg=Colors.PANEL_FALLBACK,
        text=Colors.TEXT,
        empty_text=Colors.EMPTY_TEXT,
        cell_bg='#2D2D2D',
        cell_select='white',
        switch_bg=Colors.PAGE_FRAME,
        switch_on=Colors.PAGE_GOLD,
        switch_on_text='#2b2b2b',
        switch_off_text='#aaaaaa',
        preset_colours=tuple(Colors.PRESET_COLORS),
        flat_button=Styles.FLAT_BUTTON,
        flat_button_hover=Styles.FLAT_BUTTON_HOVER,
        input_field=Styles.INPUT_FIELD,
        rgb_input=Styles.RGB_INPUT,
        icon_button_hover=Styles.ICON_BUTTON_HOVER,
        add_button=Styles.ADD_ELEMENT_BTN,
        action_text_button=Styles.ACTION_TEXT_BUTTON,
        font_button=(f"QPushButton {{ background-color: transparent; border: none; "
                     f"color: {Colors.TEXT}; font-size: 10px; text-align: left; "
                     f"padding-left: 27px; }}"),
        swatch=("QPushButton {{ background-color: rgb({r},{g},{b}); border: none; }}"
                "QPushButton:hover {{ border: 1px solid white; }}"),
        entry=("background-color: black; color: #B4964F; border: none;"
               " font-family: 'Microsoft YaHei'; font-size: 9pt;"),
        mode_title=("color: white; font-family: 'Microsoft YaHei'; font-size: 12pt;"
                    " background: transparent;"),
        mode_label=("color: white; font-family: 'Microsoft YaHei'; font-size: 9pt;"
                    " background: transparent;"),
        grid_toggle_fallback=("QPushButton { background: #4CAF50; color: white; font-size: 8px; }"
                              "QPushButton:checked { background: #4CAF50; }"
                              "QPushButton:!checked { background: #666; }"),
        stepper_fallback=("QPushButton { background: #333; color: #888; border: none; "
                          "font-size: 9px; }"),
        flag_on_fallback=("QPushButton { background: #00CED1; color: white; border: none; "
                          "font-size: 8px; }"),
        flag_off_fallback=("QPushButton { background: #555; color: white; border: none; "
                           "font-size: 8px; }"),
        sound_on=("QPushButton { background: #00CED1; color: white; border: none; "
                  "font-size: 10px; }"),
        sound_off=("QPushButton { background: #555; color: white; border: none; "
                   "font-size: 10px; }"),
    )
