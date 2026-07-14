"""GUI entry — QApplication + MainWindow shell.

MainWindow is a horizontal split: an ``ActivitySidebar`` on the left and
a ``QStackedWidget`` on the right that swaps the active panel.  Every
panel subclasses :class:`BasePanel` so they share the same ``app`` /
``bus`` plumbing.

Adding a panel: register the widget on the stacked container with the
same key the sidebar emits, and add an entry to ``sidebar._ENTRIES``.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from ...app import App
from ...core.commands import RenderAndSend

if TYPE_CHECKING:
    from ...core.ports import Platform
from ...core.events import (
    DeviceConnected,
    DeviceDisconnected,
    ErrorOccurred,
    FrameSent,
    ThemeLoaded,
)
from ..qt_tray import TrayController
from .bus_bridge import BusBridge
from .panels import (
    AboutPanel,
    ActivitySidebar,
    CloudThemeBrowser,
    ConfigurationPanel,
    DevicePanel,
    DisplayPanel,
    LedPanel,
    LocalThemeBrowser,
    MaskBrowser,
    OverlayEditorPanel,
    PreviewPanel,
    ScreencastPanel,
    StatusPanel,
    SystemPanel,
)
from .splash import auto_close, show_splash

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Top-level window: sidebar + stacked content + status bar."""

    def __init__(self, app: App) -> None:
        super().__init__()
        self._app = app
        self._bus = BusBridge(app.events)

        self.setWindowTitle("TRCC — Thermalright LCD/LED Cooler Control (next)")
        self.resize(960, 640)

        # ── Layout: sidebar | stacked content ──
        sidebar = ActivitySidebar(app, self._bus, self)
        content = QStackedWidget(self)
        content.setObjectName("trcc-content")

        # Register panels.  Key matches the sidebar entry's key.
        self._panels: dict[str, QWidget] = {
            "devices": DevicePanel(app, self._bus, self),
            "display": DisplayPanel(app, self._bus, self),
            "preview": PreviewPanel(app, self._bus, self),
            "themes":  LocalThemeBrowser(app, self._bus, self),
            "cloud":   CloudThemeBrowser(app, self._bus, self),
            "masks":   MaskBrowser(app, self._bus, self),
            "overlay": OverlayEditorPanel(app, self._bus, self),
            "screencast": ScreencastPanel(app, self._bus, self),
            "config":  ConfigurationPanel(app, self._bus, self),
            "led":     LedPanel(app, self._bus, self),
            "status":  StatusPanel(app, self._bus, self),
            "system":  SystemPanel(app, self._bus, self),
            "about":   AboutPanel(app, self._bus, self),
        }
        for widget in self._panels.values():
            content.addWidget(widget)
        # First-run users land on System (where the doctor lives) so the
        # welcome screen guides them; everyone else starts on Devices.
        initial = "system" if app.first_run.is_first_run() else "devices"
        content.setCurrentWidget(self._panels[initial])
        sidebar.select(initial)
        sidebar.selected.connect(
            lambda key: content.setCurrentWidget(
                self._panels.get(key, self._panels["devices"]),
            ),
        )

        container = QWidget(self)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(sidebar)
        row.addWidget(content, 1)
        self.setCentralWidget(container)

        status = QStatusBar(self)
        self.setStatusBar(status)
        self._status = status

        # EventBus → status bar (thread-safe via Qt.QueuedConnection)
        qconn = Qt.ConnectionType.QueuedConnection
        self._bus.device_connected.connect(self._on_connected, type=qconn)
        self._bus.device_disconnected.connect(self._on_disconnected, type=qconn)
        self._bus.frame_sent.connect(self._on_frame_sent, type=qconn)
        self._bus.theme_loaded.connect(self._on_theme_loaded, type=qconn)
        self._bus.error_occurred.connect(self._on_error, type=qconn)

        # Playback ticker — dispatches RenderAndSend to every device with
        # an active theme, at AppSettings.refresh_interval_s.  Started
        # lazily when a theme gets loaded; stops when no active themes
        # remain.
        self._ticker = QTimer(self)
        self._ticker.setSingleShot(False)
        self._ticker.timeout.connect(self._on_tick)

        self._show_platform_info()

        # Shared tray: a window-close hides to the tray (keeps the LCD running)
        # exactly like the gui skin — via the shared TrayController, not a
        # qtgui-local reinvention.  Exit (menu) or a force-quit ends the process.
        icon_path = (Path(__file__).resolve().parents[2]
                     / "assets" / "icons" / "trcc.png")
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._tray = TrayController(
            self, minimize_on_close=app.platform.minimize_on_close(), icon=icon,
        )
        self._tray.install()

    def closeEvent(self, event: Any) -> None:
        if self._tray.intercept_close(event):
            return
        # Genuine quit: stop the playback ticker; the daemon-thread loops die
        # with the process.
        self._ticker.stop()
        event.accept()

    def _show_platform_info(self) -> None:
        platform = self._app.platform
        msg = (f"{platform.distro_name()}  |  install: {platform.install_method()}"
               f"  |  config: {platform.paths().config_dir()}")
        if self._app.first_run.is_first_run():
            msg = (
                "Welcome to TRCC.  Open System → run Doctor to check your "
                "setup, then plug in a device and open Devices to scan."
            )
        self._status.showMessage(msg)

    # ── Event handlers ────────────────────────────────────────────────

    def _on_connected(self, event: DeviceConnected) -> None:
        log.info("_on_connected")
        w, h = event.resolution
        self._status.showMessage(f"Connected: {event.key} ({w}×{h})", 5000)

    def _on_disconnected(self, event: DeviceDisconnected) -> None:
        log.info("_on_disconnected")
        self._status.showMessage(f"Disconnected: {event.key}", 5000)

    def _on_frame_sent(self, event: FrameSent) -> None:
        log.info("_on_frame_sent")
        self._status.showMessage(f"Frame sent: {event.bytes_sent} bytes", 2000)

    def _on_error(self, event: ErrorOccurred) -> None:
        log.info("_on_error")
        self._status.showMessage(f"Error [{event.kind}]: {event.message}", 8000)

    def _on_theme_loaded(self, event: ThemeLoaded) -> None:
        """A theme got loaded on some device — make sure the ticker is running."""
        log.info("_on_theme_loaded")
        del event
        self._ensure_ticker_running()

    def _ensure_ticker_running(self) -> None:
        """Start the QTimer if there are active themes; stop it otherwise."""
        if not self._app.active_themes:
            if self._ticker.isActive():
                self._ticker.stop()
            return
        interval_ms = max(100, int(self._app.settings.app.refresh_interval_s * 1000))
        if not self._ticker.isActive() or self._ticker.interval() != interval_ms:
            self._ticker.start(interval_ms)

    def _on_tick(self) -> None:
        """Fire one render+send for every device with an active theme."""
        if not self._app.active_themes:
            self._ticker.stop()
            return
        for key in list(self._app.active_themes):
            try:
                self._app.dispatch(RenderAndSend(key=key))
            except Exception as e:
                log.exception("Tick failed for %s: %s", key, e)


def run(platform: Platform | None = None) -> int:
    """Start the qtgui skin from an injected ``Platform``.  Returns the exit code.

    The unified UI-launch contract (see ``METHOD_UI.md``): the composition root
    injects the ``Platform`` port; this UI composes its own App from it via the
    shared ``build_qt_app`` (Qt-first, so ``QtRenderer`` finds a real
    QApplication), then runs.  ``platform=None`` uses the host platform; the dev
    mock injects a ``MockPlatform``.
    """
    from ..qapp import build_qt_app
    app = build_qt_app(platform)
    qapp = QApplication.instance()
    if not isinstance(qapp, QApplication):   # build_qt_app just created it
        qapp = QApplication(sys.argv)
    # quitOnLastWindowClosed stays False (the shared build_qt_app default): the
    # MainWindow's TrayController hides to the tray on close and keeps the LCD
    # running, exactly like gui.  Exit (tray menu) force-quits.
    splash = show_splash()
    qapp.processEvents()
    window = MainWindow(app)
    window.show()
    auto_close(splash, after_ms=250)
    return qapp.exec()


def launch(platform: Platform | None = None) -> int:
    """Back-compat entry — ``trcc qtgui`` and the direct entry points call this.

    Identical to :func:`run`; kept as the historical name until the CLI router
    dispatches ``run`` directly.
    """
    return run(platform)


# Silence unused-import warnings for QGuiApplication (kept for reference).
_ = QGuiApplication
