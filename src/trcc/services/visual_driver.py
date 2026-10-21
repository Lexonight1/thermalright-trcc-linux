"""The cadence behind an audio visual -- the stream driver's twin.

``CaptureVisualFrame`` takes the visual reader's newest picture and hands it
to ``SendScreencastFrame``; this dispatches it every ``1/VISUAL_FPS``.  Same
``BaseSendTask`` shape and the same reason for a namespaced key as
:mod:`trcc.services.stream_driver`: the scheduler evicts by key, so a bare
device key would stop the device's own sender.
"""
from __future__ import annotations

import logging

from ..core.logs import per_frame
from ..core.models import VISUAL_FPS
from ._send_task import BaseSendTask

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class VisualDriver(BaseSendTask):
    """Dispatches ``CaptureVisualFrame`` for one device, every tick."""

    KEY_PREFIX = "visual:"
    FIXED_BEAT = True
    DEFAULT_INTERVAL_S = 1.0 / VISUAL_FPS

    def _run(self, now: float) -> float:
        """Send one picture; a missed one costs that picture, not the visual."""
        from ..core.commands import CaptureVisualFrame

        result = self._app.dispatch(CaptureVisualFrame(key=self._device_key))
        frame_log.debug("VisualDriver.run_once: %s ok=%s",
                        self._device_key, result.ok)
        return self._interval


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s visual driver."""
    key = f"{VisualDriver.KEY_PREFIX}{device_key}"
    log.debug("task_key: %s -> %s", device_key, key)
    return key
