"""
Layout constants and style definitions for PyQt6 TRCC components.

All coordinate values match Windows TRCC InitializeComponent() exactly.
Centralizes magic numbers so they're defined once and referenced everywhere.
"""


class Colors:
    """Central color palette used across all components."""

    # Dark theme base (QPalette)
    WINDOW_BG = '#232227'
    WINDOW_TEXT = '#C6C6C6'
    BASE_BG = '#1E1E1E'
    TEXT = '#C6C6C6'
    BUTTON_BG = '#3C3C3C'
    BUTTON_TEXT = '#C6C6C6'

    # Selection / accent
    ACCENT = '#4A6FA5'
    ACCENT_BORDER = '#6B8FC5'

    # Hover
    HOVER_BG = '#3A3A3A'
    HOVER_BORDER = '#555'

    # Thumbnail
    THUMB_BG = '#1A1A1A'
    THUMB_BORDER = '#333'

    # Non-local mask thumbnails (dashed border)
    NON_LOCAL_BG = '#2A2A2E'
    NON_LOCAL_BORDER = '#555'
    NON_LOCAL_HOVER_BG = '#3A3A3E'
    NON_LOCAL_HOVER_BORDER = '#777'

    # Misc
    PANEL_FALLBACK = '#2A2A2A'
    STATUS_TEXT = '#888'
    EMPTY_TEXT = '#666'
    MUTED_TEXT = '#444'
    PLACEHOLDER_BG = (40, 40, 45)  # RGB tuple for PIL

    # Device button gradients
    DEVICE_SELECTED_TOP = '#3B6B9A'
    DEVICE_SELECTED_BOTTOM = '#2A4D6E'
    DEVICE_SELECTED_BORDER = '#5B8BBA'
    DEVICE_NORMAL_TOP = '#383838'
    DEVICE_NORMAL_BOTTOM = '#2C2C2C'
    DEVICE_NORMAL_BORDER = '#444'
    DEVICE_HOVER_TOP = '#454545'
    DEVICE_HOVER_BOTTOM = '#383838'

    # Close button
    CLOSE_HOVER = '#C42B1C'

    # Overlay element states
    OVERLAY_SELECTED = 'rgba(74, 111, 165, 150)'
    OVERLAY_OCCUPIED = 'rgba(58, 58, 58, 100)'
    OVERLAY_OCCUPIED_HOVER = 'rgba(74, 74, 74, 150)'
    OVERLAY_OCCUPIED_BORDER = '#555'
    OVERLAY_OCCUPIED_HOVER_BORDER = '#666'
    OVERLAY_EMPTY_HOVER = 'rgba(50, 50, 50, 100)'

    # Preset color swatches (from Windows buttonC1-C11)
    PRESET_COLORS = [
        (224, 32, 32),    # Red
        (250, 100, 1),    # Orange
        (247, 181, 1),    # Yellow
        (109, 212, 1),    # Lime
        (68, 215, 182),   # Cyan
        (50, 197, 255),   # Light Blue
        (1, 145, 255),    # Blue
        (98, 54, 255),    # Purple
        (182, 32, 224),   # Magenta
        (255, 255, 255),  # White
        (0, 0, 0),        # Black
    ]


class Sizes:
    """Widget dimensions matching Windows component sizes."""

    # Main window
    WINDOW_W = 1454
    WINDOW_H = 800

    # Device sidebar
    SIDEBAR_W = 180
    SIDEBAR_H = 800

    # FormCZTV content area (right of sidebar)
    FORM_X = 180
    FORM_W = 1274
    FORM_H = 800

    # Preview
    PREVIEW_FRAME = 500
    PREVIEW_PANEL_H = 560  # 500 frame + 60 controls

    # Panel stack (theme browser container)
    PANEL_W = 732
    PANEL_H = 652

    # Thumbnail
    THUMB_W = 120
    THUMB_H = 140
    THUMB_IMAGE = 120
    THUMB_NAME_H = 20
    THUMB_NAME_MAX = 15
    THUMB_NAME_TRUNC = 12

    # Theme grid
    GRID_COLS = 5
    GRID_SCROLL_Y = 50
    GRID_SCROLL_H = 602  # PANEL_H - GRID_SCROLL_Y
    GRID_MARGIN = (30, 10, 0, 10)  # left, top, right, bottom
    GRID_H_SPACE = 15   # 135 - 120
    GRID_V_SPACE = 10   # 150 - 140

    # Filter buttons
    FILTER_BTN_W = 63
    FILTER_BTN_H = 18
    FILTER_BTN_Y = 29

    # Overlay grid
    OVERLAY_GRID_W = 472
    OVERLAY_GRID_H = 430
    OVERLAY_CELL = 60
    OVERLAY_X0 = 5
    OVERLAY_Y0 = 35
    OVERLAY_DX = 67
    OVERLAY_DY = 66
    OVERLAY_ROWS = 6
    OVERLAY_COLS = 7

    # Color picker
    COLOR_PANEL_W = 230
    COLOR_PANEL_H = 374

    # Add element panel
    ADD_PANEL_W = 230
    ADD_PANEL_H = 430

    # Data table
    DATA_TABLE_W = 230
    DATA_TABLE_H = 54

    # Display mode panels
    DISPLAY_MODE_W = 351
    DISPLAY_MODE_H = 100

    # Device buttons
    DEVICE_BTN_W = 140
    DEVICE_BTN_H = 50
    DEVICE_BTN_X = 25
    DEVICE_BTN_SPACING = 60
    DEVICE_AREA_Y = 160
    DEVICE_AREA_H = 560

    # Video controls
    VIDEO_CONTROLS_W = 500
    VIDEO_CONTROLS_H = 56

    # Settings panel
    SETTING_W = 732
    SETTING_H = 661


class Layout:
    """Absolute (x, y, w, h) tuples for setGeometry() calls.

    All values from Windows InitializeComponent().
    Unpack with: widget.setGeometry(*Layout.SOME_RECT)
    """

    # Device sidebar
    SIDEBAR = (0, 0, 180, 800)
    FORM_CONTAINER = (180, 0, 1274, 800)

    # Preview (within form_container)
    PREVIEW = (16, 88, 500, 560)

    # Panel stack (within form_container)
    PANEL_STACK = (532, 128, 732, 652)

    # Mode tab buttons (within form_container)
    TAB_LOCAL = (542, 90, 50, 38)
    TAB_MASK = (612, 90, 50, 38)
    TAB_CLOUD = (682, 90, 50, 38)
    TAB_SETTINGS = (882, 90, 50, 38)

    # Bottom control buttons (within form_container)
    ROTATION_COMBO = (39, 680, 108, 24)
    BRIGHTNESS_BTN = (157, 680, 52, 24)
    THEME_NAME_INPUT = (278, 684, 102, 16)
    SAVE_BTN = (383, 680, 24, 24)
    EXPORT_BTN = (412, 680, 40, 24)
    IMPORT_BTN = (453, 680, 40, 24)

    # Title bar buttons (within form_container)
    HELP_BTN = (1162, 24, 40, 40)
    CLOSE_BTN = (1212, 24, 40, 40)

    # Sensor button (within sidebar)
    SENSOR_BTN = (25, 100, 140, 50)
    ABOUT_BTN = (25, 730, 140, 50)
    DEVICE_AREA = (0, 160, 180, 560)
    NO_DEVICES_LABEL = (25, 10, 140, 50)
    HINT_LABEL = (15, 55, 150, 40)

    # UCThemeLocal filter buttons
    LOCAL_BTN_ALL = (21, 29, 63, 18)
    LOCAL_BTN_DEFAULT = (121, 29, 63, 18)
    LOCAL_BTN_USER = (221, 29, 63, 18)

    # UCThemeWeb category buttons
    WEB_CATEGORIES = [
        ('all',  21, 29, 63, 18),
        ('a',   120, 29, 63, 18),   # Gallery
        ('b',   221, 29, 63, 18),   # Tech
        ('c',   322, 29, 63, 18),   # HUD
        ('d',   421, 29, 63, 18),   # Light
        ('e',   520, 29, 63, 18),   # Nature
        ('y',   621, 29, 63, 18),   # Aesthetic
    ]

    # Scroll area (shared by all theme browsers)
    THEME_SCROLL = (0, 50, 732, 602)

    # Video controls
    PLAY_BTN = (10, 26, 34, 26)
    TIME_LABEL = (274, 26, 220, 20)
    PROGRESS_SLIDER = (10, 5, 479, 16)

    # Settings sub-panels (within UCThemeSetting)
    OVERLAY_GRID = (10, 1)
    RIGHT_STACK = (492, 1, 230, 430)
    DATA_TABLE = (492, 376)
    MASK_PANEL = (10, 441)
    BG_PANEL = (371, 441)
    SCREENCAST_PANEL = (10, 551)
    VIDEO_PANEL = (371, 551)

    # Color picker positions
    COLOR_X_SPIN = (32, 32, 53, 19)
    COLOR_Y_SPIN = (121, 32, 53, 19)
    COLOR_FONT_BTN = (12, 87, 125, 24)
    COLOR_FONT_SIZE_SPIN = (140, 89, 42, 20)
    COLOR_AREA = (8, 139, 214, 136)
    COLOR_R = (83, 304, 36, 16)
    COLOR_G = (132, 304, 36, 16)
    COLOR_B = (181, 304, 36, 16)
    COLOR_SWATCH_HISTORY_Y = 333
    COLOR_SWATCH_PRESET_Y = 354
    COLOR_SWATCH_X0 = 13
    COLOR_SWATCH_DX = 19
    COLOR_SWATCH_SIZE = 14

    # Eyedropper button (matches Windows buttonGetColor in UCXiTongXianShiColor)
    COLOR_EYEDROPPER = (12, 276, 48, 48)

    # Display mode panel toggle/action positions
    TOGGLE_DEFAULT = (0, 0, 50, 50)
    TOGGLE_MASK = (5, 5, 36, 18)
    ACTION_BTN_1 = (149, 30, 40, 40)
    ACTION_BTN_2 = (219, 30, 40, 40)

    # Add element panel buttons
    ADD_BTN_X = 12
    ADD_BTN_W = 206
    ADD_BTN_H = 35
    ADD_BTN_Y0 = 55
    ADD_BTN_DY = 42

    # UCAbout / Control Center panel
    ABOUT_CLOSE_BTN = (1212, 24, 40, 40)
    ABOUT_STARTUP = (297, 174, 14, 14)
    ABOUT_CELSIUS = (297, 214, 14, 14)
    ABOUT_FAHRENHEIT = (387, 214, 14, 14)
    ABOUT_HDD = (297, 254, 14, 14)
    ABOUT_REFRESH_INPUT = (299, 291, 36, 16)
    ABOUT_WEBSITE = (94, 726, 353, 43)
    ABOUT_VERSION = (1175, 735, 87, 27)

    # Language selection checkboxes (within UCAbout)
    ABOUT_LANG_BUTTONS = [
        # (x, y, lang_suffix)
        (297, 373, 'en'),   # English
        (387, 373, 'd'),    # Deutsch
        (477, 373, 'r'),    # Россия
        (567, 373, 'f'),    # Français
        (657, 373, 'p'),    # Português
        (297, 403, 'x'),    # 日本語
        (387, 403, 'e'),    # Español
        (477, 403, ''),     # 中文简体
        (567, 403, 'tc'),   # 中文繁體
    ]
    ABOUT_CHECKBOX_SIZE = 14


class Styles:
    """Reusable stylesheet fragments."""

    FLAT_BUTTON = "QPushButton { background-color: transparent; border: none; }"

    FLAT_BUTTON_HOVER = """
        QPushButton { background-color: transparent; border: none; }
        QPushButton:hover { background-color: rgba(255, 255, 255, 15); }
    """

    TEXT_BUTTON = """
        QPushButton {
            background: qlineargradient(
                x1:0, y1:0, x2:0, y2:1,
                stop:0 #3C3C3C, stop:1 #2E2E2E
            );
            color: #C6C6C6;
            border: 1px solid #555;
            padding: 3px 10px;
            font-size: 10px;
            border-radius: 2px;
        }
        QPushButton:hover {
            background: qlineargradient(
                x1:0, y1:0, x2:0, y2:1,
                stop:0 #4C4C4C, stop:1 #3C3C3C
            );
            color: white;
        }
    """

    ICON_BUTTON_HOVER = """
        QPushButton { background-color: transparent; border: none; }
        QPushButton:hover { background-color: rgba(255, 255, 255, 20); }
    """

    SCROLL_AREA = """
        QScrollArea { border: none; background-color: transparent; }
        QScrollBar:vertical {
            background-color: transparent; width: 10px;
        }
        QScrollBar::handle:vertical {
            background-color: #555; border-radius: 5px; min-height: 20px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    """

    THUMB_IMAGE = "background-color: #1A1A1A; border: 1px solid #333;"

    THUMB_NAME = "color: #C6C6C6; font-size: 9px; font-weight: bold; background: transparent;"

    INPUT_FIELD = (
        "background-color: rgba(51, 51, 51, 180); color: white; "
        "border: 1px solid #555; font-size: 10px;"
    )

    RGB_INPUT = (
        "background-color: rgba(51, 51, 51, 180); color: white; "
        "border: 1px solid #555; font-size: 9px;"
    )

    ADD_ELEMENT_BTN = """
        QPushButton {
            background-color: rgba(60, 60, 60, 180);
            color: #C6C6C6;
            border: 1px solid #555;
            padding: 8px;
            border-radius: 3px;
            text-align: left;
            font-size: 10px;
        }
        QPushButton:hover {
            background-color: rgba(76, 76, 76, 200);
        }
    """

    SLIDER = """
        QSlider::groove:horizontal {
            background: transparent;
            height: 6px;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #4A6FA5;
            width: 12px;
            margin: -3px 0;
            border-radius: 6px;
        }
        QSlider::sub-page:horizontal {
            background: #4A6FA5;
            border-radius: 3px;
        }
    """

    @staticmethod
    def thumb_selected(cls_name: str) -> str:
        return f"""
            {cls_name} {{
                background-color: {Colors.ACCENT};
                border: 2px solid {Colors.ACCENT_BORDER};
                border-radius: 4px;
            }}
        """

    @staticmethod
    def thumb_normal(cls_name: str) -> str:
        return f"""
            {cls_name} {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            {cls_name}:hover {{
                background-color: {Colors.HOVER_BG};
                border: 1px solid {Colors.HOVER_BORDER};
            }}
        """

    @staticmethod
    def thumb_non_local(cls_name: str) -> str:
        return f"""
            {cls_name} {{
                background-color: {Colors.NON_LOCAL_BG};
                border: 1px dashed {Colors.NON_LOCAL_BORDER};
                border-radius: 4px;
            }}
            {cls_name}:hover {{
                background-color: {Colors.NON_LOCAL_HOVER_BG};
                border: 1px dashed {Colors.NON_LOCAL_HOVER_BORDER};
            }}
        """
