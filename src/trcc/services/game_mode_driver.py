"""The once-a-second cadence behind game mode.

A ``SendTask`` like the slideshow and screencast drivers, for their reason:
the contract is ``key``/``wait``/``wake``/``run_once`` and says nothing about
sending, so a second abstraction of the same shape would be one fact twice.
The work is one Command, ``TickGameMode`` -- the reading, the hysteresis and
the frame live there, where every UI and a test reach them the same way.

**The key is namespaced** (``game:``) so registering it never evicts the
device's own ``DeviceSender``; ``App.stop_sources`` removes it with the
other drivers.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..core.logs import per_frame
from ..core.models import GAME_MODE_TICK_S
from ._send_task import BaseSendTask

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class GameModeTask(BaseSendTask):
    """Dispatches ``TickGameMode`` every :data:`GAME_MODE_TICK_S`."""

    KEY_PREFIX = "game:"
    DEFAULT_INTERVAL_S = GAME_MODE_TICK_S
    #: The one task that draws while game mode holds the panel.
    PAUSES_FOR_GAME = False

    def __init__(self, app: App, device_key: str,
                 interval_s: float | None = None) -> None:
        super().__init__(app, device_key, interval_s)
        log.info("GameModeTask: %s watching its row", device_key)

    def _run(self, now: float) -> float:
        from ..core.commands import TickGameMode

        result = self._app.dispatch(TickGameMode(key=self._device_key))
        frame_log.debug("GameModeTask: %s %s", self._device_key,
                        getattr(result, "verdict", result.message))
        return self._interval


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s game-mode task."""
    key = f"{GameModeTask.KEY_PREFIX}{device_key}"
    log.debug("task_key: %s -> %s", device_key, key)
    return key
