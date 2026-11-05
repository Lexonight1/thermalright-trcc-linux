"""The video trimmer's preview, made the way the C# makes it -- in the App.

``UCVideoCut`` (C# 2.1.8) never seeks per frame.  Preview starts ONE ffmpeg
that decodes the selected range, at the chosen frame rate and already
composed for the panel, into numbered files, and its timer shows the files
already written (``UCVideoCut.cs:1507-1537``, ``Timer_event`` :287-353).  A
held marker refreshes one still (``GetOneImage``, :1580-1630), and pressing
one kills any preview ffmpeg first (``KillFFmpegYulan``, :262-285).

Both skins started one ffmpeg process PER 41 ms TICK on the GUI thread
instead -- 70-80 ms a grab on a 320x320 clip and 160-290 ms on 1080p, so the
window froze while previewing and showed 3-6 frames a second.  That came in
with the OpenCV removal (aa6e02cb, 2026-02-08), which swapped an open capture
for a process per call and kept the timer.

It ran in each window until 2026-11-05 (ffmpeg port, increment 5).  Now the
App runs it, through the ``VideoTool`` (``PreviewRun``); a window dispatches
``PreviewClip`` / ``TickClipPreview`` / ``StopClipPreview`` and draws the JPEG
it is handed.  Measured first (``dev/tools/bench_clip_preview.py``): ~1 ms a
tick over the socket against 11 ms of ffmpeg per frame.  Each window's
preview is its own, by the token it chose, so two trimmers never stop each
other's.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..core.logs import per_frame
from ..core.models import VideoExportRequest
from ..core.ports import PreviewRun, VideoTool

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


@dataclass
class _Preview:
    """One window's preview: the run in progress and where it has got to."""
    run: PreviewRun | None = None
    still: bool = False
    index: int = 0
    #: 0-based index, in the playing range, of the frame last handed out.
    shown: int = 0
    #: The newest still asked for while one was in flight.
    pending: tuple[VideoExportRequest, tuple[int, int]] | None = field(
        default=None, repr=False)
    #: Why the last run ended with no picture, for the window to say.
    error: str = ""

    @property
    def active(self) -> bool:
        """Whether a preview or a still is in progress."""
        frame_log.debug("_Preview.active: %s", self.run is not None)
        return self.run is not None


@dataclass(frozen=True, slots=True)
class PreviewFrame:
    """What one tick hands a window: the JPEG to show (None = keep the one on
    screen), the place of the frame last shown, and whether one still runs."""
    image: bytes | None = field(repr=False)
    shown: int
    active: bool
    #: Why the preview ended with no picture -- "" while all is well.
    error: str = ""


class ClipPreviews:
    """One background run at a time per window: a playing range, or one still.

    :meth:`play` stops whatever that window had running, as the C# does.
    :meth:`still` while another still is being grabbed only remembers the
    newest request and starts it when that grab ends, so a drag never piles up
    processes and always lands on where the mouse stopped.  :meth:`tick` hands
    back the next picture to show.  Dispatches arrive on the IPC server's
    threads, so every entry takes the lock.
    """

    def __init__(self, *, video_tool: VideoTool) -> None:
        log.debug("ClipPreviews.__init__")
        self._tool = video_tool
        self._lock = threading.Lock()
        self._previews: dict[str, _Preview] = {}
        #: Each source's probed size, asked once: a drag starts many stills.
        self._sizes: dict[Path, tuple[int, int]] = {}

    def play(self, token: str, req: VideoExportRequest, box: tuple[int, int]) -> None:
        """Decode ``req``'s range at ``req.fps`` into *box*, in the background."""
        log.info("ClipPreviews.play: %s %d-%d ms at %d fps into %dx%d", token,
                 req.start_ms, req.end_ms, req.fps, *box)
        with self._lock:
            self._start(self._preview(token), req, box, still=False)

    def still(self, token: str, req: VideoExportRequest, box: tuple[int, int]) -> None:
        """Grab the one frame at ``req.start_ms``, in the background."""
        log.debug("ClipPreviews.still: %s %d ms into %dx%d", token, req.start_ms, *box)
        with self._lock:
            preview = self._preview(token)
            if preview.still and preview.active:
                preview.pending = (req, box)
                return
            self._start(preview, req, box, still=True)

    def tick(self, token: str) -> PreviewFrame:
        """The next JPEG to show for *token*'s window, or None to hold.

        A finished preview loops back to its first frame, as the C# does.
        """
        with self._lock:
            preview = self._previews.get(token)
            if preview is None:
                frame_log.debug("ClipPreviews.tick: %s has no preview", token)
                return PreviewFrame(None, 0, False)
            image = self._next(preview)
            frame_log.debug("ClipPreviews.tick: %s frame=%s shown=%d active=%s", token,
                            image is not None, preview.shown, preview.active)
            return PreviewFrame(image, preview.shown, preview.active, preview.error)

    def stop(self, token: str) -> None:
        """End *token*'s run, forget any waiting still, remove its files."""
        log.debug("ClipPreviews.stop: %s", token)
        with self._lock:
            if (preview := self._previews.pop(token, None)) is not None:
                preview.pending = None
                self._end(preview)

    def close(self) -> None:
        """End every window's run -- the App is closing."""
        with self._lock:
            log.info("ClipPreviews.close: %d preview(s)", len(self._previews))
            for preview in self._previews.values():
                self._end(preview)
            self._previews.clear()

    # ── internals, under the lock ─────────────────────────────────────

    def _preview(self, token: str) -> _Preview:
        frame_log.debug("ClipPreviews._preview: %s", token)
        return self._previews.setdefault(token, _Preview())

    def _next(self, preview: _Preview) -> bytes | None:
        """The frame at the cursor once complete; loop, or settle, at the end."""
        run = preview.run
        if run is None:
            return None
        # Whether it had ended BEFORE the frame is looked for: ffmpeg can write
        # the frame and exit between the two, and read the other way round
        # that is "exited with no frame" -- a drag's last still, dropped.
        ended = run.ended
        if (data := run.frame(preview.index)) is None:
            if ended and not preview.still and preview.index > 0:
                frame_log.debug("ClipPreviews._next: end of %d frame(s) — loop",
                                preview.index)
                preview.index = 0
                return self._next(preview)
            if ended and preview.index == 0:
                preview.error = run.error
                log.warning("ClipPreviews._next: ffmpeg exited %s with no frame: %s",
                            run.exit_code, preview.error)
                self._end(preview)
                self._next_still(preview)
            return None
        frame_log.debug("ClipPreviews._next: frame %d (%d bytes)", preview.index, len(data))
        preview.shown = preview.index
        if preview.still:
            self._end(preview)
            self._next_still(preview)
        else:
            preview.index += 1
        return data

    def _next_still(self, preview: _Preview) -> None:
        """Start the still that waited for the one just finished, if any."""
        if preview.pending is None:
            return
        (req, box), preview.pending = preview.pending, None
        log.debug("ClipPreviews._next_still: %d ms", req.start_ms)
        self._start(preview, req, box, still=True)

    def _end(self, preview: _Preview) -> None:
        """Stop the run and remove its files."""
        if preview.run is not None:
            log.debug("ClipPreviews._end: closing the run")
            preview.run.close()
        preview.run = None

    def _start(self, preview: _Preview, req: VideoExportRequest,
               box: tuple[int, int], *, still: bool) -> None:
        self._end(preview)
        preview.still, preview.index, preview.error = still, 0, ""
        preview.run = self._tool.preview(req, box, self._size(req.source), still=still)
        log.debug("ClipPreviews._start: still=%s started=%s", still,
                  preview.run is not None)

    def _size(self, source: Path) -> tuple[int, int]:
        """*source*'s probed size, asked of the tool once."""
        if source not in self._sizes:
            self._sizes[source] = self._tool.probe(source).size or (0, 0)
        log.debug("ClipPreviews._size: %s -> %s", source.name, self._sizes[source])
        return self._sizes[source]
