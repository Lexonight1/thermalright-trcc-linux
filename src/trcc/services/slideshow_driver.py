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
nothing displayed, so it resolves the same way ``RestoreDeviceState`` does, with
``_search_theme_by_name`` across the device's theme roots.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..core.logs import per_frame
from ..core.models import SLIDESHOW_POLL_S
from ._send_task import BaseSendTask

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class SlideshowDriver(BaseSendTask):
    """Asks ``AdvanceSlideshow`` whether a rotation is due, and loads it.

    Namespace + cadence + ``run_once``; the wait/wake/key machinery is
    :class:`~trcc.services._send_task.BaseSendTask`.
    """

    KEY_PREFIX = "slideshow:"
    DEFAULT_INTERVAL_S = SLIDESHOW_POLL_S

    def __init__(self, app: App, device_key: str,
                 interval_s: float | None = None) -> None:
        super().__init__(app, device_key, interval_s)
        log.info("SlideshowDriver: %s — nothing shown by it yet", device_key)
        # What THIS driver last put on the panel; None until its first load.
        self._shown: tuple[object, ...] | None = None

    def _panel(self) -> tuple[object, ...]:
        """What the panel shows now: the theme, and any source over it.

        The theme as ``current_theme`` -- the resolved path every ``LoadTheme``
        persists, the same string its result reports -- so this is a settings
        read, not a filesystem one.
        """
        s = self._app.settings.for_device(self._device_key)
        frame_log.debug("SlideshowDriver._panel: %s theme=%s", self._device_key,
                        s.current_theme)
        return (s.current_theme, s.background_path, s.screencast_region,
                s.media_player_uri)

    def _run(self, now: float) -> float:
        """Rotate if due; return the seconds to wait before asking again.

        Every failure here costs one rotation and never the driver: a theme can
        be renamed or deleted while a slideshow points at it, and a slideshow
        that stops forever because one entry went missing is worse than one that
        skips it and tries the next tick.
        """
        from ..core.commands import AdvanceSlideshow, LoadTheme, SetSlideshow
        from ..core.commands._helpers import _search_theme_by_name

        if self._shown is not None and self._panel() != self._shown:
            # Another theme, a background, a screencast or the media player took
            # the panel -- from any UI.  One source at a time, as in the C#,
            # where every other mode switches the slideshow off: it rotated
            # over the user's pick within a second, measured.
            log.info("SlideshowDriver: %s — the panel shows something the "
                     "slideshow did not put there; switching it off",
                     self._device_key)
            self._app.dispatch(SetSlideshow(key=self._device_key, enabled=False))
            return self._interval

        result = self._app.dispatch(AdvanceSlideshow(key=self._device_key))
        if not result.running:
            # On with no themes yet: keep polling, a ConfigureSlideshow may add
            # them.  (Off removes this driver -- ``SetSlideshow``.)
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
        # The theme from THIS load's result, not the live panel: a pick from
        # another UI can land while this load runs, and reading the panel
        # afterwards recorded the user's theme as the slideshow's own
        # (measured through a real daemon).  The sources over it are read
        # live -- a bundled video or screencast is set BY this load.
        self._shown = ((load.theme_path, *self._panel()[1:])
                       if load.theme_path else None)
        log.info("SlideshowDriver: %s → %s (ok=%s)",
                 self._device_key, result.theme_name, load.ok)
        return self._interval


def task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s slideshow driver (public helper).

    For callers that hold the device key but no driver instance (``App``
    removing both slots, the Commands registering/removing a task).  Single
    source: the class attribute.
    """
    key = f"{SlideshowDriver.KEY_PREFIX}{device_key}"
    log.debug("task_key: %s → %s", device_key, key)
    return key
