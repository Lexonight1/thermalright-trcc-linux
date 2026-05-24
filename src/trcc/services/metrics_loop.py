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

from ..core.events import EventBus, RefreshIntervalChanged, SensorsUpdated

log = logging.getLogger(__name__)


class MetricsLoop:
    """Background poller that publishes ``SensorsUpdated`` on the bus.

    Single subscriber pattern from the caller side:
    ``app.events.subscribe(SensorsUpdated, on_metrics)`` once, and
    the loop fans out to every widget that bridges from the event.
    """

    def __init__(self, app: App) -> None:  # type: ignore[name-defined]  # noqa: F821
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
        # Subscribe to RefreshIntervalChanged so the loop reacts the
        # moment the user adjusts the refresh-rate input — same
        # event-driven pattern Phase 4 used for video timer control.
        # DIP: MetricsLoop depends on the abstract event, not on
        # SetRefreshInterval's internals.
        app.events.subscribe(
            RefreshIntervalChanged, self._on_refresh_interval_changed,
        )

    @property
    def is_running(self) -> bool:
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
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="trcc-metrics",
        )
        self._thread.start()
        log.info("MetricsLoop: started (interval=%.2fs)",
                 float(self._app.settings.app.refresh_interval_s))

    def stop(self) -> None:
        if not self.is_running:
            log.debug("MetricsLoop.stop: not running")
            return
        log.info("MetricsLoop: stopping")
        self._stop.set()
        # Wake the sleeper so it sees the stop flag immediately.
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        log.info("MetricsLoop: stopped")

    def _on_refresh_interval_changed(self, event: RefreshIntervalChanged) -> None:
        """Wake the worker so the next iteration reads the new interval.

        ``_loop`` reads ``settings.app.refresh_interval_s`` per
        iteration; SetRefreshInterval has already updated it.  All we
        need is to cut the current sleep short.  Same shape Phase 4
        used for video timer responsiveness — event-driven, no manual
        loop.restart() call from the Command.
        """
        log.info(
            "MetricsLoop._on_refresh_interval_changed: %.2fs — waking sleeper",
            event.seconds,
        )
        self._wake.set()

    # ── Worker ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        events: EventBus = self._app.events
        while not self._stop.is_set():
            try:
                self._publish_once(events)
            except Exception:
                log.exception("MetricsLoop: poll iteration failed")
            interval = max(0.1, float(self._app.settings.app.refresh_interval_s))
            # Wait on ``_wake`` (set by stop() OR by an interval
            # change).  Clear AFTER the wait so a wake-up during the
            # NEXT iteration's wait is still observed.  ``_stop`` is
            # the authoritative shutdown flag — check it at the top
            # of the while loop.
            self._wake.wait(interval)
            self._wake.clear()

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
        from .metrics_personalize import personalize_readings

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
        events.publish(SensorsUpdated(
            reading_count=len(readings),
            readings=readings,
            temp_unit=s.temp_unit,
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
            log.debug(
                "MetricsLoop: SensorsUpdated(%d) published (raw=%d, "
                "temp_unit=%s, hdd_enabled=%s)",
                len(readings), len(raw), s.temp_unit, s.hdd_enabled,
            )
