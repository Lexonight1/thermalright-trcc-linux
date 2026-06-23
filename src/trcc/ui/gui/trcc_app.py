"""TRCCApp — legacy-look main window wired to next/ Commands.

Holds an :class:`App` handle (in-process or AppProxy when
``TRCC_DAEMON=1``) and a :class:`BusBridge` that fans EventBus
events into typed Qt signals.  Every device mutation goes through
``self._app.dispatch(Command(...))`` — the window never imports
concrete device or adapter classes.

One :class:`LCDHandler` or :class:`LEDHandler` per connected device,
keyed by ``device.info.key`` (``"vid:pid"``).  Panel stack shows the
currently-selected device; the rest keep ticking in the background.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QRegularExpression as QRE
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPalette, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from ...core.commands import (
    ConnectDevice,
    EnableOverlay,
    ListGpus,
    PlayVideo,
    SetBackground,
    SetGpuDevice,
    SetHddEnabled,
    SetLanguage,
    SetMaskVisible,
    SetRefreshInterval,
    SetTempUnit,
    StartScreencast,
    StopScreencast,
    StopVideo,
)
from ...core.models import HardwareMetrics, Kind
from ..presentation import presentation_for
from ._ui_state import UiStateStore
from .assets import Assets
from .base import create_image_button, set_background_pixmap
from .base_handler import BaseHandler
from .bus_bridge import BusBridge
from .constants import Colors, Layout, Sizes, Styles
from .lcd_handler import LCDHandler
from .led_handler import LEDHandler
from .uc_about import UCAbout, ensure_autostart
from .uc_activity_sidebar import UCActivitySidebar
from .uc_device import UCDevice
from .uc_image_cut import UCImageCut
from .uc_info_module import UCInfoModule
from .uc_led_control import UCLedControl
from .uc_preview import UCPreview
from .uc_system_info import UCSystemInfo
from .uc_theme_local import UCThemeLocal
from .uc_theme_mask import UCThemeMask
from .uc_theme_setting import UCThemeSetting
from .uc_theme_web import UCThemeWeb
from .uc_video_cut import UCVideoCut

if TYPE_CHECKING:
    from ...app import App
    from ...ipc import IPCServer

log = logging.getLogger(__name__)


# =============================================================================
# Screencast Handler
# =============================================================================

class ScreencastHandler:
    """Mediator for screencast (screen capture → LCD).

    Lifecycle is bus-driven: the handler does NOT expose a public
    ``toggle`` for callers — instead it subscribes to BusBridge's
    ``screencast_started`` / ``screencast_stopped`` signals (mirrored
    from :class:`ScreencastStarted` / :class:`ScreencastStopped`
    events).  GUI / CLI / API / daemon callers all start a session by
    dispatching :class:`StartScreencast` through :class:`App.dispatch`,
    which keeps the Command bus authoritative for the lifecycle.

    Hot-path knobs that don't warrant a round-trip through the bus
    (per-drag region updates, audio/border toggles, target LCD size)
    stay as direct setters — they tune an already-running session.

    When audio_enabled is True, captures microphone input and draws
    a spectrum visualizer bar at the bottom of each screencast frame.
    """

    def __init__(self, parent: QWidget, on_frame: Any):
        self._on_frame = on_frame
        self._active = False
        self._x = self._y = self._w = self._h = 0
        self._border = True
        self._pipewire_cast = None
        self._lcd_w = 0
        self._lcd_h = 0
        self._capture_warn_logged = False
        self._audio_enabled = False
        self._audio: Any = None  # AudioCapture instance

        self._timer = QTimer(parent)
        self._timer.timeout.connect(self._tick)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def audio_enabled(self) -> bool:
        return self._audio_enabled

    @property
    def params(self) -> tuple[int, int, int, int]:
        """Current region — ``(x, y, w, h)`` in screen pixels.

        Read by ``TRCCApp._on_screencast_toggle`` so it can bundle the
        currently configured panel coordinates into the dispatched
        :class:`StartScreencast` Command.
        """
        return self._x, self._y, self._w, self._h

    def subscribe(self, bus: BusBridge) -> None:
        """Connect ``ScreencastStarted`` / ``ScreencastStopped`` events
        to the local lifecycle hooks.

        Called by ``TRCCApp.__init__`` after the bridge is constructed.
        Separate from ``__init__`` so the handler can be built before the
        bus exists — same shape as ``LCDHandler.subscribe_to_bus``.
        """
        log.info("ScreencastHandler.subscribe: wiring bus screencast signals")
        bus.screencast_started.connect(
            self._on_bus_screencast_started,
            type=Qt.ConnectionType.QueuedConnection,
        )
        bus.screencast_stopped.connect(
            self._on_bus_screencast_stopped,
            type=Qt.ConnectionType.QueuedConnection,
        )

    def set_lcd_size(self, w: int, h: int) -> None:
        self._lcd_w = w
        self._lcd_h = h

    def set_audio_enabled(self, enabled: bool) -> None:
        """Enable/disable microphone audio visualization on screencast."""
        log.info("ScreencastHandler.set_audio_enabled: enabled=%s", enabled)
        self._audio_enabled = enabled
        if self._active and enabled and self._audio is None:
            self._start_audio()
        elif not enabled and self._audio is not None:
            self._audio.stop()
            self._audio = None

    def stop(self) -> None:
        """Emergency stop — used by system-suspend / window-close paths
        that may race against the bus delivery.  Idempotent."""
        log.info("ScreencastHandler.stop: emergency stop (active=%s)",
                 self._active)
        self._timer.stop()
        self._active = False

    def set_params(self, x: int, y: int, w: int, h: int) -> None:
        self._x, self._y, self._w, self._h = x, y, w, h

    def set_border(self, visible: bool) -> None:
        self._border = visible

    def cleanup(self) -> None:
        self._timer.stop()
        self._stop_pipewire()
        if self._audio is not None:
            self._audio.stop()
            self._audio = None

    def _on_bus_screencast_started(self, event: Any) -> None:
        """Bus subscriber — start the Qt capture timer for ``event.key``.

        Daemon-mode hasn't moved screencast to a per-device dispatcher
        yet, so a single handler still owns capture for the active LCD;
        ``event.key`` is logged for trace and ignored for routing.
        """
        log.info(
            "ScreencastHandler._on_bus_screencast_started: key=%s "
            "region=(%d,%d %dx%d) audio=%s",
            event.key, event.x, event.y, event.w, event.h, event.audio,
        )
        self._x, self._y, self._w, self._h = event.x, event.y, event.w, event.h
        self._audio_enabled = event.audio
        self._active = True

        from .screen_capture import is_wayland
        if is_wayland() and self._pipewire_cast is None:
            self._try_start_pipewire()
        if self._audio_enabled and self._audio is None:
            self._start_audio()
        self._timer.start(150)

    def _on_bus_screencast_stopped(self, event: Any) -> None:
        """Bus subscriber — tear down the Qt capture timer.

        Idempotent: safe to receive even if there was no active session
        (e.g. CLI client stopping a session that never had a GUI side).
        """
        log.info("ScreencastHandler._on_bus_screencast_stopped: key=%s",
                 event.key)
        self._active = False
        self._timer.stop()
        self._stop_pipewire()
        if self._audio is not None:
            self._audio.stop()
            self._audio = None

    def _start_audio(self) -> None:
        from trcc.services.audio import AudioCapture
        self._audio = AudioCapture()
        if not self._audio.start():
            self._audio = None

    def _draw_spectrum(self, image: Any) -> None:
        """Draw spectrum analyzer bars at the bottom of a QImage."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QPainter
        spectrum = self._audio.get_spectrum()  # type: ignore[union-attr]
        w, h = image.width(), image.height()
        bar_area_h = int(h * 0.25)  # bottom 25% of frame
        num_bars = len(spectrum)
        gap = 2
        bar_w = max(1, (w - gap * (num_bars + 1)) // num_bars)
        x_offset = (w - (bar_w + gap) * num_bars) // 2

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, level in enumerate(spectrum):
            bar_h = max(1, int(level * bar_area_h))
            x = x_offset + i * (bar_w + gap)
            y = h - bar_h
            # Gradient: green at bottom → yellow → red at top
            ratio = level
            if ratio < 0.5:
                r, g, b = int(ratio * 2 * 255), 255, 0
            else:
                r, g, b = 255, int((1 - ratio) * 2 * 255), 0
            painter.fillRect(QRectF(x, y, bar_w, bar_h), QColor(r, g, b, 200))
        painter.end()

    def _try_start_pipewire(self) -> None:
        from .pipewire_capture import PIPEWIRE_AVAILABLE, PipeWireScreenCast
        if not PIPEWIRE_AVAILABLE:
            return
        import threading
        cast = PipeWireScreenCast()
        self._pipewire_cast = cast
        def _start() -> None:
            if not cast.start(timeout=30):
                self._pipewire_cast = None
        threading.Thread(target=_start, daemon=True).start()

    def _stop_pipewire(self) -> None:
        if self._pipewire_cast is not None:
            self._pipewire_cast.stop()
            self._pipewire_cast = None

    def _tick(self) -> None:
        if not self._active or self._w <= 0 or self._h <= 0 or not self._lcd_w or not self._lcd_h:
            return
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QImage
        from PySide6.QtGui import Qt as QtGui_Qt
        frame_img: QImage | None = None

        if self._pipewire_cast is not None and self._pipewire_cast.is_running:
            frame = self._pipewire_cast.grab_frame()
            if frame is not None:
                fw, fh, rgb_bytes = frame
                full = QImage(rgb_bytes, fw, fh, fw * 3, QImage.Format.Format_RGB888)
                x1, y1 = min(self._x, fw), min(self._y, fh)
                x2, y2 = min(self._x + self._w, fw), min(self._y + self._h, fh)
                if x2 > x1 and y2 > y1:
                    frame_img = full.copy(QRect(x1, y1, x2 - x1, y2 - y1))

        if frame_img is None:
            from .screen_capture import grab_screen_region
            pixmap = grab_screen_region(self._x, self._y, self._w, self._h)
            if pixmap.isNull():
                if not self._capture_warn_logged:
                    log.warning("Screencast: all capture methods failed")
                    self._capture_warn_logged = True
                return
            self._capture_warn_logged = False
            frame_img = pixmap.toImage()

        frame_img = frame_img.scaled(
            self._lcd_w, self._lcd_h,
            QtGui_Qt.AspectRatioMode.IgnoreAspectRatio,
            QtGui_Qt.TransformationMode.SmoothTransformation)

        # Draw audio spectrum bars at the bottom of the frame
        if self._audio is not None and self._audio.running:
            self._draw_spectrum(frame_img)

        self._on_frame(frame_img)


# =============================================================================
# TRCCApp — Main Window / AppObserver
# =============================================================================

class TRCCApp(QMainWindow):
    """Main TRCC window — legacy chrome, next/ Commands underneath.

    Holds:
      _app: App                         — universal command/event hub
      _bus: BusBridge                   — Event → Qt signal bridge
      _ui_state: UiStateStore           — GUI-only persisted prefs
      _handlers: dict[str, BaseHandler] — keyed by ``device.info.key``
      _active_key: str                  — vid:pid of currently active device

    Every device write goes through ``self._app.dispatch(Command(...))``.
    Event subscriptions go through ``self._bus.X.connect(slot, QueuedConn)``
    so handlers always run on the Qt main thread.
    """

    _instance: TRCCApp | None = None

    # Emitted when a second ``trcc gui`` launch asks the running instance to
    # surface itself.  ``SingleInstance`` invokes the callback from its accept
    # thread, so this MUST be a signal (not a direct call): emitting is
    # thread-safe, and the QueuedConnection in ``__init__`` marshals the actual
    # window show/raise onto the Qt main thread (#196 — a direct cross-thread
    # QWidget call deadlocked the event loop).
    raise_requested = Signal()

    def __new__(cls, *args: Any, **kwargs: Any) -> TRCCApp:
        if cls._instance is not None:
            raise RuntimeError("TRCCApp is a singleton — use instance()")
        inst = super().__new__(cls)
        cls._instance = inst
        return inst

    @classmethod
    def instance(cls) -> TRCCApp | None:
        return cls._instance

    def is_app_visible(self) -> bool:
        return self.isVisible() and not self._minimized_to_taskbar

    def __init__(
        self,
        app: App,
        decorated: bool = False,
    ) -> None:
        super().__init__()
        from trcc.__version__ import __version__
        log.info("TRCC v%s starting", __version__)

        self._app = app
        self._minimize_on_close = app.platform.minimize_on_close()
        self._sensors = app.platform.sensors()
        self._ui_state = UiStateStore(app.platform.paths())
        # Observability state for the metrics fan-out — first call
        # after construction logs INFO, subsequent ticks DEBUG unless
        # panel visibility flips (which is itself a transition worth
        # surfacing).  Same shape Phase 0 used for the video tick.
        self._metrics_fanout_first_logged: bool = False
        self._last_vis_state: tuple[bool, bool, bool] = (False, False, False)
        # Last metrics snapshot OBSERVED from the OS dispatcher
        # (``SensorsUpdated`` at the refresh-rate cadence).  The GUI never
        # re-polls; a view-switch re-fans-out this cached object so a panel
        # opened between ticks populates immediately.
        self._last_metrics: HardwareMetrics = HardwareMetrics()
        self._ui_state.load()

        # NOTE: the saved GPU selection is applied universally by the App
        # composition root (App.__init__ seeds the enumerator from
        # settings.active_gpu) — no GUI-local hook needed.
        self._decorated = decorated
        self._drag_pos: Any = None
        self._force_quit = False
        self._minimized_to_taskbar = False
        self._data_dir = app.platform.paths().user_content_dir()

        self.setWindowTitle("TRCC-Linux - Thermalright LCD Control Center")
        self.setFixedSize(Sizes.WINDOW_W, Sizes.WINDOW_H)
        if not decorated:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)

        # Per-device handlers keyed by ``device.info.key`` ("vid:pid")
        self._handlers: dict[str, BaseHandler] = {}
        self._active_key = ''         # vid:pid of currently active device

        self._handshake_pending = False
        self._cut_mode = 'background'
        self._mask_upload_filename = ''
        self._pixmap_refs: list = []

        # IPC server set by composition root after construction
        self._ipc_server: IPCServer | None = None

        # Build UI
        self._apply_dark_theme()
        self._setup_ui()

        # Screencast handler
        self._screencast = ScreencastHandler(self, self._on_screencast_frame)

        # Connect widget signals
        self._connect_view_signals()

        # ── EventBus → BusBridge → Qt signals (QueuedConnection ensures
        # delivery on the Qt main thread regardless of publish thread).
        self._bus = BusBridge(app.events)
        # Screencast lifecycle subscribes through the bus — TRCCApp keeps
        # owning the handler, but Start/Stop now arrive as events so
        # CLI / API / daemon callers drive screencast through the same
        # Command bus as the GUI toggle.
        self._screencast.subscribe(self._bus)
        qconn = Qt.ConnectionType.QueuedConnection
        self._bus.device_connected.connect(self._on_bus_device_connected, type=qconn)
        self._bus.device_disconnected.connect(self._on_bus_device_disconnected, type=qconn)
        self._bus.frame_sent.connect(self._on_bus_frame_sent, type=qconn)
        self._bus.sensors_updated.connect(self._on_bus_sensors_updated, type=qconn)
        self._bus.video_started.connect(self._on_bus_video_started, type=qconn)
        self._bus.video_stopped.connect(self._on_bus_video_stopped, type=qconn)
        self._bus.system_suspending.connect(self._on_bus_system_suspending, type=qconn)
        # Live errors → transient tray balloon (spam-safe; render/transport
        # errors can fire per-tick, so a dialog here would storm).
        self._bus.error_occurred.connect(self._on_bus_error, type=qconn)
        self._last_error_text = ""

        # Handshake notifier — kept for legacy code paths that spawn
        # threads to do connect() under the hood.  next/'s ConnectDevice
        # Command is synchronous so most callers won't use this.
        from PySide6.QtCore import QObject
        from PySide6.QtCore import Signal as _Signal

        class _HandshakeNotifier(QObject):
            done = _Signal(object, object)
        self._hs_notifier = _HandshakeNotifier(self)
        self._hs_notifier.done.connect(self._on_handshake_done)

        # Restore temp unit from app settings.  Legacy widgets take int
        # 0/1; next/'s AppSettings.temp_unit is a "C"/"F" literal.
        saved_unit_int = 1 if app.settings.app.temp_unit == "F" else 0
        self.uc_system_info.set_temp_unit(saved_unit_int)
        self.uc_led_control.set_temp_unit(saved_unit_int)
        if saved_unit_int == 1:
            self.uc_about._set_temp('F')

        # Autostart — uc_about.ensure_autostart takes the AutostartManager
        autostart_state = ensure_autostart(app.platform.autostart())
        self.uc_about._autostart = autostart_state
        self.uc_about.startup_btn.setChecked(autostart_state)

        # System tray
        self._setup_systray()

        # Raise-existing-window (second launch) — see ``raise_requested``.
        # QueuedConnection: the emit comes from SingleInstance's accept thread,
        # the slot runs on the Qt main thread.
        self.raise_requested.connect(
            self._on_raise_requested,
            type=Qt.ConnectionType.QueuedConnection,
        )

    def _on_raise_requested(self) -> None:
        """Surface the window for a second ``trcc gui`` launch.

        Runs on the Qt main thread (queued).  Restores from the tray /
        minimized state and brings the window to the front + focus.
        """
        log.info(
            "_on_raise_requested: visible=%s minimized=%s tray=%s",
            self.isVisible(), self.isMinimized(), self._minimized_to_taskbar,
        )
        self._minimized_to_taskbar = False
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ── BusBridge subscribers (run on the Qt main thread) ───────────

    def _on_bus_error(self, event: Any) -> None:
        """Live error from the bus → transient system-tray balloon.

        Spam-safe by construction: balloons are OS-transient, and we de-dup
        an identical consecutive message (a per-tick render failure repeats
        the same text — show it once, not every frame).  The tray is the
        right surface for the legacy-skinned window: native, non-modal, no
        clash with the chrome.
        """
        from .._errors import format_device_error
        text = format_device_error(event)
        if text == self._last_error_text:
            return
        self._last_error_text = text
        log.info("_on_bus_error: [%s] %s", event.kind, event.message)
        tray = getattr(self, "_tray", None)
        if tray is not None and QSystemTrayIcon.isSystemTrayAvailable():
            tray.showMessage(
                "TRCC — device", text,
                QSystemTrayIcon.MessageIcon.Warning, 8000,
            )

    def _on_bus_device_connected(self, event: Any) -> None:
        """One device just attached/handshaked (hotplug after startup).

        Add a handler, REFRESH THE SIDEBAR so the new device's button appears
        (real-world usage: a user plugs a cooler in while the app runs), and
        configure it inactive so it renders its content immediately — same as
        the initial fleet.
        """
        log.debug("_on_bus_device_connected: key=%s", event.key)
        device = self._app.devices.get(event.key)
        if device is None:
            return
        self._add_handler(device)
        self._refresh_sidebar()
        self._configure_inactive_lcd(event.key)

    def _on_bus_device_disconnected(self, event: Any) -> None:
        """One device just detached.  Drop its handler."""
        log.debug("_on_bus_device_disconnected: key=%s", event.key)
        self._remove_handler(event.key)

    def _on_bus_frame_sent(self, event: Any) -> None:
        """A frame just went out on the wire.

        ``FrameSent`` now carries the rendered surface (legacy's
        publish-the-frame, observe-it shape), so the active handler
        displays THAT image directly — no second render.  Only the
        pure-bytes send paths (SendFrame / SendColor / SendImage /
        keepalive) leave ``surface`` None; those fall back to a one-off
        re-render.  Only the active device writes the shared preview.
        """
        log.debug("_on_bus_frame_sent")  # per-frame — DEBUG so reports aren't flooded
        if event.key != self._active_key:
            return
        handler = self._handlers.get(event.key)
        if handler is None:
            return
        # One preview path for both kinds: LCD frames carry a ``surface``,
        # LED renders carry ``display_colors`` — both flow through the same
        # FrameSent → handle_frame seam (the device's render IS the preview).
        surface = getattr(event, "surface", None)
        colors = getattr(event, "display_colors", None)
        if surface is not None:
            handler.handle_frame(surface)
        elif colors:
            handler.handle_frame({"display_colors": list(colors)})
        else:
            handler.rebuild_preview()

    def _on_bus_video_started(self, event: Any) -> None:
        """Route a ``VideoStarted`` event to its device's handler.

        The handler owns its Qt animation timer; this bridge just hands
        off the event so the handler can start ticking.  Multi-LCD safe
        — the handler filters on ``event.key == self._device_key``.
        """
        log.info(
            "_on_bus_video_started: key=%s frames=%d interval=%dms",
            event.key, event.frame_count, event.interval_ms,
        )
        handler = self._handlers.get(event.key)
        if handler is not None:
            handler.on_video_started(event)

    def _on_bus_video_stopped(self, event: Any) -> None:
        """Route a ``VideoStopped`` event to its device's handler."""
        log.info("_on_bus_video_stopped: key=%s", event.key)
        handler = self._handlers.get(event.key)
        if handler is not None:
            handler.on_video_stopped(event)

    def _on_bus_sensors_updated(self, event: Any) -> None:
        """Sensors broadcast — observe the OS snapshot + fan out to widgets.

        The OS dispatcher (``MetricsLoop`` at the refresh-rate interval)
        already produced + personalized the typed ``HardwareMetrics``; the
        GUI just OBSERVES it.  Cache it so a view-switch between ticks can
        re-render the last reading without re-polling.
        """
        log.info("_on_bus_sensors_updated")
        self._last_metrics = event.metrics
        self._fan_out_metrics(reason="bus")

    def _fan_out_metrics(self, *, reason: str) -> None:
        """Forward the last observed metrics to every visible widget.

        Single source of truth for the GUI's metrics fan-out — called by
        ``_on_bus_sensors_updated`` right after caching the broadcast AND
        by ``_show_view`` when the user opens a metrics panel (so it
        populates immediately from the cached snapshot instead of waiting
        for the next tick).  Never re-polls — the OS dispatcher owns the
        reads; the GUI observes.

        ``reason`` is a short tag for the observability log line:
        "bus" / "view-switch" / "temp-unit-changed" etc.
        """
        metrics = self._last_metrics
        readings = metrics.readings

        info_vis = self.uc_info_module.isVisible()
        sysinfo_vis = self.uc_system_info.isVisible()
        sidebar_vis = self.is_app_visible() and self.uc_activity_sidebar.isVisible()
        # INFO on first call after construction + on every visibility
        # state TRANSITION (panel opens or closes).  Per-tick stays
        # DEBUG so 2 s cadence doesn't flood.  Mirrors Phase 0's
        # transition-only skip-log shape.
        vis_state = (info_vis, sysinfo_vis, sidebar_vis)
        if (not self._metrics_fanout_first_logged
                or self._last_vis_state != vis_state):
            log.info(
                "_fan_out_metrics: reason=%s readings=%d "
                "info_vis=%s sysinfo_vis=%s sidebar_vis=%s",
                reason, len(readings), info_vis, sysinfo_vis, sidebar_vis,
            )
            self._metrics_fanout_first_logged = True
            self._last_vis_state = vis_state
        else:
            log.debug(
                "_fan_out_metrics: reason=%s readings=%d "
                "info_vis=%s sysinfo_vis=%s sidebar_vis=%s",
                reason, len(readings), info_vis, sysinfo_vis, sidebar_vis,
            )

        if info_vis:
            self.uc_info_module.update_from_metrics(metrics)
        if sysinfo_vis:
            self.uc_system_info.update_from_metrics(metrics)
        if sidebar_vis:
            self.uc_activity_sidebar.update_from_metrics(metrics)

        handler = self._handlers.get(self._active_key)
        if handler is not None:
            handler.update_metrics(metrics)

    def _on_bus_system_suspending(self, _event: Any) -> None:
        """OS is about to suspend — stop the screencast pipeline.

        Routed through ``StopScreencast`` for the active device when
        possible so daemon/CLI/API observers see the same lifecycle
        event the GUI just acted on.  Falls back to the local emergency
        ``ScreencastHandler.stop`` when there's no active device handle
        (suspend during a transient state shouldn't crash on no-handler).
        """
        log.info("_on_bus_system_suspending: stopping screencast")
        h = self._active_lcd()
        if h is not None and self._screencast.active:
            self._app.dispatch(StopScreencast(key=h.device_key))
        else:
            self._screencast.stop()

    def notify_device_failures(self, failures: list[Any]) -> None:
        """Surface devices that were found but failed to connect.

        Called once after ``show()`` with the failures the splash-time
        discover+connect collected.  Each carries an OS-correct hint the
        Platform supplied via ``check_permissions()`` (e.g. "run as
        administrator" on Windows, "run `trcc system setup`" on Linux), so the
        user sees *why* the panel is blank instead of an empty window.  No-op
        when nothing failed.
        """
        if not failures:
            return
        log.warning("notify_device_failures: %d device(s) did not connect",
                    len(failures))
        lines = [f"• {f.key}: {f.message}" for f in failures]
        hints: list[str] = []
        for f in failures:
            for hint in getattr(f, "hints", []):
                if hint not in hints:
                    hints.append(hint)
        body = "Some devices were found but did not connect:\n\n" + "\n".join(lines)
        if hints:
            body += "\n\n" + "\n".join(hints)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, "Device connection", body)

    def replay_initial_devices(self) -> None:
        """Build handlers + sidebar from ``app.devices`` after first discovery.

        Called by the composition root once after the BootstrapWorker
        finishes.  Live mutations after this come through the BusBridge.
        """
        for device in self._app.devices.values():
            self._add_handler(device)
        self._refresh_sidebar()
        # Restore last-active device or fall back to first LCD.
        target_key = self._ui_state.state.last_device_key
        if target_key not in self._handlers:
            target_key = next(
                (k for k, h in self._handlers.items() if isinstance(h, LCDHandler)),
                next(iter(self._handlers), ''),
            )

        # Render EVERY connected LCD, not just the active one — on real hardware
        # every cooler's screen shows its content simultaneously, so the mock
        # must too (and a reporter's device only renders here if it's loaded).
        # Configure the non-target LCDs first (full load incl. first-install
        # auto-load → active_themes → renders + metrics), then mark them inactive
        # so they keep rendering to the wire without owning the shared preview.
        # The target activates LAST so its frame ends up in the shared widgets.
        for key in list(self._handlers):
            if key != target_key:
                self._configure_inactive_lcd(key)

        self._show_initial_view(target_key)

    def _show_initial_view(self, target_key: str) -> None:
        """Land on the active device's view, or the deviceless home when none.

        Initial-view policy in one place (SRP): a discovered device activates
        its panel; with zero devices (or none connected) the form chrome is
        inert, so show the device-independent home/sysinfo view (live system
        metrics) instead of a blank panel.  The sidebar already carries the
        per-OS 'no devices' hint (``Platform.no_devices_hint()``).
        """
        if target_key:
            self._activate_device(target_key)
        else:
            log.info("_show_initial_view: no devices — home/sysinfo empty state")
            self._show_view('sysinfo')

    def _configure_inactive_lcd(self, key: str) -> None:
        """Load + render a connected LCD without it owning the shared preview.

        Used for non-target devices at startup AND for late (hotplug) connects,
        so EVERY connected cooler renders its content — matching real hardware
        where every screen shows at once.  No-op for non-LCD or already-
        configured handlers.  ``set_inactive`` keeps it rendering to the wire
        but drops ``_ui_active`` so it never fights the active device for the
        shared preview widgets.
        """
        handler = self._handlers.get(key)
        if not isinstance(handler, LCDHandler) or handler.is_configured:
            return
        device = self._app.devices.get(key)
        if device is None or not device.is_connected or device.profile is None:
            return
        w, h = device.profile.resolution
        if (w, h) == (0, 0):
            return
        log.info("configure_inactive_lcd: %s %dx%d", key, w, h)
        handler.apply_device_config(device.info, w, h)
        handler.set_inactive()

    # ── Handler lifecycle ───────────────────────────────────────────

    def _add_handler(self, device: Any) -> None:
        """Create a handler for one newly-attached device.

        next/'s ``Device`` exposes ``info`` (ProductInfo) and ``key``
        (vid:pid).  ``info.key`` is the registry key the handler dict
        is indexed by.
        """
        info = device.info
        if info is None:
            log.warning("_add_handler: device.info is None — skipping")
            return
        key = info.key
        if key in self._handlers:
            return
        self._handlers[key] = self._build_handler(device)

    def _build_handler(self, device: Any) -> BaseHandler:
        """Construct the handler for one device — the single build chokepoint.

        The device's ``ProductInfo`` (resolved from vid/pid + handshake) drives
        which view it presents via the shared :func:`presentation_for` backbone,
        and this is the ONE place handlers are constructed — so ``self._app``
        and the View's panels are injected the same way for every kind.  No
        per-kind branch can silently forget a dependency (the LED metrics ``--``
        bug was exactly a missing ``app=`` on a divergent branch).  Presentation
        is unchanged: each kind gets the same handler + panels it always did.
        """
        key = device.info.key
        presentation = presentation_for(device.info)
        if presentation.kind is Kind.LED:
            log.info("LED handler added: %s", key)
            return LEDHandler(
                device, self.uc_led_control, self._on_temp_unit_changed,
                app=self._app,
            )
        widgets = {
            'preview': self.uc_preview,
            'theme_setting': self.uc_theme_setting,
            'theme_local': self.uc_theme_local,
            'theme_web': self.uc_theme_web,
            'theme_mask': self.uc_theme_mask,
            'image_cut': self.uc_image_cut,
            'video_cut': self.uc_video_cut,
            'rotation_combo': self.rotation_combo,
        }
        log.info("LCD handler added: %s", key)
        return LCDHandler(
            device, widgets, self._make_timer, self._data_dir,
            is_visible_fn=self.is_app_visible,
            app=self._app, lcd_idx=key,
        )

        self._refresh_sidebar()

    def _remove_handler(self, key: str) -> None:
        """Remove and clean up one device handler."""
        handler = self._handlers.pop(key, None)
        if handler is None:
            return
        handler.cleanup()
        log.info("%s handler removed: %s", type(handler).__name__, key)

        if self._active_key == key:
            self._active_key = ''
            remaining = list(self._handlers)
            if remaining:
                self._activate_device(remaining[0])

        self._refresh_sidebar()

    def _refresh_sidebar(self) -> None:
        """Update UCDevice from the current handler set.

        Sidebar widget consumes legacy-shape dicts:
        ``{name, path, button_image, protocol, model, vid, pid, device_index}``.
        Adapt from next/ ``ProductInfo`` here so the widget code stays
        untouched.
        """
        log.debug("_refresh_sidebar")
        devices: list[dict] = []
        for idx, key in enumerate(self._handlers.keys()):
            dev = self._app.devices.get(key)
            if dev is None:
                continue
            info = dev.info
            devices.append({
                'name': f"{info.vendor} {info.product}".strip()
                        or f"Device {info.vid:04x}:{info.pid:04x}",
                'path': info.key,                   # vid:pid serves as legacy 'path'
                'button_image': getattr(info, 'button_image', '') or '',
                'protocol': info.wire.value,
                'model': getattr(info, 'model', '') or '',
                'vid': info.vid,
                'pid': info.pid,
                'device_index': idx,                # legacy display ordering
            })
        self.uc_device.update_devices(devices)

    def _activate_device(self, key: str) -> None:
        """Switch panel stack to show the device with ``key``."""
        if key == self._active_key:
            return
        log.info("_activate_device: %s", key)
        # Deactivate previous: LCDs soft-pause (keep playing on device),
        # everything else stops fully.
        if self._active_key:
            prev = self._handlers.get(self._active_key)
            if isinstance(prev, LCDHandler):
                prev.set_inactive()
            elif prev is not None:
                prev.deactivate()
        self._active_key = key
        handler = self._handlers.get(key)
        if handler is None:
            log.warning(
                "_activate_device: no handler for key=%s (known: %s)",
                key, list(self._handlers.keys()),
            )
            return

        # Persist last-active device for next launch (UI state — not
        # domain settings, so it lives in UiStateStore not app.settings).
        self._ui_state.set_last_device_key(key)

        device = self._app.devices.get(key)
        if isinstance(handler, LCDHandler):
            if device is not None and device.is_connected:
                profile = device.profile
                if profile is not None:
                    w, h = profile.resolution
                    if (w, h) == (0, 0):
                        log.debug("_activate_device: LCD %s no canvas yet — handshake", key)
                        self._start_handshake(device)
                    elif not handler.is_configured:
                        log.debug("_activate_device: LCD %s first-time config %dx%d", key, w, h)
                        handler.apply_device_config(device.info, w, h)
                        self._update_ldd_icon()
                    else:
                        log.debug("_activate_device: LCD %s reactivate %dx%d", key, w, h)
                        handler.reactivate(w, h)
                else:
                    self._start_handshake(device)
        elif isinstance(handler, LEDHandler) and not handler.active:
            log.debug("_activate_device: LED %s — showing", key)
            handler.show(device.info if device is not None else None)

        self._show_view(handler.view_name)

    # ── Timers ──────────────────────────────────────────────────────

    def _make_timer(self, callback: Any, *, single_shot: bool = False) -> QTimer:
        timer = QTimer(self)
        if single_shot:
            timer.setSingleShot(True)
        timer.timeout.connect(callback)
        return timer

    # ── Dark theme ──────────────────────────────────────────────────

    def _apply_dark_theme(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(Colors.WINDOW_BG))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.WINDOW_TEXT))
        palette.setColor(QPalette.ColorRole.Base, QColor(Colors.BASE_BG))
        palette.setColor(QPalette.ColorRole.Text, QColor(Colors.TEXT))
        palette.setColor(QPalette.ColorRole.Button, QColor(Colors.BUTTON_BG))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(Colors.BUTTON_TEXT))
        self.setPalette(palette)

    # ── System tray ─────────────────────────────────────────────────

    def _setup_systray(self) -> None:
        # __file__ = src/trcc/ui/gui/trcc_app.py  →  parents[2] = src/trcc/
        icon_path = Path(__file__).resolve().parents[2] / 'assets' / 'icons' / 'trcc.png'
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self.setWindowIcon(icon)

        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("TRCC Linux")

        menu = QMenu()
        if (show_action := menu.addAction("Show/Hide")):
            show_action.triggered.connect(self._toggle_visibility)
        menu.addSeparator()
        if (exit_action := menu.addAction("Exit")):
            exit_action.triggered.connect(self._quit_app)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: Any) -> None:
        log.info("_on_tray_activated: reason=%s", reason)
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visibility()

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self._minimized_to_taskbar = False
            self.show()
            self.activateWindow()
            self.raise_()

    def _quit_app(self) -> None:
        self._force_quit = True
        self.close()

    # ── UI Setup ────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """Build main UI layout."""
        central = QWidget()
        self.setCentralWidget(central)

        pix_form1 = set_background_pixmap(central, Assets.FORM1_BG,
            width=Sizes.WINDOW_W, height=Sizes.WINDOW_H,
            fallback_style=f"background-color: {Colors.WINDOW_BG};")
        if pix_form1:
            self._pixmap_refs.append(pix_form1)

        # Device sidebar — no detect_fn, populated via on_app_event
        self.uc_device = UCDevice(central)
        self.uc_device.setGeometry(*Layout.SIDEBAR)
        # Per-OS "no devices" guidance comes from the Platform port (one
        # source of truth), injected here so the panel stays toolkit-pure.
        self.uc_device.set_no_devices_hint(self._app.platform.no_devices_hint())

        # FormCZTV container
        self.form_container = QWidget(central)
        self.form_container.setGeometry(*Layout.FORM_CONTAINER)
        pix = set_background_pixmap(self.form_container, Assets.FORM_CZTV_BG,
            fallback_style=f"background-color: {Colors.WINDOW_BG};")
        if pix:
            self._pixmap_refs.append(pix)

        # Preview — pick a sensible default size; the LCD handler
        # calls preview.set_resolution(w, h) on connect with the real
        # canvas, so the initial dimensions only affect the brief
        # pre-connect render.
        self.uc_preview = UCPreview(320, 320, self.form_container)
        self.uc_preview.setGeometry(*Layout.PREVIEW)

        # Info module
        self.uc_info_module = UCInfoModule(self.form_container)
        self.uc_info_module.setGeometry(16, 16, 500, 70)
        self.uc_info_module.setVisible(False)

        # Image/video cutters
        self.uc_image_cut = UCImageCut(self.form_container)
        self.uc_image_cut.setGeometry(16, 88, 500, 702)
        self.uc_image_cut.setVisible(False)

        self.uc_video_cut = UCVideoCut(self.form_container)
        self.uc_video_cut.setGeometry(16, 88, 500, 702)
        self.uc_video_cut.setVisible(False)

        # Mode tabs
        self._create_mode_tabs()

        # Theme panel stack
        self.panel_stack = QStackedWidget(self.form_container)
        self.panel_stack.setGeometry(*Layout.PANEL_STACK)

        self.uc_theme_local = UCThemeLocal()
        self._set_panel_bg(self.uc_theme_local, Assets.THEME_LOCAL_BG)
        self.panel_stack.addWidget(self.uc_theme_local)

        # Cloud theme download path — wraps next/'s CloudThemeService so
        # the legacy UCThemeWeb widget keeps its (theme_id, resolution,
        # cache_dir) → str|None signature.
        #
        # IMPORTANT: download only.  Legacy splits "download" and "select"
        # into two events: the worker thread downloads (no playback),
        # then ``_on_download_complete`` auto-selects the now-cached
        # tile through the normal click handler — which routes to
        # ``LCDHandler.select_cloud_theme`` → ``LoadCloudTheme`` →
        # ``PlayVideo``.  If the download path itself dispatches
        # ``LoadCloudTheme``, playback starts twice and the cached
        # tile's QMovie thumb never gets a chance to be the trigger.
        _app_local = self._app

        def _download_theme(theme_id: str, resolution: str, cache_dir: str) -> str | None:
            del cache_dir  # next/ owns the cache path
            from ...core.models import parse_resolution
            try:
                w, h = parse_resolution(resolution)
            except ValueError:
                log.warning("_download_theme: bad resolution %r", resolution)
                return None
            try:
                mp4_path = _app_local.cloud_themes.materialise(
                    theme_id, (w, h),
                )
            except Exception as e:
                log.warning("_download_theme: materialise %s failed: %s: %s",
                            theme_id, type(e).__name__, e)
                return None
            return str(mp4_path)

        def _extract_theme(archive: str, dest: str) -> None:
            del archive, dest  # CloudThemeService downloads-and-extracts atomically

        self.uc_theme_web = UCThemeWeb(download_fn=_download_theme, extract_fn=_extract_theme)
        self._set_panel_bg(self.uc_theme_web, Assets.THEME_WEB_BG)
        self.panel_stack.addWidget(self.uc_theme_web)

        self.uc_theme_setting = UCThemeSetting(ui_state=self._ui_state)
        self.panel_stack.addWidget(self.uc_theme_setting)

        self.uc_theme_mask = UCThemeMask(paths=self._app.platform.paths())
        self._set_panel_bg(self.uc_theme_mask, Assets.THEME_MASK_BG)
        self.panel_stack.addWidget(self.uc_theme_mask)

        # Activity sidebar
        self.uc_activity_sidebar = UCActivitySidebar(self.form_container)
        self.uc_activity_sidebar.setGeometry(532, 128, 250, 500)
        self.uc_activity_sidebar.setVisible(False)

        # Bottom controls + title buttons
        self._create_bottom_controls()
        self._create_title_buttons()
        self._apply_settings_backgrounds()

        # About panel — gpu_list via the ListGpus Command (the sensor
        # aggregator's GPUs as (key, name) tuples).  Legacy fed this from
        # the enumerator's get_gpu_list(); the new tree's equivalent is the
        # ListGpus Command (the new SensorEnumerator port exposes gpus(),
        # not the legacy get_gpu_list — calling that left it always empty,
        # so the About panel wrongly showed "No GPU detected").
        gpus_result = self._app.dispatch(ListGpus())
        gpu_list: list[tuple[str, str]] = (
            [(g.key, g.name) for g in gpus_result.gpus]
            if gpus_result.ok else []
        )
        self.uc_about = UCAbout(
            parent=central, platform=self._app.platform,
            gpu_list=gpu_list, app=self._app, ui_state=self._ui_state,
        )
        self.uc_about.setGeometry(*Layout.FORM_CONTAINER)
        self.uc_about.setVisible(False)

        # System info dashboard
        from ...adapters.infra.sysinfo_config import SysInfoConfig
        self.uc_system_info = UCSystemInfo(
            self._sensors,
            sysinfo_config=SysInfoConfig(),
            parent=central)
        self.uc_system_info.setGeometry(*Layout.SYSINFO_PANEL)
        self.uc_system_info.setVisible(False)

        # LED panel — Platform port supplies the memory/disk probes.
        # Language is injected (composition root) so the panel never reaches
        # into _boot for global settings.
        self.uc_led_control = UCLedControl(central, self._app.settings.app.language)
        self.uc_led_control.setGeometry(*Layout.FORM_CONTAINER)
        self.uc_led_control.setVisible(False)
        self.uc_led_control.set_hardware_fns(
            self._app.platform.memory_info,
            self._app.platform.disk_info,
        )

        # Form1 buttons
        self.form1_close_btn = create_image_button(
            central, *Layout.FORM1_CLOSE_BTN,
            Assets.BTN_POWER, Assets.BTN_POWER_HOVER, fallback_text="X")
        self.form1_close_btn.setToolTip("Close")
        self.form1_close_btn.clicked.connect(self.close)

        self.form1_help_btn = create_image_button(
            central, *Layout.FORM1_HELP_BTN,
            Assets.BTN_HELP, None, fallback_text="?")
        self.form1_help_btn.setToolTip("Help")
        self.form1_help_btn.clicked.connect(self._on_help_clicked)

        self._create_i18n_overlays()

    def _set_panel_bg(self, widget: QWidget, asset_name: str) -> None:
        pix = set_background_pixmap(widget, asset_name)
        if pix:
            self._pixmap_refs.append(pix)

    def _create_mode_tabs(self) -> None:
        self.mode_buttons = []
        tab_configs = [
            (Layout.TAB_LOCAL, Assets.TAB_LOCAL, Assets.TAB_LOCAL_ACTIVE, 0, "Local themes"),
            (Layout.TAB_MASK, Assets.TAB_MASK, Assets.TAB_MASK_ACTIVE, 3, "Cloud masks"),
            (Layout.TAB_CLOUD, Assets.TAB_CLOUD, Assets.TAB_CLOUD_ACTIVE, 1, "Cloud backgrounds"),
            (Layout.TAB_SETTINGS, Assets.TAB_SETTINGS, Assets.TAB_SETTINGS_ACTIVE, 2, "Settings"),
        ]
        for rect, normal_img, active_img, panel_idx, tooltip in tab_configs:
            x, y, w, h = rect
            btn = create_image_button(
                self.form_container, x, y, w, h,
                normal_img, active_img, checkable=True)
            btn.setToolTip(tooltip)
            btn.setProperty('panel_idx', panel_idx)
            btn.clicked.connect(self._on_mode_button_clicked)
            self.mode_buttons.append(btn)
        if self.mode_buttons:
            self.mode_buttons[0].setChecked(True)

    def _create_bottom_controls(self) -> None:
        self.rotation_combo = QComboBox(self.form_container)
        self.rotation_combo.setGeometry(*Layout.ROTATION_COMBO)
        self.rotation_combo.addItems(["0°", "90°", "180°", "270°"])
        self.rotation_combo.setStyleSheet(
            "QComboBox { background-color: #2A2A2A; color: white; border: 1px solid #555;"
            " font-size: 10px; padding-left: 5px; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox QAbstractItemView { background-color: #2A2A2A; color: white;"
            " selection-background-color: #4A6FA5; }")
        self.rotation_combo.setToolTip("LCD rotation")
        self.rotation_combo.currentIndexChanged.connect(self._on_rotation_change)

        from ...core.registry import BRIGHTNESS_STEPS
        self._ldd_pixmaps: dict = {}
        for i, percent in enumerate(BRIGHTNESS_STEPS, start=1):
            pix = Assets.load_pixmap(f'app_brightness_{i}.png')
            if not pix.isNull():
                self._ldd_pixmaps[i] = pix        # split mode key (1-3)
                self._ldd_pixmaps[percent] = pix  # brightness key (25/50/100)

        self.ldd_btn = QPushButton(self.form_container)
        self.ldd_btn.setGeometry(*Layout.BRIGHTNESS_BTN)
        self.ldd_btn.setToolTip("Cycle brightness (Low / Medium / High)")
        self.ldd_btn.clicked.connect(self._on_ldd_click)
        self._update_ldd_icon()

        self.theme_name_input = QLineEdit(self.form_container)
        self.theme_name_input.setGeometry(*Layout.THEME_NAME_INPUT)
        self.theme_name_input.setText("Theme1")
        self.theme_name_input.setMaxLength(10)
        self.theme_name_input.setToolTip("Theme name for saving")
        self.theme_name_input.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.theme_name_input.setStyleSheet(
            "background-color: #232227; color: white; border: none;"
            " font-family: 'Microsoft YaHei'; font-size: 9pt;")
        self.theme_name_input.setValidator(
            QRegularExpressionValidator(QRE(r'[^/\\:*?"<>|\x00-\x1f]+')))

        self.save_btn = self._icon_btn(*Layout.SAVE_BTN, Assets.BTN_SAVE, "S")
        self.save_btn.setToolTip("Save theme")
        self.save_btn.clicked.connect(self._on_save_clicked)

        self.export_btn = self._icon_btn(*Layout.EXPORT_BTN, Assets.BTN_EXPORT, "Exp")
        self.export_btn.setToolTip("Export theme to file")
        self.export_btn.clicked.connect(self._on_export_clicked)

        self.import_btn = self._icon_btn(*Layout.IMPORT_BTN, Assets.BTN_IMPORT, "Imp")
        self.import_btn.setToolTip("Import theme from file")
        self.import_btn.clicked.connect(self._on_import_clicked)

    def _icon_btn(self, x: int, y: int, w: int, h: int,
                  icon_name: str, fallback_text: str) -> QPushButton:
        btn = QPushButton(self.form_container)
        btn.setGeometry(x, y, w, h)
        pix = Assets.load_pixmap(icon_name, w, h)
        if not pix.isNull():
            btn.setIcon(QIcon(pix))
            btn.setIconSize(btn.size())
            btn.setStyleSheet(Styles.ICON_BUTTON_HOVER)
            self._pixmap_refs.append(pix)
        else:
            btn.setText(fallback_text)
            btn.setStyleSheet(Styles.TEXT_BUTTON)
        return btn

    def _create_title_buttons(self) -> None:
        help_btn = create_image_button(
            self.form_container, *Layout.HELP_BTN, Assets.BTN_HELP, None, fallback_text="?")
        help_btn.setToolTip("Help")
        help_btn.clicked.connect(self._on_help_clicked)

        close_btn = create_image_button(
            self.form_container, *Layout.CLOSE_BTN,
            Assets.BTN_POWER, Assets.BTN_POWER_HOVER, fallback_text="X")
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.close)

    def _apply_settings_backgrounds(self) -> None:
        s = self.uc_theme_setting
        for panel, bg_name in [
            (s.mask_panel, 'settings_background.png'),
            (s.background_panel, 'settings_background.png'),
            (s.screencast_panel, 'settings_background.png'),
            (s.video_panel, 'settings_background.png'),
            (s.overlay_grid, 'settings_overlay.png'),
            (s.color_panel, 'settings_params.png'),
        ]:
            self._set_panel_bg(panel, bg_name)

    # ── i18n overlays ───────────────────────────────────────────────

    def _create_i18n_overlays(self) -> None:
        from ...core.i18n import (
            ABOUT_AUTOSTART_POS,
            ABOUT_GPU_POS,
            ABOUT_HDD_POS,
            ABOUT_HDD_WARN_POS,
            ABOUT_LANG_POS,
            ABOUT_MULTI_THREAD_POS,
            ABOUT_REFRESH_POS,
            ABOUT_RUNNING_MODE_POS,
            ABOUT_SINGLE_THREAD_POS,
            ABOUT_UNIT_POS,
            ABOUT_UPDATE_POS,
            ABOUT_VERSION_POS,
            BACKGROUND_LOAD_IMG_POS,
            BACKGROUND_LOAD_VIDEO_POS,
            DISPLAY_ANGLE_POS,
            EXPORT_IMPORT_POS,
            GALLERY_TAB_FONT,
            GALLERY_TAB_H,
            GALLERY_TAB_Y,
            GALLERY_TITLE_POS,
            LANGUAGE_NAMES,
            LOCAL_THEME_POS,
            MASK_DESC_POS,
            MASK_LOAD_POS,
            MASK_UPLOAD_POS,
            MEDIA_PLAYER_LOAD_POS,
            ONLINE_THEME_POS,
            OVERLAY_GRID_HINT_POS,
            PARAM_COLOUR_POS,
            PARAM_COORDINATE_POS,
            PARAM_FONT_POS,
            SAVE_AS_POS,
            TITLE_BAR_POS,
            TITLE_BAR_TEXT,
            tr,
        )
        lang = self._app.settings.app.language
        self._i18n_labels: list[tuple[QLabel, str | None]] = []

        def _lbl(parent: QWidget, text: str, x: int, y: int, w: int, h: int,
                 pt: int, key: str | None = None,
                 bold: bool = False, color: str = 'white',
                 wrap: bool = False, center: bool = False) -> QLabel:
            y_offset = max(2, pt // 4)
            lbl = QLabel(text, parent)
            lbl.setGeometry(x, y - y_offset, w, h)
            weight = " font-weight: bold;" if bold else ""
            lbl.setStyleSheet(
                f"color: {color}; font-family: 'Microsoft YaHei';"
                f" font-size: {pt}pt;{weight} background: transparent;")
            if wrap:
                lbl.setWordWrap(True)
            if center:
                lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            lbl.raise_()
            self._i18n_labels.append((lbl, key))
            return lbl

        x, y, w, h, pt = TITLE_BAR_POS
        _lbl(self.form_container, TITLE_BAR_TEXT, x, y, w, h, pt, bold=True, color='#434343')

        for key, pos in [
            ('Display Angle', DISPLAY_ANGLE_POS),
            ('Save As', SAVE_AS_POS),
            ('Export/Import', EXPORT_IMPORT_POS),
        ]:
            x, y, w, h, pt = pos
            _lbl(self.form_container, tr(key, lang), x, y, w, h, pt, key)

        grid = self.uc_theme_setting.data_table
        x, y, w, h, pt = OVERLAY_GRID_HINT_POS
        _lbl(grid, tr('Double-click to delete card', lang), x, y, w, h, pt,
             'Double-click to delete card')

        rpanel = self.uc_theme_setting.right_stack
        for key, pos in [
            ('Coordinate', PARAM_COORDINATE_POS),
            ('Font', PARAM_FONT_POS),
            ('Colour', PARAM_COLOUR_POS),
        ]:
            x, y, w, h, pt = pos
            _lbl(rpanel, tr(key, lang), x, y, w, h, pt, key)

        s = self.uc_theme_setting
        s.mask_panel.set_title(tr('Layer Mask', lang))
        s.background_panel.set_title(tr('Background', lang))
        s.screencast_panel.set_title(tr('Screencast', lang))
        s.video_panel.set_title(tr('Media Player', lang))
        self._i18n_panel_tables = [
            (s.mask_panel, 'Layer Mask'),
            (s.background_panel, 'Background'),
            (s.screencast_panel, 'Screencast'),
            (s.video_panel, 'Media Player'),
        ]

        mp = s.mask_panel
        x, y, w, h, pt = MASK_LOAD_POS
        _lbl(mp, tr('Masks', lang), x, y, w, h, pt, 'Masks', center=True)
        x, y, w, h, pt = MASK_UPLOAD_POS
        _lbl(mp, tr('Upload', lang), x, y, w, h, pt, 'Upload', center=True)
        x, y, w, h, pt = MASK_DESC_POS
        _lbl(mp, tr('PNG format, resolution must not exceed screen resolution', lang),
             x, y, w, h, pt,
             'PNG format, resolution must not exceed screen resolution', wrap=True)

        bp = s.background_panel
        for key, pos in [('Load Image', BACKGROUND_LOAD_IMG_POS),
                         ('Load Video', BACKGROUND_LOAD_VIDEO_POS)]:
            x, y, w, h, pt = pos
            _lbl(bp, tr(key, lang), x, y, w, h, pt, key)

        vp = s.video_panel
        x, y, w, h, pt = MEDIA_PLAYER_LOAD_POS
        _lbl(vp, tr('Load Video', lang), x, y, w, h, pt, 'Load Video')

        x, y, w, h, pt = LOCAL_THEME_POS
        _lbl(self.uc_theme_local, tr('Local Theme', lang), x, y, w, h, pt, 'Local Theme')

        x, y, w, h, pt = ONLINE_THEME_POS
        _lbl(self.uc_theme_mask, tr('Cloud Masks', lang), x, y, w, h, pt, 'Cloud Masks')

        x, y, w, h, pt = GALLERY_TITLE_POS
        _lbl(self.uc_theme_web, tr('Gallery', lang), x, y, w, h, pt, 'Gallery')
        tab_x_positions = [45, 135, 235, 335, 430, 525, 635]
        tab_keys: list[str | None] = ['All', 'Tech', None, 'Light', 'Nature', 'Aesthetic', 'Other']
        for tx, key in zip(tab_x_positions, tab_keys, strict=False):
            text = 'HUD' if key is None else tr(key, lang)
            lbl = _lbl(self.uc_theme_web, text,
                       tx, GALLERY_TAB_Y, 90, GALLERY_TAB_H, GALLERY_TAB_FONT, key)
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        about_items: list[tuple[str, tuple[int, ...]]] = [
            ('Start automatically', ABOUT_AUTOSTART_POS),
            ('Unit', ABOUT_UNIT_POS),
            ('Hard disk information', ABOUT_HDD_POS),
            ('Reading hard disk information may cause some mechanical hard drives to read and write frequently. If you encounter this issue, please close the project.',
             ABOUT_HDD_WARN_POS),
            ('Data refresh time', ABOUT_REFRESH_POS),
            ('Running Mode', ABOUT_RUNNING_MODE_POS),
            ('Single-threaded (low resource usage)', ABOUT_SINGLE_THREAD_POS),
            ('Multi-threaded (high resource usage)', ABOUT_MULTI_THREAD_POS),
            ('Software Update', ABOUT_UPDATE_POS),
            ('Language selection', ABOUT_LANG_POS),
            ('Graphics card', ABOUT_GPU_POS),
            ('Software version:', ABOUT_VERSION_POS),
        ]
        for key, pos in about_items:
            x, y, w, h, pt = pos
            _lbl(self.uc_about, tr(key, lang), x, y, w, h, pt, key)

        lang_combo = QComboBox(self.uc_about)
        lang_combo.setGeometry(297, 413, 200, 28)
        def _by_display_name(code: str) -> str:
            return LANGUAGE_NAMES[code]
        for code in sorted(LANGUAGE_NAMES, key=_by_display_name):
            lang_combo.addItem(LANGUAGE_NAMES[code], code)
        idx = lang_combo.findData(lang)
        if idx >= 0:
            lang_combo.setCurrentIndex(idx)
        lang_combo.setStyleSheet(
            "QComboBox { background: #2A2A2A; color: white; border: 1px solid #555;"
            " font-size: 10pt; padding-left: 5px; }"
            "QComboBox::drop-down { border: none; width: 20px; }"
            "QComboBox QAbstractItemView { background: #2A2A2A; color: white;"
            " selection-background-color: #3A3A3A; }")
        lang_combo.raise_()

        def _on_preview_lang(index: int) -> None:
            new_lang = lang_combo.itemData(index)
            for lbl, key in self._i18n_labels:
                if key is not None:
                    lbl.setText(tr(key, new_lang))
            for panel, key in self._i18n_panel_tables:
                panel.set_title(tr(key, new_lang))
            self.uc_about._on_lang_clicked(new_lang)

        lang_combo.currentIndexChanged.connect(_on_preview_lang)


    # ── View Navigation ─────────────────────────────────────────────

    def _on_mode_button_clicked(self, *_qt_args: Any) -> None:
        """Mode-button slot — reads panel_idx from sender's property."""
        log.info("_on_mode_button_clicked")
        sender = self.sender()
        if sender is None:
            return
        panel_idx = sender.property('panel_idx')
        if panel_idx is not None:
            self._show_panel(panel_idx)

    def _show_panel(self, index: int) -> None:
        self.panel_stack.setCurrentIndex(index)
        panel_to_button = {0: 0, 1: 2, 2: 3, 3: 1}
        active_btn = panel_to_button.get(index, 0)
        for i, btn in enumerate(self.mode_buttons):
            btn.setChecked(i == active_btn)
        if index != 2:
            self.uc_activity_sidebar.setVisible(False)

    # ── View-switch slots (named, not lambdas) ──────────────────────

    def _on_home_clicked(self) -> None:
        log.info("_on_home_clicked")
        self._show_view('sysinfo')

    def _on_about_clicked(self) -> None:
        log.info("_on_about_clicked")
        self._show_view('about')

    # ── Download status slots ───────────────────────────────────────

    def _on_theme_download_started(self, theme_id: str) -> None:
        log.info("_on_theme_download_started: theme_id=%s", theme_id)
        self.uc_preview.set_status(f"Downloading: {theme_id}...")

    def _on_theme_download_finished(self, theme_id: str, ok: bool) -> None:
        log.info("_on_theme_download_finished: theme_id=%s ok=%s", theme_id, ok)
        verb = 'Downloaded' if ok else 'Download failed'
        self.uc_preview.set_status(f"{verb}: {theme_id}")

    def _on_mask_download_started(self, mask_id: str) -> None:
        log.info("_on_mask_download_started: mask_id=%s", mask_id)
        self.uc_preview.set_status(f"Downloading: {mask_id}...")

    def _on_mask_download_finished(self, mask_id: str, ok: bool) -> None:
        log.info("_on_mask_download_finished: mask_id=%s ok=%s", mask_id, ok)
        verb = 'Downloaded' if ok else 'Failed'
        self.uc_preview.set_status(f"{verb}: {mask_id}")

    def _on_drag_end_noop(self) -> None:
        """No-op slot — drag-end emits the signal but we don't act on it here."""
        log.info("_on_drag_end_noop")

    def _on_element_added(self, _payload: Any) -> None:
        """Hide the activity sidebar once an element has been added to the theme."""
        log.info("_on_element_added")
        self.uc_activity_sidebar.setVisible(False)

    def _show_view(self, view: str) -> None:
        active = getattr(self, '_active_path', None)
        log.debug("view=%s active_path=%s", view, active)
        if view not in ('form', 'led'):
            log.debug("clearing active_path (was %s)", active)
            self._active_key = ''  # allow re-selecting same device on return
        self.form_container.setVisible(view == 'form')
        self.uc_about.setVisible(view == 'about')
        self.uc_system_info.setVisible(view == 'sysinfo')
        self.uc_led_control.setVisible(view == 'led')
        self.uc_activity_sidebar.setVisible(False)

        # uc_system_info populates on the periodic SensorsUpdated
        # broadcast (every refresh_interval_s).  Without an immediate
        # populate, the panel sits BLANK from open-click until the
        # next bus tick — which can be tens of seconds at user-chosen
        # intervals.  Mirror the bus fan-out one-shot so the user
        # sees data the moment the panel appears.
        if view == 'sysinfo':
            self._fan_out_metrics(reason="view-switch:sysinfo")

    # ── Signal Wiring ───────────────────────────────────────────────

    def _connect_view_signals(self) -> None:
        self.uc_device.device_selected.connect(self._on_device_widget_clicked)
        self.uc_device.home_clicked.connect(self._on_home_clicked)
        self.uc_device.about_clicked.connect(self._on_about_clicked)

        self.uc_theme_local.theme_selected.connect(self._on_local_theme_clicked)
        self.uc_theme_local.delete_requested.connect(self._on_delete_theme)
        self.uc_theme_local.delegate.connect(self._on_local_delegate)
        self.uc_theme_web.theme_selected.connect(self._on_cloud_theme_clicked)
        self.uc_theme_web.download_started.connect(self._on_theme_download_started)
        self.uc_theme_web.download_finished.connect(self._on_theme_download_finished)
        self.uc_theme_mask.mask_selected.connect(self._on_mask_clicked)
        self.uc_theme_mask.download_started.connect(self._on_mask_download_started)
        self.uc_theme_mask.download_finished.connect(self._on_mask_download_finished)

        self.uc_preview.delegate.connect(self._on_preview_delegate)
        self.uc_preview.element_drag_start.connect(self._on_drag_start)
        self.uc_preview.element_drag_move.connect(self._on_drag_move)
        self.uc_preview.element_drag_end.connect(self._on_drag_end_noop)
        self.uc_preview.element_nudge.connect(self._on_nudge)
        self._drag_origin_x = 0
        self._drag_origin_y = 0
        self._drag_elem_x = 0
        self._drag_elem_y = 0

        self.uc_theme_setting.background_changed.connect(self._on_background_toggle)
        self.uc_theme_setting.screencast_changed.connect(self._on_screencast_toggle)
        self.uc_theme_setting.delegate.connect(self._on_settings_delegate)
        self.uc_theme_setting.format_pref_changed.connect(self._on_format_pref_changed)
        self.uc_theme_setting.add_panel.hardware_requested.connect(
            self._on_overlay_add_requested)
        self.uc_theme_setting.add_panel.element_added.connect(self._on_element_added)
        self.uc_theme_setting.overlay_grid.toggle_changed.connect(self._on_overlay_toggle)
        self.uc_theme_setting.overlay_grid.element_selected.connect(self._on_element_flash)
        self.uc_theme_setting.screencast_params_changed.connect(self._screencast.set_params)
        self.uc_theme_setting.screencast_panel.border_toggled.connect(self._screencast.set_border)
        self.uc_theme_setting.screencast_panel.audio_toggled.connect(self._screencast.set_audio_enabled)
        self.uc_theme_setting.capture_requested.connect(self._on_capture_requested)
        self.uc_theme_setting.eyedropper_requested.connect(self._on_eyedropper_requested)

        self.uc_image_cut.image_cut_done.connect(self._on_image_cut_done)
        self.uc_video_cut.video_cut_done.connect(self._on_video_cut_done)

        self.uc_activity_sidebar.sensor_clicked.connect(self._on_sensor_element_add)

        self.uc_about.close_requested.connect(self._on_about_close_requested)
        self.uc_about.language_changed.connect(self._set_language)
        self.uc_about.temp_unit_changed.connect(self._on_temp_unit_changed)
        self.uc_about.hdd_toggle_changed.connect(self._on_hdd_toggle_changed)
        self.uc_about.refresh_changed.connect(self._on_refresh_changed)
        self.uc_about.gpu_changed.connect(self._on_gpu_changed)

    # ── Device Selection ────────────────────────────────────────────

    def _on_device_widget_clicked(self, device_info: dict) -> None:
        """User clicked a device in the sidebar."""
        path = device_info.get('path', '')
        log.debug("_on_device_widget_clicked: path=%s", path)
        if path:
            self._activate_device(path)

    def _on_about_close_requested(self) -> None:
        """Close button on About/Control Center panel — return to form view."""
        log.debug("_on_about_close_requested: returning to form")
        self._show_view('form')
        self.uc_device.restore_device_selection()

    def _active_handler(self) -> BaseHandler | None:
        return self._handlers.get(self._active_key)

    def _active_lcd(self) -> LCDHandler | None:
        h = self._handlers.get(self._active_key)
        return h if isinstance(h, LCDHandler) else None

    def _active_led(self) -> LEDHandler | None:
        h = self._handlers.get(self._active_key)
        return h if isinstance(h, LEDHandler) else None

    # ── Handshake (LCD resolution discovery) ────────────────────────

    def _start_handshake(self, device: Any) -> None:
        """Dispatch ConnectDevice in a background thread.

        next/'s ``ConnectDevice`` Command runs the wire-protocol handshake
        and returns a ``ConnectResult`` with the device's resolution.  We
        run it off the Qt main thread so the GUI stays responsive during
        the SCSI / HID round-trip; the result is delivered back via the
        ``_hs_notifier`` Qt signal on the main thread.
        """
        key = device.info.key if hasattr(device, "info") else str(device)
        log.debug("_start_handshake: key=%s pending=%s", key, self._handshake_pending)
        if self._handshake_pending:
            return
        self._handshake_pending = True
        self.uc_preview.set_status("Connecting to device...")

        import threading

        def worker() -> None:
            try:
                result = self._app.dispatch(ConnectDevice(key=key))
            except Exception as exc:
                log.exception("ConnectDevice raised")
                result = None
                _ = exc
            self._hs_notifier.done.emit(device, result)
        threading.Thread(target=worker, daemon=True).start()

    def _on_handshake_done(self, device: Any, result: Any) -> None:
        """Wire a ConnectResult into the handler + sidebar.

        next/'s ConnectDevice already populated ``device.profile`` (or
        left it None on failure).  This handler propagates the resolved
        identity to ``uc_device`` and surfaces the resolution to the
        active LCD handler.
        """
        key = device.info.key if hasattr(device, "info") else str(device)
        log.debug("_on_handshake_done: key=%s ok=%s",
                  key, getattr(result, "ok", False))
        self._handshake_pending = False
        if result is None or not getattr(result, "ok", False):
            self.uc_preview.set_status("Handshake failed — replug device")
            return

        live = self._app.devices.get(key)
        if live is None or live.profile is None:
            self.uc_preview.set_status("Handshake failed — no profile")
            return
        w, h = live.profile.resolution
        log.info("Handshake OK: %s -> %dx%d", key, w, h)

        # Sync sidebar from the enriched device.
        self._sync_device_identity(live)

        handler = self._handlers.get(key)
        if isinstance(handler, LCDHandler):
            log.debug("_on_handshake_done: handler is_configured=%r", handler.is_configured)
            if not handler.is_configured:
                handler.apply_device_config(live.info, w, h)
                self._update_ldd_icon()
                if self._ui_state.state.show_info_module:
                    self.uc_info_module.setVisible(True)
            else:
                log.debug("_on_handshake_done: skipping apply_device_config — already initialized")

    def _sync_device_identity(self, device: Any) -> None:
        """Propagate ``device.info.button_image`` to the sidebar widget.

        next/'s ConnectDevice already enriches ``device.info`` through
        the registry; this method handles the GUI-side view sync —
        sidebar dict refresh + button image update.
        """
        info = device.info
        btn_img = getattr(info, "button_image", "") or ""
        from ...core.registry import LCD_DEFAULT_BUTTON
        if not btn_img or btn_img == LCD_DEFAULT_BUTTON:
            log.debug("no resolved button for %s — keeping default", info.key)
            return
        product = btn_img.replace('A1', '', 1).replace('_', ' ')
        log.info("%s -> %s (%s)", info.key, btn_img, product)
        for dev in self.uc_device.devices:
            if dev.get('path') == info.key:
                dev['button_image'] = btn_img
                dev['product'] = product
                dev['name'] = f"Thermalright {product}"
                self.uc_device.update_device_button(dev)
                log.debug("_sync_device_identity: updated sidebar button for %s", info.key)
                break
        # next/'s device registry persists the button_image lookup
        # automatically through ProductInfo enrichment; no separate
        # Settings.save_device_settings call needed here.

    # ── Theme Event Handlers ─────────────────────────────────────────

    def _on_local_theme_clicked(self, theme_info: Any) -> None:
        log.debug("_on_local_theme_clicked: %s", getattr(theme_info, 'name', theme_info))
        h = self._active_lcd()
        if h:
            h.select_theme_from_path(Path(theme_info.path))
            name = theme_info.name
            if name.startswith('Custom_'):
                name = name[len('Custom_'):]
            self.theme_name_input.setText(name)

    def _on_cloud_theme_clicked(self, theme_info: Any) -> None:
        log.info("_on_cloud_theme_clicked: %s",
                 getattr(theme_info, 'name', theme_info))
        h = self._active_lcd()
        if h is None:
            log.warning(
                "_on_cloud_theme_clicked: no active LCD handler "
                "(active_key=%r) — click dropped", self._active_key,
            )
            return
        h.select_cloud_theme(theme_info)

    def _on_mask_clicked(self, mask_info: Any) -> None:
        log.debug("_on_mask_clicked: %s", getattr(mask_info, 'name', mask_info))
        h = self._active_lcd()
        if h:
            h.apply_mask(mask_info)

    def _on_local_delegate(self, cmd: Any, info: Any, data: Any) -> None:
        log.info("_on_local_delegate")
        if cmd == UCThemeLocal.CMD_SLIDESHOW:
            h = self._active_lcd()
            if h:
                h.on_slideshow_delegate()

    def _on_delete_theme(self, theme_info: Any) -> None:
        log.info("_on_delete_theme")
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Delete Theme", f"Delete theme '{theme_info.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.uc_theme_local.delete_theme(theme_info)
            h = self._active_lcd()
            # The handler tracks the active theme directory on its
            # ``_state.current_theme_path``; if the deleted theme was
            # the active one, invalidate the scene cache so the next
            # render rebuilds from whatever falls back as background.
            current = h.current_theme_path if h is not None else None
            if (h is not None and current is not None
                    and str(current) == theme_info.path):
                self._app.display.invalidate(h.device_key)
                self.uc_preview.set_image(None)
            self.uc_preview.set_status(f"Deleted: {theme_info.name}")

    # ── Settings Delegates ──────────────────────────────────────────

    def _on_settings_delegate(self, cmd: Any, info: Any, data: Any) -> None:
        log.debug("_on_settings_delegate: cmd=%s info=%s", cmd, info)
        h = self._active_lcd()
        match cmd:
            case UCThemeSetting.CMD_BACKGROUND_LOAD_IMAGE:
                self._on_load_image_clicked()
            case UCThemeSetting.CMD_BACKGROUND_LOAD_VIDEO:
                self._on_load_video_clicked()
            case UCThemeSetting.CMD_MASK_TOGGLE | UCThemeSetting.CMD_MASK_VISIBILITY:
                if h:
                    # ``SetMaskVisible`` persists the toggle, invalidates
                    # the scene cache, and publishes ``MaskVisibilityChanged``
                    # — ``DeviceRenderObserver`` picks that up and schedules
                    # a render.  No direct ``_render_and_send`` needed.
                    self._app.dispatch(SetMaskVisible(
                        key=h.device_key, visible=bool(info),
                    ))
            case UCThemeSetting.CMD_MASK_UPLOAD:
                self._on_mask_upload_clicked()
            case UCThemeSetting.CMD_MASK_POSITION:
                if h and info:
                    h.update_mask_position(info[0], info[1])
            case UCThemeSetting.CMD_MASK_LOAD | UCThemeSetting.CMD_MASK_CLOUD:
                self._show_panel(3)
            case UCThemeSetting.CMD_VIDEO_LOAD:
                self._on_media_player_load_clicked()
            case 51:
                self._show_panel(1)
            case UCThemeSetting.CMD_VIDEO_TOGGLE:
                self._on_video_display_toggle(info)
            case UCThemeSetting.CMD_OVERLAY_CHANGED:
                if h:
                    # ``_on_elements_changed`` now dispatches the next/ element
                    # LIST (id + flat font); ``on_overlay_changed`` accepts
                    # list or dict.  Gating on dict-only here silently dropped
                    # every edit (colour/drag) — the list fell to ``{}``.
                    h.on_overlay_changed(
                        info if isinstance(info, (dict, list)) else {},
                    )

    def _on_preview_delegate(self, cmd: Any, info: Any, data: Any) -> None:
        log.info("_on_preview_delegate")
        if not (h := self._active_lcd()):
            return
        match cmd:
            case UCPreview.CMD_VIDEO_PLAY_PAUSE:
                h.play_pause()
            case UCPreview.CMD_VIDEO_SEEK:
                h.seek(info)
            case UCPreview.CMD_VIDEO_FIT_WIDTH:
                h.set_video_fit_mode('width')
            case UCPreview.CMD_VIDEO_FIT_HEIGHT:
                h.set_video_fit_mode('height')

    # ── Background / Screencast / Video Toggles ─────────────────────

    def _on_background_toggle(self, enabled: bool) -> None:
        log.info("_on_background_toggle: enabled=%s", enabled)
        h = self._active_lcd()
        if not h:
            return
        if enabled and self._screencast.active:
            # User flipped to the theme-bg panel while a screencast was
            # running — tear it down through the bus so daemon/CLI/API
            # observers see the same transition the GUI just made.
            self._app.dispatch(StopScreencast(key=h.device_key))
        h.on_background_toggle(enabled)

    def _on_screencast_toggle(self, enabled: bool) -> None:
        log.info("_on_screencast_toggle: enabled=%s", enabled)
        h = self._active_lcd()
        if not h:
            return
        if enabled:
            # ``deactivate`` cancels handler timers + clears its render
            # state so the screencast pipeline owns the wire for this
            # device.  ``StartScreencast`` itself stops any video
            # playback (via its internal ``StopVideo``) — bundling that
            # here would duplicate the call.
            h.deactivate()
            h.is_background_active = False
            w, hw = h.lcd_size
            # LCD scaling target stays a direct setter: it's a Qt-only
            # render hint, not a session-lifecycle fact.
            self._screencast.set_lcd_size(w, hw)
            x, y, sw, sh = self._screencast.params
            result = self._app.dispatch(StartScreencast(
                key=h.device_key, x=x, y=y, w=sw, h=sh,
                audio=self._screencast.audio_enabled,
            ))
            if not result.ok:
                log.warning(
                    "_on_screencast_toggle: StartScreencast failed: %s",
                    result.message,
                )
                self.uc_preview.set_status(f"Screencast: {result.message}")
                return
        else:
            self._app.dispatch(StopScreencast(key=h.device_key))
        self.uc_preview.set_status(f"Screencast: {'On' if enabled else 'Off'}")

    def _on_video_display_toggle(self, enabled: bool) -> None:
        log.debug("_on_video_display_toggle: enabled=%s", enabled)
        h = self._active_lcd()
        if not h:
            return
        if not enabled:
            # Turning the "video" mode panel OFF — clear the override
            # video, then re-load the persisted theme so the device
            # shows the theme's bundled bg (image, or its own video).
            # StopVideo's VideoStopped event already stops the timer
            # through the bus_bridge observer chain.
            if h.has_video_playback:
                self._app.dispatch(StopVideo(key=h.device_key))
                self.uc_preview.set_playing(False)
                self.uc_preview.show_video_controls(False)
            if (last_path := h.current_theme_path):
                h.select_theme_from_path(Path(last_path))

    def _on_screencast_frame(self, image: Any) -> None:
        log.info("_on_screencast_frame")
        h = self._active_lcd()
        if h:
            h.on_screencast_frame(image)

    # ── File Dialogs ────────────────────────────────────────────────

    def _on_load_video_clicked(self) -> None:
        log.info("_on_load_video_clicked")
        h = self._active_lcd()
        start_dir = self._video_picker_start_dir(h)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", start_dir,
            "Video Files (*.mp4 *.avi *.mov *.gif);;All Files (*)")
        h = self._active_lcd()
        if path and h:
            w, hw = h.lcd_size
            self.uc_video_cut.set_resolution(w, hw)
            self.uc_video_cut.load_video(path)
            self._show_cutter('video')

    def _on_media_player_load_clicked(self) -> None:
        log.info("_on_media_player_load_clicked")
        h = self._active_lcd()
        start_dir = self._video_picker_start_dir(h)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", start_dir,
            "Video Files (*.mp4 *.avi *.mkv *.mov *.gif);;All Files (*)")
        h = self._active_lcd()
        if not path or not h:
            return
        if self._screencast.active:
            self._app.dispatch(StopScreencast(key=h.device_key))
        h.is_background_active = False
        # ``PlayVideo`` owns the full pipeline: decode, populate
        # MediaService playback, publish ``VideoStarted`` so the
        # handler's timer observer takes over.  Overlay-off is part of
        # "play arbitrary video" UX — disable through the Command bus
        # so persistence + render chain stays in sync.
        self._app.dispatch(EnableOverlay(key=h.device_key, enabled=False))
        result = self._app.dispatch(PlayVideo(
            key=h.device_key, path=Path(path),
        ))
        if not result.ok:
            self.uc_preview.set_status(f"Error: {result.message}")
            return
        self.uc_preview.set_playing(True)
        self.uc_preview.show_video_controls(True)
        self.uc_preview.set_status(f"Playing: {Path(path).name}")

    def _video_picker_start_dir(self, h: Any) -> str:
        """Resolve the QFileDialog start directory for a video pick.

        Defaults to the device's cloud-theme dir (where downloaded mp4s
        live) so the user lands somewhere relevant.  Falls back to ""
        if no active LCD or paths can't be resolved.
        """
        if h is None:
            return ""
        try:
            w, hw = h.lcd_size
        except (AttributeError, TypeError):
            return ""
        if not (w and hw):
            return ""
        cloud_dir = self._app.platform.paths().cloud_theme_dir(w, hw)
        return str(cloud_dir) if cloud_dir.exists() else ""

    def _on_load_image_clicked(self) -> None:
        log.info("_on_load_image_clicked")
        self._cut_mode = 'background'
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Image", "",
            "Image Files (*.png *.jpg *.jpeg *.bmp);;All Files (*)")
        h = self._active_lcd()
        if path and h:
            from PySide6.QtGui import QImage as _QImage
            img = _QImage(path)
            if img.isNull():
                self.uc_preview.set_status("Error: could not load image")
            else:
                w, hw = h.lcd_size
                self.uc_image_cut.load_image(img, w, hw)
                self._show_cutter('image')

    def _on_mask_upload_clicked(self) -> None:
        log.info("_on_mask_upload_clicked")
        self._cut_mode = 'mask'
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload Mask Image", "",
            "PNG Images (*.png);;All Files (*)")
        h = self._active_lcd()
        if path and h:
            from PySide6.QtGui import QImage as _QImage
            img = _QImage(path)
            if img.isNull():
                self.uc_preview.set_status("Error: could not load image")
                self._cut_mode = 'background'
            else:
                self._mask_upload_filename = Path(path).stem
                w, hw = h.lcd_size
                self.uc_image_cut.load_image(img, w, hw)
                self._show_cutter('image')

    def _on_save_clicked(self) -> None:
        name = self.theme_name_input.text().strip()
        log.info("_on_save_clicked: name=%r", name)
        if not name:
            self.uc_preview.set_status("Enter a theme name first")
            return
        h = self._active_lcd()
        if not h:
            return
        r = h.save_theme(name)
        if not r.ok and r.target_exists:
            from PySide6.QtWidgets import QMessageBox
            log.info("_on_save_clicked: %r exists — prompting for overwrite", name)
            reply = QMessageBox.question(
                self, "Overwrite Theme",
                f"A theme named '{name}' already exists.\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                log.info("_on_save_clicked: user confirmed overwrite of %r", name)
                h.save_theme(name, overwrite=True)
            else:
                log.info("_on_save_clicked: user declined overwrite of %r", name)
                self.uc_preview.set_status(
                    "Save cancelled — choose a different name")

    def _on_export_clicked(self) -> None:
        log.info("_on_export_clicked")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Theme", "",
            "Theme files (*.tr);;JSON (*.json);;All Files (*)")
        h = self._active_lcd()
        if path and h:
            h.export_config(Path(path))

    def _on_import_clicked(self) -> None:
        log.info("_on_import_clicked")
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Theme", "",
            "Theme files (*.tr);;JSON (*.json);;All Files (*)")
        h = self._active_lcd()
        if path and h:
            h.import_config(Path(path))

    # ── Image/Video Cutters ─────────────────────────────────────────

    def _show_cutter(self, kind: str) -> None:
        self.uc_preview.setVisible(False)
        self.uc_image_cut.setVisible(kind == 'image')
        self.uc_video_cut.setVisible(kind == 'video')
        (self.uc_image_cut if kind == 'image' else self.uc_video_cut).raise_()

    def _hide_cutters(self) -> None:
        self.uc_image_cut.setVisible(False)
        self.uc_video_cut.setVisible(False)
        self.uc_preview.setVisible(True)

    def _on_image_cut_done(self, result: Any) -> None:
        log.info("_on_image_cut_done")
        self._hide_cutters()
        h = self._active_lcd()
        if result is None or not h:
            self.uc_preview.set_status("Image crop cancelled")
            self._cut_mode = 'background'
            return
        if self._cut_mode == 'mask':
            self._save_and_apply_custom_mask(result)
        else:
            # Persist the cropped QImage as a static bg override.
            # Saving lives in the adapter (Qt is GUI-only); the Command
            # operates on the resulting Path.
            bg_path = self._save_cropped_background(h, result)
            if bg_path is None:
                self.uc_preview.set_status("Error: could not save background")
            else:
                outcome = self._app.dispatch(SetBackground(
                    key=h.device_key, path=bg_path,
                ))
                if outcome.ok:
                    self.uc_preview.set_status("Image loaded")
                else:
                    self.uc_preview.set_status(
                        f"Error: {outcome.message}",
                    )
        self._cut_mode = 'background'

    def _save_cropped_background(self, h: Any, image: Any) -> Path | None:
        """Persist the cropped image to a per-device file.

        Lives in the GUI adapter because QImage is Qt-only — the
        Command sees only the resulting Path, keeping ``core`` clean
        of any framework dependency.
        """
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QImage as _QImage
        if not isinstance(image, _QImage) or image.isNull():
            return None
        try:
            w, hw = h.lcd_size
        except (AttributeError, TypeError):
            return None
        if not (w and hw):
            return None
        # Scope the file per device so two LCDs don't trample each
        # other's backgrounds.  ``user_content_dir`` is the data-port
        # owner for user-supplied content.
        target_dir = (
            self._app.platform.paths().user_content_dir() / "backgrounds"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_key = h.device_key.replace(":", "_") or "default"
        target = target_dir / f"{safe_key}.png"
        scaled = image.convertToFormat(_QImage.Format.Format_ARGB32)
        if scaled.width() != w or scaled.height() != hw:
            scaled = scaled.scaled(
                w, hw,
                _Qt.AspectRatioMode.IgnoreAspectRatio,
                _Qt.TransformationMode.SmoothTransformation,
            )
        if not scaled.save(str(target)):
            log.warning(
                "_save_cropped_background: QImage.save failed for %s", target,
            )
            return None
        log.info("_save_cropped_background: saved %dx%d → %s", w, hw, target)
        return target

    def _save_and_apply_custom_mask(self, cropped: Any) -> None:
        import re

        from PySide6.QtGui import QImage as _QImage
        from PySide6.QtGui import QPainter as _QPainter

        from ...core.models import MaskItem
        h = self._active_lcd()
        if not h:
            return
        if not isinstance(cropped, _QImage) or cropped.isNull():
            return
        w, hw = h.lcd_size
        user_dir = self._app.platform.paths().user_mask_dir(w, hw)
        user_dir.mkdir(parents=True, exist_ok=True)

        raw_name = self._mask_upload_filename or 'custom_001'
        mask_name = re.sub(r'[^\w\-]', '_', raw_name).strip('_') or 'custom'
        base_name = mask_name
        counter = 1
        while (user_dir / mask_name).exists():
            counter += 1
            mask_name = f"{base_name}_{counter}"

        mask_dir = user_dir / mask_name
        mask_dir.mkdir(parents=True, exist_ok=True)

        from PySide6.QtCore import Qt as _Qt
        img = cropped.convertToFormat(_QImage.Format.Format_ARGB32)
        if img.width() != w or img.height() != hw:
            img = img.scaled(w, hw, _Qt.AspectRatioMode.IgnoreAspectRatio,
                             _Qt.TransformationMode.SmoothTransformation)
        img.save(str(mask_dir / '01.png'))

        thumb_size = 120
        scale = min(thumb_size / max(img.width(), 1), thumb_size / max(img.height(), 1))
        tw = int(img.width() * scale)
        th = int(img.height() * scale)
        thumb = img.scaled(tw, th, _Qt.AspectRatioMode.IgnoreAspectRatio,
                           _Qt.TransformationMode.SmoothTransformation)
        bg = _QImage(thumb_size, thumb_size, _QImage.Format.Format_RGB32)
        bg.fill(0)
        painter = _QPainter(bg)
        painter.drawImage((thumb_size - tw) // 2, (thumb_size - th) // 2, thumb)
        painter.end()
        bg.save(str(mask_dir / 'Theme.png'))
        log.info("Imported custom mask: %s", mask_name)

        new_item = MaskItem(
            name=mask_name, path=str(mask_dir),
            preview=str(mask_dir / 'Theme.png'),
            is_local=True, is_custom=True)
        h.apply_mask(new_item)
        if hasattr(self, 'uc_theme_mask'):
            self.uc_theme_mask.refresh_masks()
        self.uc_preview.set_status(f"Custom mask '{mask_name}' uploaded")

    def _on_video_cut_done(self, zt_path: Any) -> None:
        log.info("_on_video_cut_done: zt_path=%s", zt_path)
        self._hide_cutters()
        h = self._active_lcd()
        if zt_path and h:
            # ``PlayVideo`` decodes the .zt, publishes ``VideoStarted``;
            # handler observer starts the per-frame timer.  Same path
            # cloud + local video themes take.
            result = self._app.dispatch(PlayVideo(
                key=h.device_key, path=Path(zt_path),
            ))
            if result.ok:
                self.uc_preview.set_playing(True)
                self.uc_preview.show_video_controls(True)
                self.uc_preview.set_status("Video loaded")
            else:
                self.uc_preview.set_status(f"Error: {result.message}")
        else:
            self.uc_preview.set_status("Video cut cancelled")

    # ── Activity Sidebar / Overlay ───────────────────────────────────

    def _on_overlay_add_requested(self) -> None:
        log.info("_on_overlay_add_requested")
        self.uc_activity_sidebar.setVisible(True)
        self.uc_activity_sidebar.raise_()

    def _on_sensor_element_add(self, config: Any) -> None:
        log.info("_on_sensor_element_add")
        self.uc_theme_setting.overlay_grid.add_element(config)
        self.uc_activity_sidebar.setVisible(False)

    def _on_overlay_toggle(self, enabled: bool) -> None:
        log.debug("_on_overlay_toggle: enabled=%s", enabled)
        h = self._active_lcd()
        if h:
            # ``EnableOverlay`` Command persists the toggle, invalidates
            # the scene cache, and publishes ``OverlayChanged`` — same
            # path every UI uses.  No more direct device-method calls.
            self._app.dispatch(EnableOverlay(
                key=h.device_key, enabled=enabled,
            ))

    def _active_device_key(self) -> str:
        """Return the active device key, or '' if no active device."""
        return self._active_key

    def _on_element_flash(self, index: int, config: dict) -> None:
        log.info("_on_element_flash: index=%s", index)
        h = self._active_lcd()
        if h:
            h.flash_element(index)

    # ── Drag / Nudge ────────────────────────────────────────────────

    def _on_drag_start(self, lcd_x: int, lcd_y: int) -> None:
        log.info("_on_drag_start: lcd_x=%s lcd_y=%s", lcd_x, lcd_y)
        grid = self.uc_theme_setting.overlay_grid
        cfg = grid.get_selected_config()
        if cfg is None:
            idx = grid.find_nearest_element(lcd_x, lcd_y)
            if idx < 0:
                return
            grid.select_element(idx)
            cfg = grid.get_selected_config()
            if cfg is None:
                return
        self._drag_origin_x = lcd_x
        self._drag_origin_y = lcd_y
        self._drag_elem_x = cfg.x
        self._drag_elem_y = cfg.y

    def _on_drag_move(self, lcd_x: int, lcd_y: int) -> None:
        log.info("_on_drag_move: lcd_x=%s lcd_y=%s", lcd_x, lcd_y)
        cfg = self.uc_theme_setting.overlay_grid.get_selected_config()
        h = self._active_lcd()
        if cfg is None or not h:
            return
        w, hw = h.lcd_size
        new_x = max(0, min(self._drag_elem_x + (lcd_x - self._drag_origin_x), w))
        new_y = max(0, min(self._drag_elem_y + (lcd_y - self._drag_origin_y), hw))
        self.uc_theme_setting.color_panel.set_position(new_x, new_y)
        self.uc_theme_setting._on_position_changed(new_x, new_y)

    def _on_nudge(self, dx: int, dy: int) -> None:
        log.info("_on_nudge: dx=%s dy=%s", dx, dy)
        cfg = self.uc_theme_setting.overlay_grid.get_selected_config()
        h = self._active_lcd()
        if cfg is None or not h:
            return
        w, hw = h.lcd_size
        new_x = max(0, min(cfg.x + dx, w))
        new_y = max(0, min(cfg.y + dy, hw))
        self.uc_theme_setting.color_panel.set_position(new_x, new_y)
        self.uc_theme_setting._on_position_changed(new_x, new_y)

    # ── Display Settings ────────────────────────────────────────────

    def _on_rotation_change(self, index: int) -> None:
        log.debug("_on_rotation_change: index=%s", index)
        h = self._active_lcd()
        if h:
            h.set_rotation(index * 90)
            self.uc_preview.set_status(f"Rotation: {index * 90}°")

    def _on_ldd_click(self) -> None:
        log.info("_on_ldd_click")
        h = self._active_lcd()
        if not h:
            return
        if h.ldd_is_split:
            mode = (h.split_mode % 3) + 1
            h.set_split_mode(mode)
            self._update_ldd_icon()
            self.uc_preview.set_status(f"Split mode: {mode}")
        else:
            from ...core.registry import BRIGHTNESS_STEPS
            steps = BRIGHTNESS_STEPS
            cur = h.brightness_level
            nxt = steps[(steps.index(cur) + 1) % len(steps)] if cur in steps else steps[0]
            h.set_brightness(nxt)
            self._update_ldd_icon()
            self.uc_preview.set_status(f"Brightness: {nxt}%")

    def _update_ldd_icon(self) -> None:
        h = self._active_lcd()
        if h is None:
            # Pre-activation default — show the highest-brightness icon
            # instead of a blank button.  The icon updates again as
            # soon as ``_activate_device`` runs and the handler reports
            # its restored level.
            from ...core.registry import BRIGHTNESS_STEPS
            default_level = BRIGHTNESS_STEPS[-1]
            pix = self._ldd_pixmaps.get(default_level)
            if pix and not pix.isNull():
                self.ldd_btn.setIcon(QIcon(pix))
                self.ldd_btn.setIconSize(QSize(52, 24))
                self.ldd_btn.setStyleSheet(Styles.ICON_BUTTON_HOVER)
            else:
                self.ldd_btn.setText(f"L{default_level}")
                self.ldd_btn.setStyleSheet(Styles.TEXT_BUTTON)
            return
        level = h.split_mode if h.ldd_is_split else h.brightness_level
        pix = self._ldd_pixmaps.get(level)
        if pix and not pix.isNull():
            self.ldd_btn.setIcon(QIcon(pix))
            self.ldd_btn.setIconSize(QSize(52, 24))
            self.ldd_btn.setStyleSheet(Styles.ICON_BUTTON_HOVER)
        else:
            label = f"S{level}" if h.ldd_is_split else f"L{level}"
            self.ldd_btn.setText(label)
            self.ldd_btn.setStyleSheet(Styles.TEXT_BUTTON)

    # ── Global Settings ─────────────────────────────────────────────

    def _on_temp_unit_changed(self, unit: str) -> None:
        log.debug("_on_temp_unit_changed: unit=%s", unit)
        temp_unit = 1 if unit == 'F' else 0

        # Persist via the unified command bus — CLI / API / GUI all
        # route through the same SetTempUnit Command.  Command takes
        # the literal "C" / "F" string, not the int code.
        # SetTempUnit publishes ``TempUnitChanged`` and
        # ``DeviceRenderObserver`` re-renders every connected LCD;
        # no manual loop here (DRY: one re-render path).
        self._app.dispatch(SetTempUnit(unit=unit))

        # GUI-only widget updates
        self.uc_system_info.set_temp_unit(temp_unit)
        self.uc_led_control.set_temp_unit(temp_unit)
        self.uc_preview.set_status(f"Temperature: °{unit}")

    def _on_hdd_toggle_changed(self, on: bool) -> None:
        log.debug("_on_hdd_toggle_changed: on=%s", on)
        result = self._app.dispatch(SetHddEnabled(enabled=on))
        self.uc_preview.set_status(result.message)

    def _on_format_pref_changed(self, kind: str, value: int) -> None:
        """User changed time / date / temp-unit format in the overlay editor.

        Dispatched as ONE Command with ``key=None`` — :class:`SetTimeFormat`
        or :class:`SetDateFormat` in global scope — which fans the value
        out to every existing :class:`DeviceSettings` and publishes one
        per-device ``*FormatChanged`` event so ``DeviceRenderObserver``
        re-renders each LCD.  Multi-LCD users expect ONE toggle that
        applies everywhere; ``key=None`` is the single dispatch site
        for that semantic (pass a key for a per-device override).

        ``kind ∈ {'time', 'date', 'temp_unit'}``.  ``temp_unit`` is
        owned by the About-panel's dedicated ``°C/°F`` toggle
        (``_on_temp_unit_changed`` → :class:`SetTempUnit`); ignored
        here to avoid double-dispatch.
        """
        from ...core.commands import SetDateFormat, SetTimeFormat
        log.info("_on_format_pref_changed: kind=%s value=%d", kind, value)
        if kind == 'time':
            # GUI int → "12h" / "24h" literal.  TIME_FORMATS dict
            # uses 0,2=24h and 1=12h.
            fmt = "12h" if value == 1 else "24h"
            self._app.dispatch(SetTimeFormat(fmt=fmt))
        elif kind == 'date':
            # DeviceSettings.date_format takes an ICU-ish pattern.
            # Map the GUI int codes (defined alongside DATE_FORMATS
            # in core/models.py) to the canonical pattern string.
            _DATE_INT_TO_PATTERN: dict[int, str] = {
                0: "yyyy/MM/dd",
                1: "yyyy/MM/dd",
                2: "dd/MM/yyyy",
                3: "MM/dd",
                4: "dd/MM",
            }
            self._app.dispatch(SetDateFormat(
                fmt=_DATE_INT_TO_PATTERN.get(value, "yyyy/MM/dd"),
            ))
        else:
            log.debug(
                "_on_format_pref_changed: kind=%r — no Command dispatch",
                kind,
            )

    def _on_refresh_changed(self, interval: int) -> None:
        log.info("_on_refresh_changed: interval=%ss", interval)
        result = self._app.dispatch(SetRefreshInterval(seconds=float(interval)))
        log.info("_on_refresh_changed: dispatch result ok=%s message=%r",
                 result.ok, result.message)
        self.uc_preview.set_status(result.message)

    def _on_gpu_changed(self, gpu_key: str) -> None:
        log.debug("_on_gpu_changed: gpu_key=%s", gpu_key)
        # SetGpuDevice persists the choice AND pushes it into the live
        # enumerator (the universal path — CLI/API/qtgui all do the same).
        result = self._app.dispatch(SetGpuDevice(gpu_key=gpu_key))
        self.uc_preview.set_status(result.message)

    def _set_language(self, lang: str) -> None:
        log.debug("_set_language: %s", lang)
        # SetLanguage propagates to every LCD overlay through the
        # Command's execute(), same path as CLI/API.
        self._app.dispatch(SetLanguage(language=lang))
        # GUI-only follow-ups: re-render backgrounds + refresh About +
        # LED-panel localized background.
        self._apply_settings_backgrounds()
        self.uc_about.sync_language()
        self.uc_led_control.set_language(lang)

    def _on_help_clicked(self) -> None:
        log.info("_on_help_clicked")
        import webbrowser
        webbrowser.open(
            'https://github.com/Lexonight1/thermalright-trcc-linux'
            '/blob/main/doc/GUIDE_TROUBLESHOOTING.md')

    def _on_capture_requested(self) -> None:
        log.info("_on_capture_requested")
        from .screen_capture import ScreenCaptureOverlay
        self._capture_overlay = ScreenCaptureOverlay()
        self._capture_overlay.captured.connect(self._on_screen_captured)
        self._capture_overlay.show()

    def _on_screen_captured(self, pixmap: Any) -> None:
        log.info("_on_screen_captured")
        self._capture_overlay = None
        h = self._active_lcd()
        if pixmap is None or not h:
            return
        from PySide6.QtGui import QPixmap as _QPixmap
        img = pixmap.toImage() if isinstance(pixmap, _QPixmap) else pixmap
        if img.isNull():
            return
        w, hw = h.lcd_size
        self.uc_image_cut.load_image(img, w, hw)
        self._show_cutter('image')

    def _on_eyedropper_requested(self) -> None:
        log.info("_on_eyedropper_requested")
        from .eyedropper import EyedropperOverlay
        self._eyedropper_overlay = EyedropperOverlay()
        self._eyedropper_overlay.color_picked.connect(self._eyedropper_pick)
        self._eyedropper_overlay.cancelled.connect(self._eyedropper_cancelled)
        self._eyedropper_overlay.show()

    def _eyedropper_cancelled(self) -> None:
        """Eyedropper closed without a pick — release the overlay reference."""
        self._eyedropper_overlay = None

    def _eyedropper_pick(self, r: int, g: int, b: int) -> None:
        self._eyedropper_overlay = None
        self.uc_theme_setting.color_panel._apply_color(r, g, b)

    # ── Carousel Config ─────────────────────────────────────────────

    def _load_carousel_config(self, theme_dir: Path) -> None:
        """Restore the legacy ``Theme.dc`` carousel config into the UI.

        next/'s slideshow lives in :class:`SlideshowService` and is
        driven by :class:`ConfigureSlideshow` / :class:`SetSlideshow`
        Commands.  The handler is responsible for restoring per-device
        slideshow state on connect; this method becomes a no-op stub
        until Phase 5's lcd_handler rewire surfaces ``set_slideshow``
        directly to the local-theme widget.
        """
        del theme_dir  # carousel restore moved into LCDHandler

    # ── Window Events ───────────────────────────────────────────────

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)

    def mousePressEvent(self, event: Any) -> None:
        if self._decorated or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position().toPoint()
        if pos.y() < 80 or (pos.x() < 180 and pos.y() < 95):
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft())
        event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_pos = None
        event.accept()

    def closeEvent(self, event: Any) -> None:
        if (not self._force_quit
                and self._tray.isSystemTrayAvailable()
                and self._tray.isVisible()
                and not (self._minimize_on_close and self._minimized_to_taskbar)):
            event.ignore()
            if self._minimize_on_close:
                self._minimized_to_taskbar = True
                self.showMinimized()
            else:
                self.hide()
            return
        self._minimized_to_taskbar = False

        self._tray.hide()
        self._screencast.cleanup()
        for h in list(self._handlers.values()):
            h.cleanup()
        self.uc_system_info.stop_updates()
        self.uc_info_module.stop_updates()
        self.uc_activity_sidebar.stop_updates()
        if self._ipc_server:
            self._ipc_server.shutdown()
        # ``app.close()`` detaches every device + stops hotplug.
        self._app.close()
        TRCCApp._instance = None
        event.accept()
        if (app := QApplication.instance()):
            app.quit()
