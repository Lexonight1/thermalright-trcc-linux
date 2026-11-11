"""hudgui's look for the shared panels -- the device Settings panel and every
module's page -- its own styles, injected.

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

from ..module_page import ModuleLook
from ..qt_assets import Assets
from ..settings_panel.settings_skin import SettingsSkin
from ..tile_grid import TileSkin

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


def _tile_selected(cls_name: str) -> str:
    """A lit tile: hudgui's accent round it, as its theme tiles."""
    log.debug("hud _tile_selected: %s", cls_name)
    return (f"{cls_name} {{ background: rgba(127, 212, 255, 40); "
            f"border: 2px solid {ACCENT}; border-radius: 6px; }}")


def _tile_normal(cls_name: str) -> str:
    """A tile at rest: faint glass, brighter under the pointer."""
    log.debug("hud _tile_normal: %s", cls_name)
    return (f"{cls_name} {{ background: rgba(255, 255, 255, 12); "
            f"border: 2px solid transparent; border-radius: 6px; }}"
            f"{cls_name}:hover {{ background: rgba(255, 255, 255, 30); }}")


def _tile_not_local(cls_name: str) -> str:
    """A tile not yet downloaded: dashed glass."""
    log.debug("hud _tile_not_local: %s", cls_name)
    return (f"{cls_name} {{ background: rgba(255, 255, 255, 8); "
            f"border: 1px dashed {GLASS_EDGE}; border-radius: 6px; }}")


@cache
def hud_module_look() -> ModuleLook:
    """hudgui's look for a module's page: its tiles as its theme tiles, its
    controls on the glass."""
    log.debug("hud_module_look: built")
    return ModuleLook(
        tiles=TileSkin(
            image="background-color: rgba(0, 0, 0, 90); border: none; border-radius: 4px;",
            name=f"color: {TEXT}; font-size: 9px; font-weight: bold; background: transparent;",
            scroll_area=("QScrollArea { border: none; background: transparent; }"
                         "QScrollBar:vertical { background: transparent; width: 8px; }"
                         f"QScrollBar::handle:vertical {{ background: {GLASS_EDGE}; "
                         "border-radius: 4px; min-height: 20px; }"
                         "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical "
                         "{ height: 0; }"),
            selected=_tile_selected, normal=_tile_normal, not_local=_tile_not_local,
            placeholder=(16, 28, 38), placeholder_text=(150, 170, 180),
            empty_text="rgba(223, 229, 234, 90)"),
        controls=(f"QLabel {{ color: {TEXT}; background: transparent; }}"
                  f"QPushButton {{ background: {GLASS}; color: {TEXT}; "
                  f"border: 1px solid {GLASS_EDGE}; border-radius: 4px; padding: 4px 12px; }}"
                  f"QPushButton:hover {{ border-color: {ACCENT}; }}"
                  "QSlider::groove:horizontal { background: rgba(255, 255, 255, 30); "
                  "height: 6px; border-radius: 3px; }"
                  f"QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}"
                  f"QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; "
                  "margin: -4px 0; border-radius: 6px; }"),
        dimmed="rgba(223, 229, 234, 90)",
        status="color: #ffcf6e; background: transparent; font-size: 12px;",
    )
