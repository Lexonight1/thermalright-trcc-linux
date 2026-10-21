"""The cadence behind a module's frame source -- the stream driver's twin.

``CaptureSourceFrame`` takes the source's newest picture and hands it to
``SendScreencastFrame``; this dispatches it every ``1/fps``.  Same
``BaseSendTask`` shape and the same reason for a namespaced key as
:mod:`trcc.services.stream_driver`: the scheduler evicts by key, so a bare
device key would stop the device's own sender.
"""
from __future__ import annotations

import logging

from ..core.logs import per_frame
from ._send_task import BaseSendTask

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class SourceDriver(BaseSendTask):
    """Dispatches ``CaptureSourceFrame`` for one device, every tick."""

    KEY_PREFIX = "source:"
    FIXED_BEAT = True
    DEFAULT_INTERVAL_S = 1.0 / 30

    def _run(self, now: float) -> float:
        """Send one picture; a missed one costs that picture, not the source."""
        from ..core.commands import CaptureSourceFrame

        result = self._app.dispatch(CaptureSourceFrame(key=self._device_key))
        frame_log.debug("SourceDriver.run_once: %s ok=%s",
                        self._device_key, result.ok)
        return self._interval


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s source driver."""
    key = f"{SourceDriver.KEY_PREFIX}{device_key}"
    log.debug("task_key: %s -> %s", device_key, key)
    return key
