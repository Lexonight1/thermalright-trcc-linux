"""Metrics loop implementations — the underlying command stream.

`PollingMetricsLoop` is the thread-based default used by every OS today.
It ticks every connected device at 50ms (animation / video frames),
polls sensors at the user's ``settings.refresh_interval`` (Hardware
metrics), and publishes the results through ``trcc.events``.

`NullMetricsLoop` is the test / proxy stand-in: it satisfies the
contract without spinning a thread.  ``TrccProxy`` uses it because the
real loop runs in the daemon process; unit tests use it when a fixture
doesn't want sensor I/O.

Both implement ``core.ports.MetricsLoop``.  Per-OS native variants
(WMI event-based on Windows, IOReportSubscribe on macOS) plug in via
``Platform.build_metrics_loop()`` without touching this module.
"""
from __future__ import annotations

import logging
import threading
from itertools import chain
from typing import TYPE_CHECKING, Any

from ..core.events import Topic
from ..core.ports import MetricsLoop

if TYPE_CHECKING:
    from ..core.trcc import Trcc

log = logging.getLogger(__name__)

# Animation tick rate — 50ms gives 20 FPS for video and overlay updates.
_TICK_INTERVAL = 0.05


class PollingMetricsLoop(MetricsLoop):
    """Thread-based polling loop — the OS-agnostic default.

    Two cadences run in one daemon thread:

      * **Tick** (every 50ms): drive ``device.tick()`` for every connected
        LCD and LED device; publish ``Topic.FRAME`` for any frame
        produced.  Playing LCDs also get their overlay-text cache
        refreshed with the latest metrics.
      * **Poll** (every ``settings.refresh_interval`` seconds): read
        ``system_svc.all_metrics``, apply the user's temp unit, push to
        every device's ``update_metrics()``, publish ``Topic.METRICS``.

    The cadence ratio is recomputed each iteration, so settings changes
    take effect on the next tick.  ``wake()`` short-circuits the wait
    so callers can force an immediate poll after a control-center mutation.
    """

    def __init__(self, trcc: Trcc) -> None:
        self._trcc = trcc
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._trcc.system_svc is None:
            raise RuntimeError(
                'PollingMetricsLoop.start: SystemService not injected on Trcc. '
                'Pass system_svc=… at Trcc construction or call '
                'Trcc.set_system_service() before discover().')
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='trcc-metrics')
        self._thread.start()
        log.debug(
            'PollingMetricsLoop started (tick=%.0fms, poll=settings.refresh_interval)',
            _TICK_INTERVAL * 1000)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        self._wake.clear()
        self._stop.clear()

    def wake(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        from ..core.models import HardwareMetrics

        trcc = self._trcc
        tick_count = 0
        while not self._stop.is_set():
            try:
                _settings = trcc.settings
                poll_interval = max(1, _settings.refresh_interval)
                metrics_every = max(1, int(poll_interval / _TICK_INTERVAL))

                if tick_count % metrics_every == 0:
                    self._poll_metrics(HardwareMetrics, _settings)

                self._tick_lcd_devices()
                self._tick_led_devices()
            except Exception:
                log.exception('Tick loop error')

            tick_count += 1
            self._wake.wait(_TICK_INTERVAL)
            self._wake.clear()

    def _poll_metrics(self, HardwareMetrics: Any, _settings: Any) -> None:
        trcc = self._trcc
        sys_svc = trcc.system_svc
        if sys_svc is None:
            return
        try:
            metrics = HardwareMetrics.with_temp_unit(
                sys_svc.all_metrics, _settings.temp_unit)
            trcc.set_current_metrics(metrics)
            for device in tuple(chain(trcc.lcd_devices, trcc.led_devices)):
                try:
                    device.update_metrics(metrics)
                except Exception:
                    log.exception('Metrics update error')
            trcc.events.publish(Topic.METRICS, metrics)
        except Exception:
            log.exception('Metrics poll error')

    def _tick_lcd_devices(self) -> None:
        trcc = self._trcc
        for device in tuple(trcc.lcd_devices):
            path = getattr(device, 'device_path', '?') or '?'
            try:
                if (result := device.tick()) is not None:
                    trcc.events.publish(Topic.FRAME, path, result)
                elif device.playing:
                    device.update_video_cache_text(trcc.current_metrics)
            except Exception:
                log.exception('LCD tick error: %s', path)

    def _tick_led_devices(self) -> None:
        trcc = self._trcc
        for device in tuple(trcc.led_devices):
            info = device.device_info
            path = (getattr(info, 'path', '?') if info else '?') or '?'
            try:
                if (result := device.tick()) is not None:
                    trcc.events.publish(Topic.FRAME, path, result)
            except Exception:
                log.exception('LED tick error: %s', path)


class NullMetricsLoop(MetricsLoop):
    """No-op loop — for tests + proxy clients that don't own the tick.

    Composition roots that build a :class:`TrccProxy` (daemon-mode
    clients) inject this because the real loop runs in the daemon
    process.  Unit tests that don't need sensor I/O can override
    ``Platform.build_metrics_loop`` to return this.

    All methods are silent no-ops; ``is_running`` always reports False.
    """

    @property
    def is_running(self) -> bool:
        return False

    def start(self) -> None:
        log.debug('NullMetricsLoop.start: no-op')

    def stop(self) -> None:
        log.debug('NullMetricsLoop.stop: no-op')

    def wake(self) -> None:
        log.debug('NullMetricsLoop.wake: no-op')
