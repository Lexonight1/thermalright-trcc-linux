"""GUI entry — QApplication + MainWindow shell.

MainWindow is a tab container holding one panel per responsibility
(devices, display, LED).  The BusBridge connects EventBus events to
Qt signals so widgets can update on the main thread safely.
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
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
from .panels import DevicePanel, DisplayPanel, LedPanel

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Top-level window: tabs + status bar wired to EventBus events."""

    def __init__(self, app: App) -> None:
        super().__init__()
        self._app = app
        self._bus = BusBridge(app.events)

        self.setWindowTitle("TRCC — Thermalright LCD/LED Cooler Control (next)")
        self.resize(800, 520)

        tabs = QTabWidget()
        tabs.addTab(DevicePanel(app), "Devices")
        tabs.addTab(DisplayPanel(app), "Display")
        tabs.addTab(LedPanel(app), "LED")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(tabs)
        self.setCentralWidget(container)

        status = QStatusBar()
        self.setStatusBar(status)
        self._status = status

        # EventBus → status bar (thread-safe via Qt.QueuedConnection)
        qconn = Qt.ConnectionType.QueuedConnection
        self._bus.device_connected.connect(self._on_connected, type=qconn)
        self._bus.device_disconnected.connect(self._on_disconnected, type=qconn)
        self._bus.frame_sent.connect(self._on_frame_sent, type=qconn)
        self._bus.theme_loaded.connect(self._on_theme_loaded, type=qconn)
        self._bus.error_occurred.connect(self._on_error, type=qconn)

        # Playback ticker — dispatches RenderAndSend to every device with an
        # active theme, at AppSettings.refresh_interval_s.  Started lazily
        # when a theme gets loaded; stops when no active themes remain.
        self._ticker = QTimer(self)
        self._ticker.setSingleShot(False)
        self._ticker.timeout.connect(self._on_tick)

        self._show_platform_info()

    def _show_platform_info(self) -> None:
        platform = self._app.platform
        msg = (f"{platform.distro_name()}  |  install: {platform.install_method()}"
               f"  |  config: {platform.paths().config_dir()}")
        self._status.showMessage(msg)

    # ── Event handlers ────────────────────────────────────────────────

    def _on_connected(self, event: DeviceConnected) -> None:
        w, h = event.resolution
        self._status.showMessage(f"Connected: {event.key} ({w}×{h})", 5000)

    def _on_disconnected(self, event: DeviceDisconnected) -> None:
        self._status.showMessage(f"Disconnected: {event.key}", 5000)

    def _on_frame_sent(self, event: FrameSent) -> None:
        self._status.showMessage(f"Frame sent: {event.bytes_sent} bytes", 2000)

    def _on_error(self, event: ErrorOccurred) -> None:
        self._status.showMessage(f"Error [{event.kind}]: {event.message}", 8000)

    def _on_theme_loaded(self, event: ThemeLoaded) -> None:
        """A theme got loaded on some device — make sure the ticker is running."""
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
    qapp = QApplication.instance()
    if not isinstance(qapp, QApplication):
        qapp = QApplication(sys.argv)

    if app is None:
        # Import QtRenderer only after QApplication exists, so its
        # bootstrap helper finds our QApplication instead of creating a
        # bare QGuiApplication.
        from ...adapters.render.qt import QtRenderer
        from ...core.ports import Platform
        app = App(Platform.detect(), renderer=QtRenderer())

    window = MainWindow(app)
    window.show()
    return qapp.exec()


# Silence unused-import warnings for QGuiApplication (kept for reference).
_ = QGuiApplication
