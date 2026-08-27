"""The cadence behind a screencast — the piece that made CLI/API work.

``StartScreencast`` publishes ``ScreencastStarted`` and deliberately nothing
else, because the GUI's ``ScreencastHandler`` subscribes and runs a Qt timer.
That left every other client driving nothing: ``trcc display screencast``
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
import threading
from typing import TYPE_CHECKING

from ..core.logs import per_frame
from ..core.ports import SendTask

if TYPE_CHECKING:                                    # pragma: no cover
    from ..app import App

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Matches the GUI's own screencast timer (``ScreencastHandler``, 150 ms), so a
#: headless cast moves at the same rate as one driven from the window.
TICK_S = 0.15

#: Prefix that keeps this out of the device's own scheduler slot.
KEY_PREFIX = "screencast:"


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s screencast driver."""
    key = f"{KEY_PREFIX}{device_key}"
    log.debug("task_key: %s → %s", device_key, key)
    return key


class ScreencastDriver(SendTask):
    """Dispatches ``CaptureScreencastFrame`` for one device, every tick."""

    def __init__(self, app: App, device_key: str,
                 interval_s: float = TICK_S) -> None:
        log.info("ScreencastDriver: %s every %.0f ms",
                 device_key, interval_s * 1000)
        self._app = app
        self._device_key = device_key
        self._interval = interval_s
        self._wake = threading.Event()

    @property
    def key(self) -> str:
        """The NAMESPACED scheduler key — see the module docstring."""
        log.debug("ScreencastDriver.key: %s", self._device_key)
        return task_key(self._device_key)

    def wait(self, timeout: float) -> None:
        """Block until woken or *timeout* elapses."""
        frame_log.debug("ScreencastDriver.wait: %s %.3fs",
                        self._device_key, timeout)
        self._wake.wait(timeout)
        self._wake.clear()

    def wake(self) -> None:
        """Interrupt a pending :meth:`wait` (scheduler teardown)."""
        frame_log.debug("ScreencastDriver.wake: %s", self._device_key)
        self._wake.set()

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
