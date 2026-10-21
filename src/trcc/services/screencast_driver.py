"""The cadence behind a screencast — the piece that made CLI/API work.

``StartScreencast`` used to publish ``ScreencastStarted`` and deliberately
nothing else, because the GUI's ``ScreencastHandler`` subscribed and ran a Qt
timer.  That left every other client driving nothing: ``trcc display screencast``
printed "Capturing on …" and then sat in ``signal.pause()``, and the REST route
had the same shape.  This is the missing driver — it dispatches
``CaptureScreencastFrame`` on a fixed cadence from a scheduler thread, so a
headless client casts exactly like the GUI does.

**It is a `SendTask`, not a new port.**  ``SendTask``'s contract is
``key``/``wait``/``wake``/``run_once(now) -> float`` — entirely generic; only
its rationale mentions sending.  Adding a ``PeriodicTask`` with the same shape
would be one fact expressed twice, so this reuses the abstraction the tree
already has and the ``SyncSendScheduler`` that makes it testable without sleeps.

**The key is namespaced.**  ``ThreadSendScheduler.add`` is keyed by
``task.key`` and STOPS an existing task with that key, so registering this
under the bare device key would kill that device's ``DeviceSender`` and its
keepalives — the panel would go dark the moment a screencast started.
``screencast:<device key>`` cannot collide.  The matching trap is that
``App.stop_sender`` removes the bare key, which would silently leave this task
running and keep capturing for a disconnected device; ``App`` removes both.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..core.logs import per_frame
from ..core.models import SCREENCAST_TICK_S
from ._send_task import BaseSendTask

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Failed ticks in a row before the UIs are told: about three seconds.  Short
#: gaps (a lock screen flickering, the portal's first frame) say nothing.
FAILED_TICKS_BEFORE_TELLING = round(3.0 / SCREENCAST_TICK_S)


class ScreencastDriver(BaseSendTask):
    """Dispatches ``CaptureScreencastFrame`` for one device, every tick.

    Namespace + cadence + ``run_once``; the wait/wake/key machinery is
    :class:`~trcc.services._send_task.BaseSendTask`.
    """

    KEY_PREFIX = "screencast:"
    FIXED_BEAT = True
    DEFAULT_INTERVAL_S = SCREENCAST_TICK_S

    def __init__(self, app: App, device_key: str,
                 interval_s: float | None = None) -> None:
        super().__init__(app, device_key, interval_s)
        log.debug("ScreencastDriver: %s tells the UIs after %d failed ticks",
                  device_key, FAILED_TICKS_BEFORE_TELLING)
        self._failed_in_a_row = 0
        self._told = False

    def _run(self, now: float) -> float:
        """Capture one frame; return the seconds to wait before the next.

        A failed frame does NOT stop the driver — capture depends on a desktop
        session that can vanish under it (screen locked, portal revoked, the
        grab tool uninstalled).  ``CaptureScreencastFrame`` reports rather than
        raises, and the cadence is unchanged either way, so a transient failure
        costs one frame instead of the session.
        """
        from ..core.commands import CaptureScreencastFrame

        result = self._app.dispatch(CaptureScreencastFrame(key=self._device_key))
        frame_log.debug("ScreencastDriver.run_once: %s ok=%s",
                        self._device_key, result.ok)
        self._follow(result.ok, result.message)
        return self._interval

    def _follow(self, ok: bool, message: str) -> None:
        """Tell the UIs once when capture keeps failing, and once when it is back.

        It kept going silently, so a cast that could never capture -- the
        shared Windows App's offscreen Qt, 2026-10-20 -- showed the user
        nothing at all while it logged an ERROR sixteen times a second.  It
        still does not END the session: a locked screen fails for minutes and
        must not throw the cast away.
        """
        from ..core.events import ErrorOccurred

        if ok:
            if self._told:
                log.info("ScreencastDriver: %s capturing again", self._device_key)
            self._failed_in_a_row, self._told = 0, False
            return
        self._failed_in_a_row += 1
        if self._told or self._failed_in_a_row < FAILED_TICKS_BEFORE_TELLING:
            frame_log.debug("ScreencastDriver: %s failed %d in a row",
                            self._device_key, self._failed_in_a_row)
            return
        self._told = True
        log.warning("ScreencastDriver: %s has captured nothing for %d ticks "
                    "(%s) -- telling the UIs; still trying",
                    self._device_key, self._failed_in_a_row, message)
        self._app.events.publish(ErrorOccurred(
            message=f"Screencast: nothing captured for 3 seconds -- {message}",
            kind="screencast", key=self._device_key))


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s screencast driver (public helper).

    A namespaced key for callers that only have the device key, not a driver
    instance (``App.stop_sender`` removing both driver slots, the Commands
    registering/removing a task).  Single source: the class attribute.
    """
    key = f"{ScreencastDriver.KEY_PREFIX}{device_key}"
    log.debug("task_key: %s → %s", device_key, key)
    return key
