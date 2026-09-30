"""The cadence behind a web media source -- the screencast driver's twin.

``CaptureStreamFrame`` takes the stream reader's newest frame and hands it to
``SendScreencastFrame``; this dispatches it every ``STREAM_TICK_S``.  Same
``BaseSendTask`` shape and the same reason for a namespaced key as
:mod:`trcc.services.screencast_driver`: the scheduler evicts by key, so a bare
device key would stop the device's own sender.
"""
from __future__ import annotations

import logging

from ..core.logs import per_frame
from ..core.models import STREAM_TICK_S
from ._send_task import BaseSendTask

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class StreamDriver(BaseSendTask):
    """Dispatches ``CaptureStreamFrame`` for one device, every tick."""

    KEY_PREFIX = "stream:"
    DEFAULT_INTERVAL_S = STREAM_TICK_S

    def run_once(self, now: float) -> float:
        """Send one frame; a missed frame costs that frame, not the stream."""
        from ..core.commands import CaptureStreamFrame

        result = self._app.dispatch(CaptureStreamFrame(key=self._device_key))
        frame_log.debug("StreamDriver.run_once: %s ok=%s",
                        self._device_key, result.ok)
        return self._interval


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s stream driver."""
    key = f"{StreamDriver.KEY_PREFIX}{device_key}"
    log.debug("task_key: %s -> %s", device_key, key)
    return key
