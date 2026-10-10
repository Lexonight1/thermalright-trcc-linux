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

import logging
import weakref
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QIcon, QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolTip,
)

from ...core._version import parse_version
from ...core.commands import (
    ControlCenterSnapshot,
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    GetPlatformInfo,
    RefreshAutostart,
)
from ...core.models import DEFAULT_REFRESH_INTERVAL_S
from ...core.results import ControlCenterSnapshotResult
from .assets import Assets
from .base import BasePanel, create_image_button, set_background_pixmap
from .constants import Layout, Sizes, Styles

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...core.ports import CommandBus
    from ._ui_state import UiStateStore

log = logging.getLogger(__name__)


def ensure_autostart(app: CommandBus) -> bool:
    """Auto-enable autostart ONCE, on first launch; re-render it otherwise.

    GUI-launch policy, kept here deliberately rather than pushed into a
    Command: qtgui's system panel reads and toggles autostart but has never
    auto-enabled it, and moving this would hand it a behaviour it does not
    have.  That is a product decision, not a refactor.

    "First launch" is ``configured``, not ``enabled``: Enable and Disable both
    record the choice, from any UI.  Keyed on ``enabled`` it turned a user's
    "off" back on at every gui launch — a cutover regression; legacy and the
    C# (a ``boot`` marker file, ``Form1.cs:290``) both enable once.
    ``RefreshAutostart`` re-renders an existing entry so a moved install picks
    up a new launch line (#201), and never creates one — so "off" stays off.
    """
    if not app.dispatch(GetAutostartStatus()).configured:
        log.info("ensure_autostart: never configured — enabling on first launch")
        return app.dispatch(EnableAutostart()).enabled
    log.info("ensure_autostart: configured — refreshing any existing entry")
    return app.dispatch(RefreshAutostart()).enabled


def _get_install_info(
    app: CommandBus | None, ui_state: UiStateStore | None = None,
) -> tuple[str, str]:
    """Install method + distro from UiState; ask the bus on first call.

    One ``GetPlatformInfo`` answers both.  It used to be two hand-rolled
    detectors here: one imported ``detect_installer`` from an adapter — the
    Command reaches the identical function, so that half was always the same
    answer by a longer route — and the other opened ``/etc/os-release`` and
    parsed ``ID=`` itself, which no gate could see.  A UI reading an OS file
    is not an adapter import, not a service import and not a subprocess, so
    all three boundary checks stayed green over it.

    The distro string changes shape as a result: the port reports the pretty
    name ("Fedora Linux 44") where the old parser reported the ID ("fedora").
    Nothing reads it — it is cached and handed back to a field with no
    consumer — so the value is preserved rather than dropped, and removing
    the dead state is a separate cleanup.
    """
    if ui_state is not None:
        cached = ui_state.get_install_info()
        if cached is not None:
            return cached['method'], cached['distro']
    if app is None:
        # Panels are constructed without an App in some harnesses; report
        # unknown rather than falling back to a second detector, which is the
        # divergence this replaced.
        log.warning("_get_install_info: no App — install method unknown")
        return "unknown", "unknown"
    info = app.dispatch(GetPlatformInfo())
    method, distro = info.install_method, info.distro_name
    if ui_state is not None:
        ui_state.set_install_info(method, distro)
    log.info("Recorded install info: method=%s, distro=%s", method, distro)
    return method, distro


class _ToolTipFilter(QObject):
    """Turn a watched widget's ToolTip event into one callback.

    Owned by the widget it filters, so Qt destroys it with that widget and it
    can never be consulted after its Python side is gone.  A panel that filters
    its own child instead is the crash documented at the install site below.
    """

    def __init__(self, on_tooltip: Callable[[], None],
                 parent: QObject | None = None) -> None:
        log.debug("_ToolTipFilter.__init__: parent=%s", type(parent).__name__)
        super().__init__(parent)
        # WEAK, and that is the whole point.  A bound method held strongly here
        # closes a cycle that spans both object graphs -- panel owns button owns
        # filter, filter refs panel -- and Qt tears the C++ half down on a
        # different schedule than CPython frees the Python half.  Measured: an
        # identical filter holding ``lambda: None`` survives, and one holding
        # the bound method segfaults even when it is never INSTALLED.
        self._on_tooltip = weakref.WeakMethod(on_tooltip)  # type: ignore[arg-type]

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.ToolTip:
            return False
        handler = self._on_tooltip()
        if handler is None:
            # The panel is gone; the tooltip has nowhere to go.  Swallow it
            # rather than reviving a dead widget.
            log.debug("_ToolTipFilter.eventFilter: owner gone — dropping")
            return True
        log.debug("_ToolTipFilter.eventFilter: tooltip on %s",
                  type(obj).__name__)
        handler()
        return True


class UCAbout(BasePanel):
    """
    Control Center panel matching Windows UCAbout.

    Size: 1274x800 (same as FormCZTV content area).
    Background image is localized (sidebar_about_bg{lang}.png).
    Interactive elements are invisible overlays on the background image text.
    """

    CMD_HDD_REFRESH = 16
    CMD_LANGUAGE = 32
    CMD_CLOSE = 255

    language_changed = Signal(str)       # lang suffix
    close_requested = Signal()
    temp_unit_changed = Signal(str)      # 'C' or 'F'
    hdd_toggle_changed = Signal(bool)    # HDD info enabled
    refresh_changed = Signal(int)        # refresh interval (seconds)
    gpu_changed = Signal(str)            # gpu_key for metrics

    def __init__(self, parent=None,
                 gpu_list: list[tuple[str, str]] | None = None,
                 app: CommandBus | None = None,
                 ui_state: UiStateStore | None = None):
        super().__init__(parent, width=Sizes.FORM_W, height=Sizes.FORM_H)

        self._app = app              # next/ App for Command dispatch
        self._ui_state = ui_state    # GUI-only persisted prefs
        self._gpu_list = gpu_list or []
        self._lang_buttons: dict[str, QPushButton] = {}  # Legacy — populated by combo in trcc_app
        self._temp_mode = 'C'
        # Initial values pulled from App settings (per Cross-cutting setter audit)
        if app is not None:
            # One Query, three fields — and the same Query the control
            # centre uses, so the panel and the snapshot cannot disagree
            # about what the app settings are.
            cc = app.dispatch(ControlCenterSnapshot())
            self._autostart = app.dispatch(GetAutostartStatus()).enabled
            self._read_hdd = cc.hdd_enabled
            self._refresh_interval = int(cc.refresh_interval_s)
            self._gpu_device = cc.active_gpu or ''
        else:
            self._autostart = False
            self._read_hdd = False
            self._refresh_interval = int(DEFAULT_REFRESH_INTERVAL_S)
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
        # The filter is its OWN QObject, parented to the BUTTON it watches —
        # not ``self``.
        #
        # Installing the panel as a filter on its own child segfaults Qt during
        # teardown, every time.  Core dump (2026-09-20):
        #
        #     QObject::property(char const*)
        #     PySide::getWrapperForQObject(QObject*, _typeobject*)
        #     QCoreApplicationPrivate::sendThroughObjectEventFilters(...)
        #     QCoreApplicationPrivate::sendPostedEvents(...)
        #
        # Destroying the panel destroys the button, Qt dispatches through the
        # button's filter list, and PySide tries to resolve the Python wrapper
        # for a filter that is itself mid-destruction.  A filter owned by the
        # object it watches dies WITH it and is never consulted afterwards —
        # which is exactly the shape ``_BgPaintFilter`` in ``base.py`` already
        # uses, and why that one has never crashed.
        self._tooltip_filter = _ToolTipFilter(
            self._on_update_btn_tooltip, self.update_btn)
        self.update_btn.installEventFilter(self._tooltip_filter)
        self.update_btn.clicked.connect(self._on_update_clicked)
        self._latest_version: str | None = None
        self._install_method, self._distro = _get_install_info(self._app, self._ui_state)

        # The App checks for updates (UpdateWatch) -- at session start, then
        # hourly -- and this panel shows its answer: the last one now, each
        # new one through ``on_update_checked``.  It starts no thread: the
        # one it used to start held this widget and deadlocked Qt when a
        # window was dropped mid-check.
        self._show_last_update()

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

    def _on_update_btn_tooltip(self) -> None:
        """The button asked for a tooltip — place it the panel's way."""
        log.debug("_on_update_btn_tooltip")
        self._show_update_tooltip()

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
        if self._app is not None:
            self._app.dispatch(
                EnableAutostart() if self._autostart else DisableAutostart()
            )

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

    def _set_temp(self, mode: str):
        """Toggle temperature unit (radio behavior)."""
        log.info("_set_temp: %s", mode)
        self.show_temp_unit(mode)
        self.temp_unit_changed.emit(mode)

    def show_app_settings(self, cc: ControlCenterSnapshotResult) -> None:
        """Show the App's control-centre settings — sends nothing.

        The HDD box and the refresh field emit only on a click and on
        editing, so setting them is silent; the GPU picker emits on any
        index change, so it is blocked while it is set.
        """
        log.info("show_app_settings: hdd=%s refresh=%ss gpu=%s unit=%s",
                 cc.hdd_enabled, cc.refresh_interval_s, cc.active_gpu,
                 cc.temp_unit)
        self.show_temp_unit(cc.temp_unit)
        self._read_hdd = cc.hdd_enabled
        self.hdd_btn.setChecked(cc.hdd_enabled)
        self._refresh_interval = int(cc.refresh_interval_s)
        self.refresh_input.setText(str(self._refresh_interval))
        self._gpu_device = cc.active_gpu or ''
        if self._gpu_combo is not None:   # a picker only with two GPUs or more
            self._gpu_combo.blockSignals(True)
            self._gpu_combo.setCurrentIndex(self._gpu_combo.findData(self._gpu_device))
            self._gpu_combo.blockSignals(False)

    def show_autostart(self, enabled: bool) -> None:
        """Show whether TRCC starts at login -- sends nothing (the button
        emits on a click, not on ``setChecked``)."""
        log.info("show_autostart: %s", enabled)
        self._autostart = enabled
        self.startup_btn.setChecked(enabled)

    def show_temp_unit(self, mode: str) -> None:
        """Show the App's temperature unit — sends nothing back."""
        log.info("show_temp_unit: %s → %s", self._temp_mode, mode)
        self._temp_mode = mode
        self.celsius_btn.setChecked(mode == 'C')
        self.fahrenheit_btn.setChecked(mode == 'F')

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
            self._gpu_combo: QComboBox | None = None
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
        assert self._gpu_combo is not None   # connected only to the picker
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

    def _show_last_update(self) -> None:
        """The App's last answer about updates -- read once, no network."""
        if self._app is None:
            log.debug("_show_last_update: no App -- nothing to show")
            return
        from ...core.commands import UpdateStatus
        r = self._app.dispatch(UpdateStatus())
        log.debug("_show_last_update: ok=%s available=%s latest=%s",
                  r.ok, r.update_available, r.latest_version)
        self._show_update(r.ok and r.update_available, r.latest_version)

    def on_update_checked(self, event: object) -> None:
        """``UpdateChecked``: the App's newest answer, on the GUI thread."""
        available = bool(getattr(event, "ok", False)
                         and getattr(event, "update_available", False))
        latest = str(getattr(event, "latest_version", ""))
        log.info("on_update_checked: available=%s latest=%s", available,
                 latest)
        self._show_update(available, latest)

    def _show_update(self, available: bool, latest: str) -> None:
        """Show the update button when *latest* is newer than this TRCC."""
        from trcc.__version__ import __version__
        if not (available and latest
                and parse_version(latest) > parse_version(__version__)):
            log.debug("_show_update: nothing newer than %s", __version__)
            return
        self._latest_version = latest
        self._update_tooltip = f"Version {latest} available — click to update"
        self._update_overlay.show()
        log.info("Update available: %s → %s", __version__, latest)

    def _on_update_clicked(self):
        """Perform update based on install method."""
        if not self._latest_version:
            return

        log.info("_on_update_clicked: %s install, %s available",
                 self._install_method, self._latest_version)
        if self._app is None:
            log.error("_on_update_clicked: no App — widget constructed without it")
            return
        from ...core.commands import RunUpgrade
        result = self._app.dispatch(RunUpgrade())
        # The command for THIS install, to run -- TRCC no longer runs a
        # package manager that upgraded everything but itself.
        box = QMessageBox(QMessageBox.Icon.Information, "Upgrade TRCC",
                          f"Version {self._latest_version} is available.\n\n"
                          f"{result.message}", parent=self)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.exec()

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

    # --- Close ---

    def _on_close(self):
        """Handle close/back button."""
        log.info("_on_close")
        self.close_requested.emit()
