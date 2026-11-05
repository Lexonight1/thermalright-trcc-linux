"""``ffmpeg`` / ``ffprobe`` as TRCC's ``VideoTool`` -- the one place they run.

Services ask the port; none starts a program itself.  Each call carries
``SUBPROCESS_NO_WINDOW`` so Windows never flashes a console -- two of the five
probes this replaces did not, and every video load flashed one there; nor did
the decode, which moved here from ``services/media.py`` unchanged otherwise.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from ...core import toolchain
from ...core.errors import ThemeError, VideoExportError
from ...core.geometry import fit_rect_for_mode, largest_fit_size
from ...core.logs import per_frame
from ...core.models import (
    SUBPROCESS_NO_WINDOW,
    VideoDecodeRequest,
    VideoExportRequest,
    VideoProbe,
)
from ...core.ports import FrameSource, PreviewRun, VideoDecode, VideoTool
from .stream import StreamReader, _drain

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: A probe reads a container's header; a file that takes longer is not one.
PROBE_TIMEOUT_S = 10
#: An export decodes up to ``ZT_MAX_DURATION_MS`` of video at any source size.
EXPORT_TIMEOUT_S = 600
#: ffmpeg's words for a codec its build leaves out, measured 2026-11-05 on an
#: HEVC clip: upstream's, the same in 7.1 and 8.1 (Fedora 42 and 44
#: ffmpeg-free), then openSUSE's own (Tumbleweed 9.0.2, Leap 16 7.1.5).
_NO_DECODER = re.compile(r'no decoder found for: (\w+)|does not include a "(\w+)" decoder')

#: The cloud tiles: one frame, then a 120x120 GIF of the whole (short) video.
STILL_TIMEOUT_S = 10
TILE_TIMEOUT_S = 30

_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_MJPEG_QUALITY = 3       # ffmpeg -q:v; 2 = best .. 31 = worst

#: How much of ffmpeg's output one read takes, at most (``read1`` returns what
#: has arrived, so the first frames are not held back to fill it).
_CHUNK_BYTES = 1 << 18


class FfmpegTool(VideoTool):
    """The ``ffmpeg`` and ``ffprobe`` programs, found by ``core.toolchain``.
    *install_hint* names the package that brings one, for the error a missing
    one raises (``Platform.software_install_hint``); *full_ffmpeg_hint* the
    line that swaps in a build decoding every format, for the error a missing
    codec raises (``Platform.full_ffmpeg_hint``)."""

    def __init__(self, install_hint: Callable[[str], str]
                 = toolchain.generic_install_hint,
                 full_ffmpeg_hint: Callable[[], str] | None = None) -> None:
        log.debug("FfmpegTool.__init__")
        self._install_hint = install_hint
        self._full_ffmpeg_hint = full_ffmpeg_hint

    def _explain(self, stderr: str) -> str:
        """ffmpeg's *stderr* in plain words when its build lacks the video's
        codec -- an iPhone's HEVC on Fedora's ffmpeg-free -- with the way to
        get one that has it; "" for any other failure."""
        if (found := _NO_DECODER.search(stderr)) is None:
            log.debug("FfmpegTool._explain: not a missing codec")
            return ""
        codec = (found.group(1) or found.group(2)).upper()
        hint = self._full_ffmpeg_hint() if self._full_ffmpeg_hint else ""
        told = (f"this ffmpeg cannot play {codec} video -- "
                + (f"to get one that can: {hint}" if hint
                   else "install an ffmpeg build that includes it"))
        log.info("FfmpegTool._explain: %s", told)
        return told

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
            decode = FfmpegDecode(cmd, frames, request.path.name, on_done=on_done,
                                  on_error=on_error, explain=self._explain)
        except FileNotFoundError as e:
            raise ThemeError("ffmpeg not found on PATH") from e
        log.info("FfmpegTool.decode: %s decoding in the background", request.path.name)
        return decode

    def stream(self, url: str, size: tuple[int, int], fps: int) -> FrameSource:
        """ffmpeg reading *url* live (``StreamReader``)."""
        log.info("FfmpegTool.stream: %s at %dx%d, %d fps", url, *size, fps)
        return StreamReader(url, size, fps, explain=self._explain)

    def extract_frames(self, request: VideoExportRequest, out_dir: Path) -> None:
        """The clip as ``%04d.jpg`` in *out_dir*, through the same filter
        chain the trimmer's preview shows (``_composite_filters``)."""
        req = request
        vf, size_args = _composite_filters(req, self.probe(req.source).size or (0, 0))
        cmd: list[str] = [
            toolchain.executable("ffmpeg"),
            "-ss", f"{req.start_ms / 1000.0}",
            "-t", f"{(req.end_ms - req.start_ms) / 1000.0}",
            "-i", str(req.source),
            "-y",
            "-r", str(req.fps),
            *size_args,
        ]
        if vf:
            cmd.extend(["-vf", ",".join(vf)])
        cmd.extend(["-f", "image2", "-q:v", "5", str(out_dir / "%04d.jpg")])
        log.info("FfmpegTool.extract_frames: %s: %s", req.source.name, " ".join(cmd))
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=EXPORT_TIMEOUT_S,
                check=False, creationflags=SUBPROCESS_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as e:
            raise VideoExportError(
                f"ffmpeg timed out after {EXPORT_TIMEOUT_S}s.  Try a shorter clip.",
            ) from e
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            raise VideoExportError(
                self._explain(stderr)
                or f"ffmpeg exited {result.returncode}.  Last output:\n{stderr[-400:]}",
            )

    def still(self, video: Path, png: Path) -> bool:
        """*video*'s first frame as *png*."""
        log.info("FfmpegTool.still: %s -> %s", video.name, png)
        return _run_best_effort(
            [toolchain.executable("ffmpeg"),
             "-i", str(video), "-vframes", "1", "-y", str(png)],
            timeout=STILL_TIMEOUT_S, label=f"first-frame PNG for {video.name}",
        )

    def tile_gif(self, video: Path, gif: Path) -> bool:
        """A 120x120 8 fps GIF of *video*.

        Aspect kept, letterboxed in black, as the C# fits its tile PNGs to 120
        (``UCThemeWeb.SetThemeWeb``).  ``scale=120:120`` stretched a
        non-square video to the square; the gui's own copy of this chain had
        the fit, and the two had drifted.
        """
        log.info("FfmpegTool.tile_gif: %s -> %s", video.name, gif)
        return _run_best_effort(
            [toolchain.executable("ffmpeg"), "-i", str(video),
             "-vf", "scale=120:120:force_original_aspect_ratio=decrease,"
                    "pad=120:120:(ow-iw)/2:(oh-ih)/2:black,fps=8",
             "-loop", "0", "-y", str(gif)],
            timeout=TILE_TIMEOUT_S, label=f"animated GIF for {video.name}",
        )

    def lacks_decoders(self, codecs: tuple[str, ...]) -> tuple[str, ...]:
        """Asked of ``ffmpeg -decoders``: a codec with no decoder of its own
        name is one this build leaves out (Fedora's ffmpeg-free: hevc)."""
        if not toolchain.present("ffmpeg"):
            log.info("FfmpegTool.lacks_decoders: no ffmpeg to ask")
            return ()
        try:
            ran = subprocess.run([toolchain.executable("ffmpeg"), "-hide_banner", "-decoders"],
                                 capture_output=True, timeout=PROBE_TIMEOUT_S,
                                 check=False, creationflags=SUBPROCESS_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("FfmpegTool.lacks_decoders: could not ask ffmpeg (%s)", e)
            return ()
        names = {fields[1] for line in ran.stdout.decode(errors="replace").splitlines()
                 if len(fields := line.split()) > 1}
        lacking = tuple(codec for codec in codecs if codec not in names)
        log.info("FfmpegTool.lacks_decoders: %s of %s", lacking or "none", codecs)
        return lacking

    def preview(self, request: VideoExportRequest, box: tuple[int, int],
                source_wh: tuple[int, int], *, still: bool) -> PreviewRun | None:
        """ONE ffmpeg writing the preview's frames into a folder of its own."""
        folder = Path(tempfile.mkdtemp(prefix="trcc-preview-"))
        cmd = preview_command(request, folder, box, source_wh, still=still)
        # ffmpeg's complaints go to a file beside its frames, not a pipe: a
        # pipe nobody drains fills and stalls ffmpeg, and DEVNULL threw away
        # why an Apple video showed no picture.
        try:
            with (folder / _PREVIEW_ERRORS).open("wb") as errors:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                        stderr=errors,
                                        creationflags=SUBPROCESS_NO_WINDOW)
        except OSError as e:
            log.warning("FfmpegTool.preview: cannot run ffmpeg: %s", e)
            shutil.rmtree(folder, ignore_errors=True)
            return None
        log.info("FfmpegTool.preview: pid %d still=%s %s %d-%d ms into %dx%d",
                 proc.pid, still, request.source.name, request.start_ms,
                 request.end_ms, *box)
        return FfmpegPreview(proc, folder, explain=self._explain)


def _run_best_effort(cmd: list[str], *, timeout: float, label: str) -> bool:
    """Run *cmd* for a picture nothing waits on; whether it worked.  Any
    failure is a warning naming *label* -- the action, not the binary."""
    log.debug("_run_best_effort: %s", label)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                check=False, creationflags=SUBPROCESS_NO_WINDOW)
    except FileNotFoundError:
        log.warning("_run_best_effort: ffmpeg not on PATH — %s skipped", label)
        return False
    except subprocess.TimeoutExpired:
        log.warning("_run_best_effort: %s timed out after %ss", label, timeout)
        return False
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("_run_best_effort: %s failed: %s: %s", label, type(e).__name__, e)
        return False
    if result.returncode != 0:
        log.warning("_run_best_effort: %s rc=%d: %s", label, result.returncode,
                    result.stderr.decode("utf-8", errors="replace")[:200])
        return False
    return True


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
                 on_error: Callable[[str], None] | None = None,
                 explain: Callable[[str], str] | None = None) -> None:
        log.info("FfmpegDecode: %s", name)
        self._frames, self._name = frames, name
        self._on_done, self._on_error, self._explain = on_done, on_error, explain
        self._error = ""
        self._stop = threading.Event()
        self._done = threading.Event()
        self._grew = threading.Condition()
        self._began = time.monotonic()
        self._proc: Any = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           creationflags=SUBPROCESS_NO_WINDOW)
        # Six: a missing codec is named three lines from the end (_NO_DECODER).
        self._tail: deque[bytes] = deque(maxlen=6)
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
            self._error = ((self._explain(tail) if self._explain else "")
                           or f"ffmpeg exit {code}" + (f": {tail[:500]}" if tail else ""))
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


def _composite_filters(
    req: VideoExportRequest, source_wh: tuple[int, int],
) -> tuple[list[str], list[str]]:
    """ffmpeg ``-vf`` filters (and any ``-s``) that compose *req* as the panel
    shows it: rotate, then scale/crop/pad onto the panel canvas.

    ONE chain for the export and the trimmer's preview, so what the preview
    shows is what the export encodes.  A panel size of 0 (unknown) composes
    nothing beyond the rotation.  *source_wh* is the source's probed size
    (``VideoTool.probe``; ``(0, 0)`` unknown -- the panel is then filled).
    """
    log.debug("_composite_filters: %s rotation=%d fit=%s -> %dx%d",
              req.source.name, req.rotation, req.fit_mode, req.target_w,
              req.target_h)
    vf: list[str] = []
    if req.rotation == 90:
        vf.append("transpose=1")
    elif req.rotation == 180:
        vf.append("transpose=1,transpose=1")
    elif req.rotation == 270:
        vf.append("transpose=2")

    # The oracle hands ffmpeg a rect derived from the SOURCE's aspect and
    # composites it onto a panel-sized canvas; a bare ``-s panel`` stretches
    # anything that is not already the panel's shape (a 1920x1080 clip came
    # out 480x480, squashed 1.78x).  ``scale``+``pad`` is that composite in
    # one filter.  Rotation stays FIRST and the probed size is swapped to
    # match, because the C# reads post-rotation dimensions
    # (``buttonXuanzhuan_Click`` swaps bitAngleW/H at 90/270).
    if req.target_w <= 0 or req.target_h <= 0:
        log.debug("_composite_filters: panel size unknown — rotation only")
        return vf, []
    if req.rotation in (90, 270):
        source_wh = (source_wh[1], source_wh[0])
    panel = (req.target_w, req.target_h)
    if source_wh[0] > 0 and source_wh[1] > 0:
        fit = fit_rect_for_mode(source_wh, panel, req.fit_mode)
        # scale -> crop -> pad, ALWAYS all three, because the forced-axis
        # arms can overflow the canvas (a negative offset, which ``pad``
        # cannot express) while the auto arm never does.  Written as one
        # unconditional chain rather than a branch: on the auto path the
        # crop is the full rect and the pad does the letterboxing, exactly
        # as before; on a forced axis the crop takes the overflow and the
        # pad becomes the no-op.  Same filters, no arm to get wrong.
        vf.append(f"scale={fit.width}:{fit.height}")
        vf.append(
            f"crop={min(fit.width, req.target_w)}:"
            f"{min(fit.height, req.target_h)}:"
            f"{max(0, -fit.x)}:{max(0, -fit.y)}",
        )
        vf.append(
            f"pad={req.target_w}:{req.target_h}:"
            f"{max(0, fit.x)}:{max(0, fit.y)}",
        )
        size_args: list[str] = []
    else:
        # Without the source shape the aspect cannot be preserved.  Say so
        # rather than silently shipping a stretched clip.
        log.warning(
            "export_zt: source size unknown for %s — filling %dx%d, which "
            "STRETCHES a clip whose aspect differs (install ffprobe)",
            req.source, req.target_w, req.target_h,
        )
        size_args = ["-s", f"{req.target_w}x{req.target_h}"]
    return vf, size_args


def preview_command(
    req: VideoExportRequest, out_dir: Path, box: tuple[int, int],
    source_wh: tuple[int, int], *, still: bool = False,
) -> list[str]:
    """The trimmer's preview of *req*: composed as the export encodes it,
    shrunk into *box*, written as numbered JPEGs into *out_dir*.

    The C# previews this way (``UCVideoCut.cs:1507-1537``): ONE ffmpeg
    decodes the selected range at the chosen rate into numbered files, and
    its timer shows the ones already written.  *still* writes only the frame
    at ``req.start_ms`` -- the C#'s ``GetOneImage`` (:1580-1630).
    """
    log.debug("preview_command: %s %d-%d ms fps=%d box=%s still=%s",
              req.source.name, req.start_ms, req.end_ms, req.fps, box, still)
    vf, _ = _composite_filters(req, source_wh)
    vf.append(f"scale={box[0]}:{box[1]}:force_original_aspect_ratio=decrease")
    cmd = [toolchain.executable("ffmpeg"), "-v", "error",
           "-ss", f"{req.start_ms / 1000.0}"]
    if not still:
        cmd += ["-t", f"{(req.end_ms - req.start_ms) / 1000.0}"]
    cmd += ["-i", str(req.source), "-y", "-vf", ",".join(vf)]
    cmd += ["-frames:v", "1"] if still else ["-r", str(req.fps)]
    cmd += ["-q:v", "5", "-f", "image2", str(out_dir / "%05d.jpg")]
    return cmd


#: Where a preview ffmpeg's stderr goes, beside its numbered frames.
_PREVIEW_ERRORS = "ffmpeg.err"


def _frame_name(index: int) -> str:
    """ffmpeg's ``%05d.jpg`` for the 0-based *index* (ffmpeg counts from 1)."""
    frame_log.debug("_frame_name: %d", index)
    return f"{index + 1:05d}.jpg"


class FfmpegPreview(PreviewRun):
    """A preview ffmpeg and the folder it writes its numbered JPEGs into."""

    def __init__(self, proc: subprocess.Popen[bytes], folder: Path, *,
                 explain: Callable[[str], str] | None = None) -> None:
        log.debug("FfmpegPreview: pid %d into %s", proc.pid, folder)
        self._proc, self._folder, self._explain = proc, folder, explain

    def frame(self, index: int) -> bytes | None:
        """Complete = the next file exists, or ffmpeg has exited."""
        current = self._folder / _frame_name(index)
        ready = current.is_file() and (
            self.ended or (self._folder / _frame_name(index + 1)).is_file())
        frame_log.debug("FfmpegPreview.frame: %d ready=%s", index, ready)
        return current.read_bytes() if ready else None

    @property
    def ended(self) -> bool:
        frame_log.debug("FfmpegPreview.ended")
        return self._proc.poll() is not None

    @property
    def exit_code(self) -> int | None:
        frame_log.debug("FfmpegPreview.exit_code: %s", self._proc.returncode)
        return self._proc.poll()

    @property
    def error(self) -> str:
        """A missing codec in plain words (``FfmpegTool._explain``), else
        ffmpeg's own last line -- once it has failed; "" otherwise."""
        code = self._proc.poll()
        if not code:
            frame_log.debug("FfmpegPreview.error: none (exit %s)", code)
            return ""
        try:
            said = (self._folder / _PREVIEW_ERRORS).read_text(errors="replace").strip()
        except OSError:
            said = ""
        told = ((self._explain(said) if self._explain else "")
                or f"ffmpeg exited {code}" + (f": {said.splitlines()[-1]}" if said else ""))
        log.info("FfmpegPreview.error: %s", told)
        return told

    def close(self) -> None:
        """Kill ffmpeg if it still runs, and remove its files."""
        if self._proc.poll() is None:
            log.debug("FfmpegPreview.close: killing ffmpeg pid %d", self._proc.pid)
            self._proc.kill()
            self._proc.wait()
        log.debug("FfmpegPreview.close: removing %s", self._folder)
        shutil.rmtree(self._folder, ignore_errors=True)
