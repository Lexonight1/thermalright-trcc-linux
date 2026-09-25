"""VideoLoop — the one ticker that plays video on every device.

A video advances ONE frame per ``TickDisplay``, so something must dispatch it
at the video's own rate.  That something used to live in the UIs: the GUI's
animation timer, qtgui's per-device updater, the CLI ``display play`` loop.
The daemon had none, so a video loaded through it showed frame 0 forever
(#249) — and two tickers on one device would play it at double speed, because
``Playback.advance`` counts calls, not time.

This is that ticker, once, in the core, started by ``App.start_session`` like
``MetricsLoop`` and ``LedAnimationLoop``.  It reads the live playbacks on every
pass instead of subscribing to ``VideoStarted``: coldplug restores a video
theme BEFORE the session's loops start, so a subscriber would miss that first
event, while a read cannot.  Paused, stopped and detached playbacks simply
fall out of ``MediaService.playing()`` — nothing has to announce them.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from ..core.logs import per_frame

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

# How long to wait when nothing is playing: the latency from "a video was
# loaded" to its first advance.  Playing videos are paced by their own rate.
_IDLE_S = 0.1


class VideoLoop:
    """Background thread advancing every playing video at its own frame rate."""

    def __init__(self, app: App) -> None:
        log.debug("VideoLoop.__init__")
        self._app = app
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Monotonic time each device's next frame is due.
        self._due: dict[str, float] = {}
        # The last problem logged per device, so a disconnected panel is
        # reported once when it happens, not 30 times a second.
        self._problem: dict[str, str] = {}

    @property
    def is_running(self) -> bool:
        frame_log.debug("VideoLoop.is_running")
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            log.debug("VideoLoop.start: already running")
            return
        self._stop.clear()
        self._due.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="trcc-video")
        self._thread.start()
        log.info("VideoLoop: started")

    def stop(self) -> None:
        if not self.is_running:
            log.debug("VideoLoop.stop: not running")
            return
        log.info("VideoLoop: stopping")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        log.info("VideoLoop: stopped")

    def tick(self, now: float) -> float:
        """Advance every video that is due; return seconds until the next one."""
        from ..core.commands import TickDisplay

        live = self._app.media.playing()
        for gone in self._due.keys() - live.keys():
            log.info("VideoLoop: %s stopped playing", gone)
            del self._due[gone]
            self._problem.pop(gone, None)
        for key, playback in live.items():
            interval = playback.interval_ms / 1000
            if key not in self._due:
                log.info("VideoLoop: %s playing at %dms/frame",
                         key, playback.interval_ms)
                self._due[key] = now
            if now >= self._due[key]:
                self._report(key, self._app.dispatch(TickDisplay(key=key)))
                # Keep the cadence, but never try to catch up a backlog: a
                # late pass resumes one interval from NOW, or it would fire a
                # burst of frames back to back.
                nxt = self._due[key] + interval
                self._due[key] = nxt if nxt > now else now + interval
        wait = min(self._due.values(), default=now + _IDLE_S) - now
        frame_log.debug("VideoLoop.tick: %d playing, next in %.3fs",
                        len(live), wait)
        return max(0.0, min(wait, _IDLE_S))

    def _report(self, key: str, result: Any) -> None:
        """Log a tick's failure once per change of problem, never per frame."""
        problem = ("device not connected" if result.connected is False
                   else "" if result.ok else f"render failed: {result.message}")
        if problem == self._problem.get(key, ""):
            frame_log.debug("VideoLoop: %s frame %s/%s %s", key, result.cursor,
                            result.frame_count, problem or "ok")
            return
        self._problem[key] = problem
        if problem:
            log.warning("VideoLoop: %s %s (frame %s/%s)", key, problem,
                        result.cursor, result.frame_count)
        else:
            log.info("VideoLoop: %s playing again", key)

    def _loop(self) -> None:
        log.debug("VideoLoop._loop: running")
        while not self._stop.is_set():
            try:
                wait = self.tick(time.monotonic())
            except Exception:
                log.exception("VideoLoop: tick failed")
                wait = _IDLE_S
            self._stop.wait(wait)
