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
- Language checkboxes at y=413/443 (v2.1.4, shifted for Running Mode row)
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import webbrowser
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING
from urllib.request import urlopen

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolTip,
)

from ...core._version import parse_version
from .assets import Assets
from .base import BasePanel, create_image_button, set_background_pixmap
from .constants import Layout, Sizes, Styles

if TYPE_CHECKING:
    from ...app import App
    from ...core.ports import Platform
    from ._ui_state import UiStateStore

log = logging.getLogger(__name__)


def ensure_autostart(autostart) -> bool:
    """Auto-enable autostart on first launch; reflect current state otherwise.

    Takes the :class:`AutostartManager` from ``app.platform.autostart()``
    so the helper doesn't need to import the concrete Platform.  The
    "configured" first-run marker now lives on AutostartManager itself
    (`refresh()` is idempotent), so we just call `enable()` once on
    first launch and read `is_enabled()` thereafter.
    """
    if not autostart.is_enabled():
        # First launch: enable + refresh.  Idempotent across re-runs.
        try:
            autostart.enable()
            autostart.refresh()
        except Exception:
            # Best-effort — read-only Platforms (sandboxes, CI) raise here.
            log.debug("ensure_autostart: enable() failed", exc_info=True)
    return autostart.is_enabled()


_GITHUB_LATEST = (
    'https://api.github.com/repos/Lexonight1/thermalright-trcc-linux'
    '/releases/latest'
)


def _check_latest_release() -> tuple[str, dict[str, str]] | None:
    """Fetch latest GitHub release. Returns (version, {ext: download_url}) or None."""
    from urllib.error import URLError
    from urllib.request import Request
    try:
        req = Request(_GITHUB_LATEST, headers={'Accept': 'application/vnd.github+json'})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            tag = data.get('tag_name', '')
            if not (ver := tag.lstrip('v') if tag else None):
                return None
            # Map file extensions to download URLs
            assets: dict[str, str] = {}
            for asset in data.get('assets', []):
                name = asset.get('name', '')
                url = asset.get('browser_download_url', '')
                if name.endswith('.pkg.tar.zst'):
                    assets['pacman'] = url
                elif name.endswith('.rpm'):
                    assets['dnf'] = url
                elif name.endswith('.deb'):
                    assets['apt'] = url
            return ver, assets
    except (URLError, OSError, TimeoutError, ValueError) as e:
        log.debug("uc_about: GitHub release check failed: %s", e)
        return None


def _detect_distro() -> str:
    """Detect the Linux distro ID (e.g. 'fedora', 'arch', 'ubuntu')."""
    try:
        with Path('/etc/os-release').open() as f:
            for line in f:
                if line.startswith('ID='):
                    return line.strip().split('=', 1)[1].strip('"')
    except OSError:
        pass
    return 'unknown'


def _detect_install_method() -> str:
    """Detect how trcc-linux was installed.

    Returns 'pipx', 'pip', 'pacman', 'dnf', or 'apt'.
    """
    # pipx installs into its own venv
    if 'pipx' in sys.prefix:
        return 'pipx'
    try:
        from importlib.metadata import PackageNotFoundError, distribution
        dist = distribution('trcc-linux')
        installer = (dist.read_text('INSTALLER') or '').strip()
        if installer == 'pip':
            return 'pip'
    except (PackageNotFoundError, OSError) as e:
        log.debug("uc_about: trcc-linux distribution metadata unavailable: %s", e)
    # Detect which package manager installed it
    for mgr in ('pacman', 'dnf', 'apt'):
        if shutil.which(mgr):
            return mgr
    return 'pip'  # fallback


def _get_install_info(ui_state: UiStateStore | None = None) -> tuple[str, str]:
    """Get install method + distro from UiState; detect+save on first call."""
    if ui_state is not None:
        cached = ui_state.get_install_info()
        if cached is not None:
            return cached['method'], cached['distro']
    method = _detect_install_method()
    distro = _detect_distro()
    if ui_state is not None:
        ui_state.set_install_info(method, distro)
    log.info("Recorded install info: method=%s, distro=%s", method, distro)
    return method, distro


class UCAbout(BasePanel):
    """
    Control Center panel matching Windows UCAbout.

    Size: 1274x800 (same as FormCZTV content area).
    Background image is localized (sidebar_about_bg{lang}.png).
    Interactive elements are invisible overlays on the background image text.
    """

    CMD_STARTUP = 0
    CMD_HDD_REFRESH = 16
    CMD_LANGUAGE = 32
    CMD_CLOSE = 255

    language_changed = Signal(str)       # lang suffix
    close_requested = Signal()
    temp_unit_changed = Signal(str)      # 'C' or 'F'
    startup_changed = Signal(bool)       # auto-start enabled
    hdd_toggle_changed = Signal(bool)    # HDD info enabled
    refresh_changed = Signal(int)        # refresh interval (seconds)
    gpu_changed = Signal(str)            # gpu_key for metrics
    _update_available = Signal(str, dict) # (version, {mgr: download_url})
    _upgrade_finished = Signal(bool)     # True=success, False=failure

    def __init__(self, parent=None, platform: Platform | None = None,
                 gpu_list: list[tuple[str, str]] | None = None,
                 app: App | None = None,
                 ui_state: UiStateStore | None = None):
        super().__init__(parent, width=Sizes.FORM_W, height=Sizes.FORM_H)

        self._platform = platform
        self._app = app              # next/ App for Command dispatch
        self._ui_state = ui_state    # GUI-only persisted prefs
        self._gpu_list = gpu_list or []
        self._lang_buttons: dict[str, QPushButton] = {}  # Legacy — populated by combo in trcc_app
        self._temp_mode = 'C'
        autostart_mgr = platform.autostart() if platform else None
        self._autostart = autostart_mgr.is_enabled() if autostart_mgr else False
        # Initial values pulled from App settings (per Cross-cutting setter audit)
        if app is not None:
            self._read_hdd = app.settings.app.hdd_enabled
            self._refresh_interval = int(app.settings.app.refresh_interval_s)
            self._gpu_device = app.settings.app.active_gpu or ''
        else:
            self._read_hdd = False
            self._refresh_interval = 2
            self._gpu_device = ''

        # Load checkbox pixmaps
        sz = Layout.ABOUT_CHECKBOX_SIZE
        self._cb_off = Assets.load_pixmap(Assets.CHECKBOX_OFF, sz, sz)
        self._cb_on = Assets.load_pixmap(Assets.CHECKBOX_ON, sz, sz)

        self._setup_ui()
        self._apply_localized_background()

    def _apply_localized_background(self):
        """Set background image (no tiling)."""
        set_background_pixmap(self, Assets.ABOUT_BG)

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
        self.celsius_btn.clicked.connect(self._on_celsius_clicked)
        self.fahrenheit_btn = self._make_checkbox(*Layout.ABOUT_FAHRENHEIT)
        self.fahrenheit_btn.clicked.connect(self._on_fahrenheit_clicked)

        # === HDD info checkbox (buttonYP) ===
        self.hdd_btn = self._make_checkbox(*Layout.ABOUT_HDD, checked=self._read_hdd)
        self.hdd_btn.clicked.connect(self._on_hdd_clicked)

        # === Data refresh interval input (textBoxTimer) ===
        self.refresh_input = QLineEdit(str(self._refresh_interval), self)
        self.refresh_input.setGeometry(*Layout.ABOUT_REFRESH_INPUT)
        self.refresh_input.setMaxLength(3)
        self.refresh_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh_input.setValidator(QIntValidator(1, 100, self))
        self.refresh_input.setStyleSheet(
            "background-color: black; color: #B4964F; border: none;"
            " font-family: 'Microsoft YaHei'; font-size: 9pt;"
        )
        self.refresh_input.setToolTip("Data refresh interval (seconds)")
        self.refresh_input.editingFinished.connect(self._on_refresh_changed)

        # === Running Mode radio buttons (v2.1.4: buttonSingle / buttonMulti) ===
        # Visual-only — always multi-threaded on Linux (Qt signals handle threading)
        self.single_thread_btn = self._make_checkbox(
            *Layout.ABOUT_SINGLE_THREAD, checked=False)
        self.single_thread_btn.clicked.connect(self._on_single_thread_clicked)
        self.multi_thread_btn = self._make_checkbox(
            *Layout.ABOUT_MULTI_THREAD, checked=True)
        self.multi_thread_btn.clicked.connect(self._on_multi_thread_clicked)

        # Website button (invisible, over background text area)
        self.website_btn = QPushButton(self)
        self.website_btn.setGeometry(*Layout.ABOUT_WEBSITE)
        self.website_btn.setFlat(True)
        self.website_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.website_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.website_btn.setToolTip("Open thermalright.com")
        self.website_btn.clicked.connect(self._on_website_clicked)

        # Version label
        from trcc.__version__ import __version__
        self.version_label = QLabel(__version__, self)
        self.version_label.setGeometry(*Layout.ABOUT_VERSION)
        self.version_label.setStyleSheet(
            "color: white; font-size: 16px; font-weight: bold; background: transparent;"
        )
        self.version_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        # === Software update area (buttonBCZT — dark icon baked into background) ===
        # Light overlay shown on top when update is available
        self._update_tooltip = "Running latest"
        self._update_rect = self.rect().__class__(  # QRect
            *Layout.ABOUT_UPDATE_BTN)

        # Overlay label — shows light update icon when update available
        self._update_overlay = QLabel(self)
        self._update_overlay.setGeometry(*Layout.ABOUT_UPDATE_BTN)
        px = Assets.load_pixmap(Assets.UPDATE_BTN, *Layout.ABOUT_UPDATE_BTN[2:])
        if not px.isNull():
            self._update_overlay.setPixmap(px)
        self._update_overlay.hide()

        # Invisible click target (always present over the baked-in dark icon)
        self.update_btn = QPushButton(self)
        self.update_btn.setGeometry(*Layout.ABOUT_UPDATE_BTN)
        self.update_btn.setFlat(True)
        self.update_btn.setStyleSheet(Styles.FLAT_BUTTON)
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.installEventFilter(self)
        self.update_btn.clicked.connect(self._on_update_clicked)
        self._update_available.connect(self._on_update_result)
        self._upgrade_finished.connect(self._on_upgrade_done)
        self._latest_version: str | None = None
        self._install_method, self._distro = _get_install_info(self._ui_state)

        # Check GitHub for updates in background, then every hour
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._start_update_check)
        self._update_timer.start(60 * 60 * 1000)  # 1 hour
        Thread(target=self._check_for_update, daemon=True).start()

        # === GPU selection (below language row) ===
        self._setup_gpu_widget()

    def _show_update_tooltip(self):
        """Show tooltip to the right of the update button, vertically centered."""
        tip_pos = self.mapToGlobal(
            QPoint(self._update_rect.right() + 4,
                   self._update_rect.center().y() - 36))
        QToolTip.showText(tip_pos, self._update_tooltip, self,
                          self._update_rect)

    def event(self, e: QEvent) -> bool:
        """Show update tooltip to the right of the button area."""
        if e.type() == QEvent.Type.ToolTip:
            pos = e.pos()  # pyright: ignore[reportAttributeAccessIssue]
            if self._update_rect.contains(pos):
                self._show_update_tooltip()
                return True
        return super().event(e)

    def eventFilter(self, obj, e: QEvent) -> bool:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Intercept tooltip on update button to use same custom position."""
        if obj is self.update_btn and e.type() == QEvent.Type.ToolTip:
            self._show_update_tooltip()
            return True
        return super().eventFilter(obj, e)

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
        log.info("_on_startup_clicked")
        self._autostart = self.startup_btn.isChecked()
        if self._platform:
            autostart = self._platform.autostart()
            if self._autostart:
                autostart.enable()
            else:
                autostart.disable()
        self.startup_changed.emit(self._autostart)
        self.invoke_delegate(self.CMD_STARTUP, self._autostart)

    # --- Temperature unit ---

    def _on_celsius_clicked(self) -> None:
        log.info("_on_celsius_clicked")
        self._set_temp('C')

    def _on_fahrenheit_clicked(self) -> None:
        log.info("_on_fahrenheit_clicked")
        self._set_temp('F')

    def _on_website_clicked(self) -> None:
        log.info("_on_website_clicked")
        webbrowser.open('https://www.thermalright.com')

    def _start_update_check(self) -> None:
        """Kick a background update check — slot fired by the 1-hour QTimer."""
        Thread(target=self._check_for_update, daemon=True).start()

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
        log.info("_on_hdd_clicked")
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
        log.info("_on_refresh_changed")
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

    # --- GPU selection ---

    def _setup_gpu_widget(self):
        """Create GPU label or dropdown depending on GPU count."""
        x, y, w, h = Layout.ABOUT_GPU_COMBO
        if len(self._gpu_list) <= 1:
            # Single GPU or none — plain text label
            name = self._gpu_list[0][1] if self._gpu_list else 'No GPU detected'
            self._gpu_label = QLabel(name, self)
            self._gpu_label.setGeometry(x, y, w, h)
            self._gpu_label.setStyleSheet(
                "color: white; font-size: 10pt; background: transparent;"
                " padding-left: 5px;")
        else:
            # Multiple GPUs — dropdown
            self._gpu_combo = QComboBox(self)
            self._gpu_combo.setGeometry(x, y, w, h)
            for gpu_key, display_name in self._gpu_list:
                self._gpu_combo.addItem(display_name, gpu_key)
            # Pre-select saved GPU
            if self._gpu_device:
                idx = self._gpu_combo.findData(self._gpu_device)
                if idx >= 0:
                    self._gpu_combo.setCurrentIndex(idx)
            self._gpu_combo.setStyleSheet(
                "QComboBox { background: #2A2A2A; color: white; border: 1px solid #555;"
                " font-size: 10pt; padding-left: 5px; }"
                "QComboBox::drop-down { border: none; width: 20px; }"
                "QComboBox QAbstractItemView { background: #2A2A2A; color: white;"
                " selection-background-color: #3A3A3A; }")
            self._gpu_combo.currentIndexChanged.connect(self._on_gpu_selected)

    def _on_gpu_selected(self, index: int):
        """Handle GPU dropdown selection."""
        gpu_key = self._gpu_combo.itemData(index)
        if gpu_key:
            log.info("GPU selected: %s", gpu_key)
            self.gpu_changed.emit(gpu_key)

    # --- Running Mode ---

    def _on_single_thread_clicked(self) -> None:
        log.info("_on_single_thread_clicked")
        self._set_thread_mode(False)

    def _on_multi_thread_clicked(self) -> None:
        log.info("_on_multi_thread_clicked")
        self._set_thread_mode(True)

    def _set_thread_mode(self, multi: bool):
        """Toggle running mode radio buttons (visual only, not wired)."""
        self.single_thread_btn.setChecked(not multi)
        self.multi_thread_btn.setChecked(multi)

    # --- Language ---

    def _on_lang_clicked(self, lang_suffix: str):
        """Handle language selection."""
        log.info("_on_lang_clicked: lang_suffix=%s", lang_suffix)
        self.language_changed.emit(lang_suffix)

    # --- Software update ---

    # Install commands per package manager (pkexec provides the sudo prompt)
    _PKG_INSTALL: dict[str, list[str]] = {
        'pacman': ['pkexec', 'pacman', '-U', '--noconfirm'],
        'dnf':    ['pkexec', 'dnf', 'install', '-y'],
        'apt':    ['pkexec', 'apt', 'install', '-y'],
    }

    def _check_for_update(self):
        """Background thread: query for newer release via the App."""
        if self._app is not None:
            from ...core.commands import CheckForUpdate
            r = self._app.dispatch(CheckForUpdate())
            if (r.ok and getattr(r, 'update_available', False)
                    and getattr(r, 'latest_version', None)):
                # next/'s CheckForUpdateResult shape: latest_version + assets.
                assets = getattr(r, 'assets', {}) or {}
                self._update_available.emit(r.latest_version, assets)
            return
        # Fallback to the direct GitHub-API helper if no App was passed.
        if (result := _check_latest_release()):
            ver, assets = result
            self._update_available.emit(ver, assets)

    def _on_update_result(self, latest: str, assets: dict[str, str]):
        """Handle version check result (runs on main thread via signal)."""
        from trcc.__version__ import __version__
        if parse_version(latest) > parse_version(__version__):
            self._latest_version = latest
            self._pkg_assets = assets
            self._update_tooltip = f"Version {latest} available — click to update"
            self._update_overlay.show()
            log.info("Update available: %s → %s", __version__, latest)

    def _on_update_clicked(self):
        """Perform update based on install method."""
        if not self._latest_version:
            return

        self._update_overlay.hide()
        self._update_tooltip = "Updating..."
        log.info("Starting %s upgrade to %s",
                 self._install_method, self._latest_version)
        Thread(target=self._run_upgrade, daemon=True).start()

    def _run_upgrade(self):
        """Background thread: dispatch RunUpgrade via the App."""
        if self._app is None:
            log.error("_run_upgrade: no App — widget constructed without it")
            self._upgrade_finished.emit(False)
            return
        from ...core.commands import RunUpgrade
        result = self._app.dispatch(RunUpgrade())
        if result.ok:
            log.info("%s", result.message)
        else:
            log.error("Upgrade failed: %s", result.message)
        self._upgrade_finished.emit(result.ok)

    # --- Diagnostics ---

    def contextMenuEvent(self, event) -> None:
        """Right-click → 'Save diagnostic report…'.

        A context menu rather than a visible button keeps the pixel-perfect
        Windows-mirror layout untouched while still giving users (and the
        maintainer triaging an issue) a one-click `trcc report` bundle.
        """
        menu = QMenu(self)
        save_report = menu.addAction("Save diagnostic report…")
        chosen = menu.exec(event.globalPos())
        if chosen is save_report:
            self._on_save_diagnostic_report()

    def _on_save_diagnostic_report(self) -> None:
        log.info("_on_save_diagnostic_report: opening save dialog")
        if self._app is None:
            log.error("_on_save_diagnostic_report: no App — cannot dispatch")
            return
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save diagnostic report", "trcc-debug-report.txt",
            "Text files (*.txt);;All files (*)",
        )
        if not path_str:
            return
        from ...core.commands import GenerateDebugReport
        log.info("_on_save_diagnostic_report: writing report to %s", path_str)
        r = self._app.dispatch(GenerateDebugReport(
            output_path=Path(path_str), log_tail_lines=1000,
        ))
        center = self.mapToGlobal(self.rect().center())
        if r.ok:
            log.info("Diagnostic report saved: %s", r.output_path)
            QToolTip.showText(center, f"Saved report to {r.output_path}")
        else:
            log.error("Diagnostic report failed: %s", r.message)
            QToolTip.showText(center, f"Report failed: {r.message}")

    def _on_upgrade_done(self, success: bool):
        """Post-upgrade: show restart message or re-enable button on failure."""
        log.info("_on_upgrade_done: success=%s", success)
        if success:
            self._update_tooltip = "Updated — restart to apply"
        else:
            self._update_tooltip = (
                f"Version {self._latest_version} available — click to retry")
            self._update_overlay.show()

    # --- Close ---

    def _on_close(self):
        """Handle close/back button."""
        log.info("_on_close")
        self.close_requested.emit()

    # --- Public API ---

    def sync_language(self):
        """Sync background to current settings.lang."""
        self._apply_localized_background()
