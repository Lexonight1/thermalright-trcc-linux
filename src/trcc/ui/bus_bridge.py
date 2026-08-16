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

import logging

from PySide6.QtCore import QObject, Signal, SignalInstance

from ..core.events import (
    BrightnessChanged,
    DataInstalled,
    DeviceConnected,
    DeviceDisconnected,
    DeviceDiscovered,
    ErrorOccurred,
    Event,
    EventBus,
    FrameSent,
    LedColorsChanged,
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
    OrientationChanged,
    ScreencastStarted,
    ScreencastStopped,
    SensorsUpdated,
    SystemResumed,
    SystemSuspending,
    ThemeLoaded,
    VideoStarted,
    VideoStopped,
)

log = logging.getLogger(__name__)


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
    sensors_updated = Signal(object)           # SensorsUpdated
    error_occurred = Signal(object)            # ErrorOccurred
    mask_applied = Signal(object)              # MaskApplied
    mask_position_changed = Signal(object)     # MaskPositionChanged
    mask_visibility_changed = Signal(object)   # MaskVisibilityChanged
    video_started = Signal(object)             # VideoStarted
    video_stopped = Signal(object)             # VideoStopped
    screencast_started = Signal(object)        # ScreencastStarted
    screencast_stopped = Signal(object)        # ScreencastStopped
    system_suspending = Signal(object)         # SystemSuspending
    system_resumed = Signal(object)            # SystemResumed
    data_installed = Signal(object)            # DataInstalled

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        log.info("BusBridge.__init__: wiring EventBus → Qt signals")
        self._wire()

    def _wire(self) -> None:
        pairs: tuple[tuple[type[Event], SignalInstance], ...] = (
            (DeviceDiscovered, self.device_discovered),
            (DeviceConnected, self.device_connected),
            (DeviceDisconnected, self.device_disconnected),
            (FrameSent, self.frame_sent),
            (OrientationChanged, self.orientation_changed),
            (BrightnessChanged, self.brightness_changed),
            (ThemeLoaded, self.theme_loaded),
            (LedColorsChanged, self.led_colors_changed),
            (SensorsUpdated, self.sensors_updated),
            (ErrorOccurred, self.error_occurred),
            (MaskApplied, self.mask_applied),
            (MaskPositionChanged, self.mask_position_changed),
            (MaskVisibilityChanged, self.mask_visibility_changed),
            (VideoStarted, self.video_started),
            (VideoStopped, self.video_stopped),
            (ScreencastStarted, self.screencast_started),
            (ScreencastStopped, self.screencast_stopped),
            (SystemSuspending, self.system_suspending),
            (SystemResumed, self.system_resumed),
            (DataInstalled, self.data_installed),
        )
        for event_type, signal in pairs:
            self._bus.subscribe(
                event_type, _SignalForwarder(signal, event_type.__name__),
            )
        log.info("BusBridge._wire: subscribed %d event types", len(pairs))


class _SignalForwarder:
    """Callable that re-emits one event type on one Qt signal.

    A named object rather than a default-arg lambda: each subscriber has to
    remember *its own* signal, and a lambda that closes over the loop variable
    would forward every event to the last one.  This also gives the EventBus a
    subscriber with a readable ``repr`` when a handler misbehaves.
    """

    __slots__ = ("_event_name", "_signal")

    def __init__(self, signal: SignalInstance, event_name: str) -> None:
        self._signal = signal
        self._event_name = event_name

    def __call__(self, event: Event) -> None:
        self._signal.emit(event)

    def __repr__(self) -> str:
        return f"<BusBridge forwarder for {self._event_name}>"
