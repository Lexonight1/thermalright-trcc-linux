"""``ffmpeg`` / ``ffprobe`` as TRCC's ``VideoTool`` -- the one place they run.

Services ask the port; none starts a program itself.  Each call carries
``SUBPROCESS_NO_WINDOW`` so Windows never flashes a console -- two of the five
probes this replaces did not, and every video load flashed one there; nor did
the decode, which moved here from ``services/media.py`` unchanged otherwise.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from ...core import toolchain
from ...core.errors import ThemeError
from ...core.geometry import largest_fit_size
from ...core.logs import per_frame
from ...core.models import SUBPROCESS_NO_WINDOW, VideoDecodeRequest, VideoProbe
from ...core.ports import FrameSource, VideoDecode, VideoTool
from .stream import StreamReader, _drain

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A probe reads a container's header; a file that takes longer is not one.
PROBE_TIMEOUT_S = 10

_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_MJPEG_QUALITY = 3       # ffmpeg -q:v; 2 = best .. 31 = worst

#: How much of ffmpeg's output one read takes, at most (``read1`` returns what
#: has arrived, so the first frames are not held back to fill it).
_CHUNK_BYTES = 1 << 18


class FfmpegTool(VideoTool):
    """The ``ffmpeg`` and ``ffprobe`` programs, found by ``core.toolchain``.
    *install_hint* names the package that brings one, for the error a missing
    one raises (``Platform.software_install_hint``)."""

    def __init__(self, install_hint: Callable[[str], str]
                 = toolchain.generic_install_hint) -> None:
        log.debug("FfmpegTool.__init__")
        self._install_hint = install_hint

    def probe(self, path: Path) -> VideoProbe:
        """One ``ffprobe`` for everything a caller needs to know about *path*."""
        log.debug("FfmpegTool.probe: %s", path)
        if not toolchain.present("ffprobe"):
            log.info("FfmpegTool.probe: no ffprobe -- %s is unknown", path.name)
            return VideoProbe()
        cmd = [toolchain.executable("ffprobe"), "-v", "error",
               "-select_streams", "v:0",
               "-show_entries", "stream=width,height,r_frame_rate,nb_frames"
               ":format=duration",
               "-of", "json", str(path)]
        try:
            ran = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT_S,
                                 check=False, creationflags=SUBPROCESS_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("FfmpegTool.probe: ffprobe failed on %s (%s)", path.name, e)
            return VideoProbe()
        if ran.returncode != 0:
            log.warning("FfmpegTool.probe: ffprobe returned %d for %s",
                        ran.returncode, path.name)
            return VideoProbe()
        try:
            found = _read_probe(json.loads(ran.stdout or b"{}"))
        except (ValueError, TypeError, AttributeError) as e:
            log.warning("FfmpegTool.probe: unreadable ffprobe output for %s (%s)",
                        path.name, e)
            return VideoProbe()
        log.info("FfmpegTool.probe: %s -> %s", path.name, found)
        return found

    def decode(self, request: VideoDecodeRequest, frames: list[bytes], *,
               on_done: Callable[[], None],
               on_error: Callable[[str], None] | None = None) -> VideoDecode:
        """Start ffmpeg filling *frames* on a thread (#264); returns at once.
        ``ThemeError`` now when it cannot start at all."""
        cmd = decode_command(request, self._install_hint)
        try:
            decode = FfmpegDecode(cmd, frames, request.path.name,
                                  on_done=on_done, on_error=on_error)
        except FileNotFoundError as e:
            raise ThemeError("ffmpeg not found on PATH") from e
        log.info("FfmpegTool.decode: %s decoding in the background", request.path.name)
        return decode

    def stream(self, url: str, size: tuple[int, int], fps: int) -> FrameSource:
        """ffmpeg reading *url* live (``StreamReader``)."""
        log.info("FfmpegTool.stream: %s at %dx%d, %d fps", url, *size, fps)
        return StreamReader(url, size, fps)


def _read_probe(data: dict[str, Any]) -> VideoProbe:
    """ffprobe's JSON as a ``VideoProbe``.  A container that records no length
    has one from its frame count and rate, as the gui's trimmer worked it out."""
    stream = (data.get("streams") or [{}])[0]
    fps = _rate(stream.get("r_frame_rate"))
    frames = _number(stream.get("nb_frames"), int)
    duration = _number((data.get("format") or {}).get("duration"), float)
    if not duration and frames and fps:
        duration = frames / fps
    log.debug("_read_probe: %s", data)
    return VideoProbe(duration_s=duration or None,
                      width=_number(stream.get("width"), int),
                      height=_number(stream.get("height"), int),
                      fps=fps, frames=frames)


def _number(value: Any, kind: type[int] | type[float]) -> Any:
    """*value* as *kind*, 0 when absent or not a number ("N/A")."""
    try:
        found = kind(value) if value not in (None, "", "N/A") else kind(0)
    except (TypeError, ValueError):
        found = kind(0)
    log.debug("_number: %r -> %r", value, found)
    return found if found > 0 else kind(0)


def _rate(value: Any) -> float:
    """A rate like ``"30000/1001"`` as frames per second, 0 when unknown."""
    num, _, den = str(value or "").partition("/")
    found = _number(num, float) / (_number(den, float) or 1.0) if den else _number(num, float)
    log.debug("_rate: %r -> %.3f", value, found)
    return found


def decode_command(req: VideoDecodeRequest,
                   install_hint: Callable[[str], str]) -> list[str]:
    """The ffmpeg command for *req* -- decoding at its native size where it
    asks.  ``ThemeError`` when it cannot be built."""
    if not req.path.exists():
        raise ThemeError(f"Video path does not exist: {req.path}")
    if not toolchain.present("ffmpeg"):
        raise ThemeError(toolchain.missing("ffmpeg",
                                           install_hint("ffmpeg")))

    native: tuple[int, int] | None = None
    if req.size is None:
        native = req.native
        if native is None:
            # ffprobe failed (missing / unreadable / unknown codec).
            # Without dims we can't chunk the raw pipe — fail loudly
            # rather than guess.
            raise ThemeError(
                f"Could not probe native size of {req.path.name} "
                "(ffprobe failed) — install ffprobe or pass an "
                "explicit decode size",
            )
        w, h = (largest_fit_size(native, req.fit_within)
                if req.fit_within is not None else native)
        log.info("decode_command: %s native size %dx%d -> decode %dx%d (%s)",
                 req.path.name, *native, w, h,
                 "no rescale" if (w, h) == native
                 else f"the largest a fit mode draws on {req.fit_within}")
    else:
        w, h = req.size

    # Guard the frame-chunking math: frame_bytes = w*h*3, and a zero
    # dimension makes ``len(raw) % frame_bytes`` / ``// frame_bytes``
    # raise ZeroDivisionError below.  Fail loudly with the module's
    # own error — same "rather than guess" stance as the ffprobe path.
    if w <= 0 or h <= 0:
        raise ThemeError(
            f"Invalid decode size {w}x{h} for {req.path.name} — "
            "width and height must both be positive"
        )

    cmd: list[str] = [toolchain.executable("ffmpeg"),
                      "-hide_banner", "-loglevel", "error"]
    if req.rotation_degrees:
        cmd += ["-display_rotation", str(req.rotation_degrees)]
    if req.duration_s:
        cmd += ["-t", f"{req.duration_s:.3f}"]
    cmd += [
        "-i", str(req.path),
        "-r", str(req.fps),
    ]
    # Only emit -s when the output differs from the source: an explicit
    # size, or a native decode scaled down to what the fit modes need.
    if req.size is not None or (w, h) != native:
        cmd += ["-s", f"{w}x{h}"]
    # Ask ffmpeg for ENCODED frames rather than rawvideo.  Counter-
    # intuitively this is also FASTER: pushing ~45 MB of JPEG through the
    # pipe beats pushing 3.1 GB of raw RGB24, and the pipe was always the
    # bottleneck.  Measured on 897 real frames at 1600x720: 2.77s -> 0.58s
    # to decode, held in 45 MB instead of 3100 MB.  q:v 3 keeps the loss
    # under what an RGB565 panel can show — mean error 0.55/255 against
    # quantisation steps of 4-8.  (#264, #256)
    cmd += [
        "-c:v", "mjpeg",
        "-q:v", str(_MJPEG_QUALITY),
        "-f", "image2pipe",
        "pipe:1",
    ]

    log.info("decode_command: %s at %dx%d: %s", req.path.name, w, h, " ".join(cmd))
    return cmd


class FfmpegDecode(VideoDecode):
    """ffmpeg decoding a video into a list of frames, on a thread.

    #264: ``PlayVideo`` decoded every frame before it returned, and the gui
    dispatches it on its UI thread -- a 4K video froze the window for ~9 s, and
    the CLI and API waited the same way.  Now the caller waits for the first
    frames (:meth:`wait_for`) and playback starts while the rest arrives;
    *on_done* tells the owner when there are no more.

    ffmpeg is started HERE, on the caller's thread, so one that cannot start
    raises now.  stderr is drained as it comes (``stream._drain``): left
    in a pipe it fills and stalls ffmpeg.  A decode that fails partway keeps
    what it decoded -- the panel keeps playing it -- and reports once through
    *on_error*; one that fails before any frame leaves ``error`` for the
    caller to raise.  :meth:`cancel` stops it (replace, ``StopVideo``, close).
    """

    def __init__(self, cmd: list[str], frames: list[bytes], name: str, *,
                 on_done: Callable[[], None],
                 on_error: Callable[[str], None] | None = None) -> None:
        log.info("FfmpegDecode: %s", name)
        self._frames, self._name = frames, name
        self._on_done, self._on_error = on_done, on_error
        self._error = ""
        self._stop = threading.Event()
        self._done = threading.Event()
        self._grew = threading.Condition()
        self._began = time.monotonic()
        self._proc: Any = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           creationflags=SUBPROCESS_NO_WINDOW)
        self._tail: deque[bytes] = deque(maxlen=3)
        self._drain = threading.Thread(target=_drain, args=(self._proc.stderr, self._tail),
                                       daemon=True, name="trcc-decode-stderr")
        self._drain.start()
        self._thread = threading.Thread(target=self._run, daemon=True, name="trcc-decode")
        self._thread.start()

    def _run(self) -> None:
        log.debug("FfmpegDecode._run: %s", self._name)
        splitter, proc, said_first = _JpegFrames(), self._proc, False
        while not self._stop.is_set():
            chunk = proc.stdout.read1(_CHUNK_BYTES)
            if not chunk:
                break
            if new := splitter.feed(chunk):
                with self._grew:
                    self._frames.extend(new)
                    self._grew.notify_all()
                if not said_first:
                    said_first = True
                    log.info("FfmpegDecode: %s first frame after %.2f s",
                             self._name, time.monotonic() - self._began)
        if self._stop.is_set() and proc.poll() is None:
            proc.terminate()
        code = proc.wait()
        self._drain.join(timeout=2)
        proc.stdout.close()
        proc.stderr.close()
        self._finish(code, splitter.pending)

    def _finish(self, code: int, pending: int) -> None:
        """Settle the decode: tell the owner it ended, say how."""
        frames = len(self._frames)
        stopped = self._stop.is_set()
        if not stopped and code != 0:
            tail = b" ".join(self._tail).decode(errors="replace").strip()
            self._error = f"ffmpeg exit {code}" + (f": {tail[:500]}" if tail else "")
        if pending and not stopped:
            log.warning("FfmpegDecode: %s ended with %d byte(s) of a partial frame "
                        "-- dropped", self._name, pending)
        self._on_done()
        with self._grew:
            self._done.set()
            self._grew.notify_all()
        log.info("FfmpegDecode: %s %s -- %d frame(s) in %.2f s%s", self._name,
                 "cancelled" if stopped else "finished", frames,
                 time.monotonic() - self._began, f" ({self._error})" if self._error else "")
        if self._error and frames and self._on_error is not None:
            self._on_error(f"{self._name}: decoding stopped after {frames} frame(s), "
                           f"which keep playing -- {self._error}")

    def _arrived(self, frames: int) -> bool:
        ready = len(self._frames) >= frames or self._done.is_set()
        frame_log.debug("FfmpegDecode._arrived: %d -> %s", frames, ready)
        return ready

    def wait_for(self, frames: int, timeout: float) -> int:
        """Until *frames* have arrived or the decode ended; how many there are."""
        with self._grew:
            self._grew.wait_for(partial(self._arrived, frames), timeout)
        count = len(self._frames)
        log.debug("FfmpegDecode.wait_for: %s %d of %d", self._name, count, frames)
        return count

    def wait_done(self, timeout: float | None = None) -> bool:
        """Until the decode ended; whether it did."""
        done = self._done.wait(timeout)
        log.debug("FfmpegDecode.wait_done: %s -> %s", self._name, done)
        return done

    @property
    def error(self) -> str:
        log.debug("FfmpegDecode.error: %s %r", self._name, self._error)
        return self._error

    @property
    def done(self) -> bool:
        frame_log.debug("FfmpegDecode.done: %s", self._done.is_set())
        return self._done.is_set()

    def cancel(self) -> None:
        """Stop ffmpeg and the reader (idempotent)."""
        log.info("FfmpegDecode.cancel: %s", self._name)
        self._stop.set()
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._thread.join(timeout=3)


class _JpegFrames:
    """ffmpeg's ``image2pipe`` mjpeg output, split into one blob per frame as
    the bytes arrive -- a frame, or its EOI marker, may straddle two reads.

    Frames are delimited by SOI (``ff d8``) / EOI (``ff d9``).  Inside
    entropy-coded data a literal ``ff`` is byte-stuffed as ``ff 00``, so a bare
    EOI cannot appear mid-scan; the one thing that could fool this is an
    embedded EXIF thumbnail carrying its own JPEG, which ffmpeg's mjpeg encoder
    does not emit.  Verified against 897 real frames at four quality levels --
    897 of 897 each time -- but that is evidence about ffmpeg's output, not a
    proof, so bytes left over at the end are logged, never passed off as a frame.
    """

    def __init__(self) -> None:
        log.debug("_JpegFrames.__init__")
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        """The frames *chunk* completed; a partial frame waits for the next."""
        self._buffer += chunk
        frames: list[bytes] = []
        pos = 0
        while (start := self._buffer.find(_JPEG_SOI, pos)) >= 0:
            end = self._buffer.find(_JPEG_EOI, start + 2)
            if end < 0:
                pos = start                 # this frame is still arriving
                break
            frames.append(bytes(self._buffer[start:end + 2]))
            pos = end + 2
        else:
            # No SOI after pos: keep a lone trailing ff, which may be half of one.
            pos = len(self._buffer) - (1 if self._buffer.endswith(b"\xff") else 0)
        del self._buffer[:pos]
        frame_log.debug("_JpegFrames.feed: %d frame(s), %d byte(s) pending",
                        len(frames), len(self._buffer))
        return frames

    @property
    def pending(self) -> int:
        """Bytes received that are not yet a whole frame."""
        frame_log.debug("_JpegFrames.pending: %d", len(self._buffer))
        return len(self._buffer)
