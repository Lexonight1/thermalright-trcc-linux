"""A web video URL as a live frame source -- the media player's web half.

The screencast already is a live source: a driver ticks, a Command takes one
frame, ``SendScreencastFrame`` composites it under the theme and sends it.  A
URL is the same with ffmpeg in place of the screen grab.  This reader runs
ffmpeg on the URL in real time and keeps the NEWEST frame; the App's
``SourceDriver`` takes it each tick (``CaptureSourceFrame``).

Local files keep ``FfmpegTool.decode``, which reads a whole video.  A
stream never ends, and a slow download would outlast the 30 s IPC timeout, so
a URL cannot take that path -- and ``Path("https://x")`` collapses its ``//``.

Frames are letterboxed to the panel's size so every one is the same number of
bytes; the compositor fits them again under the theme.  A finite remote file
restarts at its end, as a local media player loops.  A stream that fails
before its first frame reports why, through :meth:`StreamReader.latest`.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import IO
from urllib.parse import urlsplit

from ...core import toolchain
from ...core.logs import per_frame
from ...core.models import SUBPROCESS_NO_WINDOW, RawFrame
from ...core.ports import CaptureNotReady, FrameSource

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: What ffmpeg may open on a stream's behalf -- everything an http(s), HLS or
#: RTSP source needs, and nothing that reads the local disk (``file``,
#: ``concat``, ``subfile`` ...).  The scheme allow-list stops a ``file://`` URL
#: at the door; this stops a playlist that points ffmpeg at one.
_PROTOCOLS = "http,https,tcp,tls,crypto,hls,rtsp,rtp,udp,httpproxy"
#: Give up on a silent connection after this long (ffmpeg wants microseconds).
_RW_TIMEOUT_US = 10_000_000
#: Pause between restarts, so a source that ends instantly cannot spin.
_RESTART_S = 1.0


class StreamReader(FrameSource):
    """ffmpeg reading *url* in real time; the newest frame, on demand -- a
    panel's live picture, like a module's (its ``name`` is the url)."""

    def __init__(self, url: str, size: tuple[int, int], fps: int, *,
                 explain: Callable[[str], str] | None = None) -> None:
        log.info("StreamReader: %s at %dx%d, %d fps", url, *size, fps)
        #: The tool's reading of a missing codec (``FfmpegTool._explain``),
        #: asked before this file's own words for a link that holds no video.
        self._explain = explain
        self.url = self.name = url
        self._size = size
        self._fps = fps
        self._latest: RawFrame | None = None
        self._error = ""
        self._stop = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="trcc-stream")
        self._thread.start()

    def latest(self) -> RawFrame:
        """The newest frame; ``CaptureNotReady`` while buffering, ``OSError``
        once the source has failed."""
        if self._error:
            raise OSError(self._error)
        if self._latest is None:
            raise CaptureNotReady(f"{self.url}: buffering")
        frame_log.debug("StreamReader.latest: %s", self.url)
        return self._latest

    def close(self) -> None:
        """Stop ffmpeg and the reader thread (idempotent)."""
        log.info("StreamReader.close: %s", self.url)
        self._stop.set()
        if (proc := self._proc) is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._thread.join(timeout=3)

    def _command(self) -> list[str]:
        w, h = self._size
        log.debug("_command: %s", self.url)
        return [toolchain.executable("ffmpeg"),
                "-hide_banner", "-loglevel", "error",
                "-protocol_whitelist", _PROTOCOLS,
                "-rw_timeout", str(_RW_TIMEOUT_US),
                "-re", "-i", self.url, "-an",
                "-vf", (f"fps={self._fps},"
                        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"),
                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]

    def _run(self) -> None:
        """Read until closed: loop a source that ends, stop one that fails."""
        log.debug("_run: %s", self.url)
        while not self._stop.is_set():
            frames, code, stderr = self._one_run()
            if self._stop.is_set():
                return
            if frames == 0:
                self._error = ((self._explain(stderr) if self._explain else "")
                               or explain_failure(self.url, stderr))
                log.warning("StreamReader: %s: no picture (ffmpeg exit %d): %s",
                            self.url, code, stderr)
                return
            log.info("StreamReader: %s ended after %d frames (exit %d) — "
                     "restarting", self.url, frames, code)
            time.sleep(_RESTART_S)

    def _one_run(self) -> tuple[int, int, str]:
        """One ffmpeg process: ``(frames read, exit code, stderr tail)``.

        stderr is drained as it comes, keeping the last few lines: left in a
        pipe, a source that logs an error per frame fills it and stalls ffmpeg.
        """
        w, h = self._size
        frame_bytes = w * h * 3
        frames = 0
        self._proc = proc = subprocess.Popen(
            self._command(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=SUBPROCESS_NO_WINDOW)
        assert proc.stdout is not None and proc.stderr is not None
        # Six, not three: a host that cannot be found prints its reason four
        # lines from the end, and three left it out (explain_failure).
        tail: deque[bytes] = deque(maxlen=6)
        drain = threading.Thread(target=_drain, args=(proc.stderr, tail),
                                 daemon=True, name="trcc-stream-stderr")
        drain.start()
        while not self._stop.is_set():
            data = proc.stdout.read(frame_bytes)
            if len(data) < frame_bytes:
                break
            self._latest = RawFrame(data=data, width=w, height=h)
            frames += 1
        if self._stop.is_set() and proc.poll() is None:
            # ``close`` can land before this run's process existed; a live
            # stream never ends by itself, so it is stopped here too.
            proc.terminate()
        code = proc.wait()
        drain.join(timeout=2)
        proc.stdout.close()    # else every restart leaks the pipes' fds
        proc.stderr.close()
        text = b" ".join(tail).decode(errors="replace").strip()
        log.debug("_one_run: %s frames=%d exit=%d", self.url, frames, code)
        return frames, code, text


#: ffmpeg's words when the link holds no video.  A web page -- YouTube, any
#: site -- reads as "Invalid data found"; YouTube's embed page, and a host
#: that cannot be found, as "Input/output error" (the second with its own
#: resolve line first, matched before this).  Measured, ffmpeg 7.1, 2026-11-03.
_NOT_A_VIDEO = ("Invalid data found", "Input/output error")


def explain_failure(url: str, stderr: str) -> str:
    """Why *url* gave no picture, in plain words, from ffmpeg's *stderr*.

    ffmpeg's own text stays in the log; this is what the user is shown.  An
    error not measured here is quoted rather than guessed at.
    """
    host = urlsplit(url).hostname or url
    if status := re.search(r"Server returned (\w{3}\b.*?)(?= \(| Error opening|$)", stderr):
        reason = f'{host} answered "{status.group(1)}": the link is wrong, expired or private.'
    elif name := re.search(r"Failed to resolve hostname (\S+?):", stderr):
        reason = f"Can't find {name.group(1)}: check the address and your internet connection."
    elif "Connection refused" in stderr:
        reason = f"{host} refused the connection: nothing is serving video at that address."
    elif "Connection timed out" in stderr:
        reason = f"{host} didn't answer: check the address and your connection."
    elif any(words in stderr for words in _NOT_A_VIDEO):
        reason = (f"There's no video at {url}. A YouTube or other web page isn't a video: "
                  "use a direct link to the file (.mp4, .m3u8) or a stream (rtsp://).")
    else:
        reason = f"No picture from {url}" + (f": ffmpeg said {stderr}" if stderr else ".")
    log.info("explain_failure: %s -> %s", url, reason)
    return reason


def _drain(stream: IO[bytes], tail: deque[bytes]) -> None:
    """Read *stream* to its end, keeping its last lines in *tail*."""
    log.debug("_drain: start")
    for line in stream:
        tail.append(line.strip())
