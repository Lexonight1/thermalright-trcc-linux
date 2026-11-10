"""SettingsSkin — how the device Settings panel LOOKS, as a port.

The look concern alone (his rule, 2026-11-10: a GUI is only a skin): every
colour, stylesheet and image the panel draws with, and how an image is loaded.
No geometry (``settings_layout``), no behaviour (``ThemeSettings``).

A port, filled by dependency injection: the panel knows this shape and nothing
else, and each GUI builds its own from its own look and hands it in -- the gui
from its ``Colors`` / ``Styles`` / ``Assets`` (``settings_look.gui_skin``),
hudgui from its blue glass.  Every value stays in its GUI's one home.  No field
has a default, so a GUI that misses one fails at construction instead of
drawing in someone else's colours.  Image NAMES that come from core (an action
button's icon, an element mode's picture) are not here: they name the C#'s
assets, and each skin's ``pixmap`` decides how they look.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtGui import QPixmap


@dataclass(frozen=True, slots=True)
class SettingsSkin:
    """Every look the Settings panel draws with -- injected, never imported."""

    # ── Loading ────────────────────────────────────────────────────────
    #: ``(name, width, height) -> QPixmap``; null when the image is missing.
    pixmap: Callable[..., QPixmap]
    #: ``(widget, name, width=None, height=None, fallback_style=None)``:
    #: the image painted once behind *widget*, or the fallback style.
    background: Callable[..., Any]

    # ── Images ─────────────────────────────────────────────────────────
    toggle_on: str
    toggle_off: str
    plus: str
    minus: str
    hue_thumb: str          # the hue strip's slider
    square_ring: str        # the colour square's selector
    eyedropper: str
    border: str             # a mask's eye / a cast's border, off
    border_active: str      # ... on
    grid_bg: str            # behind the element grid
    colour_bg: str          # behind the colour picker
    add_bg: str             # behind the add-element panel
    table_bg: str           # behind the format row
    mode_bg: str            # behind each of the four source panels

    # ── Colours ────────────────────────────────────────────────────────
    base_bg: str            # the grid when its image is missing
    panel_bg: str           # a sub-panel when its image is missing
    text: str               # the font button's name
    empty_text: str         # an empty cell's "+"
    cell_bg: str            # an element cell when its mode image is missing
    cell_select: str        # a selected cell's frame when its image is missing
    switch_bg: str          # a format switch's pill
    switch_on: str          # ... its chosen segment
    switch_on_text: str
    switch_off_text: str
    #: Whether the hue strip paints its own rainbow -- the gui's is in the
    #: picture behind it; a skin with no such picture needs it drawn.
    hue_rainbow: bool
    #: Whether the colour picker labels its X / Y boxes -- the gui's letters
    #: are in the picture behind them.
    coordinate_letters: bool
    #: The colour picker's fixed swatches, (r, g, b).
    preset_colours: tuple[tuple[int, int, int], ...]

    # ── Stylesheets ────────────────────────────────────────────────────
    flat_button: str
    flat_button_hover: str
    input_field: str
    rgb_input: str
    icon_button_hover: str
    add_button: str
    action_text_button: str     # an action with no icon (the Background's Box)
    font_select: str            # the font family select and its list
    #: A colour swatch, ``{r}`` ``{g}`` ``{b}`` filled in.
    swatch: str
    entry: str                  # a mask / cast coordinate box
    mode_title: str
    mode_label: str
    #: A text label on the panel, ``{pt}`` its point size.
    label: str
    # When an image is missing the control says what it is in text instead.
    grid_toggle_fallback: str
    stepper_fallback: str
    flag_on_fallback: str       # the eye / border buttons, lit
    flag_off_fallback: str
    sound_on: str               # the cast's sound button (always text)
    sound_off: str
