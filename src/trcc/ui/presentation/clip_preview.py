"""A window's trimmer preview -- the App runs it; this only asks and draws.

The ffmpeg and its frames live in the App (``services/clip_preview.py``,
through the ``VideoTool``), so the preview works the same in every window,
in-process or over the daemon socket.  This is what the two skins hold: the
same five verbs they always called (play / still / poll / stop, and the
``active`` / ``shown`` they read), each now one Command on the bus.

Toolkit-free on purpose: a skin owns the timer and turns the JPEG bytes it is
handed into a picture.  The frames are composed by the export's own filter
chain, so the preview shows what Export encodes and neither skin composes a
copy of it.
"""
from __future__ import annotations

import logging
import uuid

from ...core.commands import PreviewClip, StopClipPreview, TickClipPreview
from ...core.logs import per_frame
from ...core.models import VideoExportRequest
from ...core.ports import CommandBus

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class ClipPreview:
    """One window's preview, by a token of its own -- two trimmers open at once
    never stop each other's."""

    def __init__(self, app: CommandBus) -> None:
        self._app = app
        self._token = f"clip-{uuid.uuid4().hex[:12]}"
        self._shown = 0
        self._active = False
        log.debug("ClipPreview.__init__: %s", self._token)

    @property
    def shown(self) -> int:
        """0-based index, in the playing range, of the frame last handed out."""
        frame_log.debug("ClipPreview.shown: %d", self._shown)
        return self._shown

    @property
    def active(self) -> bool:
        """Whether a preview or a still was in progress at the last answer."""
        frame_log.debug("ClipPreview.active: %s", self._active)
        return self._active

    def play(self, req: VideoExportRequest, box: tuple[int, int]) -> None:
        """Decode ``req``'s range at ``req.fps`` into *box*, in the App."""
        log.info("ClipPreview.play: %d-%d ms at %d fps into %dx%d",
                 req.start_ms, req.end_ms, req.fps, *box)
        self._start(req, box, still=False)

    def still(self, req: VideoExportRequest, box: tuple[int, int]) -> None:
        """Grab the one frame at ``req.start_ms``, in the App."""
        log.debug("ClipPreview.still: %d ms into %dx%d", req.start_ms, *box)
        self._start(req, box, still=True)

    def poll(self) -> bytes | None:
        """The next JPEG to show, or ``None`` to hold the current picture."""
        result = self._app.dispatch(TickClipPreview(token=self._token))
        self._shown, self._active = result.shown, result.active
        frame_log.debug("ClipPreview.poll: %d bytes shown=%d active=%s",
                        len(result.image), result.shown, result.active)
        return result.image or None

    def stop(self) -> None:
        """End this window's preview in the App."""
        log.debug("ClipPreview.stop: %s", self._token)
        self._app.dispatch(StopClipPreview(token=self._token))
        self._active = False

    def _start(self, req: VideoExportRequest, box: tuple[int, int], *,
               still: bool) -> None:
        result = self._app.dispatch(PreviewClip(
            token=self._token, path=req.source, start_ms=req.start_ms,
            end_ms=req.end_ms, box_w=box[0], box_h=box[1],
            target_w=req.target_w, target_h=req.target_h, rotation=req.rotation,
            fit_mode=req.fit_mode, fps=req.fps, still=still))
        self._active = result.ok
        if not result.ok:
            log.warning("ClipPreview._start: %s", result.message)
