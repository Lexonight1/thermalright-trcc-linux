"""EventBus ↔ Qt signals adapter.

EventBus calls are synchronous and may arrive from any thread (sensor
poller, device worker, etc.).  Qt widgets must update on the main
thread.  This bridge subscribes a single handler per event type and
re-emits each event as a Qt signal, which widgets connect to with
`Qt.ConnectionType.QueuedConnection` to marshal onto the main thread.
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
    OrientationChanged,
    SensorsUpdated,
    ThemeLoaded,
)


class BusBridge(QObject):
    """Qt signals mirroring EventBus events.

    Attach to an App's events at construction; widgets connect to the
    Qt signals instead of subscribing to the bus directly.  Keeps all
    Qt code in the GUI layer.
    """

    # One Qt signal per event type, carrying the event's payload fields.
    # `object` (no specific type) keeps this layer framework-neutral.
    device_discovered = Signal(object)   # DeviceDiscovered
    device_connected = Signal(object)    # DeviceConnected
    device_disconnected = Signal(object) # DeviceDisconnected
    frame_sent = Signal(object)
    orientation_changed = Signal(object)
    brightness_changed = Signal(object)
    theme_loaded = Signal(object)
    led_colors_changed = Signal(object)
    sensors_updated = Signal(object)
    error_occurred = Signal(object)

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
        )
        for event_type, signal in pairs:
            # Each subscriber captures its own signal via default argument
            self._bus.subscribe(event_type, lambda e, sig=signal: sig.emit(e))
