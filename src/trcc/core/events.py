"""EventBus + Event hierarchy.

Devices and services publish events; UIs subscribe.  The bus is
synchronous by default — adapters bridge to their own async mechanism
(Qt signals for GUI, SSE/WebSocket for API).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .models import HardwareMetrics

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
    resolution: tuple[int, int]


@dataclass(frozen=True, slots=True)
class DeviceDisconnected(Event):
    key: str


@dataclass(frozen=True, slots=True)
class FrameSent(Event):
    key: str
    bytes_sent: int
    # The rendered pre-encode surface (Renderer-port ``Any``), carried so
    # observers — the GUI preview above all — display the EXACT frame that
    # went to the wire instead of re-rendering the whole pipeline a second
    # time (legacy's ``bus.publish('frame', …, Frame(native=img))`` shape).
    # In-process only: None on the pure-bytes send paths (SendFrame /
    # SendColor / SendImage / keepalive) and would be None across IPC,
    # where a remote client must fall back to a re-render.
    surface: Any = None
    # LED twin of ``surface``: an LED render has no image, it has per-LED
    # colors.  Carried on the SAME event so LED uses the SAME preview path as
    # LCD (one render → FrameSent → handle_frame → preview), not a parallel
    # one.  Empty for LCD / pure-bytes sends.
    display_colors: list = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class OrientationChanged(Event):
    key: str
    degrees: int


@dataclass(frozen=True, slots=True)
class BrightnessChanged(Event):
    key: str
    percent: int


@dataclass(frozen=True, slots=True)
class FitModeChanged(Event):
    key: str
    mode: str   # FitMode value: "width" | "height" | "stretch"


@dataclass(frozen=True, slots=True)
class OverlayChanged(Event):
    key: str
    enabled: bool
    # When set, GUI subscribers temporarily highlight the named element
    # for ``flash_duration_ms`` milliseconds.  Other UIs ignore.
    flash_element_id: str = ""
    flash_duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class SplitModeChanged(Event):
    key: str
    mode: int


@dataclass(frozen=True, slots=True)
class MaskApplied(Event):
    key: str
    path: str


@dataclass(frozen=True, slots=True)
class MaskPositionChanged(Event):
    key: str
    position: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class MaskVisibilityChanged(Event):
    key: str
    visible: bool


@dataclass(frozen=True, slots=True)
class ThemeSaved(Event):
    key: str
    theme_name: str
    path: str


@dataclass(frozen=True, slots=True)
class ThemeExported(Event):
    theme_name: str
    archive_path: str


@dataclass(frozen=True, slots=True)
class ThemeImported(Event):
    theme_name: str
    path: str


@dataclass(frozen=True, slots=True)
class VideoStarted(Event):
    """Published by ``PlayVideo`` after a playback is loaded.

    ``interval_ms`` is the per-frame timer interval the GUI animation
    timer should use — derived from ``playback.fps`` server-side so UIs
    don't have to query :class:`MediaService` themselves (DIP: handler
    reads the event payload, not the service).
    """
    key: str
    path: str
    frame_count: int
    interval_ms: int


@dataclass(frozen=True, slots=True)
class VideoStopped(Event):
    key: str


@dataclass(frozen=True, slots=True)
class ScreencastStarted(Event):
    """Published by ``StartScreencast`` after a screen-capture session is
    requested for a device.

    Mirrors :class:`VideoStarted` semantics: the GUI ``ScreencastHandler``
    subscribes through ``BusBridge`` and only starts its capture timer
    when this event fires — so daemon/CLI/API callers can drive
    screencast through the same Command bus as the GUI.

    ``x, y, w, h`` is the screen region (pixels).  ``audio`` toggles the
    microphone spectrum visualiser overlay on each captured frame.
    """
    key: str
    x: int
    y: int
    w: int
    h: int
    audio: bool


@dataclass(frozen=True, slots=True)
class ScreencastStopped(Event):
    """Published by ``StopScreencast`` after a device's capture session
    is torn down.  ``ScreencastHandler`` stops its timer in response."""
    key: str


@dataclass(frozen=True, slots=True)
class BackgroundChanged(Event):
    """Published when the device's static background override changes.

    Distinct from ``VideoStarted`` (which fires for video backgrounds —
    handler observer starts the per-frame timer there).  This fires
    only for image backgrounds set via ``SetBackground``; the
    ``DeviceRenderObserver`` schedules a single ``RenderAndSend`` to
    push the new bg to the device.
    """
    key: str
    path: str


@dataclass(frozen=True, slots=True)
class ThemeLoaded(Event):
    key: str
    theme_name: str


@dataclass(frozen=True, slots=True)
class LedColorsChanged(Event):
    key: str
    color_count: int


@dataclass(frozen=True, slots=True)
class LedSettingsChanged(Event):
    """A user (or remote client) mutated an LED device's settings — mode,
    colour, selected metric page/zone, carousel, etc.  Distinct from
    ``LedColorsChanged`` (which a *render* also emits) so the render observer
    can re-render on it without looping.  Published by
    ``_publish_led_settings_changed``; never by ``RenderLed``."""
    key: str


@dataclass(frozen=True, slots=True)
class SensorsUpdated(Event):
    """Periodic sensor broadcast — payload IS the personalized dict.

    ``readings``: ``{sensor_id: value}`` already processed through
    :func:`trcc.services.metrics_personalize.personalize_readings` —
    temps are in ``temp_unit`` units already (°C → °F applied at
    publish time), ``disk:*`` keys are absent when the user has HDD
    disabled.  Subscribers read the dict as-is; no further
    conversion needed at consumer-side.

    READ-ONLY CONTRACT: the same dict instance is delivered to every
    subscriber on the synchronous bus, so subscribers must NOT mutate
    it — copy first if you need to transform.  It's kept a plain
    ``dict`` (not a frozen ``MappingProxyType``) on purpose: this
    broadcast is JSON-serialisable for a future metrics-over-IPC
    stream, which a proxy view would break.

    ``temp_unit``: ``"C"`` or ``"F"``.  Tells subscribers which unit
    the temp values are in so they can render the unit SUFFIX in
    format strings (``"33°C"`` vs ``"33°F"``) without reading
    settings — the broadcast self-describes its unit semantics.

    ``reading_count``: kept for log size-hints + size-only consumers;
    redundant with ``len(readings)`` but cheap.

    ``metrics``: the typed :class:`HardwareMetrics` snapshot the OS
    dispatcher produced this tick, personalized to the same prefs.  The
    app observes it directly — see the field comment below.

    All fields have defaults so the event can be constructed without a
    full payload (tests / staged rollout); the live publisher supplies
    them all.
    """
    reading_count: int = 0
    readings: dict[str, float] = field(default_factory=dict)
    temp_unit: str = "C"
    # Typed snapshot the OS dispatcher produces this tick, personalized to
    # the same prefs as ``readings``.  The app OBSERVES this — it never
    # re-polls; subscribers read ``metrics.cpu_temp`` directly.  ALWAYS a
    # real object (empty default only for the construct-without-args path).
    metrics: HardwareMetrics = field(default_factory=HardwareMetrics)


@dataclass(frozen=True, slots=True)
class ErrorOccurred(Event):
    message: str
    kind: str = "general"
    key: str = ""


# ── Control-center settings changes (no device key — app-global) ─────


@dataclass(frozen=True, slots=True)
class TempUnitChanged(Event):
    unit: str   # "C" or "F"


@dataclass(frozen=True, slots=True)
class HddEnabledChanged(Event):
    """User toggled HDD-metrics inclusion in the broadcast.

    Published by ``SetHddEnabled.execute``.  Subscribers (today only
    ``MetricsLoop``) wake their sleep so the next personalize step
    drops / re-includes ``disk:*`` keys immediately instead of
    after a full refresh interval.
    """
    enabled: bool


@dataclass(frozen=True, slots=True)
class TimeFormatChanged(Event):
    """User changed the 12h/24h clock format.

    Per-device because :class:`DeviceSettings.time_format` is the
    persisted source of truth — ``DisplayService.compute_clock``
    reads it per render.  ``DeviceRenderObserver`` subscribes so
    the LCD re-renders on the next tick.
    """
    key: str
    fmt: str   # "12h" or "24h"


@dataclass(frozen=True, slots=True)
class DateFormatChanged(Event):
    """User changed the date pattern (e.g. yyyy/MM/dd → dd.MM.yyyy).

    Per-device for symmetry with :class:`TimeFormatChanged`.
    """
    key: str
    fmt: str   # e.g. "yyyy/MM/dd"


@dataclass(frozen=True, slots=True)
class LanguageChanged(Event):
    language: str


@dataclass(frozen=True, slots=True)
class GpuDeviceChanged(Event):
    gpu_key: str | None


@dataclass(frozen=True, slots=True)
class RefreshIntervalChanged(Event):
    seconds: float


# ── Hotplug / power transitions ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeviceAttached(Event):
    """A registry-known device just appeared on the bus.  Pre-handshake."""
    key: str
    vid: int
    pid: int


@dataclass(frozen=True, slots=True)
class DeviceDetached(Event):
    """A registry-known device just left the bus.  Distinct from
    :class:`DeviceDisconnected` (which is dispatched by explicit
    Command).  UIs that hold a per-device handle should release on this.
    """
    key: str
    vid: int
    pid: int


@dataclass(frozen=True, slots=True)
class SystemSuspending(Event):
    """The OS is about to suspend.  Power-aware adapters should
    quiesce I/O — devices behave erratically over suspend cycles."""


@dataclass(frozen=True, slots=True)
class SystemResumed(Event):
    """The OS just resumed from suspend.  Re-discover + reconnect."""


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
        self._handlers: defaultdict[type[Event], list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type[Event], handler: Handler) -> None:
        """Register *handler* for all events of *event_type*."""
        log.info("subscribe: event=%s handler=%s",
                 event_type.__name__, getattr(handler, "__qualname__", handler))
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type[Event], handler: Handler) -> None:
        """Remove a previously-registered handler.  No-op if not found."""
        log.info("unsubscribe: event=%s handler=%s",
                 event_type.__name__, getattr(handler, "__qualname__", handler))
        try:
            self._handlers[event_type].remove(handler)
        except ValueError:
            pass

    def publish(self, event: Event) -> None:
        """Fan out *event* to every handler subscribed to its type.

        Handler exceptions are logged but do not propagate — one bad
        subscriber shouldn't break event delivery for the rest.
        """
        log.debug("publish: event=%s", type(event).__name__)
        for handler in list(self._handlers[type(event)]):
            try:
                handler(event)
            except Exception:
                log.exception("EventBus handler failed for %s", type(event).__name__)

    def clear(self) -> None:
        """Drop all subscriptions (used in tests)."""
        log.info("clear: called")
        self._handlers.clear()
