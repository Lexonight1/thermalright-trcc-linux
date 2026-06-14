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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
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
from ...core.events import (
    DeviceConnected,
    DeviceDisconnected,
    ErrorOccurred,
    FrameSent,
    ThemeLoaded,
)
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


def launch(app: App | None = None) -> int:
    """Start the GUI.  Returns the exit code.

    A real QApplication (not just a QGuiApplication) is required for
    widgets — we instantiate it *before* anything that might implicitly
    create a headless QGuiApplication (notably QtRenderer).
    """
    # Silence the desktop-portal warnings before Qt initialises (no-op if
    # the QApplication already exists — env is read at first construction).
    from ..qapp import configure_qt_environment
    configure_qt_environment()
    qapp = QApplication.instance()
    if not isinstance(qapp, QApplication):
        qapp = QApplication(sys.argv)

    splash = show_splash()
    qapp.processEvents()

    if app is None:
        # Import QtRenderer only after QApplication exists, so its
        # bootstrap helper finds our QApplication instead of creating a
        # bare QGuiApplication.  Build through the canonical factory so
        # this UI becomes a daemon client when TRCC_DAEMON=1
        # instead of fighting the daemon for USB (audit bug B4).
        from ..._boot import trcc
        from ...adapters.render.qt import QtRenderer
        app = trcc(renderer=QtRenderer())

    window = MainWindow(app)
    window.show()
    auto_close(splash, after_ms=250)
    return qapp.exec()


# Silence unused-import warnings for QGuiApplication (kept for reference).
_ = QGuiApplication
