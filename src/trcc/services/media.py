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
import os
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core import toolchain
from ..core.errors import ThemeError

log = logging.getLogger(__name__)


_DEFAULT_FPS = 15          # Matches C# originalImageHz = 15
_FRAME_SIZE_RGB24 = lambda w, h: w * h * 3  # noqa: E731


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
                 duration_s: float | None = None) -> None:
        # ``size`` is the OUTPUT scale ffmpeg is told to produce.
        # ``None`` → decode at the source's native resolution and let
        # the render pipeline's fit-mode scale at composite time.  Used
        # for user-uploaded videos so the user can pick width / height /
        # stretch.  Program/cloud assets are pre-scaled to the device's
        # canvas, so callers pass a concrete tuple for those.
        self.path = path
        self.size = size
        self.fps = fps
        self.rotation_degrees = rotation_degrees
        self.duration_s = duration_s
        self.frames: list[bytes] = []

    def decode(self) -> list[bytes]:
        """Run ffmpeg, return the frames as ENCODED (JPEG) bytes.

        One is decoded per tick via ``Renderer.decode_image`` rather than the
        whole animation being held as raw pixels — see :class:`Playback`.
        """
        if not self.path.exists():
            raise ThemeError(f"Video path does not exist: {self.path}")
        if not _ffmpeg_available():
            raise ThemeError(
                "ffmpeg not found on PATH — install via your package manager "
                "(e.g. 'dnf install ffmpeg' / 'apt install ffmpeg')"
            )

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
            w, h = native
            log.info("VideoDecoder: %s native size %dx%d (no rescale)",
                     self.path.name, w, h)
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

        cmd: list[str] = [toolchain.resolve("ffmpeg") or "ffmpeg",
                          "-hide_banner", "-loglevel", "error"]
        if self.rotation_degrees:
            cmd += ["-display_rotation", str(self.rotation_degrees)]
        if self.duration_s:
            cmd += ["-t", f"{self.duration_s:.3f}"]
        cmd += [
            "-i", str(self.path),
            "-r", str(self.fps),
        ]
        # Only emit -s when the caller asked for a specific output size.
        # When ``size is None`` ffmpeg outputs the input's native frame
        # dimensions, which we already pinned (w, h) via ffprobe.
        if self.size is not None:
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

        log.debug("VideoDecoder: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd, check=True, capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode("utf-8", errors="replace").strip()
            raise ThemeError(f"ffmpeg decode failed: {stderr[:500]}") from e
        except FileNotFoundError as e:
            raise ThemeError("ffmpeg not found on PATH") from e

        self.frames = _split_jpeg_stream(proc.stdout)
        log.info("VideoDecoder: decoded %d frame(s) at %dx%d from %s "
                 "(%.1f MB encoded)",
                 len(self.frames), w, h, self.path.name,
                 sum(len(f) for f in self.frames) / 1e6)
        return self.frames


_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_MJPEG_QUALITY = 3       # ffmpeg -q:v; 2 = best .. 31 = worst


def _split_jpeg_stream(data: bytes) -> list[bytes]:
    """Split ffmpeg's ``image2pipe`` mjpeg output into one blob per frame.

    Frames are delimited by SOI (``ff d8``) / EOI (``ff d9``).  Inside
    entropy-coded data a literal ``ff`` is byte-stuffed as ``ff 00``, so a bare
    EOI cannot appear mid-scan; the one thing that could fool this is an
    embedded EXIF thumbnail carrying its own JPEG, which ffmpeg's mjpeg encoder
    does not emit.  Verified against 897 real frames at four quality levels —
    897 of 897 each time — but that is evidence about ffmpeg's output, not a
    proof, so a short read is logged rather than passed off as a full decode.
    """
    frames: list[bytes] = []
    pos = 0
    while (start := data.find(_JPEG_SOI, pos)) >= 0:
        end = data.find(_JPEG_EOI, start + 2)
        if end < 0:
            log.warning("_split_jpeg_stream: %d trailing byte(s) with no EOI "
                        "after frame %d — dropping the partial frame",
                        len(data) - start, len(frames))
            break
        frames.append(data[start:end + 2])
        pos = end + 2
    log.debug("_split_jpeg_stream: %d frame(s) from %d byte(s)",
              len(frames), len(data))
    return frames


def _ffmpeg_available() -> bool:
    """Quick check whether ffmpeg is on PATH."""
    for dir_ in os.get_exec_path():
        if (Path(dir_) / "ffmpeg").exists():
            return True
        if (Path(dir_) / "ffmpeg.exe").exists():
            return True
    return False


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
        "ffprobe", "-v", "error",
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


_ZT_MAGIC = 0xDC


class ZtDecoder:
    """Decode a Thermalright ``Theme.zt`` animation archive.

    Format (Windows ``UCVideoCut.BmpToThemeFile``)::

        byte    : 0xDC magic
        int32   : frame_count
        int32[] : per-frame timestamps in ms (frame_count entries)
        for _ in range(frame_count):
            int32 : jpeg_size
            bytes : jpeg payload

    Decoded frames are scaled to the requested ``size`` via a single
    ffmpeg ``jpeg_pipe`` invocation per frame — same approach as
    legacy.  ``fps`` is derived from the average inter-frame delay
    (timestamps are absolute ms offsets).
    """

    def __init__(self, path: Path, size: tuple[int, int]) -> None:
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
        if not _ffmpeg_available():
            raise ThemeError(
                "ffmpeg not found on PATH — install via your package manager "
                "(e.g. 'dnf install ffmpeg' / 'apt install ffmpeg')"
            )

        try:
            data = self.path.read_bytes()
        except OSError as e:
            raise ThemeError(f"Cannot read {self.path}: {e}") from e

        if not data or data[0] != _ZT_MAGIC:
            raise ThemeError(
                f"Not a Theme.zt archive (magic 0x{data[0]:02X if data else 0}): "
                f"{self.path}"
            )

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
    frames: list[bytes]
    fps: int = _DEFAULT_FPS
    cursor: int = 0
    paused: bool = False
    loop: bool = True

    @property
    def frame_count(self) -> int:
        return len(self.frames)

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
        return max(1, int(1000 / (self.fps or 30)))

    @property
    def current(self) -> bytes | None:
        """The current frame's ENCODED bytes (decode via Renderer.decode_image)."""
        return self.frames[self.cursor] if self.frames else None

    def advance(self) -> bytes | None:
        """Return the current frame's encoded bytes and advance the cursor.

        Honors ``paused`` (returns current frame without advancing) and
        ``loop`` (when False, sticks at the last frame instead of wrapping).
        """
        log.debug("advance: cursor=%d frames=%d paused=%s",
                  self.cursor, len(self.frames), self.paused)
        if not self.frames:
            return None
        frame = self.frames[self.cursor]
        if self.paused:
            return frame
        next_cursor = self.cursor + 1
        if next_cursor >= len(self.frames):
            self.cursor = 0 if self.loop else len(self.frames) - 1
        else:
            self.cursor = next_cursor
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

    def __init__(self) -> None:
        self._playbacks: dict[str, Playback] = {}

    def load_video(self, device_key: str, path: Path,
                   size: tuple[int, int] | None,
                   fps: int = _DEFAULT_FPS,
                   rotation_degrees: int = 0,
                   duration_s: float | None = None) -> Playback:
        """Decode a video / .zt animation for a device, replacing any previous playback.

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
          is only valid for ffmpeg-decoded inputs.
        """
        if size is None:
            log.info(
                "load_video: key=%s path=%s size=native fps=%d rotate=%d",
                device_key, path, fps, rotation_degrees,
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
            playback = Playback(frames=zt.frames, fps=effective_fps)
            log.info("load_video: .zt decoded — %d frames @ %d fps",
                     len(zt.frames), effective_fps)
        else:
            decoder = VideoDecoder(
                path=path, size=size, fps=fps,
                rotation_degrees=rotation_degrees,
                duration_s=duration_s,
            )
            decoder.decode()
            playback = Playback(frames=decoder.frames, fps=fps)
            log.info(
                "load_video: ffmpeg decoded %s — %d frames @ %d fps",
                path.suffix, len(decoder.frames), fps,
            )
        self._playbacks[device_key] = playback
        return playback

    def playback(self, device_key: str) -> Playback | None:
        log.debug("playback: key=%s", device_key)
        return self._playbacks.get(device_key)

    def unload(self, device_key: str) -> None:
        """Drop a playback, freeing its frame buffers."""
        had = self._playbacks.pop(device_key, None)
        if had is not None:
            log.info("unload: dropped %d-frame playback for %s",
                     len(had.frames), device_key)
