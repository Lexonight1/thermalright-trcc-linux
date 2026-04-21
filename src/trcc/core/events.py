"""EventBus + Event hierarchy.

Devices and services publish events; UIs subscribe.  The bus is
synchronous by default — adapters bridge to their own async mechanism
(Qt signals for GUI, SSE/WebSocket for API).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, DefaultDict, List, Tuple, Type

log = logging.getLogger(__name__)


# =========================================================================
# Event hierarchy
# =========================================================================


@dataclass(frozen=True, slots=True)
class Event:
    """Base event."""


@dataclass(frozen=True, slots=True)
class DeviceDiscovered(Event):
    key: str
    product_name: str


@dataclass(frozen=True, slots=True)
class DeviceConnected(Event):
    key: str
    resolution: Tuple[int, int]


@dataclass(frozen=True, slots=True)
class DeviceDisconnected(Event):
    key: str


@dataclass(frozen=True, slots=True)
class FrameSent(Event):
    key: str
    bytes_sent: int


@dataclass(frozen=True, slots=True)
class OrientationChanged(Event):
    key: str
    degrees: int


@dataclass(frozen=True, slots=True)
class BrightnessChanged(Event):
    key: str
    percent: int


@dataclass(frozen=True, slots=True)
class ThemeLoaded(Event):
    key: str
    theme_name: str


@dataclass(frozen=True, slots=True)
class LedColorsChanged(Event):
    key: str
    color_count: int


@dataclass(frozen=True, slots=True)
class SensorsUpdated(Event):
    reading_count: int


@dataclass(frozen=True, slots=True)
class ErrorOccurred(Event):
    message: str
    kind: str = "general"
    key: str = ""


# =========================================================================
# Bus
# =========================================================================


Handler = Callable[[Event], None]


class EventBus:
    """In-process event bus.

    Handlers are called synchronously on publish.  Adapters that need
    thread-safe delivery (e.g. GUI) subscribe a bridge handler that
    re-emits on their own queue or signal.
    """

    def __init__(self) -> None:
        self._handlers: DefaultDict[Type[Event], List[Handler]] = defaultdict(list)

    def subscribe(self, event_type: Type[Event], handler: Handler) -> None:
        """Register *handler* for all events of *event_type*."""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: Type[Event], handler: Handler) -> None:
        """Remove a previously-registered handler.  No-op if not found."""
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass

    def publish(self, event: Event) -> None:
        """Fan out *event* to every handler subscribed to its type.

        Handler exceptions are logged but do not propagate — one bad
        subscriber shouldn't break event delivery for the rest.
        """
        for handler in list(self._handlers[type(event)]):
            try:
                handler(event)
            except Exception:
                log.exception("EventBus handler failed for %s", type(event).__name__)

    def clear(self) -> None:
        """Drop all subscriptions (used in tests)."""
        self._handlers.clear()
