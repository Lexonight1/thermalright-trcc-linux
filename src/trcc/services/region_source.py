"""A panel's screencast as a live picture -- the ``FrameSource`` the cast is.

The desktop capture is ONE object every casting panel shares (a portal session
on Wayland); what is each panel's own is its region of it.  So the cast's
picture is that region, grabbed when the panel asks -- and it sits in the
panel's live slot (``MediaService.open_live``) like a stream or a module's
picture, which is what lets one driver feed all three.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from ..core.logs import per_frame
from ..core.models import RawFrame
from ..core.ports import CaptureFailed, CaptureNotReady, FrameSource, ScreenCapture

log = logging.getLogger(__name__)
#: ``latest`` runs once per cast frame.
frame_log = per_frame(__name__)

#: A panel's saved cast: ``(x, y, w, h, audio)``, or None for none.
Region = tuple[int, int, int, int, bool]


class RegionSource(FrameSource):
    """The region *region* names, grabbed from the shared *capture*.

    *region* is read on every grab -- the panel's saved ``screencast_region``
    -- so moving the region moves a running cast, as it always has.  ``close``
    releases nothing shared: whether the capture stops is the App's to say,
    once no panel casts (``release_unused_cast``)."""

    name = "screencast"

    def __init__(self, capture: ScreenCapture,
                 region: Callable[[], Region | None]) -> None:
        log.info("RegionSource: %s", type(capture).__name__)
        self._capture = capture
        self._region = region

    def latest(self) -> RawFrame:
        """This tick's grab of the region.  ``CaptureNotReady`` while the
        capture is starting (a consent window) or the region is gone;
        ``CaptureFailed`` for a grab that errored -- the session can blink,
        and the cast carries on."""
        box = self._region()
        if box is None:
            raise CaptureNotReady("no screencast region")
        x, y, w, h = box[0], box[1], box[2], box[3]
        frame_log.debug("RegionSource.latest: %dx%d @ %d,%d", w, h, x, y)
        try:
            return self._capture.grab_region(x, y, w, h)
        except CaptureNotReady:
            raise
        except Exception as e:
            # Capture depends on tools and a desktop session that can vanish
            # under us (screen locked, portal revoked, grim uninstalled): this
            # tick failed, the next one tries again.
            raise CaptureFailed(
                f"screencast capture failed: {type(e).__name__}: {e}") from e

    def close(self) -> None:
        """Nothing of its own to stop -- the capture is shared."""
        log.info("RegionSource.close: the shared capture stays the App's")
