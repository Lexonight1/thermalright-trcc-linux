"""TRCCApp — thin shell main window (AppObserver).

Pure GUI adapter. Knows nothing about builders, detectors, or OS adapters.
Receives Device objects injected via on_app_event() from TrccApp (core).

One LCDHandler or LEDHandler per connected device, keyed by USB path.
Panel stack shows the currently selected device; all devices tick in background.
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

from trcc.legacy.conf import Settings

from ...adapters.infra.dc_writer import read_carousel
from ...core.models import LCD_DEFAULT_BUTTON, DeviceInfo
from ...core.ports import Platform
from .assets import Assets
from .base import create_image_button, set_background_pixmap
from .base_handler import BaseHandler
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
    pass

log = logging.getLogger(__name__)


# =============================================================================
# Screencast Handler
# =============================================================================

class ScreencastHandler:
    """Mediator for screencast (screen capture → LCD).

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

    def set_lcd_size(self, w: int, h: int) -> None:
        self._lcd_w = w
        self._lcd_h = h

    def set_audio_enabled(self, enabled: bool) -> None:
        """Enable/disable microphone audio visualization on screencast."""
        self._audio_enabled = enabled
        if not enabled and self._audio is not None:
            self._audio.stop()
            self._audio = None

    def toggle(self, enabled: bool) -> None:
        self._active = enabled
        if enabled:
            from .screen_capture import is_wayland
            if is_wayland() and self._pipewire_cast is None:
                self._try_start_pipewire()
            if self._audio_enabled:
                self._start_audio()
            self._timer.start(150)
        else:
            self._timer.stop()
            self._stop_pipewire()
            if self._audio is not None:
                self._audio.stop()
                self._audio = None

    def stop(self) -> None:
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

    def _start_audio(self) -> None:
        from trcc.legacy.services.audio import AudioCapture
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
    """Main TRCC window — pure GUI adapter, AppObserver.

    Receives Device objects injected by TrccApp via on_app_event().
    Knows nothing about builders, detectors, or OS internals.

    One handler per device keyed by USB path:
      _handlers: dict[str, BaseHandler]  # keyed by USB path

    Panel stack shows the active device; all devices tick in background.
    """

    # Thread-safe bridge: core metrics loop → Qt main thread
    _metrics_signal: Signal = Signal(object)
    _frame_signal: Signal = Signal(object)          # {'path': str, 'image': Any}
    _device_added_signal: Signal = Signal(object)   # Device
    _device_removed_signal: Signal = Signal(object) # Device

    _instance: TRCCApp | None = None

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
        platform: Platform,
        decorated: bool = False,
    ) -> None:
        super().__init__()
        from trcc.__version__ import __version__
        log.info("TRCC v%s starting", __version__)

        self._platform = platform
        self._minimize_on_close = platform.minimize_on_close()

        # Universal command facade + every shared service. ``_boot.trcc()``
        # returns the cached process-wide Trcc (built by gui/launch with a
        # windowed-QApp QtRenderer); all subscriptions, system service,
        # device lists, and command facades come off it.
        from trcc.legacy._boot import trcc as _boot_trcc

        from ...services.system import SystemService
        self._trcc = _boot_trcc()
        sys_svc = self._trcc._system_svc
        if sys_svc is None:
            raise RuntimeError(
                "TRCCApp: SystemService missing on Trcc — "
                "gui/launch must pass it via _boot.trcc().")
        self._system_svc: SystemService = sys_svc

        # Apply saved GPU selection to sensor enumerator
        _settings = self._trcc.settings
        if _settings.gpu_device:
            self._system_svc.enumerator.set_preferred_gpu(_settings.gpu_device)

        self._decorated = decorated
        self._drag_pos = None
        self._force_quit = False
        self._minimized_to_taskbar = False
        self._data_dir = _settings.user_data_dir

        self.setWindowTitle("TRCC-Linux - Thermalright LCD Control Center")
        self.setFixedSize(Sizes.WINDOW_W, Sizes.WINDOW_H)
        if not decorated:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)

        # Per-device handlers keyed by USB path
        self._handlers: dict[str, BaseHandler] = {}
        self._active_path = ''       # path of device currently shown in panel stack

        self._handshake_pending = False
        self._cut_mode = 'background'
        self._mask_upload_filename = ''
        self._pixmap_refs: list = []

        # IPC server (set by composition root after construction)
        from ...ipc import IPCServer
        self._ipc_server: IPCServer | None = None

        # Build UI
        self._apply_dark_theme()
        self._setup_ui()

        # Screencast handler
        self._screencast = ScreencastHandler(self, self._on_screencast_frame)

        # Connect widget signals
        self._connect_view_signals()

        # Thread-safe signal bridges
        self._metrics_signal.connect(self._on_metrics_main_thread)
        self._frame_signal.connect(self._on_frame_main_thread)
        self._device_added_signal.connect(self._on_device_added_main_thread)
        self._device_removed_signal.connect(self._on_device_removed_main_thread)

        # EventBus subscriptions — publish callbacks bridge core threads to
        # the Qt main thread via the signals above. Replaces the legacy
        # AppObserver.on_app_event(event, data) dispatch (Phase 9).
        from trcc.legacy.core.events import Topic
        bus = self._trcc.events
        bus.subscribe(Topic.DEVICE_LIST, self._on_bus_device_list)
        bus.subscribe(Topic.DEVICE_CONNECTED, self._on_bus_device_connected)
        bus.subscribe(Topic.DEVICE_DISCONNECTED, self._on_bus_device_disconnected)
        bus.subscribe(Topic.FRAME, self._on_bus_frame)
        bus.subscribe(Topic.METRICS, self._on_bus_metrics)
        # System suspend — stop the screen capture pipeline before the
        # device list goes empty.  Trcc handles device cleanup itself;
        # this hook is just for UI-level resources.
        bus.subscribe(Topic.SYSTEM_SUSPENDED, self._on_bus_system_suspended)

        # Handshake signal
        self._handshake_done = Signal(object, object)  # type: ignore[assignment]
        # Use a QObject notifier for handshake (thread → main thread)
        from PySide6.QtCore import QObject
        from PySide6.QtCore import Signal as _Signal
        class _HandshakeNotifier(QObject):
            done = _Signal(object, object)
        self._hs_notifier = _HandshakeNotifier(self)
        self._hs_notifier.done.connect(self._on_handshake_done)

        # Restore temp unit
        saved_unit = self._trcc.settings.temp_unit
        self.uc_system_info.set_temp_unit(saved_unit)
        self.uc_led_control.set_temp_unit(saved_unit)
        if saved_unit == 1:
            self.uc_about._set_temp('F')

        # Autostart
        autostart_state = ensure_autostart(self._platform)
        self.uc_about._autostart = autostart_state
        self.uc_about.startup_btn.setChecked(autostart_state)

        # System tray
        self._setup_systray()
        # Sleep handling lives on Trcc now — wired by Platform.subscribe_power()
        # in Trcc.__init__.  Nothing to do GUI-side.

    # ── EventBus bridges — invoked on the publisher thread, re-emit on
    #    a Qt signal so handlers run on the GUI thread.  Named slots,
    #    not lambdas (per feedback_no_lambdas).

    def _on_bus_device_list(self, devices: Any) -> None:
        self._device_added_signal.emit(('changed', devices))

    def _on_bus_device_connected(self, device: Any) -> None:
        self._device_added_signal.emit(('connected', device))

    def _on_bus_device_disconnected(self, device: Any) -> None:
        self._device_removed_signal.emit(device)

    def _on_bus_frame(self, path: Any, image: Any) -> None:
        self._frame_signal.emit({'path': path, 'image': image})

    def _on_bus_metrics(self, metrics: Any) -> None:
        self._metrics_signal.emit(metrics)

    def _on_bus_system_suspended(self) -> None:
        self._screencast.stop()

    # ── Device event handlers (main thread) ─────────────────────────

    def _on_device_added_main_thread(self, payload: Any) -> None:
        match payload:
            case ('changed', devices):
                log.debug("_on_device_added_main_thread: kind=changed")
                self._rebuild_all_handlers(devices)
            case (kind, device):
                log.debug("_on_device_added_main_thread: kind=%s", kind)
                self._add_handler(device)

    def _on_device_removed_main_thread(self, device: Any) -> None:
        path = device.device_info.path if device.device_info else ''
        log.debug("_on_device_removed_main_thread: path=%s", path)
        self._remove_handler(path)

    def _rebuild_all_handlers(self, devices: list) -> None:
        """Replace all handlers with new device list from scan()."""
        for handler in list(self._handlers.values()):
            handler.cleanup()
        self._handlers.clear()
        self._active_path = ''

        for device in devices:
            self._add_handler(device)

        self._refresh_sidebar()

        # Restore last active device, fall back to first LCD
        last_idx = Settings.get_last_device()
        target = next(
            (p for p, h in self._handlers.items()
             if h.device_info and h.device_info.device_index == last_idx),
            None,
        )
        if target:
            log.debug("_rebuild_all_handlers: restored last device index=%d path=%s", last_idx, target)
        else:
            target = next(
                (p for p, h in self._handlers.items() if isinstance(h, LCDHandler)),
                next(iter(self._handlers), None),
            )
            log.debug("_rebuild_all_handlers: fallback to first LCD/device: %s", target)
        if target:
            self._activate_device(target)
        else:
            log.warning("_rebuild_all_handlers: no device to activate")

        # Restore saved themes on inactive LCD devices so they keep playing
        # video in the background even when not selected in the GUI sidebar.
        for path, handler in self._handlers.items():
            if path != target and isinstance(handler, LCDHandler):
                log.info("_rebuild_all_handlers: restoring inactive LCD %s", path)
                handler.restore_inactive_state()

    def _add_handler(self, device: Any) -> None:
        """Create handler for one new device."""
        info = device.device_info
        if info is None:
            log.warning("Device has no device_info, skipping")
            return
        path = info.path

        added = False
        if device.is_led and path not in self._handlers:
            handler = LEDHandler(
                device, self.uc_led_control, self._on_temp_unit_changed)
            self._handlers[path] = handler
            log.info("LED handler added: %s", path)
            added = True
        elif device.is_lcd and path not in self._handlers:
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
            # Compute LCD index: count LCD handlers created before this one.
            # Matches Trcc.discover() ordering which iterates in detection order.
            lcd_idx = sum(
                1 for h in self._handlers.values()
                if isinstance(h, LCDHandler)
            )
            # Route handler through Trcc.lcd (10C.2): every write goes via
            # the command bus so the dispatch path is identical whether the
            # handler is talking to a real Trcc or a TrccProxy.  The Phase 9
            # boot unification put GUI + CLI + API on the same Trcc registry,
            # so the silent no-op bug class that prompted the c7e14b69 revert
            # (handlers + commands looking at different registries) is gone.
            lcd_handler = LCDHandler(
                device, widgets, self._make_timer, self._data_dir,
                is_visible_fn=self.is_app_visible,
                app=self._trcc, lcd_idx=lcd_idx)
            self._handlers[path] = lcd_handler
            log.info("LCD handler added: %s", path)
            added = True
        if not added and path not in self._handlers:
            log.warning("_add_handler: unhandled device type %s path=%s — skipped",
                        type(device).__name__, path)

        # Button image already resolved by DeviceService._enrich_device()
        # at detection time. HID LCD (async handshake) resolved later in
        # _on_handshake_done → DeviceInfo.enrich_from_handshake → _sync_device_identity.
        self._refresh_sidebar()

    def _remove_handler(self, path: str) -> None:
        """Remove and cleanup one device handler."""
        handler = self._handlers.pop(path, None)
        if handler is None:
            return
        handler.cleanup()
        log.info("%s handler removed: %s", type(handler).__name__, path)

        if self._active_path == path:
            self._active_path = ''
            remaining = list(self._handlers)
            if remaining:
                self._activate_device(remaining[0])

        self._refresh_sidebar()

    def _refresh_sidebar(self) -> None:
        """Update UCDevice with current device list."""
        import dataclasses
        devices: list[dict] = []
        for handler in self._handlers.values():
            if (info := handler.device_info):
                devices.append(dataclasses.asdict(info))
        self.uc_device.update_devices(devices)

    def _activate_device(self, path: str) -> None:
        """Switch panel stack to show the given device."""
        if path == self._active_path:
            return
        log.info("_activate_device: %s", path)
        # Deactivate previous device before switching. LCDs go into a
        # soft-pause so their video keeps playing on the device while
        # another LCD owns the GUI; LEDs/others use the full stop.
        if self._active_path:
            prev = self._handlers.get(self._active_path)
            if isinstance(prev, LCDHandler):
                prev.set_inactive()
            elif prev is not None:
                prev.deactivate()
        self._active_path = path
        handler = self._handlers.get(path)
        if handler is None:
            log.warning("_activate_device: no handler for path=%s (known: %s)",
                        path, list(self._handlers.keys()))
            return

        # Persist last active device so we restore it on next launch
        if handler.device_info:
            Settings.save_last_device(handler.device_info.device_index)

        if isinstance(handler, LCDHandler):
            if handler.display.connected:
                if (info := handler.display.device_info):
                    w, h = info.resolution
                    if (w, h) == (0, 0):
                        log.debug("_activate_device: LCD %s resolution (0,0) — starting handshake", path)
                        self._start_handshake(info)
                    elif not handler.device_key:
                        log.debug("_activate_device: LCD %s first-time config %dx%d", path, w, h)
                        handler.apply_device_config(info, w, h)
                        self._update_ldd_icon()
                    else:
                        log.debug("_activate_device: LCD %s reactivate %dx%d", path, w, h)
                        handler.reactivate(w, h)
        elif isinstance(handler, LEDHandler):
            info = handler.device_info
            if info and not handler.active:
                log.debug("_activate_device: LED %s — showing", path)
                handler.show(info)

        self._show_view(handler.view_name)

    # ── Metrics (main thread only) ───────────────────────────────────

    def _on_metrics_main_thread(self, metrics: Any) -> None:
        """Dispatch the unified metrics broadcast to every visible GUI panel.

        One publisher (PollingMetricsLoop), one record (HardwareMetrics
        with typed fields + readings dict + _populated set), every panel
        and the active device handler observe the same data.
        """
        if self.uc_info_module.isVisible():
            self.uc_info_module.update_from_metrics(metrics)
        if self.uc_system_info.isVisible():
            self.uc_system_info.update_from_metrics(metrics)
        if self.is_app_visible() and self.uc_activity_sidebar.isVisible():
            self.uc_activity_sidebar.update_from_metrics(metrics)

        # Notify active handler for metrics-driven updates
        handler = self._handlers.get(self._active_path)
        if handler is not None:
            handler.update_metrics(metrics)

    def _on_frame_main_thread(self, payload: Any) -> None:
        """Receive a rendered frame from the background tick loop and push to preview.

        tick() renders + sends to device. This mirrors the result to the
        preview widget on the main thread — no re-render needed.
        LCD: QImage for preview widget. LED: colors dict for LED panel.
        """
        path: str = payload['path']
        image: Any = payload['image']
        if path != self._active_path:
            return
        handler = self._handlers.get(path)
        if handler is not None:
            handler.handle_frame(image)

    # ── Sleep monitor ───────────────────────────────────────────────
    # Power events are owned by Platform.subscribe_power() now and
    # forwarded into Trcc._on_suspend / _on_resume.  GUI subscribes to
    # the EventBus for `device.list` repaints when resume rediscovery
    # republishes the device snapshot — no QDBus listener needed here.

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

        # FormCZTV container
        self.form_container = QWidget(central)
        self.form_container.setGeometry(*Layout.FORM_CONTAINER)
        pix = set_background_pixmap(self.form_container, Assets.FORM_CZTV_BG,
            fallback_style=f"background-color: {Colors.WINDOW_BG};")
        if pix:
            self._pixmap_refs.append(pix)

        # Preview
        self.uc_preview = UCPreview(
            self._trcc.settings.width, self._trcc.settings.height, self.form_container)
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

        from ...adapters.infra.data_repository import DataManager
        from ...adapters.infra.theme_cloud import CloudThemeDownloader
        def _download_theme(theme_id: str, resolution: str, cache_dir: str) -> str | None:
            return CloudThemeDownloader(resolution=resolution, cache_dir=cache_dir).download_theme(theme_id)
        def _extract_theme(archive: str, dest: str) -> None:
            DataManager.extract_7z(archive, dest)
            DataManager._unwrap_nested_dir(dest)
        self.uc_theme_web = UCThemeWeb(download_fn=_download_theme, extract_fn=_extract_theme)
        self._set_panel_bg(self.uc_theme_web, Assets.THEME_WEB_BG)
        self.panel_stack.addWidget(self.uc_theme_web)

        self.uc_theme_setting = UCThemeSetting()
        self.panel_stack.addWidget(self.uc_theme_setting)

        self.uc_theme_mask = UCThemeMask()
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

        # About panel
        gpu_list = self._system_svc.enumerator.get_gpu_list()
        self.uc_about = UCAbout(
            parent=central, platform=self._platform,
            gpu_list=gpu_list, trcc=self._trcc)
        self.uc_about.setGeometry(*Layout.FORM_CONTAINER)
        self.uc_about.setVisible(False)

        # System info dashboard
        from ...adapters.system.config import SysInfoConfig
        self.uc_system_info = UCSystemInfo(
            self._system_svc.enumerator,
            sysinfo_config=SysInfoConfig(),
            parent=central)
        self.uc_system_info.setGeometry(*Layout.SYSINFO_PANEL)
        self.uc_system_info.setVisible(False)

        # LED panel — hardware fns injected
        self.uc_led_control = UCLedControl(central)
        self.uc_led_control.setGeometry(*Layout.FORM_CONTAINER)
        self.uc_led_control.setVisible(False)
        self.uc_led_control.set_hardware_fns(self._platform.get_memory_info, self._platform.get_disk_info)

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

        from ...core.models import BRIGHTNESS_STEPS
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
        lang = self._trcc.settings.lang
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
        self._show_view('sysinfo')

    def _on_about_clicked(self) -> None:
        self._show_view('about')

    # ── Download status slots ───────────────────────────────────────

    def _on_theme_download_started(self, theme_id: str) -> None:
        self.uc_preview.set_status(f"Downloading: {theme_id}...")

    def _on_theme_download_finished(self, theme_id: str, ok: bool) -> None:
        verb = 'Downloaded' if ok else 'Download failed'
        self.uc_preview.set_status(f"{verb}: {theme_id}")

    def _on_mask_download_started(self, mask_id: str) -> None:
        self.uc_preview.set_status(f"Downloading: {mask_id}...")

    def _on_mask_download_finished(self, mask_id: str, ok: bool) -> None:
        verb = 'Downloaded' if ok else 'Failed'
        self.uc_preview.set_status(f"{verb}: {mask_id}")

    def _on_drag_end_noop(self) -> None:
        """No-op slot — drag-end emits the signal but we don't act on it here."""

    def _on_element_added(self, _payload: Any) -> None:
        """Hide the activity sidebar once an element has been added to the theme."""
        self.uc_activity_sidebar.setVisible(False)

    def _show_view(self, view: str) -> None:
        active = getattr(self, '_active_path', None)
        log.debug("view=%s active_path=%s", view, active)
        if view not in ('form', 'led'):
            log.debug("clearing active_path (was %s)", active)
            self._active_path = ''  # allow re-selecting same device on return
        self.form_container.setVisible(view == 'form')
        self.uc_about.setVisible(view == 'about')
        self.uc_system_info.setVisible(view == 'sysinfo')
        self.uc_led_control.setVisible(view == 'led')
        self.uc_activity_sidebar.setVisible(False)

        # uc_system_info is a Topic.METRICS observer (see _on_metrics_main_thread);
        # visibility alone decides whether the panel renders.  No timer to start.

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
        return self._handlers.get(self._active_path)

    def _active_lcd(self) -> LCDHandler | None:
        h = self._handlers.get(self._active_path)
        return h if isinstance(h, LCDHandler) else None

    def _active_led(self) -> LEDHandler | None:
        h = self._handlers.get(self._active_path)
        return h if isinstance(h, LEDHandler) else None

    # ── Handshake (LCD resolution discovery) ────────────────────────

    def _start_handshake(self, device: DeviceInfo) -> None:
        log.debug("_start_handshake: path=%s pending=%s", device.path, self._handshake_pending)
        if self._handshake_pending:
            return
        self._handshake_pending = True
        self.uc_preview.set_status("Connecting to device...")

        import threading
        def worker() -> None:
            data = self._trcc.handshake(device)
            self._hs_notifier.done.emit(device, data)
        threading.Thread(target=worker, daemon=True).start()

    def _on_handshake_done(self, device: DeviceInfo, result: Any) -> None:
        """Wire a HandshakeResult into the device + sidebar.

        Model enrichment goes through ``DeviceInfo.enrich_from_handshake``
        (single chokepoint). This handler then propagates the resolved
        sidebar identity to ``uc_device`` and persists it via ``Settings``.
        """
        log.debug("_on_handshake_done: path=%s result=%s", device.path, result)
        self._handshake_pending = False
        if result is None:
            self.uc_preview.set_status("Handshake failed — replug device")
            return

        device.enrich_from_handshake(result)

        if device.resolution == (0, 0):
            self.uc_preview.set_status("Handshake failed — no resolution")
            return
        log.info("Handshake OK: %s -> %s (FBL=%s, PM=%s, SUB=%s, model=%r)",
                 device.path, device.resolution, device.fbl_code,
                 device.pm_byte, device.sub_byte,
                 getattr(result, 'model_name', ''))

        # Sync sidebar + persisted Settings from the enriched device.
        self._sync_device_identity(device)

        handler = self._handlers.get(device.path)
        if isinstance(handler, LCDHandler):
            w, h = device.resolution
            log.debug("_on_handshake_done: handler found device_key=%r", handler.device_key)
            if not handler.device_key:
                handler.apply_device_config(device, w, h)
                self._update_ldd_icon()
                if Settings.show_info_module():
                    self.uc_info_module.setVisible(True)
            else:
                log.debug("_on_handshake_done: skipping apply_device_config — already initialized")

    def _sync_device_identity(self, device: DeviceInfo) -> None:
        """Propagate the enriched ``device.button_image`` to sidebar + Settings.

        Model enrichment already happened in
        ``DeviceInfo.enrich_from_handshake``; this method only handles the
        view + persistence side (sidebar dict, sidebar button refresh,
        Settings save).
        """
        btn_img = device.button_image
        if not btn_img or btn_img == LCD_DEFAULT_BUTTON:
            log.debug("no resolved button for %s — keeping default", device.path)
            return
        product = btn_img.replace('A1', '', 1).replace('_', ' ')
        log.info("%s -> %s (%s)", device.path, btn_img, product)
        for dev in self.uc_device.devices:
            if dev.get('path') == device.path:
                dev['button_image'] = btn_img
                dev['product'] = product
                dev['name'] = f"Thermalright {product}"
                self.uc_device.update_device_button(dev)
                log.debug("_sync_device_identity: updated sidebar button for %s", device.path)
                break
        active_key = Settings.device_config_key(device.device_index, device.vid, device.pid)
        Settings.save_device_settings(
            active_key,
            resolved_button_image=btn_img, resolved_product=product)

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
        log.debug("_on_cloud_theme_clicked: %s", getattr(theme_info, 'name', theme_info))
        h = self._active_lcd()
        if h:
            h.select_cloud_theme(theme_info)

    def _on_mask_clicked(self, mask_info: Any) -> None:
        log.debug("_on_mask_clicked: %s", getattr(mask_info, 'name', mask_info))
        h = self._active_lcd()
        if h:
            h.apply_mask(mask_info)

    def _on_local_delegate(self, cmd: Any, info: Any, data: Any) -> None:
        if cmd == UCThemeLocal.CMD_SLIDESHOW:
            h = self._active_lcd()
            if h:
                h.on_slideshow_delegate()

    def _on_delete_theme(self, theme_info: Any) -> None:
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "Delete Theme", f"Delete theme '{theme_info.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.uc_theme_local.delete_theme(theme_info)
            h = self._active_lcd()
            if (h and h.display.current_theme_path
                    and str(h.display.current_theme_path) == theme_info.path):
                h.display.current_image = None
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
                    h.display.set_mask_visible(info)
                    h._render_and_send()
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
                    h.on_overlay_changed(info if isinstance(info, dict) else {})

    def _on_preview_delegate(self, cmd: Any, info: Any, data: Any) -> None:
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
        log.debug("_on_background_toggle: enabled=%s", enabled)
        h = self._active_lcd()
        if not h:
            return
        if enabled:
            self._screencast.toggle(False)
        h.on_background_toggle(enabled)

    def _on_screencast_toggle(self, enabled: bool) -> None:
        log.debug("_on_screencast_toggle: enabled=%s", enabled)
        h = self._active_lcd()
        if not h:
            return
        if enabled:
            h.deactivate()
            h.display.stop()
            h.is_background_active = False
            w, hw = h.display.lcd_size
            self._screencast.set_lcd_size(w, hw)
        self._screencast.toggle(enabled)
        self.uc_preview.set_status(f"Screencast: {'On' if enabled else 'Off'}")

    def _on_video_display_toggle(self, enabled: bool) -> None:
        log.debug("_on_video_display_toggle: enabled=%s", enabled)
        h = self._active_lcd()
        if not h:
            return
        if not enabled:
            if h.display.has_frames:
                h._animation_timer.stop()
                h.display.stop()
                self.uc_preview.set_playing(False)
                self.uc_preview.show_video_controls(False)
            if (last_path := h.display.current_theme_path):
                h.select_theme_from_path(Path(last_path))

    def _on_screencast_frame(self, image: Any) -> None:
        h = self._active_lcd()
        if h:
            h.on_screencast_frame(image)

    # ── File Dialogs ────────────────────────────────────────────────

    def _on_load_video_clicked(self) -> None:
        h = self._active_lcd()
        svc = h.display._display_svc if h else None
        web_dir = str(svc.web_dir) if svc and svc.web_dir else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", web_dir,
            "Video Files (*.mp4 *.avi *.mov *.gif);;All Files (*)")
        h = self._active_lcd()
        if path and h:
            w, hw = h.display.lcd_size
            self.uc_video_cut.set_resolution(w, hw)
            self.uc_video_cut.load_video(path)
            self._show_cutter('video')

    def _on_media_player_load_clicked(self) -> None:
        h = self._active_lcd()
        svc = h.display._display_svc if h else None
        web_dir = str(svc.web_dir) if svc and svc.web_dir else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Video", web_dir,
            "Video Files (*.mp4 *.avi *.mkv *.mov *.gif);;All Files (*)")
        h = self._active_lcd()
        if not path or not h:
            return
        self._screencast.toggle(False)
        h.is_background_active = False
        h._animation_timer.stop()
        h.display.stop()
        h.display.enable_overlay(False)
        result = h.display.load(path)
        if not result.get("success"):
            self.uc_preview.set_status(f"Error: {result.get('error', 'Failed to load video')}")
            return
        h.display.play()
        h._animation_timer.start(h.display.interval)
        self.uc_preview.set_playing(True)
        self.uc_preview.show_video_controls(True)
        self.uc_preview.set_status(f"Playing: {Path(path).name}")

    def _on_load_image_clicked(self) -> None:
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
                w, hw = h.display.lcd_size
                self.uc_image_cut.load_image(img, w, hw)
                self._show_cutter('image')

    def _on_mask_upload_clicked(self) -> None:
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
                w, hw = h.display.lcd_size
                self.uc_image_cut.load_image(img, w, hw)
                self._show_cutter('image')

    def _on_save_clicked(self) -> None:
        name = self.theme_name_input.text().strip()
        if not name:
            self.uc_preview.set_status("Enter a theme name first")
            return
        h = self._active_lcd()
        if h:
            h.save_theme(name)

    def _on_export_clicked(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Theme", "",
            "Theme files (*.tr);;JSON (*.json);;All Files (*)")
        h = self._active_lcd()
        if path and h:
            h.export_config(Path(path))

    def _on_import_clicked(self) -> None:
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
        self._hide_cutters()
        h = self._active_lcd()
        if result is None or not h:
            self.uc_preview.set_status("Image crop cancelled")
            self._cut_mode = 'background'
            return
        if self._cut_mode == 'mask':
            self._save_and_apply_custom_mask(result)
        else:
            h.stop_video()
            h.display.set_background(result)
            h._render_and_send()
            self.uc_preview.set_status("Image loaded")
        self._cut_mode = 'background'

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
        w, hw = h.display.lcd_size
        user_dir = Path(self._trcc.settings._path_resolver.user_masks_dir(w, hw))
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
        self._hide_cutters()
        h = self._active_lcd()
        if zt_path and h:
            h.display.load(Path(zt_path))
            h.display.play()
            h._animation_timer.start(h.display.interval)
            self.uc_preview.set_playing(True)
            self.uc_preview.show_video_controls(True)
            self.uc_preview.set_status("Video loaded")
        else:
            self.uc_preview.set_status("Video cut cancelled")

    # ── Activity Sidebar / Overlay ───────────────────────────────────

    def _on_overlay_add_requested(self) -> None:
        self.uc_activity_sidebar.setVisible(True)
        self.uc_activity_sidebar.raise_()

    def _on_sensor_element_add(self, config: Any) -> None:
        self.uc_theme_setting.overlay_grid.add_element(config)
        self.uc_activity_sidebar.setVisible(False)

    def _on_overlay_toggle(self, enabled: bool) -> None:
        log.debug("_on_overlay_toggle: enabled=%s", enabled)
        h = self._active_lcd()
        if h:
            h.display.enable_overlay(enabled)

        active_key = Settings.device_config_key(
            *self._active_device_index_vid_pid())
        if active_key:
            cfg = Settings.get_device_config(active_key)
            overlay = cfg.get('overlay', {})
            overlay['enabled'] = enabled
            Settings.save_device_setting(active_key, 'overlay', overlay)

    def _active_device_index_vid_pid(self) -> tuple[int, int, int]:
        h = self._active_lcd()
        if h and h.display and h.display.device_info:
            info = h.display.device_info
            return info.device_index, info.vid, info.pid
        return 0, 0, 0

    def _on_element_flash(self, index: int, config: dict) -> None:
        h = self._active_lcd()
        if h:
            h.flash_element(index)

    # ── Drag / Nudge ────────────────────────────────────────────────

    def _on_drag_start(self, lcd_x: int, lcd_y: int) -> None:
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
        cfg = self.uc_theme_setting.overlay_grid.get_selected_config()
        h = self._active_lcd()
        if cfg is None or not h:
            return
        w, hw = h.display.lcd_size
        new_x = max(0, min(self._drag_elem_x + (lcd_x - self._drag_origin_x), w))
        new_y = max(0, min(self._drag_elem_y + (lcd_y - self._drag_origin_y), hw))
        self.uc_theme_setting.color_panel.set_position(new_x, new_y)
        self.uc_theme_setting._on_position_changed(new_x, new_y)

    def _on_nudge(self, dx: int, dy: int) -> None:
        cfg = self.uc_theme_setting.overlay_grid.get_selected_config()
        h = self._active_lcd()
        if cfg is None or not h:
            return
        w, hw = h.display.lcd_size
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
        h = self._active_lcd()
        if not h:
            return
        if h.ldd_is_split:
            mode = (h.split_mode % 3) + 1
            h.set_split_mode(mode)
            self._update_ldd_icon()
            self.uc_preview.set_status(f"Split mode: {mode}")
        else:
            from ...core.models import BRIGHTNESS_STEPS
            steps = BRIGHTNESS_STEPS
            cur = h.brightness_level
            nxt = steps[(steps.index(cur) + 1) % len(steps)] if cur in steps else steps[0]
            h.set_brightness(nxt)
            self._update_ldd_icon()
            self.uc_preview.set_status(f"Brightness: {nxt}%")

    def _update_ldd_icon(self) -> None:
        h = self._active_lcd()
        if not h:
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

        # Persist via Trcc — same code path as CLI `trcc temp-unit` and
        # API `PUT /app/temp-unit`. Side effects below stay GUI-only.
        self._trcc.control_center.set_temp_unit(unit)

        # GUI-only: re-render each LCD handler's preview
        for handler in self._handlers.values():
            if isinstance(handler, LCDHandler):
                handler._render_and_send()

        # GUI-only widget updates
        self.uc_system_info.set_temp_unit(temp_unit)
        self.uc_led_control.set_temp_unit(temp_unit)
        self.uc_preview.set_status(f"Temperature: °{unit}")

    def _on_hdd_toggle_changed(self, on: bool) -> None:
        log.debug("_on_hdd_toggle_changed: on=%s", on)
        result = self._trcc.control_center.set_hdd_enabled(on)
        # Same side effect as the legacy path: poll loop re-reads the flag
        self.uc_preview.set_status(result.format())

    def _on_refresh_changed(self, interval: int) -> None:
        log.debug("_on_refresh_changed: interval=%s", interval)
        result = self._trcc.control_center.set_metrics_refresh(interval)
        # Poll loop re-reads settings.refresy_interval each tick
        self.uc_preview.set_status(result.format())

    def _on_gpu_changed(self, gpu_key: str) -> None:
        log.debug("_on_gpu_changed: gpu_key=%s", gpu_key)
        # Single call — ``Trcc.set_gpu_device`` now propagates
        # ``enumerator.set_preferred_gpu`` itself.
        result = self._trcc.control_center.set_gpu_device(gpu_key)
        self.uc_preview.set_status(result.format())

    def _set_language(self, lang: str) -> None:
        log.debug("_set_language: %s", lang)
        # ``Trcc.set_language`` propagates the new lang to every LCD's
        # overlay (issue #141) — no GUI-side manual loop needed.
        self._trcc.control_center.set_language(lang)
        # GUI-only follow-ups: re-render backgrounds + refresh About +
        # LED-panel localized background.  These touch widgets, not
        # device state, so they stay here.
        self._apply_settings_backgrounds()
        self.uc_about.sync_language()
        self.uc_led_control.apply_localized_background()

    def _on_help_clicked(self) -> None:
        import webbrowser
        webbrowser.open(
            'https://github.com/Lexonight1/thermalright-trcc-linux'
            '/blob/main/doc/GUIDE_TROUBLESHOOTING.md')

    def _on_capture_requested(self) -> None:
        from .screen_capture import ScreenCaptureOverlay
        self._capture_overlay = ScreenCaptureOverlay()
        self._capture_overlay.captured.connect(self._on_screen_captured)
        self._capture_overlay.show()

    def _on_screen_captured(self, pixmap: Any) -> None:
        self._capture_overlay = None
        h = self._active_lcd()
        if pixmap is None or not h:
            return
        from PySide6.QtGui import QPixmap as _QPixmap
        img = pixmap.toImage() if isinstance(pixmap, _QPixmap) else pixmap
        if img.isNull():
            return
        w, hw = h.display.lcd_size
        self.uc_image_cut.load_image(img, w, hw)
        self._show_cutter('image')

    def _on_eyedropper_requested(self) -> None:
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
        config = read_carousel(str(theme_dir / 'Theme.dc'))
        if config is None:
            return
        all_themes = self.uc_theme_local._all_themes
        slideshow_names = [
            all_themes[idx].name
            for idx in config.theme_indices
            if 0 <= idx < len(all_themes)
        ]
        self.uc_theme_local._lunbo_array = slideshow_names
        self.uc_theme_local._slideshow = config.enabled
        self.uc_theme_local._slideshow_interval = config.interval_seconds
        self.uc_theme_local.timer_input.setText(str(config.interval_seconds))
        px = (self.uc_theme_local._lunbo_on if config.enabled
              else self.uc_theme_local._lunbo_off)
        if not px.isNull():
            self.uc_theme_local.slideshow_btn.setIcon(QIcon(px))
            self.uc_theme_local.slideshow_btn.setIconSize(
                self.uc_theme_local.slideshow_btn.size())
        self.uc_theme_local._apply_decorations()

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
        self._trcc.cleanup()
        TRCCApp._instance = None
        event.accept()
        if (app := QApplication.instance()):
            app.quit()
