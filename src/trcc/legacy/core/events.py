"""EventBus — async notification surface for the universal TRCC command layer.

Commands emit events (frame ready, metrics updated, device connect/disconnect,
data ready, update available). Each UI bridges events to its own plumbing:
GUI → Qt signals, API → WebSocket messages, CLI → stdout streams.

Framework-neutral: no Qt, no asyncio. Thread-safe via a single lock so
background-thread publishes (video tick, sensor poll, USB hotplug) deliver
safely to subscribers.

Event names are strings by convention — keep them flat and stable.

Lifecycle / streaming::

    'frame'                  → (device_idx: int, frame: Frame)
    'progress'               → (device_idx: int, percent, current, total)
    'metrics'                → dict   (HardwareMetrics.__dict__ or adjacent)
    'device.connected'       → DeviceInfo
    'device.disconnected'    → DeviceInfo
    'data.ready'             → None   (theme/web/mask archives extracted)
    'update.available'       → UpdateResult

LCD state changes (multi-UI sync — published by `LCDCommands` after
each successful mutation)::

    'lcd.brightness'         → (lcd: int, percent: int)
    'lcd.rotation'           → (lcd: int, degrees: int)
    'lcd.split_mode'         → (lcd: int, mode: int)
    'lcd.fit_mode'           → (lcd: int, mode: str)
    'lcd.theme'              → (lcd: int, name: str, kind: 'local'|'cloud')
    'lcd.mask'               → (lcd: int, name: str)
    'lcd.overlay_enabled'    → (lcd: int, enabled: bool)
    'lcd.overlay'            → (lcd: int, config: dict)

LED state changes (published by `LEDCommands`)::

    'led.color'              → (led: int, r: int, g: int, b: int, zone: int | None)
    'led.mode'               → (led: int, mode, zone: int | None)
    'led.brightness'         → (led: int, percent: int, zone: int | None)
    'led.toggled'            → (led: int, on: bool, zone: int | None)
    'led.zone_sync'          → (led: int, enabled: bool, interval_s: int | None)
    'led.clock'              → (led: int, is_24h: bool)
    'led.sensor'             → (led: int, source: str)

App-level state changes (published by `ControlCenterCommands`)::

    'control_center.autostart'  → enabled: bool
    'control_center.temp_unit'  → unit: 'C' | 'F'
    'control_center.language'   → lang: str
    'control_center.hdd'        → enabled: bool
    'control_center.refresh'    → seconds: int
    'control_center.gpu'        → gpu_key: str
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from threading import Lock
from typing import Any, Final

log = logging.getLogger(__name__)


class Topic:
    """Canonical event topic strings — every publisher and subscriber uses these.

    Using a class with class attributes (rather than an Enum) keeps the
    wire format trivially serializable: each topic IS the string the
    daemon sees on the socket. Cosmic Python's "favor explicit
    registration through a simple dict" applied at the topic layer.
    """

    # Device lifecycle
    DEVICE_LIST: Final = 'device.list'                  # payload: tuple of devices
    DEVICE_CONNECTED: Final = 'device.connected'        # payload: device
    DEVICE_DISCONNECTED: Final = 'device.disconnected'  # payload: device

    # Streaming. Tick / streaming hot path uses ``device_path`` (str) as the
    # identifier — wire-friendly and unambiguous across LCD + LED.
    FRAME: Final = 'frame'                              # payload: (device_path, Frame|tick_result)
    PROGRESS: Final = 'progress'                        # payload: (device_path, pct, cur, tot)
    METRICS: Final = 'metrics'                          # payload: HardwareMetrics

    # Bootstrap progress
    BOOTSTRAP_PROGRESS: Final = 'bootstrap.progress'    # payload: str message
    DATA_READY: Final = 'data.ready'                    # payload: None

    # System power events (Trcc publishes from Platform.subscribe_power callback)
    SYSTEM_SUSPENDED: Final = 'system.suspended'        # payload: None — fires before device cleanup
    SYSTEM_RESUMED: Final = 'system.resumed'            # payload: None — fires after rediscover

    # LCD state changes (LCDCommands publishes after each successful mutation)
    LCD_BRIGHTNESS: Final = 'lcd.brightness'
    LCD_ROTATION: Final = 'lcd.rotation'
    LCD_SPLIT_MODE: Final = 'lcd.split_mode'
    LCD_FIT_MODE: Final = 'lcd.fit_mode'
    LCD_THEME: Final = 'lcd.theme'
    LCD_MASK: Final = 'lcd.mask'
    LCD_OVERLAY_ENABLED: Final = 'lcd.overlay_enabled'
    LCD_OVERLAY: Final = 'lcd.overlay'

    # LED state changes (LEDCommands publishes)
    LED_COLOR: Final = 'led.color'
    LED_MODE: Final = 'led.mode'
    LED_BRIGHTNESS: Final = 'led.brightness'
    LED_TOGGLED: Final = 'led.toggled'
    LED_ZONE_SYNC: Final = 'led.zone_sync'
    LED_CLOCK: Final = 'led.clock'
    LED_SENSOR: Final = 'led.sensor'

    # App-level (ControlCenterCommands publishes)
    CONTROL_CENTER_AUTOSTART: Final = 'control_center.autostart'
    CONTROL_CENTER_TEMP_UNIT: Final = 'control_center.temp_unit'
    CONTROL_CENTER_LANGUAGE: Final = 'control_center.language'
    CONTROL_CENTER_HDD: Final = 'control_center.hdd'
    CONTROL_CENTER_REFRESH: Final = 'control_center.refresh'
    CONTROL_CENTER_GPU: Final = 'control_center.gpu'


class EventBus:
    """Minimal publish/subscribe bus.

    Callbacks run on the thread that calls `publish()`. UI adapters that
    need thread-hop (e.g., GUI → main thread) must do it themselves.
    A failing callback logs + continues; one broken subscriber never
    blocks the rest.
    """

    def __init__(self) -> None:
        self._subs: dict[int, tuple[str, Callable[..., Any]]] = {}
        self._next_id: int = 0
        self._lock = Lock()

    def subscribe(self, event: str, callback: Callable[..., Any]) -> int:
        """Register a callback for `event`. Returns a subscription id."""
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subs[sub_id] = (event, callback)
        log.debug("subscribe: id=%d event=%r", sub_id, event)
        return sub_id

    def unsubscribe(self, sub_id: int) -> None:
        """Remove a subscription. No-op if already gone."""
        with self._lock:
            removed = self._subs.pop(sub_id, None)
        if removed is not None:
            log.debug("unsubscribe: id=%d event=%r", sub_id, removed[0])

    def publish(self, event: str, *payload: Any) -> None:
        """Notify all subscribers of `event`. Payload is passed positionally."""
        with self._lock:
            targets = [
                cb for _sid, (ev, cb) in self._subs.items() if ev == event
            ]
        if not targets:
            return
        log.debug("publish: event=%r subscribers=%d", event, len(targets))
        for cb in targets:
            try:
                cb(*payload)
            except Exception:
                log.exception("EventBus subscriber for %r raised", event)

    def clear(self) -> None:
        """Drop every subscription — used during cleanup/teardown."""
        with self._lock:
            self._subs.clear()
            self._next_id = 0
        log.debug("clear: all subscriptions removed")
