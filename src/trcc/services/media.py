"""MediaService — video decoding + playback state.

VideoDecoder pipes ffmpeg → raw RGB24 frames (in-memory, no BMP-to-disk
like legacy Windows did).  ZtDecoder reads Thermalright's `Theme.zt`
animation archives — a JPEG sequence with per-frame timestamps that the
Windows app emits via UCVideoCut.BmpToThemeFile.  MediaService owns
playback state (current frame, cursor, fps) so DisplayService can ask
for "the frame at time t".

ffmpeg is a runtime dependency.  If it isn't on PATH, decode() raises
ThemeError with a user-facing install hint.
"""
from __future__ import annotations

import logging
import struct
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from ..core import toolchain
from ..core.errors import ThemeError
from ..core.geometry import largest_fit_size
from ..core.logs import per_frame
from ..core.models import ZT_MAGIC, VisualSpec
from ..core.ports import SoundInput, VisualEngine
from .media_stream import StreamReader, _drain
from .visual_stream import VisualReader

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


_DEFAULT_FPS = 15          # Matches C# originalImageHz = 15


def _frame_size_rgb24(width: int, height: int) -> int:
    """Bytes in one RGB24 frame — three channels, one byte each.

    A ``def``, not an assigned lambda.  It carried ``# noqa: E731`` (ruff's
    "do not assign a lambda, use a def"), and a suppression comment is the
    thing CLAUDE.md bans outright rather than the rule it silences.
    """
    frame_log.debug("_frame_size_rgb24: %dx%d", width, height)
    return width * height * 3


# =========================================================================
# VideoDecoder — one-shot ffmpeg → list[RawFrame]
# =========================================================================


class VideoDecoder:
    """Decode a video file to a list of in-memory RGB24 frames via ffmpeg.

    No playback state, no disk temp files.  Caller owns the resulting
    frames.  Large videos allocate proportionally — trim with
    `duration_s` if memory pressure matters.
    """

    def __init__(self, path: Path, size: tuple[int, int] | None,
                 fps: int = _DEFAULT_FPS,
                 rotation_degrees: int = 0,
                 duration_s: float | None = None,
                 install_hint: Callable[[str], str] = toolchain.generic_install_hint,
                 fit_within: tuple[int, int] | None = None,
                 ) -> None:
        self._install_hint = install_hint
        # With ``size=None``: the canvas the fit modes draw on.  The native
        # frame is scaled down to the largest any of them needs
        # (``geometry.largest_fit_size``) -- never up, aspect kept.
        self.fit_within = fit_within
        # ``size`` is the OUTPUT scale ffmpeg is told to produce.
        # ``None`` → decode at the source's native resolution and let
        # the render pipeline's fit-mode scale at composite time.  Used
        # for user-uploaded videos so the user can pick width / height /
        # stretch.  Program/cloud assets are pre-scaled to the device's
        # canvas, so callers pass a concrete tuple for those.
        log.debug("__init__: path=%s size=%s", path, size)
        self.path = path
        self.size = size
        self.fps = fps
        self.rotation_degrees = rotation_degrees
        self.duration_s = duration_s
        self.frames: list[bytes] = []

    def decode(self) -> list[bytes]:
        """Run ffmpeg to the end, return the frames as ENCODED (JPEG) bytes.

        One is decoded per tick via ``Renderer.decode_image`` rather than the
        whole animation being held as raw pixels — see :class:`Playback`.
        The same path as :meth:`start`, waited out: one way ffmpeg is run.
        """
        playback = Playback(frames=[], complete=False)
        decode = self.start(playback)
        decode.wait_done()
        if decode.error:
            raise ThemeError(f"ffmpeg decode failed: {decode.error}")
        self.frames = playback.frames
        log.info("VideoDecoder: decoded %d frame(s) from %s (%.1f MB encoded)",
                 len(self.frames), self.path.name,
                 sum(len(f) for f in self.frames) / 1e6)
        return self.frames

    def start(self, playback: Playback,
              on_error: Callable[[str], None] | None = None) -> BackgroundDecode:
        """Start ffmpeg filling *playback*'s frames on a thread (#264); returns
        at once.  ``ThemeError`` now when it cannot start at all."""
        cmd = self.command()
        try:
            decode = BackgroundDecode(cmd, playback, self.path.name, on_error=on_error)
        except FileNotFoundError as e:
            raise ThemeError("ffmpeg not found on PATH") from e
        log.info("VideoDecoder.start: %s decoding in the background", self.path.name)
        return decode

    def command(self) -> list[str]:
        """The ffmpeg command for this decode -- probing the native size where
        asked.  ``ThemeError`` when it cannot be built."""
        if not self.path.exists():
            raise ThemeError(f"Video path does not exist: {self.path}")
        if not toolchain.present("ffmpeg"):
            raise ThemeError(toolchain.missing("ffmpeg",
                                               self._install_hint("ffmpeg")))

        native: tuple[int, int] | None = None
        if self.size is None:
            native = _probe_video_size(self.path)
            if native is None:
                # ffprobe failed (missing / unreadable / unknown codec).
                # Without dims we can't chunk the raw pipe — fail loudly
                # rather than guess.
                raise ThemeError(
                    f"Could not probe native size of {self.path.name} "
                    "(ffprobe failed) — install ffprobe or pass an "
                    "explicit decode size",
                )
            w, h = (largest_fit_size(native, self.fit_within)
                    if self.fit_within is not None else native)
            log.info("VideoDecoder: %s native size %dx%d -> decode %dx%d (%s)",
                     self.path.name, *native, w, h,
                     "no rescale" if (w, h) == native
                     else f"the largest a fit mode draws on {self.fit_within}")
        else:
            w, h = self.size

        # Guard the frame-chunking math: frame_bytes = w*h*3, and a zero
        # dimension makes ``len(raw) % frame_bytes`` / ``// frame_bytes``
        # raise ZeroDivisionError below.  Fail loudly with the module's
        # own error — same "rather than guess" stance as the ffprobe path.
        if w <= 0 or h <= 0:
            raise ThemeError(
                f"Invalid decode size {w}x{h} for {self.path.name} — "
                "width and height must both be positive"
            )

        cmd: list[str] = [toolchain.executable("ffmpeg"),
                          "-hide_banner", "-loglevel", "error"]
        if self.rotation_degrees:
            cmd += ["-display_rotation", str(self.rotation_degrees)]
        if self.duration_s:
            cmd += ["-t", f"{self.duration_s:.3f}"]
        cmd += [
            "-i", str(self.path),
            "-r", str(self.fps),
        ]
        # Only emit -s when the output differs from the source: an explicit
        # size, or a native decode scaled down to what the fit modes need.
        if self.size is not None or (w, h) != native:
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

        log.info("VideoDecoder.command: %s at %dx%d: %s", self.path.name, w, h, " ".join(cmd))
        return cmd


_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_MJPEG_QUALITY = 3       # ffmpeg -q:v; 2 = best .. 31 = worst


#: How much of ffmpeg's output one read takes, at most (``read1`` returns what
#: has arrived, so the first frames are not held back to fill it).
_CHUNK_BYTES = 1 << 18


class BackgroundDecode:
    """ffmpeg decoding a video into a :class:`Playback`'s frames, on a thread.

    #264: ``PlayVideo`` decoded every frame before it returned, and the gui
    dispatches it on its UI thread -- a 4K video froze the window for ~9 s, and
    the CLI and API waited the same way.  Now the caller waits for the first
    frames (:meth:`wait_for`) and playback starts while the rest arrives; the
    playback holds at the last decoded frame until ``complete`` is set.

    ffmpeg is started HERE, on the caller's thread, so one that cannot start
    raises now.  stderr is drained as it comes (``media_stream._drain``): left
    in a pipe it fills and stalls ffmpeg.  A decode that fails partway keeps
    what it decoded -- the panel keeps playing it -- and reports once through
    *on_error*; one that fails before any frame leaves ``error`` for the
    caller to raise.  :meth:`cancel` stops it (replace, ``StopVideo``, close).
    """

    def __init__(self, cmd: list[str], playback: Playback, name: str, *,
                 on_error: Callable[[str], None] | None = None) -> None:
        log.info("BackgroundDecode: %s", name)
        self._playback, self._name, self._on_error = playback, name, on_error
        self.error = ""
        self._stop = threading.Event()
        self._done = threading.Event()
        self._grew = threading.Condition()
        self._began = time.monotonic()
        self._proc: Any = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._tail: deque[bytes] = deque(maxlen=3)
        self._drain = threading.Thread(target=_drain, args=(self._proc.stderr, self._tail),
                                       daemon=True, name="trcc-decode-stderr")
        self._drain.start()
        self._thread = threading.Thread(target=self._run, daemon=True, name="trcc-decode")
        self._thread.start()

    def _run(self) -> None:
        log.debug("BackgroundDecode._run: %s", self._name)
        splitter, proc, said_first = _JpegFrames(), self._proc, False
        while not self._stop.is_set():
            chunk = proc.stdout.read1(_CHUNK_BYTES)
            if not chunk:
                break
            if new := splitter.feed(chunk):
                with self._grew:
                    self._playback.frames.extend(new)
                    self._grew.notify_all()
                if not said_first:
                    said_first = True
                    log.info("BackgroundDecode: %s first frame after %.2f s",
                             self._name, time.monotonic() - self._began)
        if self._stop.is_set() and proc.poll() is None:
            proc.terminate()
        code = proc.wait()
        self._drain.join(timeout=2)
        proc.stdout.close()
        proc.stderr.close()
        self._finish(code, splitter.pending)

    def _finish(self, code: int, pending: int) -> None:
        """Settle the decode: mark the playback complete, say how it ended."""
        frames = len(self._playback.frames)
        stopped = self._stop.is_set()
        if not stopped and code != 0:
            tail = b" ".join(self._tail).decode(errors="replace").strip()
            self.error = f"ffmpeg exit {code}" + (f": {tail[:500]}" if tail else "")
        if pending and not stopped:
            log.warning("BackgroundDecode: %s ended with %d byte(s) of a partial frame "
                        "-- dropped", self._name, pending)
        self._playback.complete = True
        with self._grew:
            self._done.set()
            self._grew.notify_all()
        log.info("BackgroundDecode: %s %s -- %d frame(s) in %.2f s%s", self._name,
                 "cancelled" if stopped else "finished", frames,
                 time.monotonic() - self._began, f" ({self.error})" if self.error else "")
        if self.error and frames and self._on_error is not None:
            self._on_error(f"{self._name}: decoding stopped after {frames} frame(s), "
                           f"which keep playing -- {self.error}")

    def _arrived(self, frames: int) -> bool:
        ready = len(self._playback.frames) >= frames or self._done.is_set()
        frame_log.debug("BackgroundDecode._arrived: %d -> %s", frames, ready)
        return ready

    def wait_for(self, frames: int, timeout: float) -> int:
        """Until *frames* have arrived or the decode ended; how many there are."""
        with self._grew:
            self._grew.wait_for(partial(self._arrived, frames), timeout)
        count = len(self._playback.frames)
        log.debug("BackgroundDecode.wait_for: %s %d of %d", self._name, count, frames)
        return count

    def wait_done(self, timeout: float | None = None) -> bool:
        """Until the decode ended; whether it did."""
        done = self._done.wait(timeout)
        log.debug("BackgroundDecode.wait_done: %s -> %s", self._name, done)
        return done

    @property
    def done(self) -> bool:
        frame_log.debug("BackgroundDecode.done: %s", self._done.is_set())
        return self._done.is_set()

    def cancel(self) -> None:
        """Stop ffmpeg and the reader (idempotent)."""
        log.info("BackgroundDecode.cancel: %s", self._name)
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


def _split_jpeg_stream(data: bytes) -> list[bytes]:
    """Every whole frame in a complete mjpeg stream (``_JpegFrames``, once)."""
    splitter = _JpegFrames()
    frames = splitter.feed(data)
    if splitter.pending:
        log.warning("_split_jpeg_stream: %d trailing byte(s) with no EOI after "
                    "frame %d -- dropping the partial frame", splitter.pending, len(frames))
    log.debug("_split_jpeg_stream: %d frame(s) from %d byte(s)", len(frames), len(data))
    return frames


#: ``load_video`` returns once this many frames are in: two tell an animation
#: from a still (``PlayVideo``'s static-background rule), and are ~0.1 s of 4K.
_FIRST_FRAMES = 2
#: A video that has not produced its first frames by then is reported, not
#: waited on -- well inside the 30 s IPC timeout a UI waits for an answer.
_FIRST_FRAMES_TIMEOUT_S = 20.0


def _expected_frames(path: Path, fps: int, duration_s: float | None) -> int:
    """How many frames the decode should yield: duration x fps, 0 if unknown."""
    duration = _probe_video_duration(path)
    if duration is not None and duration_s:
        duration = min(duration, duration_s)
    expected = round(duration * fps) if duration else 0
    log.debug("_expected_frames: %s -> %d", path.name, expected)
    return expected


def _probe_video_duration(path: Path) -> float | None:
    """The container's duration in seconds, or None when ffprobe cannot say --
    the estimate a progress bar shows while the rest still decodes (#264)."""
    if not toolchain.present("ffprobe"):
        log.info("_probe_video_duration: no ffprobe -- length grows as it decodes")
        return None
    cmd = [toolchain.executable("ffprobe"), "-v", "error",
           "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=10)
        duration = float(out.decode("utf-8", errors="replace").strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError,
            ValueError) as e:
        log.info("_probe_video_duration: %s has none (%s)", path.name, e)
        return None
    log.debug("_probe_video_duration: %s -> %.2f s", path.name, duration)
    return duration if duration > 0 else None


def _probe_video_size(path: Path) -> tuple[int, int] | None:
    """Return the video's native ``(width, height)`` or ``None`` on failure.

    Lets :class:`VideoDecoder` skip ffmpeg's ``-s`` flag (native decode)
    while still knowing how to chunk the raw RGB24 pipe output.  Same
    pattern as ``services/video_export._probe_video_duration``.
    """
    log.debug("_probe_video_size: path=%s", path)
    if not toolchain.present("ffprobe"):
        log.warning("_probe_video_size: ffprobe not on PATH")
        return None
    cmd = [
        toolchain.executable("ffprobe"), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        str(path),
    ]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, timeout=10,
        ).decode("utf-8", errors="replace").strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError) as e:
        log.warning("_probe_video_size: ffprobe failed for %s: %s",
                    path.name, e)
        return None
    try:
        w_s, h_s = out.split("x", 1)
        return int(w_s), int(h_s)
    except (ValueError, AttributeError):
        log.warning("_probe_video_size: bad ffprobe output %r for %s",
                    out, path.name)
        return None


# =========================================================================
# ZtDecoder — Theme.zt JPEG-sequence archive
# =========================================================================


class ZtDecoder:
    """Decode a Thermalright ``Theme.zt`` animation archive.

    Format (Windows ``UCVideoCut.BmpToThemeFile``)::

        byte    : 0xDC magic
        int32   : frame_count
        int32[] : per-frame timestamps in ms (frame_count entries)
        for _ in range(frame_count):
            int32 : jpeg_size
            bytes : jpeg payload

    **Nothing here decodes, and nothing here scales.**  The payloads are
    already JPEG on disk, so :meth:`decode` parses the header and returns
    them unchanged; one is decoded per tick by ``Renderer.decode_image``
    (``services/display.py``).  It runs **no ffmpeg at all**, so it does not
    need one installed.  It used to refuse without one, on the grounds that
    :class:`MediaService` "may reach :class:`VideoDecoder` for the same
    theme" — it does not: the ``.zt`` branch of ``load_video`` never falls
    through, and ``VideoDecoder`` checks for ffmpeg itself.  The C# reads a
    ``.zt`` with a plain FileStream (``UCVideoCutF.cs:518``).

    ``size`` is therefore NOT a scale target.  A ``.zt`` is authored AT the
    canvas by ``UCVideoCut``, and :meth:`MediaService.load_video` refuses one
    without an explicit size rather than guess; this class uses ``size``
    only in its completion log, so a ``.zt`` authored for another panel is
    visible in a report.  ``fps`` is derived from the average inter-frame
    delay (timestamps are absolute ms offsets).
    """

    def __init__(self, path: Path, size: tuple[int, int]) -> None:
        log.debug("__init__: path=%s size=%s", path, size)
        self.path = path
        self.size = size
        self.frames: list[bytes] = []
        self.timestamps: list[int] = []
        self.delays: list[int] = []
        self.fps: int = _DEFAULT_FPS

    def decode(self) -> list[bytes]:
        """Read header + payloads, returning the ENCODED frames unchanged."""
        if not self.path.exists():
            raise ThemeError(f".zt path does not exist: {self.path}")

        try:
            data = self.path.read_bytes()
        except OSError as e:
            raise ThemeError(f"Cannot read {self.path}: {e}") from e

        if not data or data[0] != ZT_MAGIC:
            # The format spec used to hold a conditional, which Python reads
            # as an invalid spec -- so a bad .zt raised ValueError, not this.
            # A slice needs no branch for the empty file.
            raise ThemeError(f"Not a Theme.zt archive (starts {data[:4]!r}): "
                             f"{self.path}")

        try:
            (frame_count,) = struct.unpack_from("<i", data, 1)
        except struct.error as e:
            raise ThemeError(f"Truncated .zt header: {self.path}") from e
        if frame_count <= 0 or frame_count > 10_000:
            raise ThemeError(f".zt frame_count out of range: {frame_count}")

        # Timestamps + JPEG payloads laid out back-to-back.
        pos = 5
        try:
            self.timestamps = list(
                struct.unpack_from(f"<{frame_count}i", data, pos)
            )
            pos += 4 * frame_count

            payloads: list[bytes] = []
            for _ in range(frame_count):
                (size,) = struct.unpack_from("<i", data, pos)
                pos += 4
                if size <= 0 or pos + size > len(data):
                    raise ThemeError(
                        f".zt frame size out of range or truncated at frame "
                        f"{len(payloads)}: size={size} pos={pos} "
                        f"file_len={len(data)}"
                    )
                payloads.append(data[pos:pos + size])
                pos += size
        except struct.error as e:
            raise ThemeError(f".zt parse error in {self.path}: {e}") from e

        # Already encoded on disk (the shipped archives carry JPEG), so this
        # is where the compression used to be thrown away: ``_decode_payloads``
        # expanded every one to raw RGB24 up front — 3.1 GB for an 897-frame
        # 1600x720 animation.  Keeping the bytes and decoding one per tick is
        # smaller AND what the C# does (FormCZTV.cs:1946 reads Theme.zt
        # straight into imageArray without decoding).
        self.frames = payloads

        # Per-frame delays from timestamps (last frame inherits previous).
        for i, ts in enumerate(self.timestamps):
            nxt = (
                self.timestamps[i + 1]
                if i + 1 < len(self.timestamps)
                else (ts + (self.delays[-1] if self.delays else 42))
            )
            self.delays.append(max(1, nxt - ts))

        avg_delay = sum(self.delays) / len(self.delays) if self.delays else 42
        self.fps = max(1, round(1000.0 / avg_delay)) if avg_delay > 0 else _DEFAULT_FPS

        log.info(
            "ZtDecoder: decoded %d frame(s) at %dx%d fps=%d from %s",
            len(self.frames), self.size[0], self.size[1], self.fps, self.path.name,
        )
        return self.frames



# =========================================================================
# MediaService — playback state on top of the decoder
# =========================================================================


@dataclass
class Playback:
    """Current playback cursor for a video-backed theme.

    ``frames`` are **encoded** (JPEG/PNG) bytes, not raw pixels, and exactly
    one is decoded per tick via ``Renderer.decode_image``.  Held raw, an
    897-frame 1600x720 animation cost 3.1 GB (#264, #256); encoded it is
    45 MB.  This is also what the C# does — ``imageArray`` holds the bytes
    and ``ByteToBitmap`` decodes one per timer tick, disposing the previous
    (FormCZTV.cs:2176).
    """
    #: Out of the repr — this is the whole animation (45 MB encoded for the
    #: 897-frame case above), and %s on a Playback is a log record.
    frames: list[bytes] = field(repr=False)
    fps: int = _DEFAULT_FPS
    cursor: int = 0
    paused: bool = False
    loop: bool = True
    #: Whether these frames were decoded at their NATIVE size, so the
    #: compositor still has to fit them (``DeviceSettings.fit_mode``).  The
    #: decode size IS the origin -- ``load_video`` is told ``None`` for user
    #: uploads and a canvas size for program/cloud assets -- so carrying the
    #: answer here means nobody downstream re-derives it from something else
    #: and gets a different one.  ``Paths.is_user_content`` is where the
    #: question is decided.
    is_user_content: bool = False
    #: False while a :class:`BackgroundDecode` is still appending to
    #: ``frames`` (#264: playback starts on the first frames, not the last).
    complete: bool = True
    #: The decode's expected length (ffprobe duration x fps), 0 when unknown --
    #: what ``frame_count`` reports until the decode completes, so a progress
    #: bar and clock show the video's real length from the first frame.
    expected_frames: int = 0

    @property
    def frame_count(self) -> int:
        """The video's length in frames: the expected total while it is still
        decoding (never less than what is already here), the exact count once
        complete.  Stepping and seeking use ``len(frames)`` -- what exists."""
        decoded = len(self.frames)
        count = decoded if self.complete else max(decoded, self.expected_frames)
        frame_log.debug("frame_count: %d (decoded %d, complete=%s)",
                        count, decoded, self.complete)
        return count

    @property
    def interval_ms(self) -> int:
        """Milliseconds per frame — the cadence a ticker should run at.

        Clamped to >=1 ms so a degenerate ``fps=0`` cannot stall an event loop,
        and defaulted to 30 fps when a decoder reported nothing.  Lives on the
        playback because the playback is what knows its own rate: both
        ``PlayVideo`` (for ``VideoStarted``) and ``TickDisplay`` (for the
        ``RenderResult`` a UI paces itself from) read it here rather than each
        re-deriving ``1000 / fps``.
        """
        frame_log.debug("interval_ms")
        return max(1, int(1000 / (self.fps or 30)))

    @property
    def current(self) -> bytes | None:
        """The current frame's ENCODED bytes (decode via Renderer.decode_image)."""
        frame_log.debug("current")
        return self.frames[self.cursor] if self.frames else None

    def advance(self) -> bytes | None:
        """Return the current frame's encoded bytes and advance the cursor.

        Honors ``paused`` (returns current frame without advancing) and
        ``loop`` (when False, sticks at the last frame instead of wrapping).
        """
        frame_log.debug("advance: cursor=%d frames=%d paused=%s",
                  self.cursor, len(self.frames), self.paused)
        if not self.frames:
            return None
        frame = self.frames[self.cursor]
        if self.paused:
            return frame
        # One read of the length: a decode thread may append between two.
        decoded = len(self.frames)
        next_cursor = self.cursor + 1
        if next_cursor < decoded:
            self.cursor = next_cursor
        elif not self.complete:
            # The end of what is decoded, not of the video: hold here until
            # more arrives -- wrapping now would restart a video half-read.
            frame_log.debug("advance: holding at %d of %d decoded", self.cursor, decoded)
        else:
            self.cursor = 0 if self.loop else decoded - 1
        return frame

    def seek(self, frame_index: int) -> None:
        """Jump to *frame_index* (clamped to valid range)."""
        if not self.frames:
            log.debug("Playback.seek: no frames loaded")
            return
        clamped = max(0, min(frame_index, len(self.frames) - 1))
        log.info("Playback.seek: cursor %d → %d", self.cursor, clamped)
        self.cursor = clamped

    def pause(self, paused: bool) -> None:
        if paused != self.paused:
            log.info("Playback.pause: %s → %s", self.paused, paused)
        self.paused = paused

    def set_loop(self, loop: bool) -> None:
        if loop != self.loop:
            log.info("Playback.set_loop: %s → %s", self.loop, loop)
        self.loop = loop

    def reset(self) -> None:
        log.info("Playback.reset: cursor=%d paused=%s → 0/False",
                 self.cursor, self.paused)
        self.cursor = 0
        self.paused = False


class MediaService:
    """Owns the per-device playback cursor for video-backed themes.

    DisplayService asks this for "the frame to composite right now"; the
    service handles advancing on tick and looping.  Static-image themes
    never interact with this class.
    """

    #: The reader a web source gets.  A class attribute so a test swaps in a
    #: fake without ffmpeg or a network (``tests/test_media_stream.py``).
    stream_reader: type[StreamReader] = StreamReader
    #: The reader an audio visual gets; swapped the same way in a test.
    visual_reader: type[VisualReader] = VisualReader

    def __init__(self, install_hint: Callable[[str], str]
                 = toolchain.generic_install_hint) -> None:
        log.debug("__init__")
        self._install_hint = install_hint
        self._playbacks: dict[str, Playback] = {}
        self._streams: dict[str, StreamReader] = {}
        self._visuals: dict[str, VisualReader] = {}
        #: The decodes still filling a playback, by device (#264).
        self._decodes: dict[str, BackgroundDecode] = {}

    def load_video(self, device_key: str, path: Path,
                   size: tuple[int, int] | None,
                   fps: int = _DEFAULT_FPS,
                   rotation_degrees: int = 0,
                   duration_s: float | None = None,
                   fit_within: tuple[int, int] | None = None,
                   on_error: Callable[[str], None] | None = None) -> Playback:
        """Decode a video / .zt animation for a device, replacing any previous playback.

        An ffmpeg video returns once its first frames are in, still decoding
        (#264); ``ThemeError`` when it yields none.  *on_error* hears of a
        decode that fails after that -- what it decoded keeps playing.

        Dispatches by suffix: ``.zt`` → :class:`ZtDecoder`, anything else
        through ``ffmpeg`` via :class:`VideoDecoder``.  ``.zt`` carries its
        own per-frame timing so we honour the decoder's derived ``fps``
        when the caller takes the default.

        ``size`` is the decode-time output scale:

        * ``tuple(w, h)`` — decode at that exact size (pre-scale).  Used
          for program/cloud assets that are already authored at the
          device's canvas resolution.
        * ``None`` — decode at the source video's NATIVE size.  Used for
          user-uploaded videos in ``user_content_dir`` so the render
          pipeline's ``fit_mode`` (width / height / stretch) can scale at
          composite time.  ``.zt`` is fixed-size by format, so ``None``
          is only valid for ffmpeg-decoded inputs.  *fit_within* (the
          canvas) then caps it at the largest size a fit mode draws.
        """
        if size is None:
            log.info(
                "load_video: key=%s path=%s size=native fit_within=%s fps=%d rotate=%d",
                device_key, path, fit_within, fps, rotation_degrees,
            )
        else:
            log.info(
                "load_video: key=%s path=%s size=%dx%d fps=%d rotate=%d",
                device_key, path, size[0], size[1], fps, rotation_degrees,
            )
        if path.suffix.lower() == ".zt":
            if size is None:
                # .zt is a baked JPEG sequence — the encoded size is the
                # frame size.  Asking for "native" decode of a .zt is a
                # programming error (caller should pass canvas size, since
                # .zt is authored AT canvas during VideoCropDialog save).
                raise ThemeError(
                    ".zt decode requires an explicit size — pass the "
                    "device's canvas resolution",
                )
            zt = ZtDecoder(path=path, size=size)
            zt.decode()
            effective_fps = fps if fps != _DEFAULT_FPS else zt.fps
            # ``.zt`` is authored AT canvas by ``UCVideoCut`` and rejects a
            # native decode above, so it is never user-fitted content.
            playback = Playback(frames=zt.frames, fps=effective_fps)
            log.info("load_video: .zt decoded — %d frames @ %d fps",
                     len(zt.frames), effective_fps)
        else:
            decoder = VideoDecoder(
                path=path, size=size, fps=fps,
                rotation_degrees=rotation_degrees,
                duration_s=duration_s,
                install_hint=self._install_hint,
                fit_within=fit_within if size is None else None,
            )
            playback = Playback(frames=[], fps=fps, is_user_content=size is None,
                                complete=False,
                                expected_frames=_expected_frames(path, fps, duration_s))
            decode = decoder.start(playback, on_error)
            got = decode.wait_for(_FIRST_FRAMES, _FIRST_FRAMES_TIMEOUT_S)
            if not got:
                decode.cancel()
                raise ThemeError(f"ffmpeg decode failed: {decode.error}" if decode.error
                                 else f"{path.name} gave no frame within "
                                      f"{_FIRST_FRAMES_TIMEOUT_S:.0f} s")
            log.info("load_video: %s playing on its first %d frame(s) of ~%d, %s",
                     path.name, got, playback.frame_count,
                     "decoded" if decode.done else "the rest decoding")
            self._replace_decode(device_key, None if decode.done else decode)
            self._playbacks[device_key] = playback
            return playback
        self._replace_decode(device_key, None)
        self._playbacks[device_key] = playback
        return playback

    def _replace_decode(self, device_key: str, decode: BackgroundDecode | None) -> None:
        """Cancel the decode still filling *device_key*'s old playback -- only
        now, when a new one replaces it, so a load that fails leaves it be."""
        old = self._decodes.pop(device_key, None)
        if old is not None and old is not decode:
            log.info("_replace_decode: %s -- the previous decode is cancelled", device_key)
            old.cancel()
        if decode is not None:
            self._decodes[device_key] = decode

    def playback(self, device_key: str) -> Playback | None:
        frame_log.debug("playback: key=%s", device_key)
        return self._playbacks.get(device_key)

    def playing(self) -> dict[str, Playback]:
        """Every playback that should advance now: has frames, not paused.

        A snapshot, so a caller on another thread can iterate it while a
        Command loads or unloads a video.
        """
        live = {key: pb for key, pb in list(self._playbacks.items())
                if pb.frames and not pb.paused}
        frame_log.debug("playing: %s", sorted(live))
        return live

    def open_stream(self, device_key: str, url: str,
                    size: tuple[int, int], fps: int) -> StreamReader:
        """Start reading *url* for *device_key*, replacing any stream it had."""
        log.info("open_stream: %s <- %s (%dx%d @ %d fps)",
                 device_key, url, *size, fps)
        self.close_stream(device_key)
        reader = self._streams[device_key] = self.stream_reader(url, size, fps)
        return reader

    def stream(self, device_key: str) -> StreamReader | None:
        frame_log.debug("stream: key=%s", device_key)
        return self._streams.get(device_key)

    def close_stream(self, device_key: str) -> None:
        """Stop *device_key*'s web source, if it has one (idempotent)."""
        if (reader := self._streams.pop(device_key, None)) is not None:
            log.info("close_stream: %s (%s)", device_key, reader.url)
            reader.close()

    def open_visual(self, device_key: str, spec: VisualSpec,
                    sound: Callable[[], SoundInput],
                    engine: Callable[[], VisualEngine],
                    thread: Callable[[], AbstractContextManager[None]]
                    ) -> VisualReader:
        """Start the visual *spec* names drawing *device_key*'s background
        from what is playing, replacing any visual it had.  *thread* is the
        platform's per-thread setup, which the reader's thread enters."""
        log.info("open_visual: %s <- %s (%dx%d)", device_key, spec.kind,
                 spec.width, spec.height)
        self.close_visual(device_key)
        reader = self._visuals[device_key] = self.visual_reader(
            spec, sound, engine, thread)
        return reader

    def visual(self, device_key: str) -> VisualReader | None:
        frame_log.debug("visual: key=%s", device_key)
        return self._visuals.get(device_key)

    def close_visual(self, device_key: str) -> None:
        """Stop *device_key*'s audio visual, if it has one (idempotent)."""
        if (reader := self._visuals.pop(device_key, None)) is not None:
            log.info("close_visual: %s (%s)", device_key, reader.name)
            reader.close()

    def unload(self, device_key: str) -> None:
        """Drop a playback, freeing its frame buffers -- and end a stream
        or an audio visual."""
        self.close_stream(device_key)
        self.close_visual(device_key)
        self._replace_decode(device_key, None)
        had = self._playbacks.pop(device_key, None)
        if had is not None:
            log.info("unload: dropped %d-frame playback for %s",
                     len(had.frames), device_key)
