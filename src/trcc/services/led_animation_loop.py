"""LedAnimationLoop — fast background tick that animates LED devices.

LED effect modes (breathing / colour-cycle / rainbow), the test-mode colour
cycle, and the metric-page carousel each advance ONE step per ``RenderLed``.
The sensor broadcast (``MetricsLoop``, ~2 s) is far too slow to drive them — at
that cadence a breathing pulse takes minutes and colour-cycle/rainbow look
frozen and identical (the reported bug).  Legacy drove ``device.tick()`` every
~50 ms; the C# ``Timer_event`` runs ~167 ms.

This loop restores that: a ~150 ms tick that re-renders every connected LED
device whose mode is actually animating, via the universal ``RenderLed``
command (so the device AND the GUI preview advance together).  Static LEDs with
no carousel/test are skipped, so an idle fleet costs nothing.

Mirrors ``MetricsLoop``'s daemon-thread + start/stop lifecycle.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from ..core.led_models import LEDMode

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)

# ~the C# FormLED Timer_event cadence (167 ms); a touch faster for smoothness.
_TICK_INTERVAL_S = 0.15

# Modes whose output changes every tick (need the fast cadence).  STATIC and the
# sensor-linked modes don't — TEMP/LOAD_LINKED follow the slow sensor broadcast.
_ANIMATED_MODES = frozenset({
    LEDMode.BREATHING, LEDMode.COLORFUL, LEDMode.RAINBOW,
})


class LedAnimationLoop:
    """Background ~150 ms ticker re-rendering animating LED devices."""

    def __init__(self, app: App) -> None:  # type: ignore[name-defined]
        self._app = app
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._first_tick_logged = False

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            log.debug("LedAnimationLoop.start: already running")
            return
        self._stop.clear()
        self._first_tick_logged = False
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="trcc-led-anim",
        )
        self._thread.start()
        log.info("LedAnimationLoop: started (interval=%.0fms)",
                 _TICK_INTERVAL_S * 1000)

    def stop(self) -> None:
        if not self.is_running:
            log.debug("LedAnimationLoop.stop: not running")
            return
        log.info("LedAnimationLoop: stopping")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        log.info("LedAnimationLoop: stopped")

    # ── Worker ────────────────────────────────────────────────────────

    def animating_keys(self) -> list[str]:
        """Connected LED devices whose mode / carousel / test is moving."""
        keys: list[str] = []
        for key, device in self._app.devices.items():
            if not (device.is_led and device.is_connected):
                continue
            s = self._app.settings.for_led(key)
            if s.mode in _ANIMATED_MODES or s.zone_sync or s.test_mode:
                keys.append(key)
        return keys

    def tick(self) -> None:
        """Re-render every animating LED once (one effect step)."""
        from ..core.commands import RenderLed
        for key in self.animating_keys():
            if not self._first_tick_logged:
                log.info("LedAnimationLoop: animating %s (first tick)", key)
                self._first_tick_logged = True
            self._app.dispatch(RenderLed(key=key))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("LedAnimationLoop: tick failed")
            self._stop.wait(_TICK_INTERVAL_S)
