"""MetricsLoop — periodic sensor broadcast.

Legacy ran a ``PollingMetricsLoop`` thread that every
``refresh_interval_s`` polled the OS sensors and published
``Topic.METRICS`` so subscribers (system info widget, activity
sidebar, LCD overlay, LED render) saw fresh readings on a single
cadence.

next/ ships the per-OS sensor enumerator (``platform.sensors()``)
and the ``SensorsUpdated`` event class, but the bridge that turns
"the aggregator's dict refreshed" into "fire ``SensorsUpdated`` on
the bus" was missing.  Result: ``bus_bridge.sensors_updated.connect``
subscribers in the GUI (``TRCCApp._on_bus_sensors_updated`` →
``uc_system_info`` / ``uc_activity_sidebar``) waited forever for an
event that never arrived.

This service is the missing chokepoint.  Owns one daemon thread.
Reads ``settings.app.refresh_interval_s`` per iteration so a control-
center change to "Refresh every Ns" takes effect on the next sleep.
"""
from __future__ import annotations

import logging
import threading

from ..core.events import (
    Event,
    EventBus,
    HddEnabledChanged,
    RefreshIntervalChanged,
    SensorsUpdated,
    TempUnitChanged,
)
from ..core.logs import per_frame
from ..core.models import MIN_REFRESH_INTERVAL_S

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: How many refresh intervals to wait for a sweep before publishing anyway.
#: Only reached when the poll thread never started or has died, so it trades a
#: slower degraded cadence for never tying with a healthy sweep.
_SWEEP_GRACE = 2.0


class MetricsLoop:
    """Background poller that publishes ``SensorsUpdated`` on the bus.

    Single subscriber pattern from the caller side:
    ``app.events.subscribe(SensorsUpdated, on_metrics)`` once, and
    the loop fans out to every widget that bridges from the event.
    """

    def __init__(self, app: App) -> None:  # type: ignore[name-defined]  # noqa: F821
        log.debug("__init__: app=%s", app)
        self._app = app
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # ``_wake`` is set BOTH for stop AND for "interval changed"
        # so a SetRefreshInterval dispatched mid-sleep cuts the wait
        # short and the next loop iteration picks up the new value.
        # Loop discriminates via ``_stop`` after waking.  Without
        # this, an interval change has up to one full OLD-cycle of
        # latency before the new cadence kicks in.
        self._wake = threading.Event()
        # First-publish flag — INFO on first SensorsUpdated of each
        # ``start()`` cycle proves the loop is alive; subsequent ticks
        # stay DEBUG so 2 s cadence doesn't drown the log.  Same shape
        # Phase 0 used for ``_animation_first_tick_logged``.
        self._first_publish_logged: bool = False
        # Subscribe to every user-pref change that affects the next
        # broadcast's content or cadence, so the loop reacts the
        # moment the user toggles a relevant setting — same event-
        # driven pattern Phase 4 used for video timer control.
        # DIP: MetricsLoop depends on the abstract events, not on
        # the Commands' internals.
        #
        # All three publishers route to one handler — DRY since the
        # wake action is identical regardless of which pref changed.
        for event_cls in (
            RefreshIntervalChanged,  # cadence change
            TempUnitChanged,         # broadcast values must reconvert
            HddEnabledChanged,       # broadcast must re-filter disk:*
        ):
            app.events.subscribe(event_cls, self._wake_for_pref_change)

    @property
    def is_running(self) -> bool:
        log.debug("is_running")
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            log.debug("MetricsLoop.start: already running")
            return
        self._stop.clear()
        self._wake.clear()
        # Reset diagnostic flags on every restart so a reconnect
        # (stop/start cycle) gets a fresh "first publish" line.
        self._first_publish_logged = False
        # Kick the sensor enumerator's background poll thread so the
        # ``_readings`` cache it returns from ``read_all()`` actually
        # refreshes — without this, ``read_all()`` returns the same
        # snapshot it got from its bootstrap-on-first-call poll
        # forever, and downstream consumers see frozen values.  Legacy
        # composition root did this from ``SystemService.start_polling``;
        # next/ doesn't have a SystemService so MetricsLoop owns the
        # lifecycle here.
        interval = float(self._app.settings.app.refresh_interval_s)
        try:
            # ``self._wake.set`` is the listener: the poll thread calls it
            # after every completed sweep, so this loop's existing wait wakes
            # on FRESH DATA rather than on a private clock.  ``_wake`` was
            # already set by pref changes and by ``stop()``; a sweep is simply
            # a third setter, which is why this needs no new mechanism.
            self._app.platform.sensors().start_polling(
                interval, on_sweep=self._wake.set)
        except Exception as e:
            log.warning(
                "MetricsLoop: sensors.start_polling(%.2fs) raised %s — "
                "broadcasts will publish frozen values from the bootstrap "
                "snapshot", interval, e,
            )
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="trcc-metrics",
        )
        self._thread.start()
        log.info("MetricsLoop: started (interval=%.2fs)", interval)

    def stop(self) -> None:
        if not self.is_running:
            log.debug("MetricsLoop.stop: not running")
            return
        log.info("MetricsLoop: stopping")
        self._stop.set()
        # Wake the sleeper so it sees the stop flag immediately.
        self._wake.set()
        # Stop the sensor enumerator's poll thread alongside ours —
        # we own its lifecycle since start().
        try:
            self._app.platform.sensors().stop_polling()
        except Exception:
            log.exception("MetricsLoop: sensors.stop_polling() raised")
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        log.info("MetricsLoop: stopped")

    def _wake_for_pref_change(self, event: Event) -> None:
        """Wake the worker on any user-pref change that affects the next broadcast.

        ``_loop`` reads ``settings.app.{refresh_interval_s,temp_unit,
        hdd_enabled}`` at the top of each iteration; the relevant
        Setter Command has already updated them.  All we need is to
        cut the current sleep short so the next iteration sees the
        new value and publishes a fresh ``SensorsUpdated``.

        One handler for three publishers — wake action is identical;
        the log line records WHICH pref changed so the user can
        confirm via tail.
        """
        log.info(
            "MetricsLoop._wake_for_pref_change: %s — waking sleeper",
            event,
        )
        self._wake.set()

    # ── Worker ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        events: EventBus = self._app.events
        while not self._stop.is_set():
            interval = max(
                MIN_REFRESH_INTERVAL_S,
                float(self._app.settings.app.refresh_interval_s),
            )
            # Push the cadence down to the sweep at the one place we already
            # read it.  The enumerator polls on its OWN thread, and its
            # interval used to be set once at ``start_polling`` and never
            # again — so a ``SetRefreshInterval`` moved this broadcast and
            # left the sweep feeding it running at the boot-time rate for the
            # life of the process.  A push here (rather than a second
            # subscription to ``RefreshIntervalChanged``) keeps ONE reader of
            # the setting and needs no branch: ``set_interval`` returns
            # immediately when the value has not moved.
            self._app.platform.sensors().set_interval(interval)
            # WAIT FIRST, then publish — the order is the fix, not a detail.
            # Publishing at the top of the loop raced the poll thread's first
            # sweep, and losing that race broadcast the EMPTY bootstrap cache:
            # measured 1 start in 3, subscribers showed 0 °C and held it for a
            # full interval.  Waiting first means no broadcast can precede the
            # sweep that fills it.
            #
            # ``_wake`` is set by a completed sweep, by a pref change, and by
            # ``stop()``.  The wait here is therefore a FALLBACK, not the
            # clock: it keeps broadcasts flowing if the poll thread never
            # started (``start_polling`` raised, which warns) or has died.
            #
            # That degraded path publishes at HALF the configured rate, not at
            # the old rate — worth stating plainly rather than calling it
            # unchanged.  The DATA is still fresh there, because with no poll
            # thread alive ``read_all`` falls through ``_refresh_if_stale``
            # and sweeps inline on this thread; it is only the cadence that
            # halves.  Clear AFTER the wait, so a sweep landing during the
            # publish below is still observed on the next pass.
            #
            # It waits a MULTIPLE of the interval, and that is load-bearing.
            # A bare ``interval`` ties with the sweep's own period, and
            # measured it lost the tie: the timeout fired just BEFORE each
            # sweep, so every cycle published twice — once on the timeout at a
            # full interval of staleness, once on the sweep at ~0 — which is
            # the defect this change exists to remove, at double the rate.
            # The fallback must only fire when a sweep genuinely did not come.
            self._wake.wait(interval * _SWEEP_GRACE)
            self._wake.clear()
            # Re-checked because ``stop()`` wakes us too, and a stopping loop
            # must not emit one last broadcast on its way out.
            if self._stop.is_set():
                break
            try:
                self._publish_once(events)
            except Exception:
                log.exception("MetricsLoop: poll iteration failed")

    def _publish_once(self, events: EventBus) -> None:
        """Trigger one sensor read + broadcast.

        Two steps:

          1. Read raw readings from ``platform.sensors().read_all()``
             — canonical °C, all keys present.
          2. Apply user prefs via
             :func:`metrics_personalize.personalize_readings` (temp
             unit conversion + HDD filter) — single conversion +
             filter site for the entire pipeline.
          3. Publish ``SensorsUpdated`` carrying the personalized
             dict + temp_unit so subscribers don't re-read settings.

        Matches legacy's ``PollingMetricsLoop._poll_metrics`` shape —
        broadcast at the boundary, consumers downstream are pure
        renderers.
        """
        from .metrics_personalize import (
            personalize_metrics,
            personalize_readings,
        )

        try:
            sensors = self._app.platform.sensors()
        except Exception as e:
            log.warning("MetricsLoop: platform.sensors() raised %s", e)
            return
        try:
            raw = sensors.read_all()
        except Exception as e:
            log.warning("MetricsLoop: sensors.read_all() raised %s", e)
            return

        s = self._app.settings.app
        readings = personalize_readings(
            raw,
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )
        # Typed snapshot from the same enumerator, personalized the same
        # way — the OS dispatcher's per-tick metrics object the GUI
        # observes at this refresh interval (no GUI-side re-poll).
        raw_snapshot = sensors.snapshot()
        metrics = personalize_metrics(
            raw_snapshot,
            temp_unit=s.temp_unit,
            hdd_enabled=s.hdd_enabled,
        )
        # Cache the RAW sample so per-tick consumers (RenderLed on the 150 ms
        # animation loop) render from this steady once-per-interval reading
        # instead of re-polling the sensors every tick — the re-poll made the
        # LED metric flicker on instantaneous values.  Raw, not personalized:
        # those consumers do their own temp-unit conversion.
        self._app.last_raw_readings = raw
        self._app.last_raw_snapshot = raw_snapshot
        events.publish(SensorsUpdated(
            reading_count=len(readings),
            readings=readings,
            temp_unit=s.temp_unit,
            metrics=metrics,
        ))
        # First publish proves the polling chain is alive; subsequent
        # publishes stay DEBUG so 2 s cadence doesn't flood.
        if not self._first_publish_logged:
            log.info(
                "MetricsLoop: SensorsUpdated published — first tick "
                "after start (raw=%d → personalized=%d, temp_unit=%s, "
                "hdd_enabled=%s, sample=%s)",
                len(raw), len(readings), s.temp_unit, s.hdd_enabled,
                sorted(readings)[:5],
            )
            self._first_publish_logged = True
        else:
            frame_log.debug(
                "MetricsLoop: SensorsUpdated(%d) published (raw=%d, "
                "temp_unit=%s, hdd_enabled=%s)",
                len(readings), len(raw), s.temp_unit, s.hdd_enabled,
            )
