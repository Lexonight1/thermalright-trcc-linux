"""The video trimmer's preview, made the way the C# makes it -- shared by both skins.

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

Toolkit-free on purpose: this owns the process and the files; a skin owns the
timer and turns the JPEG bytes it is handed into a picture.  The frames are
composed by the EXPORTER's own filter chain (``preview_command``), so the
preview shows what Export encodes and neither skin composes a copy of it.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from ...core.logs import per_frame
from ...core.models import SUBPROCESS_NO_WINDOW, VideoExportRequest
from ...services.video_export import preview_command, probe_dimensions

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


def _frame_name(index: int) -> str:
    """ffmpeg's ``%05d.jpg`` for the 0-based *index* (ffmpeg counts from 1)."""
    frame_log.debug("_frame_name: %d", index)
    return f"{index + 1:05d}.jpg"


class ClipPreview:
    """One background ffmpeg at a time: a playing range, or one still.

    :meth:`play` kills whatever was running, as the C# does.  :meth:`still`
    while another still is being grabbed only remembers the newest request
    and starts it when that grab ends, so a drag never piles up processes and
    always lands on where the mouse stopped.  :meth:`poll` hands back the next
    picture to show, or ``None`` to keep the one on screen.
    """

    def __init__(self) -> None:
        log.debug("ClipPreview.__init__")
        self._proc: subprocess.Popen[bytes] | None = None
        self._dir: Path | None = None
        self._source_wh: tuple[int, int] = (0, 0)
        self._still = False
        self._index = 0
        self._shown = 0
        #: The newest still asked for while one was in flight.
        self._pending: tuple[VideoExportRequest, tuple[int, int]] | None = None

    @property
    def shown(self) -> int:
        """0-based index, in the playing range, of the frame last handed out."""
        frame_log.debug("ClipPreview.shown: %d", self._shown)
        return self._shown

    @property
    def active(self) -> bool:
        """Whether a preview or a still is in progress."""
        frame_log.debug("ClipPreview.active: %s", self._dir is not None)
        return self._dir is not None

    def load(self, source: Path) -> None:
        """A new clip: stop the old one and probe this one's size ONCE."""
        self.stop()
        self._source_wh = probe_dimensions(source)
        log.info("ClipPreview.load: %s is %dx%d", source.name, *self._source_wh)

    def play(self, req: VideoExportRequest, box: tuple[int, int]) -> None:
        """Decode ``req``'s range at ``req.fps`` into *box*, in the background."""
        log.info("ClipPreview.play: %d-%d ms at %d fps into %dx%d",
                 req.start_ms, req.end_ms, req.fps, *box)
        self._start(req, box, still=False)

    def still(self, req: VideoExportRequest, box: tuple[int, int]) -> None:
        """Grab the one frame at ``req.start_ms``, in the background."""
        log.debug("ClipPreview.still: %d ms into %dx%d", req.start_ms, *box)
        if self._still and self.active:
            self._pending = (req, box)
            return
        self._start(req, box, still=True)

    def poll(self) -> bytes | None:
        """The next JPEG to show, or ``None`` to hold the current picture.

        A file counts only once it is COMPLETE -- the next one exists or
        ffmpeg has exited -- so a frame being written is never read half-done.
        A finished preview loops back to its first frame, as the C# does.
        """
        if self._dir is None or self._proc is None:
            return None
        done = self._proc.poll() is not None
        current = self._dir / _frame_name(self._index)
        if not current.is_file():
            if done and not self._still and self._index > 0:
                frame_log.debug("ClipPreview.poll: end of %d frame(s) — loop",
                                self._index)
                self._index = 0
                return self.poll()
            if done and self._index == 0:
                log.warning("ClipPreview.poll: ffmpeg exited %s with no frame",
                            self._proc.returncode)
                self._end()
                self._next_still()
            return None
        if not done and not (self._dir / _frame_name(self._index + 1)).is_file():
            return None
        data = current.read_bytes()
        frame_log.debug("ClipPreview.poll: frame %d (%d bytes)", self._index,
                        len(data))
        self._shown = self._index
        if self._still:
            self._end()
            self._next_still()
        else:
            self._index += 1
        return data

    def stop(self) -> None:
        """Kill the running ffmpeg, forget any waiting still, remove the files."""
        log.debug("ClipPreview.stop: pending=%s", self._pending is not None)
        self._pending = None
        self._end()

    def _next_still(self) -> None:
        """Start the still that waited for the one just finished, if any."""
        if self._pending is None:
            return
        (req, box), self._pending = self._pending, None
        log.debug("ClipPreview._next_still: %d ms", req.start_ms)
        self._start(req, box, still=True)

    def _end(self) -> None:
        """Kill the running ffmpeg and remove its files."""
        if self._proc is not None and self._proc.poll() is None:
            log.debug("ClipPreview._end: killing ffmpeg pid %d", self._proc.pid)
            self._proc.kill()
            self._proc.wait()
        self._proc = None
        if self._dir is not None:
            log.debug("ClipPreview._end: removing %s", self._dir)
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def _start(self, req: VideoExportRequest, box: tuple[int, int], *,
               still: bool) -> None:
        self._end()
        self._dir = Path(tempfile.mkdtemp(prefix="trcc-preview-"))
        self._still = still
        self._index = 0
        cmd = preview_command(req, self._dir, box, self._source_wh, still=still)
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=SUBPROCESS_NO_WINDOW,
            )
        except OSError as e:
            log.warning("ClipPreview._start: cannot run ffmpeg: %s", e)
            self.stop()
            return
        log.debug("ClipPreview._start: pid %d still=%s", self._proc.pid, still)
