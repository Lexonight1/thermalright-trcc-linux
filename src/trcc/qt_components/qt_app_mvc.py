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
from __future__ import annotations

import locale
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QRegularExpression as QRE
from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette, QRegularExpressionValidator
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

# Import MVC core
from ..core import (
    DeviceInfo,
    PlaybackState,
    ThemeInfo,
    create_controller,
)
from ..core.controllers import FormLEDController
from ..dc_writer import CarouselConfig, read_carousel_config, write_carousel_config
from ..paths import (
    device_config_key,
    get_device_config,
    get_saved_temp_unit,
    get_web_dir,
    get_web_masks_dir,
    save_device_setting,
    save_temp_unit,
)
from ..sensor_enumerator import SensorEnumerator

# Import view components
from .assets import Assets, load_pixmap
from .base import create_image_button, set_background_pixmap
from .constants import Colors, Layout, Sizes, Styles
from .uc_about import UCAbout, ensure_autostart
from .uc_activity_sidebar import UCActivitySidebar
from .uc_device import UCDevice
from .uc_image_cut import UCImageCut
from .uc_info_module import UCInfoModule
from .uc_hr10_control import UCHr10Control
from .uc_led_control import UCLedControl
from .uc_preview import UCPreview
from .uc_system_info import UCSystemInfo
from .uc_theme_local import UCThemeLocal
from .uc_theme_mask import UCThemeMask
from .uc_theme_setting import UCThemeSetting
from .uc_theme_web import UCThemeWeb
from .uc_video_cut import UCVideoCut

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
        lang = (locale.getlocale()[0]
                or os.environ.get('LANG', '').split('.')[0]
                or 'en')
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
    Main TRCC application window (singleton).

    This View:
    - Owns the FormCZTVController for business logic
    - Renders UI with Windows-matching backgrounds via QPalette
    - Forwards user events to controller
    - Subscribes to controller callbacks for updates
    """

    _instance: 'TRCCMainWindowMVC | None' = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is not None:
            raise RuntimeError("TRCCMainWindowMVC is a singleton — use instance()")
        inst = super().__new__(cls)
        cls._instance = inst
        return inst

    @classmethod
    def instance(cls) -> 'TRCCMainWindowMVC | None':
        """Return the existing window instance, or None."""
        return cls._instance

    def __init__(self, data_dir: Path | None = None, decorated: bool = False):
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
        self._data_dir = data_dir or Path(__file__).parent.parent / 'data'
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

        # LED animation timer (30ms for LED effect ticks, matches FormLED timer1)
        self._led_timer = QTimer(self)
        self._led_timer.timeout.connect(self._on_led_tick)

        # LED controller (lazy — created on first LED device selection)
        self._led_controller: FormLEDController | None = None
        self._led_active = False
        self._hr10_active = False  # True when HR10 panel is shown (vs generic LED)

        # Drive metrics timer for HR10 (1-second polling, slower than LED tick)
        self._drive_metrics_timer = QTimer(self)
        self._drive_metrics_timer.timeout.connect(self._on_drive_metrics_tick)

        # Language for localized backgrounds
        self._lang = detect_language()

        # Per-device config tracking
        self._active_device_key = ''

        # Pixmap references to prevent GC
        self._pixmap_refs = []

        # Setup UI
        self._apply_dark_theme()
        self._setup_ui()
        self._connect_controller_callbacks()
        self._connect_view_signals()

        # Restore saved temperature unit preference
        saved_unit = get_saved_temp_unit()
        self.controller.overlay.set_temp_unit(saved_unit)
        self.uc_system_info.set_temp_unit(saved_unit)
        self.uc_hr10_control.set_temp_unit(saved_unit)
        if saved_unit == 1:
            self.uc_about._set_temp('F')

        # Auto-enable autostart on first launch (matches Windows KaijiQidong)
        autostart_state = ensure_autostart()
        self.uc_about._autostart = autostart_state
        self.uc_about.startup_btn.setChecked(autostart_state)

        # System tray icon
        self._setup_systray()

        # Detect devices immediately, then poll every 5s
        self._on_device_poll()
        self._device_timer.start(5000)

        # UCDevice auto-selects first device during _setup_ui(), but the signal
        # fires before _connect_view_signals(). Re-trigger selection now that
        # callbacks are wired so _active_device_key gets set and config persists.
        if self.uc_device.selected_device and not self._active_device_key:
            self._on_device_widget_clicked(self.uc_device.selected_device)

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
        icon_path = Path(__file__).parent.parent / 'assets' / 'icons' / 'trcc.png'
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self.setWindowIcon(icon)

        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("TRCC Linux")

        menu = QMenu()
        show_action = menu.addAction("Show/Hide")
        if show_action:
            show_action.triggered.connect(self._toggle_visibility)
        menu.addSeparator()
        exit_action = menu.addAction("Exit")
        if exit_action:
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

        # === LED Control panel (hidden, shown when LED device is selected) ===
        self.uc_led_control = UCLedControl(central)
        self.uc_led_control.setGeometry(*Layout.FORM_CONTAINER)
        self.uc_led_control.setVisible(False)

        # === HR10 Control panel (hidden, shown when HR10 device is selected) ===
        self.uc_hr10_control = UCHr10Control(central)
        self.uc_hr10_control.setGeometry(*Layout.FORM_CONTAINER)
        self.uc_hr10_control.setVisible(False)

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
            x, y, w, h = rect
            btn = create_image_button(
                self.form_container, x, y, w, h,
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
        """Switch between the five content views.

        Args:
            view: 'form' (device/themes), 'about' (control center),
                  'sysinfo' (dashboard), 'led' (LED control), or 'hr10' (HR10)
        """
        self.form_container.setVisible(view == 'form')
        self.uc_about.setVisible(view == 'about')
        self.uc_system_info.setVisible(view == 'sysinfo')
        self.uc_led_control.setVisible(view == 'led')
        self.uc_hr10_control.setVisible(view == 'hr10')

        if view == 'sysinfo':
            self.uc_system_info.start_updates()
        else:
            self.uc_system_info.stop_updates()

        # Start/stop drive metrics timer for HR10
        if view == 'hr10':
            self._drive_metrics_timer.start(1000)
        else:
            self._drive_metrics_timer.stop()

        # Stop LED timer when leaving LED/HR10 view
        if view not in ('led', 'hr10') and self._led_active:
            self._stop_led_view()

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
        self.uc_system_info.set_temp_unit(temp_int)
        self.uc_hr10_control.set_temp_unit(temp_int)
        save_temp_unit(temp_int)
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
            # Load carousel config for new resolution
            self._load_carousel_config(theme_dir)

        # Cloud themes — per-resolution Web directory (matches Windows Web/{W}{H}/)
        web_dir = Path(get_web_dir(width, height))

        self.uc_theme_web.set_web_directory(web_dir)
        self.uc_theme_web.set_resolution(f'{width}x{height}')

        masks_dir = Path(get_web_masks_dir(width, height))
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

        # Sync LED panel
        self.uc_led_control.set_language(lang)
        self.uc_hr10_control.set_language(lang)

    def _init_theme_directories(self):
        """Initialize theme browser directories."""
        w, h = self.controller.lcd_width, self.controller.lcd_height

        theme_dir = self._data_dir / f'Theme{w}{h}'
        self.uc_theme_local.set_theme_directory(theme_dir)
        if theme_dir.exists():
            # Load carousel config (Theme.dc) after themes are loaded
            self._load_carousel_config(theme_dir)

        web_dir = Path(get_web_dir(w, h))

        self.uc_theme_web.set_web_directory(web_dir)
        self.uc_theme_web.set_resolution(f'{w}x{h}')

        masks_dir = Path(get_web_masks_dir(w, h))
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
        """Handle device selection — restore per-device config."""
        self._active_device_key = device_config_key(
            device.device_index, device.vid, device.pid)
        self.uc_preview.set_status(f"Device: {device.path}")

        # Update resolution if changed
        if device.resolution != (self.controller.lcd_width, self.controller.lcd_height):
            self._on_resolution_changed(*device.resolution)

        # Restore per-device brightness and rotation
        cfg = get_device_config(self._active_device_key)
        brightness_level = cfg.get('brightness_level', 2)
        rotation_index = cfg.get('rotation', 0) // 90

        self._brightness_level = brightness_level
        self._update_brightness_icon()
        brightness_values = {1: 25, 2: 50, 3: 100}
        self.controller.set_brightness(brightness_values.get(brightness_level, 50))

        self.rotation_combo.blockSignals(True)
        self.rotation_combo.setCurrentIndex(rotation_index)
        self.rotation_combo.blockSignals(False)
        self.controller.set_rotation(rotation_index * 90)

        # Restore per-device theme (or auto-load first available)
        saved_theme = cfg.get('theme_path')
        theme_loaded = False
        if saved_theme:
            theme_path = Path(saved_theme)
            if theme_path.exists():
                if theme_path.suffix in ('.mp4', '.avi', '.mkv', '.webm'):
                    preview = theme_path.parent / f"{theme_path.stem}.png"
                    theme = ThemeInfo.from_video(
                        theme_path, preview if preview.exists() else None)
                    self.controller.themes.select_theme(theme)
                else:
                    self._select_theme_from_path(theme_path)
                theme_loaded = True

        if not theme_loaded:
            # Auto-load first local theme so config is always populated
            # (ensures --last-one works after first GUI session)
            w, h = device.resolution
            theme_base = self._data_dir / f'Theme{w}{h}'
            if theme_base.exists():
                for item in sorted(theme_base.iterdir()):
                    if item.is_dir() and (item / '00.png').exists():
                        self._select_theme_from_path(item)
                        break

        # Restore per-device carousel
        carousel = cfg.get('carousel')
        if carousel and isinstance(carousel, dict):
            self.uc_theme_local._lunbo_array = carousel.get('themes', [])
            self.uc_theme_local._slideshow = carousel.get('enabled', False)
            self.uc_theme_local._slideshow_interval = carousel.get('interval', 3)
            self.uc_theme_local.timer_input.setText(
                str(carousel.get('interval', 3)))
            px = (self.uc_theme_local._lunbo_on if carousel.get('enabled')
                  else self.uc_theme_local._lunbo_off)
            if not px.isNull():
                self.uc_theme_local.slideshow_btn.setIcon(QIcon(px))
                self.uc_theme_local.slideshow_btn.setIconSize(
                    self.uc_theme_local.slideshow_btn.size())
            self.uc_theme_local._apply_decorations()
            self._update_slideshow_state()
        else:
            # No carousel config — stop slideshow, clear badges
            self._slideshow_timer.stop()
            self.uc_theme_local._lunbo_array = []
            self.uc_theme_local._slideshow = False
            self.uc_theme_local._apply_decorations()

        # Restore per-device overlay config
        overlay = cfg.get('overlay')
        if overlay and isinstance(overlay, dict):
            enabled = overlay.get('enabled', False)
            config = overlay.get('config', {})
            if config:
                self.uc_theme_setting.load_from_overlay_config(config)
                self.controller.overlay.set_config(config)
            self.uc_theme_setting.set_overlay_enabled(enabled)
            self.controller.overlay.enable(enabled)
            if enabled:
                self.uc_info_module.setVisible(True)
                self.uc_info_module.start_updates()
                self.start_metrics()
            else:
                self.uc_info_module.setVisible(False)
                self.uc_info_module.stop_updates()
                self.stop_metrics()
        else:
            # No overlay config — disable overlay
            self.uc_theme_setting.set_overlay_enabled(False)
            self.controller.overlay.enable(False)
            self.uc_info_module.setVisible(False)
            self.uc_info_module.stop_updates()
            self.stop_metrics()
            self.uc_theme_setting.overlay_grid.clear_all()

    def _on_send_complete(self, success: bool):
        """Handle LCD send completion."""
        if success:
            self.uc_preview.set_status("Sent to LCD")
        else:
            self.uc_preview.set_status("Send failed — run 'trcc hid-debug' for details")

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
        # Note: _on_device_widget_clicked handles routing to form vs LED view
        self.uc_about.close_requested.connect(self._show_form)
        self.uc_about.language_changed.connect(self.set_language)
        self.uc_about.temp_unit_changed.connect(self._on_temp_unit_changed)
        self.uc_about.hdd_toggle_changed.connect(self._on_hdd_toggle_changed)
        self.uc_about.refresh_changed.connect(self._on_refresh_changed)

    def _on_device_widget_clicked(self, device_info: dict):
        """Forward device selection to controller.

        Routes to LED panel for LED devices, LCD form for everything else.
        """
        implementation = device_info.get('implementation', 'generic')
        device = DeviceInfo(
            name=device_info.get('name', 'LCD'),
            path=device_info.get('path', ''),
            resolution=device_info.get('resolution', (320, 320)),
            model=device_info.get('model'),
            vid=device_info.get('vid', 0),
            pid=device_info.get('pid', 0),
            device_index=device_info.get('device_index', 0),
            protocol=device_info.get('protocol', 'scsi'),
            device_type=device_info.get('device_type', 1),
            implementation=implementation,
        )

        if implementation == 'hid_led':
            self._show_led_view(device)
        else:
            # Stop LED mode if switching from LED to LCD device
            if self._led_active:
                self._stop_led_view()
            self._show_view('form')
            # Skip full reload if same device already selected
            current = self.controller.devices.get_selected()
            if current and current.path == device.path:
                return
            self.controller.devices.select_device(device)

    def _select_theme_from_path(self, path: Path):
        """Load a local/mask theme by directory path.

        Shared by local theme clicks and mask clicks — both have the same
        structure (01.png, config1.dc, optional 00.png). from_directory()
        detects mask-only (no 00.png) and load_local_theme() handles it.
        """
        if not path.exists():
            return
        self._slideshow_timer.stop()
        self.stop_metrics()
        theme = ThemeInfo.from_directory(path)
        self.controller.themes.select_theme(theme)
        self._load_theme_overlay_config(path)
        if self._active_device_key:
            save_device_setting(self._active_device_key, 'theme_path', str(path))

    def _on_local_theme_clicked(self, theme_info: dict):
        """Forward local theme selection to controller."""
        self._select_theme_from_path(Path(theme_info.get('path', '')))
        # Update name input so re-saving overwrites the same theme
        name = theme_info.get('name', '')
        if name.startswith('Custom_'):
            name = name[len('Custom_'):]
        self.theme_name_input.setText(name)

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
            if self._active_device_key:
                save_device_setting(self._active_device_key, 'theme_path',
                                    str(video_path))

    def _on_mask_clicked(self, mask_info: dict):
        """Apply mask overlay on top of current content (preserves video)."""
        mask_path = mask_info.get('path')
        if mask_path:
            mask_dir = Path(mask_path)
            self.controller.apply_mask(mask_dir)
            self._load_theme_overlay_config(mask_dir)
        else:
            self.uc_preview.set_status(f"Mask: {mask_info.get('name', 'Unknown')}")

    def _on_overlay_changed(self, element_data: dict):
        """Forward overlay change to controller for live preview."""
        if not element_data:
            return
        self.controller.overlay.set_config(element_data)
        self.controller.render_overlay_and_preview()

        # Save overlay config per-device
        if self._active_device_key:
            save_device_setting(self._active_device_key, 'overlay', {
                'enabled': self.uc_theme_setting.overlay_grid.overlay_enabled,
                'config': element_data,
            })

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
        from .pipewire_capture import PIPEWIRE_AVAILABLE, PipeWireScreenCast
        if not PIPEWIRE_AVAILABLE:
            return

        import threading
        cast = PipeWireScreenCast()
        self._pipewire_cast = cast

        def _start():
            if not cast.start(timeout=30):
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
        w, h = self.controller.lcd_width, self.controller.lcd_height
        web_dir = get_web_dir(w, h)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", web_dir,
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
            w, h = self.controller.lcd_width, self.controller.lcd_height
            theme_dir = self._data_dir / f'Theme{w}{h}'
            self.uc_theme_local.set_theme_directory(theme_dir)
            self.uc_theme_local.load_themes()
            # Persist saved theme path for --last-one resume
            if self._active_device_key and self.controller.current_theme_path:
                save_device_setting(self._active_device_key, 'theme_path',
                                    str(self.controller.current_theme_path))

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
                w, h = self.controller.lcd_width, self.controller.lcd_height
                theme_dir = self._data_dir / f'Theme{w}{h}'
                self.uc_theme_local.set_theme_directory(theme_dir)
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
            # Save cropped image as background (Windows: ImageCut_OK_Write)
            self.controller.current_image = result
            # Save to working dir for later theme save
            bg_path = self.controller.working_dir / '00.png'
            result.save(str(bg_path))
            # Set as overlay background so mask/overlays render on top
            self.controller.overlay.set_background(result)
            # Re-render with overlays
            preview = self.controller.render_overlay_and_preview()
            if preview:
                self.controller._update_preview(preview)
            self.uc_preview.set_status("Image cropped and saved")
        else:
            self.uc_preview.set_status("Image crop cancelled")

    def _on_video_cut_done(self, zt_path):
        """Handle video export completion.

        Args:
            zt_path: Path to exported Theme.zt, or '' on cancel.
        """
        self._hide_cutters()
        if zt_path:
            # Copy Theme.zt to working dir for later theme save
            import shutil
            dest_path = self.controller.working_dir / 'Theme.zt'
            shutil.copy(zt_path, dest_path)
            # Load and play
            self.controller.video.load(dest_path)
            self.controller.video.play()
            self.uc_preview.set_status("Video exported and saved")
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

        # Save overlay enabled state per-device
        if self._active_device_key:
            cfg = get_device_config(self._active_device_key)
            overlay = cfg.get('overlay', {})
            overlay['enabled'] = enabled
            save_device_setting(self._active_device_key, 'overlay', overlay)

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
        self.controller.overlay.flash_skip_index = index  # type: ignore[attr-defined]
        self._flash_timer.start(980)  # 14 ticks * 70ms = 980ms
        self.controller.render_overlay_and_preview()

    def _on_flash_timeout(self):
        """End element flash — restore normal rendering."""
        self.controller.overlay.flash_skip_index = -1  # type: ignore[attr-defined]
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
            if (self.controller.current_theme_path and
                    str(self.controller.current_theme_path) == theme_info.get('path')):
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

        # Save carousel config (Theme.dc) - Windows cmd=48
        self._save_carousel_config()

        # Persist carousel state per-device
        if self._active_device_key:
            themes = self.uc_theme_local.get_slideshow_themes()
            save_device_setting(self._active_device_key, 'carousel', {
                'enabled': self.uc_theme_local.is_slideshow(),
                'interval': self.uc_theme_local.get_slideshow_interval(),
                'themes': [t.get('name', '') for t in themes],
            })

    def _get_theme_dir(self) -> Path:
        """Get current theme directory for this resolution."""
        w, h = self.controller.lcd_width, self.controller.lcd_height
        return self._data_dir / f'Theme{w}{h}'

    def _save_carousel_config(self):
        """Save carousel/slideshow config to Theme.dc (Windows cmd=48)."""
        theme_dir = self._get_theme_dir()
        if not theme_dir.exists():
            return

        # Build theme index list from selected themes
        all_themes = self.uc_theme_local._all_themes
        slideshow_themes = self.uc_theme_local.get_slideshow_themes()
        theme_indices = []
        for t in slideshow_themes:
            name = t.get('name', '')
            for i, at in enumerate(all_themes):
                if at.get('name') == name:
                    theme_indices.append(i)
                    break

        # Pad to 6 slots with -1
        while len(theme_indices) < 6:
            theme_indices.append(-1)

        # Get current theme index
        selected = self.uc_theme_local.get_selected_theme()
        current_idx = 0
        if selected:
            name = selected.get('name', '')
            for i, at in enumerate(all_themes):
                if at.get('name') == name:
                    current_idx = i
                    break

        # Build config
        config = CarouselConfig(
            current_theme=current_idx,
            enabled=self.uc_theme_local.is_slideshow(),
            interval_seconds=self.uc_theme_local.get_slideshow_interval(),
            count=len([i for i in theme_indices if i >= 0]),
            theme_indices=theme_indices[:6],
            lcd_rotation=self.rotation_combo.currentIndex() + 1,  # 1-4
        )

        # Write to Theme.dc
        try:
            write_carousel_config(config, str(theme_dir / 'Theme.dc'))
        except Exception as e:
            print(f"Failed to save carousel config: {e}")

    def _load_carousel_config(self, theme_dir: Path):
        """Load carousel/slideshow config from Theme.dc."""
        config_path = theme_dir / 'Theme.dc'
        config = read_carousel_config(str(config_path))
        if config is None:
            return

        # Get all themes list
        all_themes = self.uc_theme_local._all_themes

        # Restore slideshow theme selections
        slideshow_names = []
        for idx in config.theme_indices:
            if 0 <= idx < len(all_themes):
                slideshow_names.append(all_themes[idx].get('name', ''))
        self.uc_theme_local._lunbo_array = slideshow_names
        self.uc_theme_local._slideshow = config.enabled
        self.uc_theme_local._slideshow_interval = config.interval_seconds

        # Update UI
        self.uc_theme_local.timer_input.setText(str(config.interval_seconds))
        px = (self.uc_theme_local._lunbo_on if config.enabled
              else self.uc_theme_local._lunbo_off)
        if not px.isNull():
            self.uc_theme_local.slideshow_btn.setIcon(QIcon(px))
            self.uc_theme_local.slideshow_btn.setIconSize(
                self.uc_theme_local.slideshow_btn.size())

        # Refresh decorations to show badges
        self.uc_theme_local._apply_decorations()

        # Start timer if enabled
        self._update_slideshow_state()

        # Restore LCD rotation (1-4 to 0-3 index)
        if 1 <= config.lcd_rotation <= 4:
            self.rotation_combo.setCurrentIndex(config.lcd_rotation - 1)

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
            from .base import pixmap_to_pil
            from .screen_capture import grab_screen_region

            pixmap = grab_screen_region(
                self._screencast_x, self._screencast_y,
                self._screencast_w, self._screencast_h)
            if pixmap.isNull():
                return
            pil_img = pixmap_to_pil(pixmap)

        lcd_w, lcd_h = self.controller.lcd_width, self.controller.lcd_height
        pil_img = pil_img.resize((lcd_w, lcd_h), PILImage.Resampling.LANCZOS)

        # Apply overlay if enabled
        if self.controller.overlay.is_enabled():
            pil_img = self.controller.overlay.render(pil_img)

        self.uc_preview.set_image(pil_img)
        self.controller._send_frame_to_lcd(pil_img)

    # =========================================================================
    # LED Device View
    # =========================================================================

    def _show_led_view(self, device: DeviceInfo):
        """Show the LED control panel for an LED device.

        Creates FormLEDController if needed, configures for device,
        wires signals, and starts the 30ms animation timer.

        Matches Windows: Form1 routes device1 to FormLED, not FormCZTV.
        """
        # Stop LCD timers
        self._animation_timer.stop()
        self._slideshow_timer.stop()
        self._screencast_timer.stop()
        self._screencast_active = False
        self.controller.video.stop()

        # Create controller on first use
        if self._led_controller is None:
            self._led_controller = FormLEDController()
            self._connect_led_signals()

        from ..led_device import LED_STYLES
        led_style = 1

        # Resolve LED style from model name.
        # The model was already identified by probe_led_model() during
        # device detection, so we match by model name instead of doing
        # another handshake (which would timeout since firmware already
        # responded to the detection probe).
        model = device.model or ''
        for style_id, style in LED_STYLES.items():
            if style.model_name == model:
                led_style = style_id
                break

        # Initialize controller for this device
        self._led_controller.initialize(device, led_style)

        # HR10 (style 13) gets its own panel; everything else uses generic LED
        self._hr10_active = (led_style == 13)

        if self._hr10_active:
            # Configure HR10 panel
            style = LED_STYLES.get(led_style)
            if style:
                self.uc_hr10_control.initialize(
                    led_style, style.segment_count, style.zone_count, self._lang
                )
            self._connect_hr10_signals()
            self._show_view('hr10')
        else:
            # Configure generic LED panel
            style = LED_STYLES.get(led_style)
            if style:
                self.uc_led_control.initialize(
                    led_style, style.segment_count, style.zone_count, self._lang
                )
            self._show_view('led')

        self._led_active = True

        # Start LED animation timer (30ms = FormLED timer1 interval)
        self._led_timer.start(30)

    def _stop_led_view(self):
        """Stop LED mode — save config, stop timer, release protocol."""
        self._led_timer.stop()
        self._drive_metrics_timer.stop()
        self._led_active = False
        self._hr10_active = False
        if self._led_controller:
            self._led_controller.cleanup()

    def _connect_led_signals(self):
        """Wire UCLedControl signals to FormLEDController."""
        if not self._led_controller:
            return

        ctrl = self._led_controller

        # Mode/color/brightness → controller
        self.uc_led_control.mode_changed.connect(
            lambda mode: ctrl.led.set_mode(mode))
        self.uc_led_control.color_changed.connect(
            lambda r, g, b: ctrl.led.set_color(r, g, b))
        self.uc_led_control.brightness_changed.connect(
            lambda val: ctrl.led.set_brightness(val))
        self.uc_led_control.global_toggled.connect(
            lambda on: ctrl.led.toggle_global(on))
        self.uc_led_control.segment_clicked.connect(
            lambda idx: ctrl.led.toggle_segment(idx, not ctrl.led.model.state.segment_on[idx]))

        # Controller → view (preview colors)
        ctrl.led.on_preview_update = self._on_led_colors_update
        ctrl.on_status_update = lambda text: self.uc_led_control.set_status(text)

    def _connect_hr10_signals(self):
        """Wire UCHr10Control signals to FormLEDController.

        Safe to call multiple times — disconnects old signals first
        to prevent accumulation from repeated device clicks.
        """
        if not self._led_controller:
            return

        ctrl = self._led_controller
        panel = self.uc_hr10_control

        # Disconnect any existing connections to prevent accumulation
        try:
            panel.mode_changed.disconnect()
        except TypeError:
            pass
        try:
            panel.color_changed.disconnect()
        except TypeError:
            pass
        try:
            panel.brightness_changed.disconnect()
        except TypeError:
            pass
        try:
            panel.display_metric_changed.disconnect()
        except TypeError:
            pass

        # Mode/color/brightness → controller (same interface as generic LED)
        panel.mode_changed.connect(
            lambda mode: ctrl.led.set_mode(mode))
        panel.color_changed.connect(
            lambda r, g, b: ctrl.led.set_color(r, g, b))
        panel.brightness_changed.connect(
            lambda val: ctrl.led.set_brightness(val))

        # Display metric selection → controller display value
        panel.display_metric_changed.connect(
            self._on_hr10_metric_changed)

        # Controller → HR10 view (preview colors)
        ctrl.led.on_preview_update = self._on_led_colors_update
        ctrl.on_status_update = lambda text: panel.set_status(text)

    def _on_led_tick(self):
        """Called every 30ms — advance LED animation and send to device."""
        if self._led_controller and self._led_active:
            self._led_controller.led.tick()

    def _on_led_colors_update(self, colors):
        """Forward computed LED colors to the active preview widget."""
        if self._hr10_active:
            self.uc_hr10_control.set_led_colors(colors)
        else:
            self.uc_led_control.set_led_colors(colors)

    def _on_hr10_metric_changed(self, metric_key: str):
        """Handle display metric selection change from HR10 panel."""
        self._hr10_push_display_value()

    def _hr10_push_display_value(self):
        """Push the current display text to the LED controller for rendering.

        The ° LED sits physically between digit 2 and digit 1 (rightmost).
        For temperature, append C/F so it lands in digit 1 (right of °).
        Layout: [Digit4][Digit3][Digit2][°][Digit1][MB/s][%]
        """
        if not self._led_controller or not self._hr10_active:
            return
        panel = self.uc_hr10_control
        text = panel._display._value_text.strip()
        unit = panel._display._unit_text

        # Map unit to indicator set + adjust text for physical layout
        indicators: set = set()
        if '°' in unit:
            indicators.add('deg')
            # Append C or F so it renders in digit 1 (right of ° symbol)
            if 'C' in unit:
                text = text + 'C'
            elif 'F' in unit:
                text = text + 'F'
        if '%' in unit:
            indicators.add('%')
        if 'MB' in unit or 'mb' in unit:
            indicators.add('mbs')

        self._led_controller.led.set_display_value(text, indicators)

    def _on_drive_metrics_tick(self):
        """Called every 1s — poll drive metrics for HR10 display."""
        if not self._hr10_active:
            return
        try:
            from ..system_info import get_disk_stats, get_disk_temperature
            metrics = get_disk_stats()
            temp = get_disk_temperature()
            if temp is not None:
                metrics['disk_temp'] = temp
            self.uc_hr10_control.update_drive_metrics(metrics)
            # Push updated value to the LED controller for device rendering
            self._hr10_push_display_value()
        except Exception:
            pass

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
        """Load overlay config from theme's config.json or config1.dc into settings panel."""
        overlay_config = None

        # Try reference config.json first (Custom_ themes saved with path refs)
        json_path = theme_dir / 'config.json'
        if json_path.exists():
            try:
                from ..dc_parser import load_config_json
                result = load_config_json(str(json_path))
                if result is not None:
                    overlay_config = result[0]  # (overlay_config, display_options)
            except Exception:
                pass

        # Fall back to binary config1.dc
        if overlay_config is None:
            dc_path = theme_dir / 'config1.dc'
            if not dc_path.exists():
                return
            try:
                from ..dc_parser import dc_to_overlay_config, parse_dc_file
                dc_data = parse_dc_file(str(dc_path))
                overlay_config = dc_to_overlay_config(dc_data)
            except Exception:
                return

        if overlay_config:
            self.uc_theme_setting.load_from_overlay_config(overlay_config)
            self.controller.overlay.set_config(overlay_config)
            self.controller.overlay.enable(True)
            self.start_metrics()

            # Persist to per-device config so overlay survives device re-selection
            if self._active_device_key:
                save_device_setting(self._active_device_key, 'overlay', {
                    'enabled': True,
                    'config': overlay_config,
                })

    # =========================================================================
    # Device Hot-Plug
    # =========================================================================

    def _on_device_poll(self):
        """Poll for LCD and LED device connections."""
        try:
            from ..scsi_device import find_lcd_devices
            devices = find_lcd_devices()

            # Update sidebar (handles both connect and disconnect)
            self.uc_device.update_devices(devices)

            # Auto-select first device if none selected
            if devices and not self.controller.devices.get_selected() and not self._led_active:
                d = devices[0]
                implementation = d.get('implementation', 'generic')
                device = DeviceInfo(
                    name=d.get('name', 'LCD'),
                    path=d.get('path', ''),
                    resolution=d.get('resolution', (320, 320)),
                    vendor=d.get('vendor'),
                    product=d.get('product'),
                    model=d.get('model'),
                    vid=d.get('vid', 0),
                    pid=d.get('pid', 0),
                    device_index=d.get('device_index', 0),
                    protocol=d.get('protocol', 'scsi'),
                    device_type=d.get('device_type', 1),
                    implementation=implementation,
                )
                if implementation == 'hid_led':
                    self._show_led_view(device)
                else:
                    self.controller.devices.select_device(device)
                    self.uc_preview.set_status(f"Device: {device.path}")
        except ImportError:
            pass
        except Exception as e:
            print(f"[device_poll] {e}")

    def _on_rotation_change(self, index):
        """Handle rotation combobox change (Windows UpDateUCComboBox1).

        Windows maps: mode 1→0°, 2→90°, 3→180°, 4→270°
        We use 0-indexed: 0→0°, 1→90°, 2→180°, 3→270°
        """
        rotation = index * 90
        self.controller.set_rotation(rotation)
        if self._active_device_key:
            save_device_setting(self._active_device_key, 'rotation', rotation)
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
        if self._active_device_key:
            save_device_setting(self._active_device_key, 'brightness_level',
                                self._brightness_level)
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
        """Hide to tray on window close; full quit only via tray Exit.

        If system tray is not available (e.g. GNOME without AppIndicator),
        close the window instead of hiding to an invisible tray.
        """
        if not self._force_quit and self._tray.isSystemTrayAvailable() and self._tray.isVisible():
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
        self._led_timer.stop()
        self._drive_metrics_timer.stop()
        if self._led_controller:
            self._led_controller.cleanup()
        self.uc_system_info.stop_updates()
        self.uc_info_module.stop_updates()
        self.uc_activity_sidebar.stop_updates()
        self.controller.video.stop()
        self.controller.cleanup()
        TRCCMainWindowMVC._instance = None
        event.accept()
        app = QApplication.instance()
        if app:
            app.quit()


def run_mvc_app(data_dir: Path | None = None, decorated: bool = False,
                start_hidden: bool = False):
    """Run the MVC PyQt6 application.

    Args:
        data_dir: Override data directory path.
        decorated: Use decorated window with titlebar.
        start_hidden: Start minimized to system tray (--last-one autostart).
    """
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    font = QFont("Microsoft YaHei", 10)
    if not font.exactMatch():
        font = QFont("Sans Serif", 10)
    app.setFont(font)

    window = TRCCMainWindowMVC(data_dir, decorated=decorated)
    if not start_hidden:
        window.show()

    return app.exec()


if __name__ == '__main__':
    sys.exit(run_mvc_app())
