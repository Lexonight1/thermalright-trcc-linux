"""hudgui's look for the device Settings panel -- its own styles, injected.

The gui's panel, its layout and its behaviour; hudgui's look: the blue glass
of its bar and dropped panels, its text colour, its accent, and its chrome
images in neutral grey (``qt_assets.Assets.pixmap``) where the gui's are gold.
Built here, from hudgui's own values, and handed to the shared panel as a
``SettingsSkin`` -- the gui's look is never imported, nor this one by the gui.
"""
from __future__ import annotations

import logging
from functools import cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget

from ..qt_assets import Assets
from ..settings_panel.settings_skin import SettingsSkin

log = logging.getLogger(__name__)

#: hudgui's own values -- the glass, its text, its accent.
TEXT = "#dfe5ea"
ACCENT = "#7fd4ff"
GLASS = "rgba(255, 255, 255, 14)"
GLASS_EDGE = "rgba(255, 255, 255, 30)"
FIELD = ("background-color: rgba(0, 0, 0, 90); color: #dfe5ea; "
         "border: 1px solid rgba(255, 255, 255, 40); border-radius: 3px;")
_GLASS_NAME = "hudSettingsGlass"


def _pixmap(name: object, width: int | None = None, height: int | None = None) -> QPixmap:
    """hudgui's chrome: the image in neutral grey, at the size asked."""
    pixmap = Assets.pixmap(name) if isinstance(name, str) else QPixmap()
    log.debug("hud _pixmap: %s %sx%s", name, width, height)
    if width and height and not pixmap.isNull():
        pixmap = pixmap.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    return pixmap


def _glass(widget: QWidget, _image: object, _width: int | None = None,
           _height: int | None = None, fallback_style: str | None = None) -> None:
    """Behind a sub-panel: hudgui's glass, not the gui's painted background."""
    log.debug("hud _glass: %s", type(widget).__name__)
    widget.setObjectName(_GLASS_NAME)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setStyleSheet(f"#{_GLASS_NAME} {{ background: {GLASS}; "
                         f"border: 1px solid {GLASS_EDGE}; border-radius: 6px; }}")


@cache
def hud_skin() -> SettingsSkin:
    """hudgui's Settings look -- one instance, shared by every panel."""
    log.debug("hud_skin: built")
    hover = (f"QPushButton {{ background-color: transparent; border: none; }}"
             f"QPushButton:hover {{ background-color: {GLASS}; border-radius: 4px; }}")
    flag = ("QPushButton {{ background: {}; color: #0b1620; border: none; "
            "border-radius: 3px; font-size: {}px; }}")
    return SettingsSkin(
        pixmap=_pixmap,
        background=_glass,
        toggle_on='shared_toggle_on.png',
        toggle_off='shared_toggle_off.png',
        plus='display_mode_plus.png',
        minus='display_mode_minus.png',
        hue_thumb='color_panel_slider_thumb.png',
        square_ring='color_panel_selector_ring.png',
        eyedropper='color_panel_eyedropper.png',
        border='display_mode_border.png',
        border_active='display_mode_border_active.png',
        grid_bg='', colour_bg='', add_bg='', table_bg='', mode_bg='',
        base_bg="transparent",
        panel_bg="transparent",
        text=TEXT,
        empty_text="rgba(223, 229, 234, 90)",
        cell_bg="rgba(255, 255, 255, 18)",
        cell_select=ACCENT,
        switch_bg="rgba(255, 255, 255, 30)",
        switch_on=ACCENT,
        switch_on_text="#0b1620",
        switch_off_text=TEXT,
        hue_rainbow=True,           # no picture behind the strip
        coordinate_letters=True,
        preset_colours=((255, 255, 255), (192, 192, 192), (127, 212, 255), (0, 120, 215),
                        (0, 200, 120), (255, 214, 0), (255, 140, 0), (232, 17, 35),
                        (180, 0, 158), (255, 0, 255), (0, 0, 0)),
        flat_button="QPushButton { background-color: transparent; border: none; }",
        flat_button_hover=hover,
        input_field=FIELD,
        rgb_input=FIELD,
        icon_button_hover=hover,
        add_button=(f"QPushButton {{ background-color: {GLASS}; color: {TEXT}; "
                    f"border: 1px solid {GLASS_EDGE}; padding: 8px; border-radius: 4px; "
                    f"text-align: left; }}"
                    f"QPushButton:hover {{ border-color: {ACCENT}; }}"),
        action_text_button=(f"QPushButton {{ background-color: transparent; color: {TEXT}; "
                            f"border: 1px solid {ACCENT}; border-radius: 5px; }}"
                            f"QPushButton:hover {{ background-color: {GLASS}; }}"
                            f"QPushButton:disabled {{ color: gray; border-color: gray; }}"),
        # A field with its arrow, so it reads as a select, not as text.
        font_select=(f"QFontComboBox {{ background-color: rgba(0, 0, 0, 90); "
                     f"border: 1px solid rgba(255, 255, 255, 40); border-radius: 3px; "
                     f"color: {TEXT}; font-size: 10px; padding-left: 6px; }}"
                     f"QFontComboBox QAbstractItemView {{ background-color: #10202b; "
                     f"color: {TEXT}; selection-background-color: rgba(127, 212, 255, 70); }}"),
        swatch=("QPushButton {{ background-color: rgb({r},{g},{b}); border: none; "
                "border-radius: 2px; }}"
                f"QPushButton:hover {{{{ border: 1px solid {ACCENT}; }}}}"),
        entry=f"background-color: rgba(0, 0, 0, 90); color: {ACCENT}; border: none;",
        mode_title=f"color: {TEXT}; font-size: 12pt; font-weight: bold; background: transparent;",
        mode_label=f"color: {TEXT}; font-size: 9pt; background: transparent;",
        label=f"color: {TEXT}; font-size: {{pt}}pt; background: transparent;",
        grid_toggle_fallback=flag.format(ACCENT, 8),
        stepper_fallback=flag.format(GLASS, 9),
        flag_on_fallback=flag.format(ACCENT, 8),
        flag_off_fallback=flag.format("#555", 8),
        sound_on=flag.format(ACCENT, 10),
        sound_off=flag.format("#555", 10),
    )
