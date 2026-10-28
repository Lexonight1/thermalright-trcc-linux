"""The cadence behind a theme's spectrum elements.

A theme is drawn when a reading changes -- every couple of seconds -- which is
far too slow for a live equaliser.  While the device's layout holds a
``spectrum`` element this renders the theme ~25 times a second (the display
service paints the bars over each frame); while it holds none it sleeps and
the sound stays closed.  Same ``BaseSendTask`` shape, and the same namespaced
key, as :mod:`trcc.services.source_driver`.
"""
from __future__ import annotations

import logging

from ..core.logs import per_frame
from ._send_task import BaseSendTask

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class SpectrumDriver(BaseSendTask):
    """Renders *device_key*'s theme at frame rate while it has a spectrum."""

    KEY_PREFIX = "spectrum:"
    FIXED_BEAT = True
    DEFAULT_INTERVAL_S = 1.0 / 25
    #: How often a theme with no spectrum looks again.
    IDLE_S = 1.0

    def _run(self, now: float) -> float:
        from ..core.commands import RenderAndSend

        try:
            elements = self._app.display.spectrum_elements(self._device_key)
        except RuntimeError:
            # No Renderer attached (a headless App): nothing to draw on.
            frame_log.debug("SpectrumDriver: %s has no display yet",
                            self._device_key)
            return self.IDLE_S
        feed = self._app.spectrum_feed
        if not elements or self._device_key in self._app.held:
            feed.stop()
            return self.IDLE_S
        first = elements[0]
        feed.start(
            max(e.get("bars", 18) for e in elements),
            first.get("mode", "audio"), first.get("lms_url", ""),
            first.get("lms_player", ""),
            tuple(w.strip().lower() for w in first.get("mpris", "").split(",")
                  if w.strip()))
        if not feed.busy:
            return self.IDLE_S
        result = self._app.dispatch(RenderAndSend(key=self._device_key))
        frame_log.debug("SpectrumDriver.run_once: %s ok=%s",
                        self._device_key, result.ok)
        return self._interval


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s spectrum driver."""
    key = f"{SpectrumDriver.KEY_PREFIX}{device_key}"
    log.debug("task_key: %s -> %s", device_key, key)
    return key
