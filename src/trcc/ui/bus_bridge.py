"""EventBus → Qt signals bridge — one bridge, every Qt skin.

``EventBus`` calls are synchronous and arrive from arbitrary threads (the
sensor poller, a device worker, the daemon IPC server).  Qt widgets must
update on the main thread.  This bridge subscribes once per event type and
re-emits each event as a Qt signal; widgets connect with
``Qt.ConnectionType.QueuedConnection`` to marshal onto the main thread.

**One bridge for both skins.**  ``ui/gui`` and ``ui/qtgui`` each carried a
copy, and they had drifted: the qtgui copy was a strict *subset* — no
``video_*``, no ``screencast_*``, no ``system_*`` — so that skin could not
learn a video had started or that the machine was suspending, whatever its
widgets did.  A shared bridge means a new event is one row here and both
skins can see it.  Lives beside ``ui/qt_tray.py`` and ``ui/qapp.py``, the
established home for Qt code both skins share.
"""
from __future__ import annotations

import dataclasses
import logging
from functools import partial

from PySide6.QtCore import QEvent, QObject, Signal, SignalInstance
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from ..core.events import (
    AutostartChanged,
    BackgroundChanged,
    BrightnessChanged,
    DataInstalled,
    DeviceConnected,
    DeviceDisconnected,
    DeviceDiscovered,
    DiskDeviceChanged,
    ErrorOccurred,
    Event,
    EventBus,
    FitModeChanged,
    FrameSent,
    GameModeChanged,
    GameModeEngaged,
    GpuDeviceChanged,
    HddEnabledChanged,
    LanguageChanged,
    LedColorsChanged,
    LedSettingsChanged,
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
    OrientationChanged,
    OverlayChanged,
    RamLightingChanged,
    RefreshIntervalChanged,
    RgbFollowChanged,
    RgbFollowSent,
    RgbLightsChanged,
    ScreencastRegionChanged,
    ScreencastStarted,
    ScreencastStopped,
    SensorDashboardChanged,
    SensorsUpdated,
    SlideshowChanged,
    SplitModeChanged,
    SystemResumed,
    SystemSuspending,
    TempUnitChanged,
    ThemeDeleted,
    ThemeImported,
    ThemeLoaded,
    ThemeSaved,
    UpdateChecked,
    VideoAdvanced,
    VideoExportFinished,
    VideoExportProgress,
    VideoPauseChanged,
    VideoStarted,
    VideoStopped,
)
from ..core.logs import per_frame, recurring_warning

log = logging.getLogger(__name__)
#: Every bus event crosses this bridge, including the per-frame ones.
frame_log = per_frame(__name__)

class BusBridge(QObject):
    """Qt signals mirroring EventBus events.

    Construct once at window boot, attached to ``app.events``.  Widgets
    connect to the Qt signals — no widget should ``app.events.subscribe``
    directly; that is what keeps all Qt code in the UI layer.
    """

    # One signal per event type; the payload is the event dataclass itself.
    # ``object`` (rather than a concrete type) keeps this layer framework-neutral.
    device_discovered = Signal(object)         # DeviceDiscovered
    device_connected = Signal(object)          # DeviceConnected
    device_disconnected = Signal(object)       # DeviceDisconnected
    frame_sent = Signal(object)                # FrameSent
    orientation_changed = Signal(object)       # OrientationChanged
    brightness_changed = Signal(object)        # BrightnessChanged
    theme_loaded = Signal(object)              # ThemeLoaded
    led_colors_changed = Signal(object)        # LedColorsChanged
    rgb_follow_sent = Signal(object)           # RgbFollowSent
    update_checked = Signal(object)            # UpdateChecked
    sensors_updated = Signal(object)           # SensorsUpdated
    error_occurred = Signal(object)            # ErrorOccurred
    mask_applied = Signal(object)              # MaskApplied
    mask_position_changed = Signal(object)     # MaskPositionChanged
    mask_visibility_changed = Signal(object)   # MaskVisibilityChanged
    video_started = Signal(object)             # VideoStarted
    video_advanced = Signal(object)            # VideoAdvanced
    video_stopped = Signal(object)             # VideoStopped
    video_pause_changed = Signal(object)       # VideoPauseChanged
    video_export_progress = Signal(object)     # VideoExportProgress
    video_export_finished = Signal(object)     # VideoExportFinished
    screencast_started = Signal(object)        # ScreencastStarted
    screencast_stopped = Signal(object)        # ScreencastStopped
    slideshow_changed = Signal(object)         # SlideshowChanged
    game_mode_engaged = Signal(object)         # GameModeEngaged
    system_suspending = Signal(object)         # SystemSuspending
    system_resumed = Signal(object)            # SystemResumed
    data_installed = Signal(object)            # DataInstalled
    # The user's theme list changed -- saved, imported or deleted, by any UI.
    # One signal: a window re-lists rather than patching its grid per kind.
    themes_changed = Signal(object)
    # An app-wide setting changed (the control centre: temperature unit,
    # language, GPU, refresh interval, HDD, disk, dashboard, autostart); a
    # window re-reads what it shows.
    app_settings_changed = Signal(object)
    # A device's saved settings changed — by this UI, another one, or the
    # App.  One signal for all of them, so a window re-reads what the App now
    # holds instead of keeping a slot per setting; every one carries ``key``.
    # Not ``LedColorsChanged``: every render publishes it.
    settings_changed = Signal(object)
    # Frames flow again after the window was hidden: show the current one
    # now, since a still panel may not send another for a while.
    frames_resumed = Signal()

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        self._frames = _FrameForwarder(self.frame_sent, FrameSent.__name__)
        self._frames_on = False
        self._previews: tuple[QWidget, ...] = ()
        log.info("BusBridge.__init__: wiring EventBus → Qt signals")
        self._wire()

    def follow_previews(self, *previews: QWidget) -> None:
        """Take frames only while one of *previews* is on screen.

        A frame is a picture the App encodes for this window and the window
        decodes -- work for nobody when no preview is showing: the window
        hidden in the tray or minimised, or another page open (settings,
        system info).  Measured 2026-10-10 with the window hidden: the App at
        26-27% and the window at 7-10%; with no window, the App at 8%.  qtgui
        already paused its preview timers this way per panel
        (``TicksWhileShown``); this does it for the frames themselves, in both
        skins.  Every other event keeps flowing, so nothing is stale on return.

        Qt sends a hide event to every visible child of a widget it hides, so
        watching the previews themselves also catches the window hiding.
        Minimised windows still count as visible to Qt; their state change is
        watched on each preview's window.
        """
        log.info("BusBridge.follow_previews: %s",
                 [type(w).__name__ for w in previews])
        self._previews = previews
        for widget in {*previews, *(w.window() for w in previews)}:
            widget.installEventFilter(self)
        self._sync_frames()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Show, hide and minimise of a followed preview or its window."""
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide,
                            QEvent.Type.WindowStateChange):
            log.debug("BusBridge.eventFilter: %s %s", type(watched).__name__,
                      event.type().name)
            self._sync_frames()
        return False

    def _sync_frames(self) -> None:
        """Frames on while any followed preview is on screen."""
        on = any(w.isVisible() and not w.window().isMinimized()
                 for w in self._previews)
        log.debug("BusBridge._sync_frames: %s -> %s", self._frames_on, on)
        self._set_frames(on)

    def _set_frames(self, on: bool) -> None:
        """Subscribe or drop the frame forwarder; announce frames resuming."""
        if on == self._frames_on:
            return
        log.info("BusBridge: frames %s", "on" if on else "off (window hidden)")
        self._frames_on = on
        if on:
            self._bus.subscribe(FrameSent, self._frames)
            self.frames_resumed.emit()
        else:
            self._bus.unsubscribe(FrameSent, self._frames)

    def _wire(self) -> None:
        pairs: tuple[tuple[type[Event], SignalInstance], ...] = (
            (DeviceDiscovered, self.device_discovered),
            (DeviceConnected, self.device_connected),
            (DeviceDisconnected, self.device_disconnected),
            (OrientationChanged, self.orientation_changed),
            (BrightnessChanged, self.brightness_changed),
            (ThemeLoaded, self.theme_loaded),
            (LedColorsChanged, self.led_colors_changed),
            (RgbFollowSent, self.rgb_follow_sent),
            (UpdateChecked, self.update_checked),
            (SensorsUpdated, self.sensors_updated),
            (ErrorOccurred, self.error_occurred),
            (MaskApplied, self.mask_applied),
            (MaskPositionChanged, self.mask_position_changed),
            (MaskVisibilityChanged, self.mask_visibility_changed),
            (VideoStarted, self.video_started),
            (VideoAdvanced, self.video_advanced),
            (VideoStopped, self.video_stopped),
            (VideoPauseChanged, self.video_pause_changed),
            (VideoExportProgress, self.video_export_progress),
            (VideoExportFinished, self.video_export_finished),
            (ScreencastStarted, self.screencast_started),
            (ScreencastStopped, self.screencast_stopped),
            (SlideshowChanged, self.slideshow_changed),
            (GameModeEngaged, self.game_mode_engaged),
            (SystemSuspending, self.system_suspending),
            (SystemResumed, self.system_resumed),
            (DataInstalled, self.data_installed),
            (ThemeSaved, self.themes_changed),
            (ThemeImported, self.themes_changed),
            (ThemeDeleted, self.themes_changed),
            (TempUnitChanged, self.app_settings_changed),
            (LanguageChanged, self.app_settings_changed),
            (GpuDeviceChanged, self.app_settings_changed),
            (RefreshIntervalChanged, self.app_settings_changed),
            (HddEnabledChanged, self.app_settings_changed),
            (RgbFollowChanged, self.app_settings_changed),
            (RamLightingChanged, self.app_settings_changed),
            (RgbLightsChanged, self.app_settings_changed),
            (DiskDeviceChanged, self.app_settings_changed),
            (SensorDashboardChanged, self.app_settings_changed),
            (AutostartChanged, self.app_settings_changed),
            (BrightnessChanged, self.settings_changed),
            (OrientationChanged, self.settings_changed),
            (SplitModeChanged, self.settings_changed),
            (GameModeChanged, self.settings_changed),
            (FitModeChanged, self.settings_changed),
            (OverlayChanged, self.settings_changed),
            (BackgroundChanged, self.settings_changed),
            (MaskApplied, self.settings_changed),
            (MaskPositionChanged, self.settings_changed),
            (MaskVisibilityChanged, self.settings_changed),
            (ScreencastRegionChanged, self.settings_changed),
            (LedSettingsChanged, self.settings_changed),
        )
        subscribed = tuple(
            (event_type, _SignalForwarder(signal, event_type.__name__))
            for event_type, signal in pairs)
        for event_type, forwarder in subscribed:
            self._bus.subscribe(event_type, forwarder)
        # Frames are on until a followed window says otherwise, so a bridge
        # with no window (a test, a headless tool) still sees every frame.
        self._set_frames(True)
        subscribed = (*subscribed, (FrameSent, self._frames))
        # A freed bridge takes its subscriptions with it.  Left on the bus,
        # every forwarder raises "Signal source has been deleted" and the bus
        # logs a traceback per dead bridge per publish -- measured: one dead
        # bridge left 40 handlers.  The slot is a module function over the bus
        # and the pairs, never ``self``, so it cannot keep the bridge alive.
        self.destroyed.connect(partial(_unsubscribe, self._bus, subscribed))
        log.info("BusBridge._wire: subscribed %d event types", len(pairs) + 1)


def _unsubscribe(bus: EventBus,
                 subscribed: tuple[tuple[type[Event], _SignalForwarder], ...],
                 *_: object) -> None:
    """Drop a destroyed bridge's forwarders from the App's bus."""
    log.info("BusBridge destroyed: unsubscribing %d forwarders", len(subscribed))
    for event_type, forwarder in subscribed:
        bus.unsubscribe(event_type, forwarder)


class _SignalForwarder:
    """Callable that re-emits one event type on one Qt signal.

    A named object rather than a default-arg lambda: each subscriber has to
    remember *its own* signal, and a lambda that closes over the loop variable
    would forward every event to the last one.  This also gives the EventBus a
    subscriber with a readable ``repr`` when a handler misbehaves.
    """

    __slots__ = ("_event_name", "_signal")

    def __init__(self, signal: SignalInstance, event_name: str) -> None:
        log.debug("__init__: signal=%s event_name=%s", signal, event_name)
        self._signal = signal
        self._event_name = event_name

    def __call__(self, event: Event) -> None:
        frame_log.debug("forwarding %s to its Qt signal", self._event_name)
        self._signal.emit(event)

    def __repr__(self) -> str:
        return f"<BusBridge forwarder for {self._event_name}>"


class _FrameForwarder(_SignalForwarder):
    """``FrameSent`` with its picture as a surface, wherever the App runs.

    Across the daemon socket the live surface cannot travel; the App sends the
    same frame as JPEG bytes (``FrameSent.image``) instead.  Decoding it HERE,
    once, gives every window one event shape -- they read ``surface`` and
    never ask the App to draw the frame again.  It runs on the thread that
    received the event, not the UI thread.
    """

    __slots__ = ()

    def __call__(self, event: Event) -> None:
        if (isinstance(event, FrameSent) and event.surface is None
                and event.pixels):
            # Shared memory (``trcc.frame_share``): the raw pixels, no decode.
            # ``copy`` so the image owns them past this call.
            width, height, stride = event.shared_shape
            picture = QImage(event.pixels, width, height, stride,
                             QImage.Format.Format_ARGB32).copy()
            frame_log.debug("BusBridge: FrameSent %s shared %dx%d", event.key,
                            width, height)
            event = dataclasses.replace(event, surface=picture)
        elif (isinstance(event, FrameSent) and event.surface is None
                and event.image):
            picture = QImage.fromData(event.image)
            if picture.isNull():
                recurring_warning(log, "BusBridge: FrameSent for %s carried %d "
                                  "byte(s) that would not decode", event.key,
                                  len(event.image))
            else:
                frame_log.debug("BusBridge: FrameSent %s decoded %dx%d",
                                event.key, picture.width(), picture.height())
                event = dataclasses.replace(event, surface=picture)
        super().__call__(event)
