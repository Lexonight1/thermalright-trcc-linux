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

    # A full-window page (About, RGB Lighting, Thanks, Modules): its title
    # bar's gold and its body's frame
    PAGE_GOLD = '#b49653'
    PAGE_FRAME = '#434343'

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
    PLACEHOLDER_BG = (40, 40, 45)  # RGB tuple

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

    # The tiles and their grid are the shared grid's own (``ui/tile_grid``).
    GRID_SCROLL_Y = 50
    GRID_SCROLL_H = 602  # PANEL_H - GRID_SCROLL_Y

    # Filter buttons
    FILTER_BTN_W = 63
    FILTER_BTN_H = 18
    FILTER_BTN_Y = 29

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


class Layout:
    """Absolute (x, y, w, h) tuples for setGeometry() calls.

    All values from Windows InitializeComponent().
    Unpack with: widget.setGeometry(*Layout.SOME_RECT)
    """

    # Device sidebar
    SIDEBAR = (0, 0, 180, 800)
    FORM_CONTAINER = (180, 0, 1274, 800)

    # A full-window page's gold title bar and framed body, in its own
    # coordinates (``base.paint_page``)
    PAGE_TITLE_BAR = (10, 20, 1254, 48)
    PAGE_BODY = (12, 98, 1250, 680)

    # Form1-level buttons (on central, visible in sensor/home view only)
    # Windows: buttonPower at (1392, 24), buttonHelp at (1342, 24)
    FORM1_CLOSE_BTN = (1392, 24, 40, 40)
    FORM1_HELP_BTN = (1342, 24, 40, 40)

    # Preview (within form_container)
    PREVIEW = (16, 88, 500, 560)

    # Panel stack (within form_container)
    PANEL_STACK = (532, 128, 732, 652)

    # Mode tab buttons (within form_container)
    TAB_LOCAL = (542, 90, 50, 38)
    TAB_MASK = (612, 90, 50, 38)
    TAB_CLOUD = (682, 90, 50, 38)
    TAB_SETTINGS = (882, 90, 50, 38)
    #: Where a module's tab goes, in the order modules claim them -- the
    #: spacing of the C#'s tabs, before Settings.  TRCC's own, not the C#'s:
    #: the Audio Visualizer's audio-bars tab takes the first.
    TAB_MODULE_SLOTS = ((752, 90, 50, 38), (822, 90, 50, 38))

    # Device fingerprint line — selectable/copyable, beneath the rotation row
    # (preview ends y=648; the bottom control row sits at y=680).
    DEVICE_INFO = (16, 712, 500, 44)

    # Bottom control row (within form_container) -- FormCZTV 2.1.8: the
    # brightness slider ``ucScrollB1`` (164, 680) 180x24, the theme name
    # ``textBoxCMM`` (378, 684) 102x16, save ``buttonBCZT`` (482, 680) 24x24.
    # 2.1.8 hid the form's export / import (y=880) and kept the pair on the
    # local-theme panel (UCThemeLocal 441,28 / 482,28), where the gui has them.
    ROTATION_COMBO = (39, 680, 108, 24)
    BRIGHTNESS_SLIDER = (164, 680, 180, 24)
    THEME_NAME_INPUT = (378, 684, 102, 16)
    SAVE_BTN = (482, 680, 24, 24)

    # Title bar buttons (within form_container)
    HELP_BTN = (1162, 24, 40, 40)
    CLOSE_BTN = (1212, 24, 40, 40)

    # Sensor button (within sidebar)
    SENSOR_BTN = (25, 100, 140, 50)
    ABOUT_BTN = (25, 730, 140, 50)
    #: Where a module's view button goes in the sidebar, above About, in the
    #: order modules claim them -- RGB Lighting's.  TRCC's own, not the C#'s;
    #: the device list scrolls in what is left.
    SIDEBAR_MODULE_SLOTS = ((25, 670, 140, 50),)
    DEVICE_AREA = (0, 160, 180, 500)
    NO_DEVICES_LABEL = (25, 10, 140, 50)
    HINT_LABEL = (15, 55, 150, 40)

    # UCThemeLocal filter buttons
    LOCAL_BTN_ALL = (21, 29, 63, 18)

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

    # Video controls
    PLAY_BTN = (10, 26, 34, 26)
    HEIGHT_FIT_BTN = (64, 26, 34, 26)   # C# buttonTPJCH
    WIDTH_FIT_BTN = (108, 26, 34, 26)   # C# buttonTPJCW
    TIME_LABEL = (274, 26, 220, 20)
    PROGRESS_SLIDER = (10, 5, 479, 16)

    # UCSystemInfoOptions (sensor grid) — Windows: (190, 98) on Form1, size 1254x692
    # Sibling of FormCZTV on Form1, not a child
    SYSINFO_PANEL = (190, 98, 1254, 692)

    # UCAbout / Control Center panel
    ABOUT_CLOSE_BTN = (1212, 24, 40, 40)
    ABOUT_STARTUP = (297, 174, 14, 14)
    ABOUT_CELSIUS = (297, 214, 14, 14)
    ABOUT_FAHRENHEIT = (387, 214, 14, 14)
    ABOUT_HDD = (297, 254, 14, 14)
    ABOUT_REFRESH_INPUT = (299, 291, 36, 16)
    ABOUT_WEBSITE = (94, 726, 353, 43)
    ABOUT_VERSION = (1150, 735, 87, 27)
    ABOUT_UPDATE_BTN = (297, 373, 48, 26)

    # Running Mode radio buttons (v2.1.4: buttonSingle / buttonMulti)
    ABOUT_SINGLE_THREAD = (297, 334, 14, 14)
    ABOUT_MULTI_THREAD = (567, 334, 14, 14)

    # Language selection checkboxes (within UCAbout) — v2.1.4 coordinates
    ABOUT_LANG_BUTTONS = [
        # (x, y, lang_code)
        (297, 413, 'en'),      # English
        (387, 413, 'de'),      # Deutsch
        (477, 413, 'ru'),      # Русский
        (567, 413, 'fr'),      # Français
        (657, 413, 'pt'),      # Português
        (297, 443, 'ja'),      # 日本語
        (387, 443, 'es'),      # Español
        (477, 443, 'zh'),      # 中文简体
        (567, 443, 'zh_TW'),   # 中文繁體
        (657, 443, 'ko'),      # 한국어
    ]
    ABOUT_GPU_COMBO = (297, 456, 250, 28)
    ABOUT_CHECKBOX_SIZE = 14

    # Re-export from i18n — single source of truth for locale→suffix mapping
    from ...core.i18n import LOCALE_TO_LANG


class Styles:
    """Reusable stylesheet fragments."""

    FLAT_BUTTON = "QPushButton { background-color: transparent; border: none; }"

    FLAT_BUTTON_HOVER = """
        QPushButton { background-color: transparent; border: none; }
        QPushButton:hover { background-color: rgba(255, 255, 255, 15); }
    """

    #: An action of ours with no Windows icon: its name, drawn like the
    #: white outlined icons beside it.

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

    # A widget that must not paint its own background, so a parent's QPalette
    # image shows through.  Ancestor stylesheets block palette backgrounds
    # (see the note in uc_device), which is why this is set per-child rather
    # than inherited.
    TRANSPARENT_BG = "background: transparent;"

    # Preview status text — one colour, two sizes.  The OVER_IMAGE variant
    # sits on top of the preview bitmap and must not paint a backdrop.
    STATUS_LABEL = f"color: {Colors.STATUS_TEXT}; font-size: 11px;"
    STATUS_LABEL_OVER_IMAGE = (
        f"color: {Colors.STATUS_TEXT}; font-size: 10px; "
        f"background: transparent;"
    )

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
