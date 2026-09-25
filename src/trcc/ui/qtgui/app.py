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
from collections.abc import Callable
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
from ...core.commands import (
    ControlCenterSnapshot,
    GetFirstRunStatus,
    GetPlatformInfo,
    ListDevices,
    RefreshAutostart,
    RenderAndSend,
    RestoreDeviceState,
)

if TYPE_CHECKING:
    from ...core.ports import Platform
from ...core.events import (
    DeviceConnected,
    DeviceDisconnected,
    ErrorOccurred,
    FrameSent,
    ThemeLoaded,
    VideoStarted,
    VideoStopped,
)
from ...core.models import Wire
from ..bus_bridge import BusBridge
from ..qt_tray import TrayController
from .device_selection import DeviceSelection
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
from .preview_surface import FRAME_EDGE, PreviewSurface

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Top-level window: sidebar + stacked content + status bar."""

    def __init__(self, app: App) -> None:
        super().__init__()
        self._app = app
        self._bus = BusBridge(app.events)

        # #201 repair — the same Command cli, api and gui all dispatch, and
        # the one capability qtgui could not reach.  An entry keeps whatever
        # launch command it was written with forever, so a moved install stops
        # autostarting while the panel still reads "enabled" (``is_enabled()``
        # is ``path.is_file()``, which a stale entry satisfies).  Dispatched
        # unconditionally because the port's contract is that refresh NEVER
        # installs an entry that is not there — gui's extra first-launch
        # auto-enable is gui's own product decision and stays there.
        autostart = app.dispatch(RefreshAutostart())
        log.info("MainWindow.__init__: autostart refresh — enabled=%s target=%s",
                 autostart.enabled, autostart.target)

        self.setWindowTitle("TRCC — Thermalright LCD/LED Cooler Control (next)")
        # Rail + a 480px preview + a usable tool column.  ``ui/gui`` is a
        # fixed 1454x800 for the same three regions.
        self.resize(1440, 760)

        # ── Layout: sidebar | stacked content ──
        sidebar = self._sidebar = ActivitySidebar(app, self._bus, self)
        content = QStackedWidget(self)
        content.setObjectName("trcc-content")

        self._lcd_selection = DeviceSelection(self)
        self._led_selection = DeviceSelection(self)
        # Held with its concrete type: the panel dict is ``dict[str, QWidget]``
        # and the surface feeds this one a render size.
        self._state_panel = PreviewPanel(
            app, self._bus, self, selection=self._lcd_selection,
        )

        # Register panels.  Key matches the sidebar entry's key.
        self._panels: dict[str, QWidget] = {
            "devices": DevicePanel(app, self._bus, self),
            "display": DisplayPanel(app, self._bus, self,
                                    selection=self._lcd_selection),
            "preview": self._state_panel,
            "themes":  LocalThemeBrowser(app, self._bus, self,
                                    selection=self._lcd_selection),
            "cloud":   CloudThemeBrowser(app, self._bus, self,
                                    selection=self._lcd_selection),
            "masks":   MaskBrowser(app, self._bus, self,
                                    selection=self._lcd_selection),
            "overlay": OverlayEditorPanel(app, self._bus, self,
                                    selection=self._lcd_selection),
            "screencast": ScreencastPanel(app, self._bus, self,
                                    selection=self._lcd_selection),
            "config":  ConfigurationPanel(app, self._bus, self,
                                    selection=self._lcd_selection),
            "led":     LedPanel(app, self._bus, self,
                                    selection=self._led_selection),
            "status":  StatusPanel(app, self._bus, self,
                                    selection=self._lcd_selection),
            "system":  SystemPanel(app, self._bus, self),
            "about":   AboutPanel(app, self._bus, self),
        }
        for widget in self._panels.values():
            content.addWidget(widget)
        # First-run users land on System (where the doctor lives) so the
        # welcome screen guides them; everyone else starts on Devices.
        initial = ("system" if app.dispatch(GetFirstRunStatus()).is_first_run
                   else "devices")
        content.setCurrentWidget(self._panels[initial])
        sidebar.select(initial)
        self._wire_device_selection(sidebar)

        self._content = content
        sidebar.selected.connect(self._on_navigate)

        self.setCentralWidget(self._build_chrome(app, sidebar, content))

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
        self._bus.video_started.connect(self._on_video_started, type=qconn)
        self._bus.video_stopped.connect(self._on_video_stopped, type=qconn)

        # Display-start restore — METHOD_UI.md's entry contract, which qtgui
        # did not honour at all: `RestoreLastTheme` was reachable ONLY from the
        # display panel's "Restore last" button, so connecting a device and
        # opening qtgui showed nothing until you clicked it.  cli and api have
        # dispatched `RestoreDeviceState` at their display-start since #150.
        #
        # It sits HERE, after the subscriptions above, on purpose: the restore
        # loads a theme, which publishes `ThemeLoaded`, which is what starts
        # the render ticker.  Dispatched any earlier in __init__ the event
        # would fire into an unconnected bus and nothing would ever animate.
        self._restore_display_state()

        # Metrics ticker — dispatches RenderAndSend to every device with an
        # active theme, at AppSettings.refresh_interval_s.  Started lazily when
        # a theme gets loaded; stops when no active themes remain.
        self._ticker = QTimer(self)
        self._ticker.setSingleShot(False)
        self._ticker.timeout.connect(self._on_tick)

        # Devices whose video is playing.  The core's VideoLoop advances them
        # at their own frame rate (#249); this skin only needs to know so the
        # metrics ticker above does not render them a second time.
        self._playing: set[str] = set()

        self._show_platform_info()

        # Shared tray: a window-close hides to the tray (keeps the LCD running)
        # exactly like the gui skin — via the shared TrayController, not a
        # qtgui-local reinvention.  Exit (menu) or a force-quit ends the process.
        icon_path = (Path(__file__).resolve().parents[2]
                     / "assets" / "icons" / "trcc.png")
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._tray = TrayController(
            self,
            # The same Query ``_show_platform_info`` uses — asking the bus
            # instead of ``app.platform``, which an AppProxy does not have.
            minimize_on_close=app.dispatch(GetPlatformInfo()).minimize_on_close,
            icon=icon,
        )
        self._tray.install()

    def closeEvent(self, event: Any) -> None:
        if self._tray.intercept_close(event):
            return
        # Genuine quit: stop the metrics ticker; the core's loops (video
        # included) are stopped by ``App.close``.
        self._ticker.stop()
        event.accept()
        # End the event loop so ``run``'s ``finally: app.close()`` actually
        # runs.  ``quitOnLastWindowClosed`` is False (hide-to-tray), so
        # accepting the close does NOT return from ``qapp.exec()`` — without
        # this the process lived on with the metrics thread still polling, the
        # panel still lit, and /dev/sgN still held.  Same last two lines as
        # gui's closeEvent; App teardown stays in ``run``'s finally so it
        # happens exactly once.
        log.info("MainWindow.closeEvent: real quit — quitting the event loop")
        if (qapp := QApplication.instance()) is not None:
            qapp.quit()

    def _show_platform_info(self) -> None:
        # One Query carries all three: PlatformInfoResult already flattens
        # distro/install/config_dir, so this needs no GetPaths beside it.
        log.debug("_show_platform_info")
        info = self._app.dispatch(GetPlatformInfo())
        msg = (f"{info.distro_name}  |  install: {info.install_method}"
               f"  |  config: {info.config_dir}")
        if self._app.dispatch(GetFirstRunStatus()).is_first_run:
            msg = (
                "Welcome to TRCC.  Open System → run Doctor to check your "
                "setup, then plug in a device and open Devices to scan."
            )
        self._status.showMessage(msg)

    # ── Event handlers ────────────────────────────────────────────────

    def _restore_display_state(self, key: str | None = None) -> None:
        """Give every attached LCD a renderable display state.  Idempotent.

        Enumerates through ``ListDevices`` rather than ``app.devices``: the
        latter is an ``AttributeError`` under ``TRCC_DAEMON=1``, where a UI
        holds an ``AppProxy`` that exposes ``dispatch`` and nothing else, and
        that Query exists precisely because nothing else could answer "which
        devices are there".

        ``key=None`` covers the coldplug fleet — devices attached by
        ``discover_and_connect`` BEFORE this window existed, which never emit
        ``DeviceConnected`` anywhere this window can hear it.  A key restores
        the one device that just arrived.  ``RestoreDeviceState`` no-ops when a
        theme is already active, so the two paths may overlap freely.
        """
        fleet = self._app.dispatch(ListDevices()).devices
        # Entry log with the resolved count — THE RULE.  Without it this method
        # is SILENT on an empty fleet, which is precisely the case a reader
        # needs to distinguish from "ran and restored nothing".
        log.info("_restore_display_state: key=%s, %d device(s) attached",
                 key or "<all>", len(fleet))
        for entry in fleet:
            if key is not None and entry.key != key:
                continue
            if not entry.connected or entry.wire == Wire.LED.value:
                log.debug("_restore_display_state: skip %s (wire=%s connected=%s)",
                          entry.key, entry.wire, entry.connected)
                continue
            result = self._app.dispatch(RestoreDeviceState(key=entry.key))
            log.info("_restore_display_state: %s → ok=%s %s",
                     entry.key, result.ok, result.message)

    def _on_connected(self, event: DeviceConnected) -> None:
        log.info("_on_connected: %s", event.key)
        w, h = event.resolution
        self._status.showMessage(f"Connected: {event.key} ({w}×{h})", 5000)
        # A hotplugged device needs the same display-start restore the
        # coldplug fleet gets in __init__ — otherwise it attaches and sits dark.
        self._restore_display_state(event.key)

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
        log.debug("_ensure_ticker_running")
        if not any(d.has_active_theme
                   for d in self._app.dispatch(ListDevices()).devices):
            if self._ticker.isActive():
                self._ticker.stop()
            return
        snap = self._app.dispatch(ControlCenterSnapshot())
        interval_ms = max(100, int(snap.refresh_interval_s * 1000))
        if not self._ticker.isActive() or self._ticker.interval() != interval_ms:
            self._ticker.start(interval_ms)

    def _on_video_started(self, event: VideoStarted) -> None:
        """A video began on a device — the core ticks it; note it as playing."""
        log.info("_on_video_started: key=%s interval_ms=%d frames=%d",
                 event.key, event.interval_ms, event.frame_count)
        self._playing.add(event.key)

    def _on_video_stopped(self, event: VideoStopped) -> None:
        """Video ended on a device — the metrics ticker renders it again."""
        log.info("_on_video_stopped: key=%s", event.key)
        self._playing.discard(event.key)

    def _build_chrome(
        self, app: App, sidebar: ActivitySidebar, content: QStackedWidget,
    ) -> QWidget:
        """Assemble rail | preview | content, with the preview permanent.

        The preview sits BETWEEN the other two and never leaves, which is the
        whole point: ``ui/gui`` keeps it beside the tool stack (preview at
        x 196-696, tools at x 712-1444 of a fixed 1454x800) so changing a
        colour and seeing the result is ONE screen with no navigation.

        Unconditional rather than shown only for "device" panels: one widget,
        always there, no classification of thirteen panels into workspace and
        full-window and no branch to get wrong.
        """
        log.info("_build_chrome: rail | preview | content")
        self._preview_surface = PreviewSurface(
            app, self._bus, self._lcd_selection, self,
        )
        self._preview_surface.setFixedWidth(FRAME_EDGE + 16)
        # The state read-out reports the size of the render the surface just
        # produced rather than dispatching a second BuildPreview of its own.
        self._preview_surface.rendered.connect(self._state_panel.set_render_size)

        container = QWidget(self)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(sidebar)
        row.addWidget(self._preview_surface)
        row.addWidget(content, 1)
        return container

    def _wire_device_selection(self, sidebar: ActivitySidebar) -> None:
        """Make the rail the one place a device is chosen.

        ONE :class:`DeviceSelection` per window, per KIND: every panel that
        edits an LCD shares the first, the LED panel has its own.  An LCD
        workspace and an LED workspace are different contexts -- ``ui/gui``
        makes them different top-level views -- so an LCD pick must not blank
        the LED panel.  Before this, each panel owned a private picker and
        they diverged silently on a two-device fleet.

        Three connections, and the seeding:

        * the rail announces, MainWindow routes by kind, every panel observes;
        * a pick made in a panel's own picker echoes back onto the rail, so
          the highlighted row is always the device the window is editing;
        * the rail is built BEFORE the panels, so its first auto-selection
          fires before any of this exists and it is the pickers that seed the
          window.  Reflecting the result back removes the dependence on the
          registry happening to list an LCD first.
        """
        log.info("_wire_device_selection: one LCD + one LED selection")
        sidebar.device_chosen.connect(self._on_device_chosen)
        self._lcd_selection.changed.connect(sidebar.show_device)
        self._led_selection.changed.connect(sidebar.show_device)
        sidebar.show_device(self._lcd_selection.key)

    def _on_navigate(self, key: str) -> None:
        """Show the panel the rail asked for; fall back to Devices."""
        log.info("MainWindow._on_navigate: key=%s", key)
        self._content.setCurrentWidget(
            self._panels.get(key, self._panels["devices"]),
        )

    def _on_device_chosen(self, key: str, kind: str) -> None:
        """Route a rail choice to the DeviceSelection for that device kind.

        ``DeviceEntry.kind`` is the router: an LED controller has no
        renderable screen, so pushing its key at the LCD panels would leave
        every one of them showing "no data" for a device that is working fine.
        """
        log.info("MainWindow._on_device_chosen: key=%s kind=%s", key, kind)
        target = self._led_selection if kind == "led" else self._lcd_selection
        target.set_key(key)

    def _on_tick(self) -> None:
        """Fire one render+send for every device with an active theme.

        Skips any device playing a video — the core's VideoLoop already
        renders it at frame rate, and rendering it from both would double its
        wire traffic.  Same rule the gui skin states as "video playback owns
        the wire".
        """
        rendering = [d.key for d in self._app.dispatch(ListDevices()).devices
                     if d.has_active_theme]
        if not rendering:
            self._ticker.stop()
            return
        for key in rendering:
            if key in self._playing:
                log.debug("_on_tick: %s is playing a video — skip", key)
                continue
            try:
                self._app.dispatch(RenderAndSend(key=key))
            except Exception as e:
                log.exception("Tick failed for %s: %s", key, e)


def run(
    platform: Platform | None = None,
    on_ready: Callable[[MainWindow], None] | None = None,
    *,
    force_exit: bool = True,
    start_hidden: bool = False,
) -> int:
    """Start the qtgui skin from an injected ``Platform``.  Returns the exit code.

    A thin alias over the UI bus (``ui/_base.py``); the launch sequence is
    ``UserInterface.start``, shared with every other face.  ``QtGuiUI`` keeps
    what is qtgui's own: the inline coldplug (gui runs it on a splash worker;
    qtgui runs it before the window builds so pickers and browsers populate at
    construction) and its window.

    ``force_exit`` stays here rather than on the face: ``os._exit`` must run
    after the bus's ``finally`` has closed the App, so it cannot live inside
    ``QtGuiUI.run``.
    """
    log.info("run: delegating to the UI bus (start_hidden=%s)", start_hidden)
    from .._uis import QtGuiUI
    exit_code = QtGuiUI(start_hidden=start_hidden, on_ready=on_ready).start(platform)
    if force_exit:
        import os as _os
        _os._exit(exit_code)
    return exit_code


def launch(
    platform: Platform | None = None,
    on_ready: Callable[[MainWindow], None] | None = None,
    *,
    force_exit: bool = True,
    start_hidden: bool = False,
) -> int:
    """Back-compat entry — ``trcc qtgui`` and the direct entry points call this.

    Identical to :func:`run`; kept as the historical name until the CLI router
    dispatches ``run`` directly.
    """
    log.debug("launch: platform=%s on_ready=%s", platform, on_ready)
    return run(platform, on_ready, force_exit=force_exit,
               start_hidden=start_hidden)


# Silence unused-import warnings for QGuiApplication (kept for reference).
_ = QGuiApplication
