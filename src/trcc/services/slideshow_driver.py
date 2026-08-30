"""The cadence behind a slideshow — the piece that makes CLI/API rotate.

``ConfigureSlideshow`` persists which themes rotate and how often, and then
nothing rotates them: the only caller of ``SlideshowService.advance`` was the
gui's own ``QTimer``.  A slideshow configured through ``trcc display slideshow``
or ``POST /slideshow`` was saved, reported back correctly, and **never
switched a theme** — the failure said so nowhere, because every surface agreed
the slideshow was enabled.  ``services/slideshow`` names this gap in its own
docstring and calls the driver "a separate piece of work [that] has not been
done".  This is that work.

**It is a `SendTask`, not a new port** — the same reasoning
``screencast_driver`` records.  ``SendTask``'s contract is
``key``/``wait``/``wake``/``run_once(now) -> float`` and only its rationale
mentions sending, so a second abstraction of identical shape would be one fact
expressed twice.  Reusing it also inherits ``SyncSendScheduler``, so the cadence
is testable by ticking a clock instead of sleeping.

**The key is namespaced**, for the reason the screencast driver learned the hard
way: ``ThreadSendScheduler.add`` is keyed by ``task.key`` and STOPS an existing
task with that key, so registering under the bare device key would kill that
device's ``DeviceSender`` and its keepalives.  ``App.stop_sender`` removes this
key alongside the screencast one, or a disconnected device would keep rotating.

**Why it resolves the name itself.**  ``AdvanceSlideshow`` deliberately returns
a NAME and not a path — its docstring explains that a UI should resolve the name
against whatever it is currently displaying before switching.  A driver has
nothing displayed, so it resolves the same way ``RestoreLastTheme`` does, with
``_search_theme_by_name`` across the device's theme roots.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from ..core.logs import per_frame
from ..core.models import SLIDESHOW_POLL_S
from ..core.ports import SendTask

if TYPE_CHECKING:                                    # pragma: no cover
    from ..app import App

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Re-exported from core, which owns it — a Command's default argument cannot
#: reach a function-local import, and both need the same number.
POLL_S = SLIDESHOW_POLL_S

#: Prefix that keeps this out of the device's own scheduler slot.
KEY_PREFIX = "slideshow:"


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s slideshow driver."""
    key = f"{KEY_PREFIX}{device_key}"
    log.debug("task_key: %s → %s", device_key, key)
    return key


class SlideshowDriver(SendTask):
    """Asks ``AdvanceSlideshow`` whether a rotation is due, and loads it."""

    def __init__(self, app: App, device_key: str,
                 interval_s: float = POLL_S) -> None:
        log.info("SlideshowDriver: %s polling every %.0f ms",
                 device_key, interval_s * 1000)
        self._app = app
        self._device_key = device_key
        self._interval = interval_s
        self._wake = threading.Event()

    @property
    def key(self) -> str:
        """The NAMESPACED scheduler key — see the module docstring."""
        log.debug("SlideshowDriver.key: %s", self._device_key)
        return task_key(self._device_key)

    def wait(self, timeout: float) -> None:
        """Block until woken or *timeout* elapses."""
        frame_log.debug("SlideshowDriver.wait: %s %.3fs",
                        self._device_key, timeout)
        self._wake.wait(timeout)
        self._wake.clear()

    def wake(self) -> None:
        """Interrupt a pending :meth:`wait` (scheduler teardown)."""
        frame_log.debug("SlideshowDriver.wake: %s", self._device_key)
        self._wake.set()

    def run_once(self, now: float) -> float:
        """Rotate if due; return the seconds to wait before asking again.

        Every failure here costs one rotation and never the driver: a theme can
        be renamed or deleted while a slideshow points at it, and a slideshow
        that stops forever because one entry went missing is worse than one that
        skips it and tries the next tick.
        """
        from ..core.commands import AdvanceSlideshow, LoadTheme
        from ..core.commands._helpers import _search_theme_by_name

        result = self._app.dispatch(AdvanceSlideshow(key=self._device_key))
        if not result.running:
            # Configured off, or no themes. Keep polling rather than
            # unregistering: the user may enable it again without restarting,
            # and a settings read is cheap.
            frame_log.debug("SlideshowDriver: %s not running", self._device_key)
            return self._interval
        if result.theme_name is None:
            frame_log.debug("SlideshowDriver: %s not due", self._device_key)
            return self._interval

        path = _search_theme_by_name(self._app, self._device_key,
                                     result.theme_name)
        if path is None:
            log.warning(
                "SlideshowDriver: %s — slideshow names theme %r but it is not "
                "under any of this device's theme roots; skipping this turn",
                self._device_key, result.theme_name,
            )
            return self._interval

        load = self._app.dispatch(LoadTheme(key=self._device_key, path=path))
        log.info("SlideshowDriver: %s → %s (ok=%s)",
                 self._device_key, result.theme_name, load.ok)
        return self._interval
