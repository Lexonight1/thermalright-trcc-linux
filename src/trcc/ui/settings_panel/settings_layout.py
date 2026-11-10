"""The device Settings panel's LAYOUT -- where every control sits.

The layout concern alone (his rule, 2026-11-10: a GUI is only a skin): no
colour, no image, no behaviour.  What a control sends and shows is
``ui/presentation/theme_settings.ThemeSettings``; how it looks is the skin.
Every value is the Windows app's own geometry (``InitializeComponent()`` of
UCThemeSetting and its sub-controls), in pixels of the 732 x 661 panel or of
the sub-panel named.  Qt-free: ints and ``(x, y)`` / ``(x, y, w, h)`` tuples.

These lived in ``gui/constants.py`` (``Layout`` / ``Sizes``) beside the rest of
the gui's geometry, and 32 more were bare numbers inside the widgets.
"""
from __future__ import annotations

# ── The panel and where its sub-panels sit ─────────────────────────────

SETTING_W = 732
SETTING_H = 661

OVERLAY_GRID = (10, 1)
RIGHT_STACK = (492, 1, 230, 430)
DATA_TABLE = (492, 376)
MASK_PANEL = (10, 441)
BG_PANEL = (371, 441)
SCREENCAST_PANEL = (10, 551)
VIDEO_PANEL = (371, 551)

# ── The overlay grid: 7 x 6 cells under its switch ─────────────────────

OVERLAY_GRID_W = 472
OVERLAY_GRID_H = 430
OVERLAY_CELL = 60
OVERLAY_X0 = 5
OVERLAY_Y0 = 35
OVERLAY_DX = 67
OVERLAY_DY = 66
OVERLAY_ROWS = 6
OVERLAY_COLS = 7
#: The grid's on/off switch.
GRID_TOGGLE = (5, 5, 36, 18)
#: A cell's three text rows: its name, its live value, its unit.
CELL_NAME_ROW = (2, 1, 56, 18)
CELL_VALUE_ROW = (2, 21, 56, 18)
CELL_UNIT_ROW = (2, 41, 56, 18)

# ── The colour picker (UCXiTongXianShiColor) ───────────────────────────

COLOR_PANEL_W = 230
COLOR_PANEL_H = 374
COLOR_X_SPIN = (32, 32, 53, 19)
COLOR_Y_SPIN = (121, 32, 53, 19)
COLOR_FONT_BTN = (12, 87, 125, 24)
COLOR_FONT_SIZE_SPIN = (140, 89, 42, 20)
COLOR_AREA = (8, 139, 214, 136)        # UCColorC, the colour square
COLOR_HUE = (65, 273, 158, 19)         # UCColorB, the hue strip
COLOR_R = (83, 304, 36, 16)
COLOR_G = (132, 304, 36, 16)
COLOR_B = (181, 304, 36, 16)
COLOR_SWATCH_HISTORY_Y = 333
COLOR_SWATCH_PRESET_Y = 354
COLOR_SWATCH_X0 = 13
COLOR_SWATCH_DX = 19
COLOR_SWATCH_SIZE = 14
#: Windows buttonGetColor in UCXiTongXianShiColor.
COLOR_EYEDROPPER = (12, 276, 48, 48)

# ── The add-element panel ──────────────────────────────────────────────

ADD_PANEL_W = 230
ADD_PANEL_H = 430
ADD_BTN_X = 12
ADD_BTN_W = 206
ADD_BTN_H = 35
ADD_BTN_Y0 = 55
ADD_BTN_DY = 42

# ── The data table: the selected element's format row ──────────────────

DATA_TABLE_W = 230
DATA_TABLE_H = 54
#: The unit, °C/°F and 12h/24h switches share one place (one shows at a time).
FORMAT_SWITCH = (80, 15, 70, 24)
DATE_SWITCH = (15, 15, 200, 24)
TEXT_INPUT = (15, 15, 200, 22)

# ── The four display-mode panels ───────────────────────────────────────

DISPLAY_MODE_W = 351
DISPLAY_MODE_H = 100
TOGGLE_MASK = (5, 5, 36, 18)
MODE_TITLE = (44, 5, 140, 18)
ACTION_BTN_1 = (149, 30, 40, 40)
ACTION_BTN_2 = (219, 30, 40, 40)
ACTION_BTN_3 = (289, 30, 40, 40)   # ours: the Background panel's Box

# The Layer Mask panel: its two actions sit further left, beside X / Y.
MASK_ACTION_BTN_1 = (115, 30, 40, 40)  # Masks
MASK_ACTION_BTN_2 = (175, 30, 40, 40)  # Upload
MASK_TEXTBOX_X = (259, 40, 38, 16)
MASK_TEXTBOX_Y = (259, 65, 38, 16)
MASK_BTN_ADD_X = (301, 42, 14, 14)
MASK_BTN_SUB_X = (319, 42, 14, 14)
MASK_BTN_ADD_Y = (301, 67, 14, 14)
MASK_BTN_SUB_Y = (319, 67, 14, 14)
MASK_BTN_EYE = (309, 6, 24, 16)

# The Screencast panel: X / Y / W / H with their steppers, border and sound.
CAST_TEXTBOX_X = (110, 40, 56, 16)
CAST_TEXTBOX_Y = (110, 65, 56, 16)
CAST_TEXTBOX_W = (241, 40, 56, 16)
CAST_TEXTBOX_H = (241, 65, 56, 16)
CAST_BTN_ADD_X = (171, 42, 14, 14)
CAST_BTN_SUB_X = (189, 42, 14, 14)
CAST_BTN_ADD_Y = (171, 67, 14, 14)
CAST_BTN_SUB_Y = (189, 67, 14, 14)
CAST_BTN_ADD_W = (301, 42, 14, 14)
CAST_BTN_SUB_W = (319, 42, 14, 14)
CAST_BTN_ADD_H = (301, 67, 14, 14)
CAST_BTN_SUB_H = (319, 67, 14, 14)
CAST_BTN_BORDER = (309, 16, 24, 16)
CAST_BTN_AUDIO = (280, 16, 24, 16)
