"""
PyQt6 UCAbout - Control Center / About panel.

Matches Windows TRCC.UCAbout (1274x800)
Shows auto-start, temperature unit, HDD toggle, refresh interval,
language selection, app info, and website link.

Windows controls (from UCAbout.cs):
- button1:      (297, 174) 14x14  Auto-start checkbox
- buttonC:      (297, 214) 14x14  Celsius radio
- buttonF:      (387, 214) 14x14  Fahrenheit radio
- buttonYP:     (297, 254) 14x14  HDD info checkbox
- textBoxTimer: (299, 291) 36x16  Refresh interval (1-100)
- Language checkboxes at y=373/403
"""

import shutil
import webbrowser
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QIcon, QIntValidator, QPalette
from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton

from .assets import Assets, load_pixmap
from .base import BasePanel, create_image_button
from .constants import Layout, Sizes, Styles

# Linux autostart desktop file
_AUTOSTART_DIR = Path.home() / '.config' / 'autostart'
_AUTOSTART_FILE = _AUTOSTART_DIR / 'trcc.desktop'


def _get_trcc_exec() -> str:
    """Resolve full path to trcc binary for autostart Exec= line.

    Tries (in order):
    1. shutil.which('trcc') — pip-installed entry point on PATH
    2. PYTHONPATH=<src> python3 -m trcc.cli — git clone fallback
    """
    import sys
    trcc_path = shutil.which('trcc')
    if trcc_path:
        return trcc_path
    # Fallback: use the running Python to invoke trcc as a module
    # Resolve the src/ directory so PYTHONPATH is set correctly
    src_dir = str(Path(__file__).parent.parent.parent)
    return f'env PYTHONPATH={src_dir} {sys.executable} -m trcc.cli'


def _make_desktop_entry() -> str:
    """Build autostart .desktop file with resolved trcc path."""
    exec_path = _get_trcc_exec()
    return f"""\
[Desktop Entry]
Type=Application
Name=TRCC Linux
Comment=Thermalright LCD Control Center
Exec={exec_path} --last-one
Icon=trcc
Terminal=false
Categories=Utility;System;
X-GNOME-Autostart-enabled=true
"""


def _is_autostart_enabled() -> bool:
    """Check if autostart desktop file exists."""
    return _AUTOSTART_FILE.exists()


def _set_autostart(enabled: bool):
    """Create or remove autostart desktop file."""
    if enabled:
        _AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        _AUTOSTART_FILE.write_text(_make_desktop_entry())
        print(f"[+] Autostart enabled: {_AUTOSTART_FILE}")
    else:
        if _AUTOSTART_FILE.exists():
            _AUTOSTART_FILE.unlink()
        print("[-] Autostart disabled")


def ensure_autostart():
    """Auto-enable autostart on first launch (matches Windows KaijiQidong).

    On first launch: creates .desktop file and marks config as configured.
    On subsequent launches: refreshes .desktop if Exec path changed.
    Returns the current autostart state (bool).
    """
    from ..paths import load_config, save_config

    config = load_config()

    if not config.get('autostart_configured'):
        # First launch — auto-enable (like Windows registry auto-add)
        _set_autostart(True)
        config['autostart_configured'] = True
        save_config(config)
        return True

    if _AUTOSTART_FILE.exists():
        # Refresh .desktop in case Exec path changed (like Windows path mismatch check)
        current = _AUTOSTART_FILE.read_text()
        expected = _make_desktop_entry()
        if current != expected:
            _AUTOSTART_FILE.write_text(expected)
            print(f"[~] Autostart refreshed: {_AUTOSTART_FILE}")

    return _is_autostart_enabled()


class UCAbout(BasePanel):
    """
    Control Center panel matching Windows UCAbout.

    Size: 1274x800 (same as FormCZTV content area).
    Background image is localized (A0关于{lang}.png).
    Interactive elements are invisible overlays on the background image text.
    """

    CMD_STARTUP = 0
    CMD_HDD_REFRESH = 16
    CMD_LANGUAGE = 32
    CMD_CLOSE = 255

    language_changed = pyqtSignal(str)       # lang suffix
    close_requested = pyqtSignal()
    temp_unit_changed = pyqtSignal(str)      # 'C' or 'F'
    startup_changed = pyqtSignal(bool)       # auto-start enabled
    hdd_toggle_changed = pyqtSignal(bool)    # HDD info enabled
    refresh_changed = pyqtSignal(int)        # refresh interval (seconds)

    def __init__(self, lang: str = 'en', parent=None):
        super().__init__(parent, width=Sizes.FORM_W, height=Sizes.FORM_H)

        self._lang = lang
        self._lang_buttons = {}
        self._temp_mode = 'C'
        self._autostart = _is_autostart_enabled()
        self._read_hdd = True
        self._refresh_interval = 1

        # Load checkbox pixmaps
        sz = Layout.ABOUT_CHECKBOX_SIZE
        self._cb_off = load_pixmap(Assets.CHECKBOX_OFF, sz, sz)
        self._cb_on = load_pixmap(Assets.CHECKBOX_ON, sz, sz)

        self._setup_ui()
        self._apply_background()

    def _apply_background(self):
        """Set localized background image via QPalette."""
        bg_name = Assets.get_localized(Assets.ABOUT_BG, self._lang)
        pixmap = load_pixmap(bg_name)
        if not pixmap.isNull():
            palette = self.palette()
            palette.setBrush(QPalette.ColorRole.Window, QBrush(pixmap))
            self.setPalette(palette)
            self.setAutoFillBackground(True)

    def _setup_ui(self):
        """Build UI with invisible click targets over background image text."""
        # Close / logout button (top-right)
        self.close_btn = create_image_button(
            self, *Layout.ABOUT_CLOSE_BTN,
            Assets.ABOUT_LOGOUT, Assets.ABOUT_LOGOUT_HOVER,
            fallback_text="X"
        )
        self.close_btn.clicked.connect(self._on_close)

        # === Auto-start checkbox (button1) ===
        self.startup_btn = self._make_checkbox(
            *Layout.ABOUT_STARTUP, checked=self._autostart)
        self.startup_btn.clicked.connect(self._on_startup_clicked)

        # === Temperature unit radio buttons ===
        self.celsius_btn = self._make_checkbox(*Layout.ABOUT_CELSIUS, checked=True)
        self.celsius_btn.clicked.connect(lambda: self._set_temp('C'))
        self.fahrenheit_btn = self._make_checkbox(*Layout.ABOUT_FAHRENHEIT)
        self.fahrenheit_btn.clicked.connect(lambda: self._set_temp('F'))

        # === HDD info checkbox (buttonYP) ===
        self.hdd_btn = self._make_checkbox(*Layout.ABOUT_HDD, checked=True)
        self.hdd_btn.clicked.connect(self._on_hdd_clicked)

        # === Data refresh interval input (textBoxTimer) ===
        self.refresh_input = QLineEdit("1", self)
        self.refresh_input.setGeometry(*Layout.ABOUT_REFRESH_INPUT)
        self.refresh_input.setMaxLength(3)
        self.refresh_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh_input.setValidator(QIntValidator(1, 100, self))
        self.refresh_input.setStyleSheet(
            "background-color: black; color: #B4964F; border: none;"
            " font-family: 'Microsoft YaHei'; font-size: 9pt;"
        )
        self.refresh_input.editingFinished.connect(self._on_refresh_changed)

        # === Language selection checkboxes ===
        for x, y, lang_suffix in Layout.ABOUT_LANG_BUTTONS:
            btn = self._make_checkbox(x, y, Layout.ABOUT_CHECKBOX_SIZE,
                                      Layout.ABOUT_CHECKBOX_SIZE,
                                      checked=(lang_suffix == self._lang))
            btn.clicked.connect(
                lambda checked, ls=lang_suffix: self._on_lang_clicked(ls))
            self._lang_buttons[lang_suffix] = btn

        # Website button (invisible, over background text area)
        self.website_btn = QPushButton(self)
        self.website_btn.setGeometry(*Layout.ABOUT_WEBSITE)
        self.website_btn.setFlat(True)
        self.website_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.website_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.website_btn.clicked.connect(
            lambda: webbrowser.open('https://www.thermalright.com'))

        # Version label
        self.version_label = QLabel("1.0.0", self)
        self.version_label.setGeometry(*Layout.ABOUT_VERSION)
        self.version_label.setStyleSheet(
            "color: white; font-size: 12px; background: transparent;"
        )
        self.version_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

    def _make_checkbox(self, x, y, w, h, checked=False):
        """Create a checkbox-style toggle button using Windows checkbox images."""
        btn = QPushButton(self)
        btn.setGeometry(x, y, w, h)
        btn.setFlat(True)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setStyleSheet(Styles.FLAT_BUTTON)

        if not self._cb_off.isNull() and not self._cb_on.isNull():
            icon = QIcon(self._cb_off)
            icon.addPixmap(self._cb_on, QIcon.Mode.Normal, QIcon.State.On)
            btn.setIcon(icon)
            btn.setIconSize(btn.size())
        return btn

    # --- Auto-start ---

    def _on_startup_clicked(self):
        """Toggle auto-start on login."""
        self._autostart = self.startup_btn.isChecked()
        _set_autostart(self._autostart)
        self.startup_changed.emit(self._autostart)
        self.invoke_delegate(self.CMD_STARTUP, self._autostart)

    # --- Temperature unit ---

    def _set_temp(self, mode: str):
        """Toggle temperature unit (radio behavior)."""
        self._temp_mode = mode
        self.celsius_btn.setChecked(mode == 'C')
        self.fahrenheit_btn.setChecked(mode == 'F')
        self.temp_unit_changed.emit(mode)

    @property
    def temp_mode(self):
        return self._temp_mode

    # --- HDD info ---

    def _on_hdd_clicked(self):
        """Toggle hard disk information reading."""
        self._read_hdd = self.hdd_btn.isChecked()
        self.hdd_toggle_changed.emit(self._read_hdd)
        self.invoke_delegate(self.CMD_HDD_REFRESH, self._read_hdd,
                             self._refresh_interval)

    @property
    def read_hdd(self):
        return self._read_hdd

    # --- Refresh interval ---

    def _on_refresh_changed(self):
        """Handle refresh interval input change (1-100 seconds)."""
        text = self.refresh_input.text().strip()
        if not text:
            self.refresh_input.setText("1")
            text = "1"
        val = max(1, min(100, int(text)))
        self.refresh_input.setText(str(val))
        self._refresh_interval = val
        self.refresh_changed.emit(val)
        self.invoke_delegate(self.CMD_HDD_REFRESH, self._read_hdd, val)

    @property
    def refresh_interval(self):
        return self._refresh_interval

    # --- Language ---

    def _on_lang_clicked(self, lang_suffix: str):
        """Handle language checkbox click (radio behavior)."""
        for ls, btn in self._lang_buttons.items():
            btn.setChecked(ls == lang_suffix)
        self._lang = lang_suffix
        self._apply_background()
        self.language_changed.emit(lang_suffix)

    # --- Close ---

    def _on_close(self):
        """Handle close/back button."""
        self.close_requested.emit()

    # --- Public API ---

    def set_language(self, lang: str):
        """Update language from external source."""
        self._lang = lang
        for ls, btn in self._lang_buttons.items():
            btn.setChecked(ls == lang)
        self._apply_background()
