"""MediaService — video playback state, and which decoder fills it.

A video file is decoded by the injected ``VideoTool`` (ffmpeg today,
``adapters/media/ffmpeg.py``) into ENCODED frames, on its thread, while the
first ones already play (#264).  ``ZtDecoder`` reads Thermalright's
``Theme.zt`` archives -- a JPEG sequence with per-frame timestamps that the
Windows app emits via UCVideoCut.BmpToThemeFile -- here, since that is file
parsing and needs no tool.  MediaService owns playback state (current frame,
cursor, fps) so DisplayService can ask for "the frame at time t".
"""
from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path

from ..core.errors import ThemeError
from ..core.logs import per_frame
from ..core.models import ZT_MAGIC, PanelSource, VideoDecodeRequest, VideoProbe
from ..core.ports import FrameSource, VideoDecode, VideoTool

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


_DEFAULT_FPS = 15          # Matches C# originalImageHz = 15


#: ``load_video`` returns once this many frames are in: two tell an animation
#: from a still (``PlayVideo``'s static-background rule), and are ~0.1 s of 4K.
_FIRST_FRAMES = 2
#: A video that has not produced its first frames by then is reported, not
#: waited on -- well inside the 30 s IPC timeout a UI waits for an answer.
_FIRST_FRAMES_TIMEOUT_S = 20.0


def _expected_frames(probe: VideoProbe, fps: int, duration_s: float | None) -> int:
    """How many frames the decode should yield: duration x fps, 0 if unknown."""
    duration = probe.duration_s
    if duration is not None and duration_s:
        duration = min(duration, duration_s)
    expected = round(duration * fps) if duration else 0
    log.debug("_expected_frames: %s -> %d", probe, expected)
    return expected


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
    :class:`MediaService` "may reach the ffmpeg decoder for the same
    theme" — it does not: the ``.zt`` branch of ``load_video`` never falls
    through, and the ffmpeg decode checks for ffmpeg itself.  The C# reads a
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
    #: Whether these frames were decoded at their NATIVE size, so the
    #: compositor still has to fit them (``DeviceSettings.fit_mode``).  The
    #: decode size IS the origin -- ``load_video`` is told ``None`` for user
    #: uploads and a canvas size for program/cloud assets -- so carrying the
    #: answer here means nobody downstream re-derives it from something else
    #: and gets a different one.  ``Paths.is_user_content`` is where the
    #: question is decided.
    is_user_content: bool = False
    #: False while a ``VideoTool`` decode is still appending to ``frames``
    #: (#264: playback starts on the first frames, not the last).
    complete: bool = True
    #: The decode's expected length (ffprobe duration x fps), 0 when unknown --
    #: what ``frame_count`` reports until the decode completes, so a progress
    #: bar and clock show the video's real length from the first frame.
    expected_frames: int = 0
    #: Which load this is -- never reused, so the background's identity
    #: (``DisplayService._background_token``) moves when the video does.  It
    #: borrowed ``DeviceSettings.background_path`` for that, which held the
    #: theme's own video as if the user had picked it.
    serial: int = field(default_factory=count(1).__next__)

    @property
    def advancing(self) -> bool:
        """Whether the video loop moves this playback on: it has frames and is
        not paused.  The ONE reading -- ``MediaService.playing`` (what the
        loop ticks) and the App's cadence owner (whether the loop is what
        drives the panel) used to spell it each their own way, and agreed
        only because no empty playback is ever registered.  What to DRAW is
        another question: a paused video still shows its frame."""
        moving = bool(self.frames) and not self.paused
        frame_log.debug("Playback.advancing: %s", moving)
        return moving

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
            # The end: back to the first frame -- a video always loops, as
            # the C#'s player does (UCBoFangQiKongZhi.Timer_Get_Image wraps).
            self.cursor = 0
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

    def finish(self) -> None:
        """The decode ended: these are all the frames there will be."""
        log.info("Playback.finish: %d frame(s)", len(self.frames))
        self.complete = True

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

    def __init__(self, *, video_tool: VideoTool) -> None:
        log.debug("__init__")
        #: What a video is probed, decoded and streamed with (``VideoTool``) --
        #: injected, never run here.
        self._tool = video_tool
        self._playbacks: dict[str, Playback] = {}
        #: Each panel's ONE live picture -- a module's, a web stream's -- and
        #: whose it is.  One slot, so opening one ends the last: the sources
        #: are exclusive here, as they are in Settings.
        self._live: dict[str, tuple[PanelSource, FrameSource]] = {}
        #: The decodes still filling a playback, by device (#264).
        self._decodes: dict[str, VideoDecode] = {}

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
        through the ``VideoTool``.  ``.zt`` carries its
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
            probe = self._tool.probe(path)     # ONE read: its size and its length
            playback = Playback(frames=[], fps=fps, is_user_content=size is None,
                                complete=False,
                                expected_frames=_expected_frames(probe, fps, duration_s))
            decode = self._tool.decode(
                VideoDecodeRequest(path=path, size=size, fps=fps,
                                   rotation_degrees=rotation_degrees,
                                   duration_s=duration_s,
                                   fit_within=fit_within if size is None else None,
                                   native=probe.size),
                playback.frames, on_done=playback.finish, on_error=on_error)
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

    def _replace_decode(self, device_key: str, decode: VideoDecode | None) -> None:
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
                if pb.advancing}
        frame_log.debug("playing: %s", sorted(live))
        return live

    def open_stream(self, device_key: str, url: str,
                    size: tuple[int, int], fps: int) -> FrameSource:
        """Start reading *url* for *device_key* -- its live picture now."""
        log.info("open_stream: %s <- %s (%dx%d @ %d fps)",
                 device_key, url, *size, fps)
        reader = self._tool.stream(url, size, fps)
        self.open_live(device_key, PanelSource.MEDIA, reader)
        return reader

    def open_live(self, device_key: str, kind: PanelSource,
                  source: FrameSource) -> None:
        """*source*, of *kind*, is *device_key*'s live picture now -- the one
        it had, of whatever kind, is closed."""
        log.info("open_live: %s <- %s %s", device_key, kind.value, source.name)
        self.close_live(device_key)
        self._live[device_key] = (kind, source)

    def live(self, device_key: str,
             kind: PanelSource | None = None) -> FrameSource | None:
        """*device_key*'s live picture -- only if it is of *kind*, when given:
        a module asking after its own never gets a stream that replaced it."""
        entry = self._live.get(device_key)
        found = entry[1] if entry and (kind is None or entry[0] is kind) else None
        frame_log.debug("live: %s kind=%s -> %s", device_key,
                        kind and kind.value, found and found.name)
        return found

    def live_kind(self, device_key: str) -> PanelSource | None:
        """Whose *device_key*'s live picture is, or None for none."""
        entry = self._live.get(device_key)
        frame_log.debug("live_kind: %s -> %s", device_key, entry and entry[0].value)
        return entry[0] if entry else None

    def close_live(self, device_key: str,
                   kind: PanelSource | None = None) -> None:
        """Stop *device_key*'s live picture (idempotent) -- only if it is of
        *kind*, when given, so a stale tick of one source cannot end another."""
        entry = self._live.get(device_key)
        if entry is None or (kind is not None and entry[0] is not kind):
            frame_log.debug("close_live: %s has no %s live picture", device_key,
                            kind.value if kind else "")
            return
        del self._live[device_key]
        log.info("close_live: %s (%s %s)", device_key, entry[0].value,
                 entry[1].name)
        entry[1].close()

    def unload(self, device_key: str) -> None:
        """Drop a playback, freeing its frame buffers -- and end a stream
        or a module's source."""
        # Not a cast: ``StopVideo`` -- what calls this -- never ended one, and
        # a cast is ended by ``StopScreencast``, by another source taking the
        # slot (``open_live``), or by the panel leaving (``stop_sources``).
        if self.live_kind(device_key) is not PanelSource.SCREENCAST:
            self.close_live(device_key)
        self._replace_decode(device_key, None)
        had = self._playbacks.pop(device_key, None)
        if had is not None:
            log.info("unload: dropped %d-frame playback for %s",
                     len(had.frames), device_key)
