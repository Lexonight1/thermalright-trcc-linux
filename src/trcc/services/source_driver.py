"""The ONE driver behind a panel's live picture -- a screencast, a web stream,
a module's visual.

Each tick dispatches ``CaptureSourceFrame``, which takes the picture from the
panel's live slot (``MediaService.open_live``) and hands it to
``SendScreencastFrame``.  There were three of these, one per kind, identical
but for the Command they dispatched; the kind is now data.

One instance per (panel, kind), each under its own key: when a stream takes
over from a cast, the cast's driver must still be there to see it go and end
the cast properly (mic, capture, ``ScreencastStopped``).  One key per panel
would have replaced it silently.  Namespaced, as every task here is: the
scheduler evicts by key, so a bare device key would stop the device's sender.

One failure rule for every kind (his decision, 2026-11-04): a tick that
failed is counted, and when they keep failing every UI is told ONCE while it
keeps trying; a source that is gone for good ends in the Command.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..core.logs import per_frame
from ..core.models import PanelSource
from ._send_task import BaseSendTask

if TYPE_CHECKING:
    from ..app import App

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Seconds of failing ticks before every UI is told: short gaps (a lock
#: screen flickering, a portal's first frame) say nothing.
TELL_AFTER_S = 3.0

#: What every UI is told when a kind keeps failing: the line, and the
#: ``ErrorOccurred.kind`` each UI already sorts it by.
NOTICES: dict[PanelSource, tuple[str, str]] = {
    PanelSource.SCREENCAST: ("Screencast: nothing captured for 3 seconds",
                             "screencast"),
    PanelSource.MEDIA: ("Media player: nothing reached the panel for 3 seconds",
                        "video"),
    PanelSource.MODULE: ("Module picture: nothing reached the panel for 3 "
                         "seconds", "video"),
}


class SourceDriver(BaseSendTask):
    """Dispatches ``CaptureSourceFrame`` for one panel's *kind* of live
    picture, every tick -- and tells the UIs when it keeps failing."""

    KEY_PREFIX = "live:"
    FIXED_BEAT = True
    DEFAULT_INTERVAL_S = 1.0 / 30

    def __init__(self, app: App, device_key: str, kind: PanelSource,
                 interval_s: float | None = None) -> None:
        super().__init__(app, device_key, interval_s)
        self._kind = kind
        self._tell_after = max(1, round(TELL_AFTER_S / self._interval))
        self._failed_in_a_row = 0
        self._told = False
        log.debug("SourceDriver: %s %s tells the UIs after %d failed ticks",
                  device_key, kind.value, self._tell_after)

    @property
    def key(self) -> str:
        """This panel's *kind* of live picture's own scheduler slot."""
        key = task_key(self._device_key, self._kind)
        frame_log.debug("SourceDriver.key: %s", key)
        return key

    def _run(self, now: float) -> float:
        """Send one picture; a missed one costs that picture, not the source."""
        from ..core.commands import CaptureSourceFrame

        result = self._app.dispatch(
            CaptureSourceFrame(key=self._device_key, kind=self._kind))
        frame_log.debug("SourceDriver.run_once: %s %s ok=%s", self._device_key,
                        self._kind.value, result.ok)
        self._follow(result.ok, result.message)
        return self._interval

    def _follow(self, ok: bool, message: str) -> None:
        """Tell the UIs once when sending keeps failing, and log when it is
        back.  It never ENDS the source: a locked screen fails for minutes
        and must not throw the cast away -- a source gone for good ends in
        the Command, with its own notice."""
        from ..core.events import ErrorOccurred

        if ok:
            if self._told:
                log.info("SourceDriver: %s %s sending again", self._device_key,
                         self._kind.value)
            self._failed_in_a_row, self._told = 0, False
            return
        self._failed_in_a_row += 1
        if self._told or self._failed_in_a_row < self._tell_after:
            frame_log.debug("SourceDriver: %s %s failed %d in a row",
                            self._device_key, self._kind.value,
                            self._failed_in_a_row)
            return
        self._told = True
        line, kind = NOTICES[self._kind]
        log.warning("SourceDriver: %s %s has sent nothing for %d ticks (%s) -- "
                    "telling the UIs; still trying", self._device_key,
                    self._kind.value, self._failed_in_a_row, message)
        self._app.events.publish(ErrorOccurred(
            message=f"{line} -- {message}", kind=kind, key=self._device_key))


#: What :func:`cadence_owner` calls a box over the theme.
BOX_OWNER = "box"


def cadence_owner(app: App, key: str) -> str | None:
    """Which OTHER producer already drives this panel, if any.

    A reactive render is only worth dispatching when nothing else is
    writing the device.  When something is, the extra frame is at best
    redundant and at worst visible:

    * **game** — game mode holds the panel; its own task draws the only
      frames it shows, as the C#'s ``Timer_event`` draws nothing else
      (FormCZTV.cs:2988).
    * **screencast** — its tick composes background + mask + metrics at
      capture rate, and its background is the captured region while this
      one's is the theme.  Two DIFFERENT pictures, so at ~7 fps capture
      against a 2 s sensor tick the panel alternated ~13 screen frames
      with one theme frame: the "blinking metrics mask" (2026-09-14).
    * **video** — the animation tick already sends at the video's fps.
    * **stream / module** — a web source or a module's source (an audio
      visual): its driver
      sends at its own rate, the theme's overlay composited on top.
      Measured on the mock 320x320 panel: 40 composites and 40 wire writes
      per 40 ``SensorsUpdated``, on top of the tick's own.

    * **box** -- a module's picture in a box over the theme
      (``DeviceSettings.inset``): :class:`InsetDriver` sends the theme
      frame, box included, at the module's rate.  LAST, because every
      owner above already draws the box into its own frames -- or, for
      a live picture, covers the theme it would sit on.

    ``paused`` is load-bearing, not a detail.  ``lcd_handler.play_pause``
    stops the animation timer while ``PauseVideo`` leaves the ``Playback``
    in place, so a paused video has a playback and NO producer.  Keying
    off the playback's existence would freeze a paused panel's clock and
    metrics — which is why this reads the flag and not the object.

    Gating the PRODUCER, not the send: metrics still refresh, and sooner,
    because the owning tick composes them faster than the sensor cadence.

    The LED animation loop deliberately does NOT appear here.  It answers
    a different question: its devices are filtered out of the device-wide
    branch above, because a reactive LED render is not a duplicate but a
    deliberately different one — ``RenderLed(advance=False)`` HOLDS the
    carousel so a slider drag cannot race the metric page forward (#193 /
    #202).  Folding it in would be duplication removal that changes
    behaviour.
    """
    if app.game_engaged(key):
        return "game"
    # A cast, a stream, a module's picture: whichever holds the slot.
    if (live := app.media.live_kind(key)) is not None:
        return live.value
    playback = app.media.playback(key)
    if playback is not None and playback.advancing:
        return "video"
    if app.media.inset(key) is not None:
        return BOX_OWNER
    frame_log.debug("cadence_owner: %s — nothing else owns this panel", key)
    return None


class InsetDriver(BaseSendTask):
    """Sends panel *key*'s theme frame, with its box, at the box's module
    rate -- only while the box is what drives the panel
    (:func:`cadence_owner`): under a playing video, that video's tick
    already draws the box into every frame it sends."""

    KEY_PREFIX = "inset:"
    FIXED_BEAT = True
    DEFAULT_INTERVAL_S = 1.0 / 30

    def _run(self, now: float) -> float:
        """One frame, when the box owns the panel and a theme is on it."""
        from ..core.commands import RenderAndSend
        key = self._device_key
        owner = cadence_owner(self._app, key)
        if owner != BOX_OWNER or key in self._app.held \
                or key not in self._app.active_themes:
            frame_log.debug("InsetDriver: %s not sent (owner %s, held %s)", key,
                            owner, key in self._app.held)
            return self._interval
        result = self._app.dispatch(RenderAndSend(key=key))
        frame_log.debug("InsetDriver: %s sent ok=%s", key, result.ok)
        return self._interval


def inset_task_key(device_key: str) -> str:
    """The scheduler key for *device_key*'s box driver."""
    key = f"{InsetDriver.KEY_PREFIX}{device_key}"
    frame_log.debug("inset_task_key: %s -> %s", device_key, key)
    return key


def task_key(device_key: str, kind: PanelSource) -> str:
    """The scheduler key for *device_key*'s *kind* of live picture's driver."""
    key = f"{SourceDriver.KEY_PREFIX}{kind.value}:{device_key}"
    frame_log.debug("task_key: %s %s -> %s", device_key, kind.value, key)
    return key


def live_task_keys(device_key: str) -> tuple[str, ...]:
    """Every live-picture driver key *device_key* can have -- what letting the
    panel go removes."""
    keys = (*(task_key(device_key, kind) for kind in NOTICES),
            inset_task_key(device_key))
    log.debug("live_task_keys: %s -> %s", device_key, keys)
    return keys
