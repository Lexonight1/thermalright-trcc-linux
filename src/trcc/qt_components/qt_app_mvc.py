"""
PyQt6 Main Application Window using MVC Architecture.

This is a View that uses FormCZTVController for all business logic.
The controller can be reused with any GUI framework (Tkinter, GTK, etc.)

Visual polish matches Windows TRCC exactly:
- QPalette backgrounds (not stylesheets) for image backgrounds
- Localized backgrounds based on system language
- Windows asset images for buttons, tabs, panels
- Exact coordinate positioning matching Windows InitializeComponent()
"""

import locale
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QPushButton,
    QStackedWidget, QFileDialog, QComboBox, QLineEdit,
    QSystemTrayIcon, QMenu,
)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QFont, QPalette, QColor, QIcon, QRegularExpressionValidator
from PyQt6.QtCore import QRegularExpression as QRE

# Import MVC core
from ..core import (
    FormCZTVController, create_controller,
    ThemeInfo, DeviceInfo, PlaybackState,
)

# Import view components
from .assets import load_pixmap, Assets
from .base import create_image_button, set_background_pixmap
from .constants import Colors, Sizes, Layout, Styles
from .uc_device import UCDevice
from .uc_preview import UCPreview
from .uc_theme_local import UCThemeLocal
from .uc_theme_web import UCThemeWeb
from .uc_theme_mask import UCThemeMask
from .uc_theme_setting import UCThemeSetting
from .uc_about import UCAbout
from .uc_system_info import UCSystemInfo
from ..sensor_enumerator import SensorEnumerator
from .uc_video_cut import UCVideoCut
from .uc_image_cut import UCImageCut
from .uc_info_module import UCInfoModule
from .uc_activity_sidebar import UCActivitySidebar

# Language code mapping: system locale -> Windows asset suffix
LOCALE_TO_LANG = {
    'zh_CN': '',     # Chinese Simplified = default (no suffix)
    'zh_TW': 'tc',   # Traditional Chinese
    'en': 'en',       # English
    'de': 'd',        # German
    'es': 'e',        # Spanish
    'fr': 'f',        # French
    'pt': 'p',        # Portuguese
    'ru': 'r',        # Russian
    'ja': 'x',        # Japanese
}


def detect_language() -> str:
    """Detect system language and return Windows asset suffix."""
    try:
        lang = locale.getdefaultlocale()[0] or 'en'
    except Exception:
        lang = 'en'

    # Try exact match first
    if lang in LOCALE_TO_LANG:
        return LOCALE_TO_LANG[lang]

    # Try language prefix (e.g., 'en_US' -> 'en')
    prefix = lang.split('_')[0]
    if prefix in LOCALE_TO_LANG:
        return LOCALE_TO_LANG[prefix]

    return 'en'  # Default to English


class TRCCMainWindowMVC(QMainWindow):
    """
    Main TRCC application window using MVC pattern.

    This View:
    - Owns the FormCZTVController for business logic
    - Renders UI with Windows-matching backgrounds via QPalette
    - Forwards user events to controller
    - Subscribes to controller callbacks for updates
    """

    def __init__(self, data_dir: Path = None, decorated: bool = False):
        super().__init__()

        self._decorated = decorated
        self._drag_pos = None
        self._force_quit = False

        self.setWindowTitle("TRCC Linux - Thermalright LCD Control Center")
        self.setFixedSize(Sizes.WINDOW_W, Sizes.WINDOW_H)

        if not decorated:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
            )

        # Create controller (business logic lives here)
        self._data_dir = data_dir or Path(__file__).parent.parent.parent / 'data'
        self.controller = create_controller(self._data_dir)

        # Animation timer (view owns timer, controller owns logic)
        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._on_animation_tick)

        # Metrics timer for live overlay updates (1s interval)
        self._metrics_timer = QTimer(self)
        self._metrics_timer.timeout.connect(self._on_metrics_tick)

        # Device hot-plug poll timer (5s interval)
        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self._on_device_poll)

        # Screencast timer (~6.67 FPS, matches Windows TPXSCount >= 3 at 50ms)
        self._screencast_timer = QTimer(self)
        self._screencast_timer.timeout.connect(self._on_screencast_tick)
        self._screencast_x = 0
        self._screencast_y = 0
        self._screencast_w = 0
        self._screencast_h = 0
        self._screencast_active = False

        # PipeWire portal capture for Wayland (GNOME/KDE)
        self._pipewire_cast = None
        self._screencast_border = True  # Windows myYcbk

        # Element flash timer (Windows shanPingTimer: ~1s blink on element select)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._on_flash_timeout)

        # Slideshow timer (Windows myLunBoTimer: auto-rotate themes)
        self._slideshow_timer = QTimer(self)
        self._slideshow_timer.timeout.connect(self._on_slideshow_tick)
        self._slideshow_index = 0

        # Language for localized backgrounds
        self._lang = detect_language()

        # Pixmap references to prevent GC
        self._pixmap_refs = []

        # Setup UI
        self._apply_dark_theme()
        self._setup_ui()
        self._connect_controller_callbacks()
        self._connect_view_signals()

        # System tray icon
        self._setup_systray()

        # Detect devices immediately, then poll every 5s
        self._on_device_poll()
        self._device_timer.start(5000)

    def _apply_dark_theme(self):
        """Apply dark theme via QPalette (not stylesheet - blocks palette on children)."""
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(Colors.WINDOW_BG))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.WINDOW_TEXT))
        palette.setColor(QPalette.ColorRole.Base, QColor(Colors.BASE_BG))
        palette.setColor(QPalette.ColorRole.Text, QColor(Colors.TEXT))
        palette.setColor(QPalette.ColorRole.Button, QColor(Colors.BUTTON_BG))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(Colors.BUTTON_TEXT))
        self.setPalette(palette)

    def _setup_systray(self):
        """Create system tray icon with context menu."""
        icon_path = Path(__file__).parent.parent.parent / 'assets' / 'icons' / 'trcc.png'
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self.setWindowIcon(icon)

        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("TRCC Linux")

        menu = QMenu()
        show_action = menu.addAction("Show/Hide")
        show_action.triggered.connect(self._toggle_visibility)
        menu.addSeparator()
        exit_action = menu.addAction("Exit")
        exit_action.triggered.connect(self._quit_app)
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        """Handle tray icon click — left-click toggles visibility."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visibility()

    def _toggle_visibility(self):
        """Toggle window visibility."""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.activateWindow()
            self.raise_()

    def _quit_app(self):
        """Quit application from tray menu."""
        self._force_quit = True
        self.close()

    def _localized(self, base_name: str) -> str:
        """Get localized asset name for current language."""
        return Assets.get_localized(base_name, self._lang)

    def _set_panel_background(self, widget: QWidget, asset_name: str):
        """Set background image on a panel via QPalette."""
        pix = set_background_pixmap(widget, asset_name)
        if pix:
            self._pixmap_refs.append(pix)

    def _setup_ui(self):
        """
        Build the main UI layout matching Windows TRCC exactly.

        Windows Layout (Form1 + FormCZTV):
        - Form1: 1454x800
        - UCDevice sidebar: x=0, width=180
        - FormCZTV content: x=180, size=1274x800
          - Preview: (16, 88) size 500x560
          - Mode tabs: y=90
          - Theme panels: (532, 128) size 732x652
          - Bottom controls: y=680
        """
        central = QWidget()
        self.setCentralWidget(central)
        # No stylesheet on central - would override QPalette on children

        # === Left: Device sidebar (180px) ===
        self.uc_device = UCDevice(central)
        self.uc_device.setGeometry(*Layout.SIDEBAR)

        # === FormCZTV content area (x=180, 1274x800) ===
        self.form_container = QWidget(central)
        self.form_container.setGeometry(*Layout.FORM_CONTAINER)

        # Set FormCZTV background image (localized)
        form_bg_name = self._localized(Assets.FORM_CZTV_BG)
        pix = set_background_pixmap(self.form_container, form_bg_name,
            fallback_style=f"background-color: {Colors.WINDOW_BG};")
        if pix:
            self._pixmap_refs.append(pix)

        # Preview
        lcd_w, lcd_h = self.controller.lcd_width, self.controller.lcd_height
        self.uc_preview = UCPreview(lcd_w, lcd_h, self.form_container)
        self.uc_preview.setGeometry(*Layout.PREVIEW)

        # Info module (compact sensor bar above preview, hidden by default)
        self.uc_info_module = UCInfoModule(self.form_container)
        self.uc_info_module.setGeometry(16, 16, 500, 70)
        self.uc_info_module.setVisible(False)

        # Image cropper (replaces preview when cropping, hidden by default)
        self.uc_image_cut = UCImageCut(self.form_container)
        self.uc_image_cut.setGeometry(16, 88, 500, 702)
        self.uc_image_cut.setVisible(False)

        # Video trimmer (replaces preview when trimming, hidden by default)
        self.uc_video_cut = UCVideoCut(self.form_container)
        self.uc_video_cut.setGeometry(16, 88, 500, 702)
        self.uc_video_cut.setVisible(False)

        # Mode tab buttons at y=90
        self._create_mode_tabs()

        # Theme panels container
        self.panel_stack = QStackedWidget(self.form_container)
        self.panel_stack.setGeometry(*Layout.PANEL_STACK)
        # No stylesheet on stack - would override QPalette on children

        # Create theme panels with localized backgrounds
        self.uc_theme_local = UCThemeLocal()
        self._set_panel_background(self.uc_theme_local, self._localized(Assets.THEME_LOCAL_BG))
        self.panel_stack.addWidget(self.uc_theme_local)

        self.uc_theme_web = UCThemeWeb()
        self._set_panel_background(self.uc_theme_web, self._localized(Assets.THEME_WEB_BG))
        self.panel_stack.addWidget(self.uc_theme_web)

        self.uc_theme_mask = UCThemeMask()
        self._set_panel_background(self.uc_theme_mask, self._localized(Assets.THEME_MASK_BG))
        self.panel_stack.addWidget(self.uc_theme_mask)

        self.uc_theme_setting = UCThemeSetting()
        self.panel_stack.addWidget(self.uc_theme_setting)

        # Activity sidebar (sensor list for overlay element addition, hidden)
        self.uc_activity_sidebar = UCActivitySidebar(self.form_container)
        self.uc_activity_sidebar.setGeometry(532, 128, 250, 500)
        self.uc_activity_sidebar.setVisible(False)

        # Bottom control buttons at y=680
        self._create_bottom_controls()

        # Title bar buttons (Help, Close)
        self._create_title_buttons()

        # Apply localized display mode backgrounds to settings panel
        self._apply_settings_backgrounds()

        # === About / Control Center panel (hidden, replaces form_container) ===
        self.uc_about = UCAbout(self._lang, central)
        self.uc_about.setGeometry(*Layout.FORM_CONTAINER)
        self.uc_about.setVisible(False)

        # === Sensor Enumerator (hardware sensor discovery) ===
        self._sensor_enumerator = SensorEnumerator()
        self._sensor_enumerator.discover()

        # === System Info dashboard (hidden, shown by sensor/home button) ===
        self.uc_system_info = UCSystemInfo(self._sensor_enumerator, self._lang, central)
        self.uc_system_info.setGeometry(*Layout.FORM_CONTAINER)
        self.uc_system_info.setVisible(False)

        # Initialize theme directories
        self._init_theme_directories()

    def _create_mode_tabs(self):
        """Create mode tab buttons matching Windows positions."""
        self.mode_buttons = []

        # (layout_rect, normal_img, active_img, panel_index)
        tab_configs = [
            (Layout.TAB_LOCAL, Assets.TAB_LOCAL, Assets.TAB_LOCAL_ACTIVE, 0),
            (Layout.TAB_MASK, Assets.TAB_MASK, Assets.TAB_MASK_ACTIVE, 2),
            (Layout.TAB_CLOUD, Assets.TAB_CLOUD, Assets.TAB_CLOUD_ACTIVE, 1),
            (Layout.TAB_SETTINGS, Assets.TAB_SETTINGS, Assets.TAB_SETTINGS_ACTIVE, 3),
        ]

        for rect, normal_img, active_img, panel_idx in tab_configs:
            btn = create_image_button(
                self.form_container, *rect,
                normal_img, active_img, checkable=True
            )
            btn.clicked.connect(lambda checked, idx=panel_idx: self._show_panel(idx))
            self.mode_buttons.append(btn)

        if self.mode_buttons:
            self.mode_buttons[0].setChecked(True)

    def _show_panel(self, index):
        """Show panel at index and update button states."""
        self.panel_stack.setCurrentIndex(index)
        panel_to_button = {0: 0, 1: 2, 2: 1, 3: 3}
        active_btn = panel_to_button.get(index, 0)
        for i, btn in enumerate(self.mode_buttons):
            btn.setChecked(i == active_btn)

    def _show_view(self, view: str):
        """Switch between the three content views.

        Args:
            view: 'form' (device/themes), 'about' (control center), or 'sysinfo' (dashboard)
        """
        self.form_container.setVisible(view == 'form')
        self.uc_about.setVisible(view == 'about')
        self.uc_system_info.setVisible(view == 'sysinfo')

        if view == 'sysinfo':
            self.uc_system_info.start_updates()
        else:
            self.uc_system_info.stop_updates()

    def _show_about(self):
        """Show the About / Control Center panel."""
        self._show_view('about')

    def _show_system_info(self):
        """Show the System Info dashboard."""
        self._show_view('sysinfo')

    def _show_form(self):
        """Show the main form (device/themes) view."""
        self._show_view('form')
        self.uc_device.restore_device_selection()

    def _on_temp_unit_changed(self, unit: str):
        """Handle temperature unit change from Control Center."""
        temp_int = 1 if unit == 'F' else 0
        self.controller.overlay.set_temp_unit(temp_int)
        self.uc_preview.set_status(f"Temperature: °{unit}")

    def _on_hdd_toggle_changed(self, enabled: bool):
        """Handle HDD info toggle from Control Center."""
        self.uc_preview.set_status(
            f"HDD info: {'Enabled' if enabled else 'Disabled'}")

    def _on_refresh_changed(self, interval: int):
        """Handle data refresh interval change from Control Center.

        Updates the metrics timer that drives overlay system info updates.
        Windows: value is 1-100 (seconds).
        """
        ms = interval * 1000
        if self._metrics_timer.isActive():
            self._metrics_timer.setInterval(ms)
        self.uc_preview.set_status(f"Refresh: {interval}s")

    def _on_resolution_changed(self, width: int, height: int):
        """Handle LCD resolution change from settings.

        Updates controller, preview frame, image/video cutters,
        theme directories, and cloud theme URLs.
        """
        self.controller.set_resolution(width, height)

        # Update preview frame for new resolution
        self.uc_preview.set_resolution(width, height)

        # Update image/video cutters
        self.uc_image_cut.set_resolution(width, height)
        self.uc_video_cut.set_resolution(width, height)

        # Update settings panel (screencast aspect ratio)
        self.uc_theme_setting.set_resolution(width, height)

        # Reload theme directories for new resolution
        theme_dir = self._data_dir / f'Theme{width}{height}'
        if theme_dir.exists():
            self.uc_theme_local.set_theme_directory(theme_dir)

        # Cloud themes — per-resolution videos directory
        videos_dir = self._get_videos_dir(width, height)
        self.uc_theme_web.set_videos_directory(videos_dir)
        self.uc_theme_web.set_resolution(f'{width}x{height}')

        masks_dir = self._data_dir / 'cloud_masks' / f'zt{width}{height}'
        self.uc_theme_mask.set_mask_directory(masks_dir)
        self.uc_theme_mask.set_resolution(f'{width}x{height}')

        self.uc_preview.set_status(f"Resolution: {width}×{height}")

    def _create_bottom_controls(self):
        """Create bottom control bar matching Windows FormCZTV positions.

        Windows layout at y=680:
        - ucComboBoxA1 (rotation): Point(39, 680), Size(108, 24)
        - buttonLDD (brightness): Point(157, 680), Size(52, 24)
        - textBoxCMM (theme name): Point(278, 684), Size(102, 16)
        - buttonBCZT (save): Point(383, 680), Size(24, 24)
        - buttonDaoChu (export): Point(412, 680), Size(40, 24)
        - buttonDaoRu (import): Point(453, 680), Size(40, 24)
        """
        # === Rotation combobox (ucComboBoxA1) ===
        self.rotation_combo = QComboBox(self.form_container)
        self.rotation_combo.setGeometry(*Layout.ROTATION_COMBO)
        self.rotation_combo.addItems(["0°", "90°", "180°", "270°"])
        self.rotation_combo.setStyleSheet(
            "QComboBox { background-color: #2A2A2A; color: white; border: 1px solid #555;"
            " font-size: 10px; padding-left: 5px; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox QAbstractItemView { background-color: #2A2A2A; color: white;"
            " selection-background-color: #4A6FA5; }"
        )
        self.rotation_combo.currentIndexChanged.connect(self._on_rotation_change)

        # === Brightness button (buttonLDD) — cycles PL1→PL2→PL3→PL1 ===
        self._brightness_level = 2  # Default L2 (75%), Windows starts at L2
        self._brightness_pixmaps = {}
        for level in range(4):
            pix = load_pixmap(f'PL{level}.png')
            if not pix.isNull():
                self._brightness_pixmaps[level] = pix

        self.brightness_btn = QPushButton(self.form_container)
        self.brightness_btn.setGeometry(*Layout.BRIGHTNESS_BTN)
        self._update_brightness_icon()
        self.brightness_btn.clicked.connect(self._on_brightness_cycle)

        # === Theme name input (textBoxCMM) ===
        self.theme_name_input = QLineEdit(self.form_container)
        self.theme_name_input.setGeometry(*Layout.THEME_NAME_INPUT)
        self.theme_name_input.setText("Theme1")
        self.theme_name_input.setMaxLength(10)
        self.theme_name_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.theme_name_input.setStyleSheet(
            "background-color: #232227; color: white; border: none;"
            " font-family: 'Microsoft YaHei'; font-size: 9pt;"
        )
        # Block invalid filename chars (Windows Path.GetInvalidFileNameChars)
        self.theme_name_input.setValidator(
            QRegularExpressionValidator(QRE(r'[^/\\:*?"<>|\x00-\x1f]+')))

        # === Icon buttons (save/export/import) with text fallback ===
        self.save_btn = self._create_icon_or_text_btn(
            *Layout.SAVE_BTN, Assets.BTN_SAVE, "S")
        self.save_btn.clicked.connect(self._on_save_clicked)

        self.export_btn = self._create_icon_or_text_btn(
            *Layout.EXPORT_BTN, Assets.BTN_EXPORT, "Exp")
        self.export_btn.clicked.connect(self._on_export_clicked)

        self.import_btn = self._create_icon_or_text_btn(
            *Layout.IMPORT_BTN, Assets.BTN_IMPORT, "Imp")
        self.import_btn.clicked.connect(self._on_import_clicked)

    def _create_icon_or_text_btn(self, x, y, w, h, icon_name, fallback_text):
        """Create a button that shows an icon if available, or text fallback."""
        btn = QPushButton(self.form_container)
        btn.setGeometry(x, y, w, h)
        pix = load_pixmap(icon_name, w, h)
        if not pix.isNull():
            btn.setIcon(QIcon(pix))
            btn.setIconSize(btn.size())
            btn.setStyleSheet(Styles.ICON_BUTTON_HOVER)
            self._pixmap_refs.append(pix)
        else:
            btn.setText(fallback_text)
            btn.setStyleSheet(Styles.TEXT_BUTTON)
        return btn

    def _create_title_buttons(self):
        """Create title bar buttons (Help, Power/Close)."""
        # Help button — Windows opens LCDHelp.pdf; we open the install guide
        help_btn = create_image_button(
            self.form_container, *Layout.HELP_BTN,
            Assets.BTN_HELP, None, fallback_text="?"
        )
        help_btn.clicked.connect(self._on_help_clicked)

        # Close/Power button
        close_btn = create_image_button(
            self.form_container, *Layout.CLOSE_BTN,
            Assets.BTN_POWER, Assets.BTN_POWER_HOVER, fallback_text="X"
        )
        close_btn.clicked.connect(self.close)

    def _on_help_clicked(self):
        """Open User Guide PDF. Windows opens LCDHelp.pdf; we open Thermalright User Guide."""
        import webbrowser
        from pathlib import Path

        doc = Path(__file__).resolve().parents[3] / 'doc' / 'User Guide.pdf'
        if doc.exists():
            webbrowser.open(doc.as_uri())
        else:
            webbrowser.open('https://github.com/thermalright/trcc-linux#readme')

    def _apply_settings_backgrounds(self):
        """Apply localized P01 backgrounds to display mode panels in UCThemeSetting.

        Windows pattern: FormCZTV.set_panel_images() sets backgrounds on
        UCThemeSetting sub-panels based on language.
        """
        setting = self.uc_theme_setting

        # Mask/Layout panel at (10, 441) - P01布局蒙板{lang}.png
        mask_bg = self._localized('P01布局蒙板.png')
        self._set_panel_background(setting.mask_panel, mask_bg)

        # Background panel at (371, 441) - P01背景显示{lang}.png
        bg_bg = self._localized('P01背景显示.png')
        self._set_panel_background(setting.background_panel, bg_bg)

        # Screencast panel at (10, 551) - P01投屏显示xy{lang}.png
        sc_bg = self._localized('P01投屏显示xy.png')
        self._set_panel_background(setting.screencast_panel, sc_bg)

        # Video player panel at (371, 551) - P01播放器{lang}.png
        vp_bg = self._localized('P01播放器.png')
        self._set_panel_background(setting.video_panel, vp_bg)

        # Overlay grid (ucXiTongXianShi1) at (10, 1) - P01内容{lang}.png
        content_bg = self._localized('P01内容.png')
        self._set_panel_background(setting.overlay_grid, content_bg)

        # Color picker (ucXiTongXianShiColor1) at (492, 1) - P01参数面板{lang}.png
        params_bg = self._localized('P01参数面板.png')
        self._set_panel_background(setting.color_panel, params_bg)

    def set_language(self, lang: str):
        """
        Switch all localized backgrounds to a new language.

        Matches Windows FormCZTV.set_panel_images() pattern.

        Args:
            lang: Language suffix ('en', 'tc', 'd', 'e', 'f', 'p', 'r', 'x', '' for Chinese)
        """
        self._lang = lang

        # Re-apply main background
        self._set_panel_background(self.form_container, self._localized(Assets.FORM_CZTV_BG))

        # Re-apply theme panel backgrounds
        self._set_panel_background(self.uc_theme_local, self._localized(Assets.THEME_LOCAL_BG))
        self._set_panel_background(self.uc_theme_web, self._localized(Assets.THEME_WEB_BG))
        self._set_panel_background(self.uc_theme_mask, self._localized(Assets.THEME_MASK_BG))

        # Re-apply settings sub-panel backgrounds
        self._apply_settings_backgrounds()

        # Sync about panel
        self.uc_about.set_language(lang)

    def _get_videos_dir(self, w: int, h: int) -> Path:
        """Get cloud theme videos directory for a resolution.

        Checks per-resolution bundled dir first, then flat fallback (for
        backwards compat with existing 320x320 installs), then user cache.
        """
        # Per-resolution bundled: src/data/videos/{W}{H}/
        per_res = self._data_dir / 'videos' / f'{w}{h}'
        if per_res.exists():
            return per_res

        # Flat bundled: src/data/videos/ (only valid for 320x320 legacy)
        flat = self._data_dir / 'videos'
        if (w, h) == (320, 320) and flat.exists():
            return flat

        # User-downloaded cloud cache: ~/.trcc/cloud_themes/{W}{H}/
        cache = Path.home() / '.trcc' / 'cloud_themes' / f'{w}{h}'
        return cache

    def _init_theme_directories(self):
        """Initialize theme browser directories."""
        w, h = self.controller.lcd_width, self.controller.lcd_height

        theme_dir = self._data_dir / f'Theme{w}{h}'
        if theme_dir.exists():
            self.uc_theme_local.set_theme_directory(theme_dir)

        videos_dir = self._get_videos_dir(w, h)
        self.uc_theme_web.set_videos_directory(videos_dir)
        self.uc_theme_web.set_resolution(f'{w}x{h}')

        masks_dir = self._data_dir / 'cloud_masks' / f'zt{w}{h}'
        self.uc_theme_mask.set_mask_directory(masks_dir)
        self.uc_theme_mask.set_resolution(f'{w}x{h}')

    # =========================================================================
    # Controller Callbacks (controller -> view updates)
    # =========================================================================

    def _connect_controller_callbacks(self):
        """Subscribe to controller callbacks."""
        # Main controller
        self.controller.on_preview_update = self._on_controller_preview_update
        self.controller.on_status_update = self._on_controller_status_update
        self.controller.on_error = self._on_controller_error

        # Video
        self.controller.video.on_state_changed = self._on_video_state_changed
        self.controller.video.on_progress_update = self._on_video_progress_update
        self.controller.video.on_video_loaded = self._on_video_loaded

        # Devices
        self.controller.devices.on_device_selected = self._on_device_selected
        self.controller.devices.on_send_complete = self._on_send_complete

        # Overlay
        self.controller.overlay.on_config_changed = self._on_overlay_config_changed

    def _on_controller_preview_update(self, image):
        """Handle preview image update from controller."""
        self.uc_preview.set_image(image)

    def _on_controller_status_update(self, text):
        """Handle status update from controller."""
        self.uc_preview.set_status(text)

    def _on_controller_error(self, message):
        """Handle error from controller."""
        self.uc_preview.set_status(f"Error: {message}")

    def _on_video_state_changed(self, state: PlaybackState):
        """Handle video state change."""
        if state == PlaybackState.PLAYING:
            self.uc_preview.set_playing(True)
            self.uc_preview.show_video_controls(True)
            interval = self.controller.get_video_interval()
            self._animation_timer.start(interval)
        elif state == PlaybackState.PAUSED:
            self.uc_preview.set_playing(False)
            self._animation_timer.stop()
        else:  # STOPPED
            self.uc_preview.set_playing(False)
            self.uc_preview.show_video_controls(False)
            self._animation_timer.stop()

    def _on_video_progress_update(self, percent, current_time, total_time):
        """Handle video progress update."""
        self.uc_preview.set_progress(percent, current_time, total_time)

    def _on_device_selected(self, device: DeviceInfo):
        """Handle device selection."""
        self.uc_preview.set_status(f"Device: {device.path}")
        # Update resolution if changed
        if device.resolution != (self.controller.lcd_width, self.controller.lcd_height):
            self.uc_preview.set_resolution(*device.resolution)

    def _on_send_complete(self, success: bool):
        """Handle LCD send completion."""
        self.uc_preview.set_status("Sent to LCD" if success else "Send failed")

    def _on_video_loaded(self, state):
        """Handle video loaded - show controls."""
        self.uc_preview.show_video_controls(True)

    def _on_overlay_config_changed(self):
        """Re-render preview when overlay config changes."""
        self.controller.render_overlay_and_preview()

    # =========================================================================
    # View Signals (view -> controller actions)
    # =========================================================================

    def _connect_view_signals(self):
        """Connect view widget signals to controller actions."""
        self.uc_device.device_selected.connect(self._on_device_widget_clicked)
        self.uc_theme_local.theme_selected.connect(self._on_local_theme_clicked)
        self.uc_theme_local.delete_requested.connect(self._on_delete_theme)
        self.uc_theme_local.delegate.connect(self._on_local_delegate)
        self.uc_theme_web.theme_selected.connect(self._on_cloud_theme_clicked)
        self.uc_theme_web.download_started.connect(
            lambda tid: self.uc_preview.set_status(f"Downloading: {tid}..."))
        self.uc_theme_web.download_finished.connect(self._on_cloud_download_finished)
        self.uc_theme_mask.mask_selected.connect(self._on_mask_clicked)
        self.uc_preview.delegate.connect(self._on_preview_delegate)

        # Settings panel mode toggles and delegate
        self.uc_theme_setting.background_changed.connect(self._on_background_toggle)
        self.uc_theme_setting.screencast_changed.connect(self._on_screencast_toggle)
        self.uc_theme_setting.delegate.connect(self._on_settings_delegate)

        # Image/video cutters
        self.uc_image_cut.image_cut_done.connect(self._on_image_cut_done)
        self.uc_video_cut.video_cut_done.connect(self._on_video_cut_done)

        # Activity sidebar → overlay grid
        self.uc_activity_sidebar.sensor_clicked.connect(self._on_sensor_element_add)
        self.uc_theme_setting.overlay_grid.add_requested.connect(self._on_overlay_add_requested)

        # Overlay on/off toggle → info module visibility
        self.uc_theme_setting.overlay_grid.toggle_changed.connect(self._on_overlay_toggle)

        # Screencast coordinate changes + border toggle
        self.uc_theme_setting.screencast_params_changed.connect(
            self._on_screencast_params_changed)
        self.uc_theme_setting.screencast_panel.border_toggled.connect(
            self._on_screencast_border_toggle)

        # Element flash on select (Windows shanPingCount/shanPingTimer)
        self.uc_theme_setting.overlay_grid.element_selected.connect(
            self._on_element_flash)

        # Screen capture and eyedropper
        self.uc_theme_setting.capture_requested.connect(self._on_capture_requested)
        self.uc_theme_setting.eyedropper_requested.connect(self._on_eyedropper_requested)

        # Mask download feedback
        self.uc_theme_mask.download_started.connect(
            lambda mask_id: self.uc_preview.set_status(f"Downloading: {mask_id}..."))
        self.uc_theme_mask.download_finished.connect(
            lambda mask_id, ok: self.uc_preview.set_status(
                f"{'Downloaded' if ok else 'Failed'}: {mask_id}"))

        # Sidebar navigation
        self.uc_device.home_clicked.connect(self._show_system_info)
        self.uc_device.about_clicked.connect(self._show_about)
        self.uc_device.device_selected.connect(lambda _: self._show_form())
        self.uc_about.close_requested.connect(self._show_form)
        self.uc_about.language_changed.connect(self.set_language)
        self.uc_about.temp_unit_changed.connect(self._on_temp_unit_changed)
        self.uc_about.hdd_toggle_changed.connect(self._on_hdd_toggle_changed)
        self.uc_about.refresh_changed.connect(self._on_refresh_changed)
        self.uc_about.resolution_changed.connect(self._on_resolution_changed)

    def _on_device_widget_clicked(self, device_info: dict):
        """Forward device selection to controller."""
        device = DeviceInfo(
            name=device_info.get('name', 'LCD'),
            path=device_info.get('path', ''),
            resolution=device_info.get('resolution', (320, 320)),
        )
        self.controller.devices.select_device(device)

    def _on_local_theme_clicked(self, theme_info: dict):
        """Forward local theme selection to controller and load overlay config."""
        self._slideshow_timer.stop()  # Manual select stops slideshow rotation
        self.stop_metrics()  # Reset metrics from previous theme
        path = Path(theme_info.get('path', ''))
        if path.exists():
            theme = ThemeInfo.from_directory(path)
            self.controller.themes.select_theme(theme)
            self._load_theme_overlay_config(path)

    def _on_cloud_theme_clicked(self, theme_info: dict):
        """Forward cloud theme selection to controller.

        Cloud videos are backgrounds — overlay (mask + metrics) persists.
        Don't stop metrics; they keep rendering on top of video frames.
        """
        video_path = theme_info.get('video')
        if video_path:
            video_path = Path(video_path)
            preview_path = video_path.parent / f"{video_path.stem}.png"
            theme = ThemeInfo.from_video(video_path, preview_path if preview_path.exists() else None)
            self.controller.themes.select_theme(theme)

    def _on_mask_clicked(self, mask_info: dict):
        """Forward mask selection to controller."""
        mask_path = mask_info.get('path')
        if mask_path:
            self.controller.apply_mask(Path(mask_path))
        else:
            self.uc_preview.set_status(f"Mask: {mask_info.get('name', 'Unknown')}")

    def _on_overlay_changed(self, element_data: dict):
        """Forward overlay change to controller for live preview."""
        if not element_data:
            return
        self.controller.overlay.set_config(element_data)
        self.controller.render_overlay_and_preview()

    def _on_background_toggle(self, enabled: bool):
        """Handle background display toggle from settings (Windows cmd 1 / myMode=0)."""
        if enabled:
            # Stop other modes, show static background
            self._animation_timer.stop()
            self.controller.video.stop()
            self._screencast_timer.stop()
            self._screencast_active = False
            self.controller.render_overlay_and_preview()
        self.uc_preview.set_status(f"Background: {'On' if enabled else 'Off'}")

    def _on_screencast_toggle(self, enabled: bool):
        """Handle screencast toggle — start/stop real-time capture loop.

        Matches Windows myMode=16 behavior: timer-driven CopyFromScreen
        at ~6.67 FPS, scaled to LCD resolution and sent to device.

        On Wayland (GNOME/KDE), tries PipeWire portal capture first.
        Falls back to grab_screen_region() on X11 or when PipeWire is
        unavailable.
        """
        self._screencast_active = enabled
        if enabled:
            # Stop other modes
            self._animation_timer.stop()
            self.controller.video.stop()

            # Try PipeWire on Wayland (GNOME/KDE where grabWindow is blank)
            from .screen_capture import is_wayland
            if is_wayland() and self._pipewire_cast is None:
                self._try_start_pipewire()

            self._screencast_timer.start(150)  # ~6.67 FPS
        else:
            self._screencast_timer.stop()
            self._stop_pipewire()
        self.uc_preview.set_status(f"Screencast: {'On' if enabled else 'Off'}")

    def _try_start_pipewire(self):
        """Attempt to start PipeWire portal capture (non-blocking).

        The portal triggers a user consent dialog. If the user approves,
        _on_screencast_tick() will use PipeWire frames instead of
        grab_screen_region().
        """
        from .pipewire_capture import PipeWireScreenCast, PIPEWIRE_AVAILABLE
        if not PIPEWIRE_AVAILABLE:
            return

        import threading
        self._pipewire_cast = PipeWireScreenCast()

        def _start():
            if not self._pipewire_cast.start(timeout=30):
                self._pipewire_cast = None

        # Start in background so portal dialog doesn't block the GUI
        threading.Thread(target=_start, daemon=True).start()

    def _stop_pipewire(self):
        """Stop PipeWire capture if active."""
        if self._pipewire_cast is not None:
            self._pipewire_cast.stop()
            self._pipewire_cast = None

    def _on_mask_display_toggle(self, enabled):
        """Toggle mask visibility on preview/LCD (Windows SetDrawMengBan)."""
        self.controller.overlay.set_mask_visible(enabled)
        self.controller.render_overlay_and_preview()
        self.uc_preview.set_status(f"Mask: {'On' if enabled else 'Off'}")

    def _switch_to_mask_tab(self):
        """Switch to Mask browser tab (panel index 2)."""
        self._show_panel(2)

    def _on_mask_reset(self):
        """Clear mask from preview (Windows buttonYDMB_Click / cmd 99)."""
        self.controller.overlay.set_theme_mask(None)
        self.controller.render_overlay_and_preview()
        self.uc_preview.set_status("Mask cleared")

    def _on_video_display_toggle(self, enabled):
        """Toggle video playback mode (Windows cmd 3 / myMode=48)."""
        if enabled:
            # Resume playback if video is loaded (Windows: ucBoFangQiKongZhi1.Player())
            if self.controller.video.has_frames():
                self.controller.play_pause()
            self.uc_preview.set_status("Video mode: On")
        else:
            self.controller.video.stop()
            self._animation_timer.stop()
            self.uc_preview.set_status("Video mode: Off")

    def _on_settings_delegate(self, cmd, info, data):
        """Handle delegate events from settings panel."""
        if cmd == UCThemeSetting.CMD_BACKGROUND_LOAD_IMAGE:
            self._on_load_image_clicked()
        elif cmd == UCThemeSetting.CMD_BACKGROUND_LOAD_VIDEO:
            self._on_load_video_clicked()
        elif cmd == UCThemeSetting.CMD_MASK_TOGGLE:
            self._on_mask_display_toggle(info)
        elif cmd == UCThemeSetting.CMD_MASK_LOAD:
            self._switch_to_mask_tab()
        elif cmd == UCThemeSetting.CMD_MASK_RESET:
            self._on_mask_reset()
        elif cmd == UCThemeSetting.CMD_VIDEO_LOAD:
            self._on_load_video_clicked()
        elif cmd == UCThemeSetting.CMD_VIDEO_TOGGLE:
            self._on_video_display_toggle(info)
        elif cmd == UCThemeSetting.CMD_OVERLAY_CHANGED:
            self._on_overlay_changed(info if isinstance(info, dict) else {})

    def _on_preview_delegate(self, cmd, info, data):
        """Handle preview panel commands."""
        if cmd == UCPreview.CMD_VIDEO_PLAY_PAUSE:
            self.controller.play_pause()
        elif cmd == UCPreview.CMD_VIDEO_SEEK:
            self.controller.seek_video(info)

    def _on_load_video_clicked(self):
        """Handle load video → open file dialog → show video cutter."""
        videos_dir = self._data_dir / 'videos'
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", str(videos_dir),
            "Video Files (*.mp4 *.avi *.mov *.gif);;All Files (*)"
        )
        if path:
            w, h = self.controller.lcd_width, self.controller.lcd_height
            self.uc_video_cut.set_resolution(w, h)
            self.uc_video_cut.load_video(path)
            self._show_video_cutter()

    def _on_load_image_clicked(self):
        """Handle load image → open file dialog → show image cutter."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Image", "",
            "Image Files (*.png *.jpg *.jpeg *.bmp);;All Files (*)"
        )
        if path:
            try:
                from PIL import Image as PILImage
                pil_img = PILImage.open(path)
                w, h = self.controller.lcd_width, self.controller.lcd_height
                self.uc_image_cut.load_image(pil_img, w, h)
                self._show_image_cutter()
            except Exception as e:
                self.uc_preview.set_status(f"Error: {e}")

    def _on_save_clicked(self):
        """Handle save theme button click (Windows buttonBCZT_Click)."""
        name = self.theme_name_input.text().strip()
        if not name:
            self.uc_preview.set_status("Enter a theme name first")
            return
        success, msg = self.controller.save_theme(name, self._data_dir)
        self.uc_preview.set_status(msg)
        if success:
            self.uc_theme_local.load_themes()

    def _on_export_clicked(self):
        """Handle export theme button click."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Theme", "",
            "Theme files (*.tr);;JSON (*.json);;All Files (*)"
        )
        if path:
            success, msg = self.controller.export_config(Path(path))
            self.uc_preview.set_status(msg)

    def _on_import_clicked(self):
        """Handle import theme button click."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Theme", "",
            "Theme files (*.tr);;JSON (*.json);;All Files (*)"
        )
        if path:
            success, msg = self.controller.import_config(Path(path), self._data_dir)
            self.uc_preview.set_status(msg)
            if success:
                self.uc_theme_local.load_themes()

    def _on_send_clicked(self):
        """Handle send to LCD button click."""
        self.controller.send_current_image()

    # =========================================================================
    # Image/Video Cutters
    # =========================================================================

    def _show_image_cutter(self):
        """Show the image cropper, hiding the preview."""
        self.uc_preview.setVisible(False)
        self.uc_video_cut.setVisible(False)
        self.uc_image_cut.setVisible(True)
        self.uc_image_cut.raise_()

    def _show_video_cutter(self):
        """Show the video trimmer, hiding the preview."""
        self.uc_preview.setVisible(False)
        self.uc_image_cut.setVisible(False)
        self.uc_video_cut.setVisible(True)
        self.uc_video_cut.raise_()

    def _hide_cutters(self):
        """Hide cutters and restore preview."""
        self.uc_image_cut.setVisible(False)
        self.uc_video_cut.setVisible(False)
        self.uc_preview.setVisible(True)

    def _on_image_cut_done(self, result):
        """Handle image crop completion.

        Args:
            result: Cropped PIL Image at target resolution, or None on cancel.
        """
        self._hide_cutters()
        if result is not None:
            self.controller.current_image = result
            self.controller._update_preview(result)
            self.uc_preview.set_status("Image cropped")
        else:
            self.uc_preview.set_status("Image crop cancelled")

    def _on_video_cut_done(self, zt_path):
        """Handle video export completion.

        Args:
            zt_path: Path to exported Theme.zt, or '' on cancel.
        """
        self._hide_cutters()
        if zt_path:
            self.controller.video.load(Path(zt_path))
            self.controller.video.play()
            self.uc_preview.set_status("Video exported")
        else:
            self.uc_preview.set_status("Video cut cancelled")

    # =========================================================================
    # Activity Sidebar / Overlay Elements
    # =========================================================================

    def _on_overlay_add_requested(self):
        """Show activity sidebar when overlay grid requests add."""
        self.uc_activity_sidebar.setVisible(True)
        self.uc_activity_sidebar.raise_()
        self.uc_activity_sidebar.start_updates()

    def _on_sensor_element_add(self, config):
        """Add sensor element to overlay grid from activity sidebar."""
        self.uc_theme_setting.overlay_grid.add_element(config)
        self.uc_activity_sidebar.setVisible(False)
        self.uc_activity_sidebar.stop_updates()

    def _on_overlay_toggle(self, enabled):
        """Toggle overlay display and info module visibility."""
        self.uc_info_module.setVisible(enabled)
        if enabled:
            self.uc_info_module.start_updates()
            self.start_metrics()
        else:
            self.uc_info_module.stop_updates()
            self.stop_metrics()

    def _on_screencast_params_changed(self, x, y, w, h):
        """Store screencast region coordinates for the capture loop."""
        self._screencast_x = x
        self._screencast_y = y
        self._screencast_w = w
        self._screencast_h = h
        self.uc_preview.set_status(f"Cast: {x},{y} {w}x{h}")

    def _on_screencast_border_toggle(self, visible: bool):
        """Handle screen cast border visibility toggle (Windows myYcbk / cmd=69)."""
        self._screencast_border = visible

    def _on_element_flash(self, index: int, config: dict):
        """Flash/blink selected overlay element on preview (Windows shanPingCount).

        Hides the element for ~1 second so the user can spot its position.
        """
        self.controller.overlay.flash_skip_index = index
        self._flash_timer.start(980)  # 14 ticks * 70ms = 980ms
        self.controller.render_overlay_and_preview()

    def _on_flash_timeout(self):
        """End element flash — restore normal rendering."""
        self.controller.overlay.flash_skip_index = -1
        self.controller.render_overlay_and_preview()

    # =========================================================================
    # Delete Theme (Windows cmd=32)
    # =========================================================================

    def _on_delete_theme(self, theme_info: dict):
        """Handle theme delete request — show confirmation, delete directory."""
        from PyQt6.QtWidgets import QMessageBox
        name = theme_info.get('name', 'Unknown')
        reply = QMessageBox.question(
            self, "Delete Theme",
            f"Delete theme '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.uc_theme_local.delete_theme(theme_info)
            # If deleted theme was the current one, clear preview
            if (self.controller.current_theme and
                    getattr(self.controller.current_theme, 'path', None) == theme_info.get('path')):
                self.controller.current_image = None
                self.uc_preview.set_image(None)
            self.uc_preview.set_status(f"Deleted: {name}")

    # =========================================================================
    # Slideshow / Carousel (Windows cmd=48, myLunBoTimer)
    # =========================================================================

    def _on_local_delegate(self, cmd, info, data):
        """Handle delegate events from local themes panel."""
        if cmd == UCThemeLocal.CMD_SLIDESHOW:
            self._update_slideshow_state()

    def _update_slideshow_state(self):
        """Update slideshow timer based on current UCThemeLocal state."""
        if (self.uc_theme_local.is_slideshow()
                and self.uc_theme_local.get_slideshow_themes()):
            interval_s = self.uc_theme_local.get_slideshow_interval()
            self._slideshow_index = 0
            self._slideshow_timer.start(interval_s * 1000)
        else:
            self._slideshow_timer.stop()

    def _on_slideshow_tick(self):
        """Auto-rotate to next theme in slideshow (Windows Timer_event lunbo)."""
        themes = self.uc_theme_local.get_slideshow_themes()
        if not themes:
            self._slideshow_timer.stop()
            return

        self._slideshow_index = (self._slideshow_index + 1) % len(themes)
        theme_info = themes[self._slideshow_index]

        # Load theme without resetting slideshow state
        path = Path(theme_info.get('path', ''))
        if path.exists():
            theme = ThemeInfo.from_directory(path)
            self.controller.themes.select_theme(theme)
            self._load_theme_overlay_config(path)

    # =========================================================================
    # Cloud Theme Download Feedback
    # =========================================================================

    def _on_cloud_download_finished(self, theme_id: str, success: bool):
        """Handle cloud theme download completion."""
        if success:
            self.uc_preview.set_status(f"Downloaded: {theme_id}")
        else:
            self.uc_preview.set_status(f"Download failed: {theme_id}")

    def _on_screencast_tick(self):
        """Capture screen region, scale to LCD, update preview, send to LCD.

        Called every 150ms by _screencast_timer when screencast mode is active.
        Matches Windows Timer_event() myMode==16 with TPXSCount>=3.

        Uses PipeWire portal frames on Wayland (GNOME/KDE) when available,
        with client-side crop to the user's X/Y/W/H region. Falls back to
        grab_screen_region() on X11 or when PipeWire isn't running.
        """
        if (not self._screencast_active
                or self._screencast_w <= 0
                or self._screencast_h <= 0):
            return

        from PIL import Image as PILImage

        pil_img = None

        # Try PipeWire first (Wayland GNOME/KDE)
        if self._pipewire_cast is not None and self._pipewire_cast.is_running:
            frame = self._pipewire_cast.grab_frame()
            if frame is not None:
                fw, fh, rgb_bytes = frame
                full = PILImage.frombytes('RGB', (fw, fh), rgb_bytes)
                # Crop to user's selected region
                x2 = min(self._screencast_x + self._screencast_w, fw)
                y2 = min(self._screencast_y + self._screencast_h, fh)
                x1 = min(self._screencast_x, fw)
                y1 = min(self._screencast_y, fh)
                if x2 > x1 and y2 > y1:
                    pil_img = full.crop((x1, y1, x2, y2))

        # Fallback: X11 / grim direct capture
        if pil_img is None:
            from .screen_capture import grab_screen_region
            from .base import pixmap_to_pil

            pixmap = grab_screen_region(
                self._screencast_x, self._screencast_y,
                self._screencast_w, self._screencast_h)
            if pixmap.isNull():
                return
            pil_img = pixmap_to_pil(pixmap)

        lcd_w, lcd_h = self.controller.lcd_width, self.controller.lcd_height
        pil_img = pil_img.resize((lcd_w, lcd_h), PILImage.LANCZOS)

        # Apply overlay if enabled
        if self.controller.overlay.is_enabled():
            pil_img = self.controller.overlay.render(pil_img)

        self.uc_preview.set_image(pil_img)
        self.controller._send_frame_to_lcd(pil_img)

    def _on_capture_requested(self):
        """Launch screen capture overlay → feed to image cutter."""
        from .screen_capture import ScreenCaptureOverlay
        self._capture_overlay = ScreenCaptureOverlay()
        self._capture_overlay.captured.connect(self._on_screen_captured)
        self._capture_overlay.show()

    def _on_screen_captured(self, pil_img):
        """Handle captured screen region."""
        self._capture_overlay = None
        if pil_img is None:
            return
        w, h = self.controller.lcd_width, self.controller.lcd_height
        self.uc_image_cut.load_image(pil_img, w, h)
        self._show_image_cutter()

    def _on_eyedropper_requested(self):
        """Launch eyedropper color picker overlay."""
        from .eyedropper import EyedropperOverlay
        self._eyedropper_overlay = EyedropperOverlay()
        self._eyedropper_overlay.color_picked.connect(self._on_eyedropper_picked)
        self._eyedropper_overlay.cancelled.connect(self._on_eyedropper_done)
        self._eyedropper_overlay.show()

    def _on_eyedropper_picked(self, r, g, b):
        """Handle picked color from eyedropper."""
        self._eyedropper_overlay = None
        self.uc_theme_setting.color_panel._apply_color(r, g, b)

    def _on_eyedropper_done(self):
        """Eyedropper cancelled."""
        self._eyedropper_overlay = None

    # =========================================================================
    # DC File Loading
    # =========================================================================

    def _load_theme_overlay_config(self, theme_dir: Path):
        """Load overlay config from theme's config1.dc into settings panel."""
        dc_path = theme_dir / 'config1.dc'
        if not dc_path.exists():
            return
        try:
            from ..dc_parser import parse_dc_file, dc_to_overlay_config
            dc_data = parse_dc_file(dc_path)
            overlay_config = dc_to_overlay_config(dc_data)
            self.uc_theme_setting.load_from_overlay_config(overlay_config)

            # Auto-enable overlay and start metrics when DC has elements
            if overlay_config:
                self.controller.overlay.set_config(overlay_config)
                self.controller.overlay.enable(True)
                self.start_metrics()
        except Exception:
            pass

    # =========================================================================
    # Device Hot-Plug
    # =========================================================================

    def _on_device_poll(self):
        """Poll for LCD device connections."""
        try:
            from ..scsi_device import find_lcd_devices
            devices = find_lcd_devices()

            # Update sidebar (handles both connect and disconnect)
            self.uc_device.update_devices(devices)

            # Auto-select first device if none selected
            if devices and not self.controller.devices.get_selected():
                d = devices[0]
                device = DeviceInfo(
                    name=d.get('name', 'LCD'),
                    path=d.get('path', ''),
                    resolution=d.get('resolution', (320, 320)),
                    vendor=d.get('vendor'),
                    product=d.get('product'),
                )
                self.controller.devices.select_device(device)
                self.uc_preview.set_status(f"Device: {device.path}")
        except (ImportError, Exception):
            pass

    def _on_rotation_change(self, index):
        """Handle rotation combobox change (Windows UpDateUCComboBox1).

        Windows maps: mode 1→0°, 2→90°, 3→180°, 4→270°
        We use 0-indexed: 0→0°, 1→90°, 2→180°, 3→270°
        """
        rotation = index * 90
        self.controller.set_rotation(rotation)
        self.uc_preview.set_status(f"Rotation: {rotation}°")

    def _on_brightness_cycle(self):
        """Cycle brightness level (Windows buttonLDD_Click).

        Windows cycles 1→2→3→1 (skips L0). We match that behavior.
        """
        self._brightness_level = (self._brightness_level % 3) + 1  # 1→2→3→1
        self._update_brightness_icon()
        brightness_values = {1: 25, 2: 50, 3: 100}
        brightness = brightness_values[self._brightness_level]
        self.controller.set_brightness(brightness)
        self.uc_preview.set_status(f"Brightness: L{self._brightness_level} ({brightness}%)")

    def _update_brightness_icon(self):
        """Update brightness button icon for current level."""
        pix = self._brightness_pixmaps.get(self._brightness_level)
        if pix and not pix.isNull():
            self.brightness_btn.setIcon(QIcon(pix))
            self.brightness_btn.setIconSize(QSize(52, 24))
            self.brightness_btn.setStyleSheet(Styles.ICON_BUTTON_HOVER)
        else:
            self.brightness_btn.setText(f"L{self._brightness_level}")
            self.brightness_btn.setStyleSheet(Styles.TEXT_BUTTON)

    def _on_animation_tick(self):
        """Handle animation timer tick - forward to controller."""
        self.controller.video_tick()

    def _on_metrics_tick(self):
        """Collect system metrics and re-render overlay, send to LCD."""
        try:
            from ..system_info import get_all_metrics
            metrics = get_all_metrics()
            self.controller.overlay.update_metrics(metrics)
            if self.controller.current_image and self.controller.overlay.is_enabled():
                img = self.controller.render_overlay_and_preview()
                if img and self.controller.auto_send and not self.controller.video.is_playing():
                    self.controller._send_frame_to_lcd(img)
        except ImportError:
            pass

    def start_metrics(self):
        """Start live metrics collection for overlay display."""
        self.controller.overlay.enable(True)
        self._metrics_timer.start(1000)

    def stop_metrics(self):
        """Stop live metrics collection."""
        self.controller.overlay.enable(False)
        self._metrics_timer.stop()

    # =========================================================================
    # Borderless Window Drag
    # =========================================================================

    def mousePressEvent(self, event):
        """Start drag when clicking title bar area (borderless mode only)."""
        if self._decorated or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position().toPoint()
        # Drag from sidebar top (x<180, y<95) or FormCZTV header (y<80)
        if pos.y() < 80 or (pos.x() < 180 and pos.y() < 95):
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        """Move window while dragging (borderless mode only)."""
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        event.accept()

    def mouseReleaseEvent(self, event):
        """End drag."""
        self._drag_pos = None
        event.accept()

    # =========================================================================
    # Cleanup
    # =========================================================================

    def closeEvent(self, event):
        """Hide to tray on window close; full quit only via tray Exit."""
        if not self._force_quit:
            event.ignore()
            self.hide()
            return

        # Full quit — stop timers and clean up
        self._tray.hide()
        self._animation_timer.stop()
        self._slideshow_timer.stop()
        self._screencast_timer.stop()
        self._stop_pipewire()
        self._metrics_timer.stop()
        self._device_timer.stop()
        self.uc_system_info.stop_updates()
        self.uc_info_module.stop_updates()
        self.uc_activity_sidebar.stop_updates()
        self.controller.video.stop()
        self.controller.cleanup()
        event.accept()
        QApplication.instance().quit()


def run_mvc_app(data_dir: Path = None, decorated: bool = False):
    """Run the MVC PyQt6 application."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    font = QFont("Microsoft YaHei", 10)
    if not font.exactMatch():
        font = QFont("Sans Serif", 10)
    app.setFont(font)

    window = TRCCMainWindowMVC(data_dir, decorated=decorated)
    window.show()

    return app.exec()


if __name__ == '__main__':
    sys.exit(run_mvc_app())
