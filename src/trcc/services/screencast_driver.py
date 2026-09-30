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

from ..core.logs import per_frame
from ..core.models import SCREENCAST_TICK_S
from ._send_task import BaseSendTask

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class ScreencastDriver(BaseSendTask):
    """Dispatches ``CaptureScreencastFrame`` for one device, every tick.

    Namespace + cadence + ``run_once``; the wait/wake/key machinery is
    :class:`~trcc.services._send_task.BaseSendTask`.
    """

    KEY_PREFIX = "screencast:"
    DEFAULT_INTERVAL_S = SCREENCAST_TICK_S

    def run_once(self, now: float) -> float:
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
        return self._interval


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s screencast driver (public helper).

    A namespaced key for callers that only have the device key, not a driver
    instance (``App.stop_sender`` removing both driver slots, the Commands
    registering/removing a task).  Single source: the class attribute.
    """
    key = f"{ScreencastDriver.KEY_PREFIX}{device_key}"
    log.debug("task_key: %s → %s", device_key, key)
    return key
