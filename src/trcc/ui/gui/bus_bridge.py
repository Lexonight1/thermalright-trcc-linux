"""EventBus → Qt signals bridge for the legacy-style GUI.

next/ EventBus calls are synchronous and arrive from arbitrary threads
(sensor poller, device worker, daemon IPC server).  Qt widgets must
update on the main thread.  This bridge subscribes once per Event type
and re-emits each event as a Qt signal; widgets connect to the Qt
signals with ``Qt.ConnectionType.QueuedConnection`` to marshal onto
the main thread.

Layered + DRY: this is the same pattern :mod:`next.ui.qtgui.bus_bridge`
uses, with one extra ``system_suspending`` / ``system_resumed`` pair
(legacy gui subscribed to ``Topic.SYSTEM_SUSPENDED`` and the next port
needs the same hook).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ...core.events import (
    BrightnessChanged,
    DeviceConnected,
    DeviceDisconnected,
    DeviceDiscovered,
    ErrorOccurred,
    EventBus,
    FrameSent,
    LedColorsChanged,
    MaskApplied,
    MaskPositionChanged,
    MaskVisibilityChanged,
    OrientationChanged,
    SensorsUpdated,
    SystemResumed,
    SystemSuspending,
    ThemeLoaded,
)


class BusBridge(QObject):
    """Qt signals mirroring EventBus events.

    Construct once at MainWindow boot, attach to ``app.events``.  Widgets
    connect to the Qt signals — no widget should ``app.events.subscribe``
    directly, that keeps all Qt code in the GUI layer.
    """

    # One signal per event type, payload is the event dataclass itself.
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
    system_suspending = Signal(object)         # SystemSuspending
    system_resumed = Signal(object)            # SystemResumed

    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self._bus = bus
        self._wire()

    def _wire(self) -> None:
        pairs = (
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
            (SystemSuspending, self.system_suspending),
            (SystemResumed, self.system_resumed),
        )
        for event_type, signal in pairs:
            # Default-arg capture: the lambda binds ``sig`` at definition
            # time, not call time, so each subscriber forwards to the
            # right signal.
            self._bus.subscribe(
                event_type,
                lambda e, sig=signal: sig.emit(e),
            )
