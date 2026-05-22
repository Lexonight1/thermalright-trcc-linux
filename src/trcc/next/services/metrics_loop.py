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

from ..core.events import EventBus, SensorsUpdated

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

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            log.debug("MetricsLoop.start: already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="trcc-next-metrics",
        )
        self._thread.start()
        log.info("MetricsLoop: started")

    def stop(self) -> None:
        if not self.is_running:
            log.debug("MetricsLoop.stop: not running")
            return
        log.info("MetricsLoop: stopping")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        log.info("MetricsLoop: stopped")

    # ── Worker ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        events: EventBus = self._app.events
        while not self._stop.is_set():
            try:
                self._publish_once(events)
            except Exception:
                log.exception("MetricsLoop: poll iteration failed")
            interval = max(0.1, float(self._app.settings.app.refresh_interval_s))
            self._stop.wait(interval)

    def _publish_once(self, events: EventBus) -> None:
        """Trigger one sensor read + broadcast.

        Reads via ``aggregator.read_all()`` so the cached dict is
        guaranteed fresh; ``SensorsUpdated`` carries no payload itself
        (subscribers call ``ReadSensors`` to pull the typed view they
        want — keeps the event lightweight + lets each consumer choose
        its preferred shape).
        """
        try:
            sensors = self._app.platform.sensors()
        except Exception as e:
            log.warning("MetricsLoop: platform.sensors() raised %s", e)
            return
        try:
            readings = sensors.read_all()
        except Exception as e:
            log.warning("MetricsLoop: sensors.read_all() raised %s", e)
            return
        events.publish(SensorsUpdated(reading_count=len(readings)))
        log.debug("MetricsLoop: SensorsUpdated(%d) published", len(readings))
