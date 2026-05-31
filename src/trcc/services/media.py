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

from ..core.errors import ThemeError
from ..core.models import RawFrame

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

    def __init__(self, path: Path, size: tuple[int, int],
                 fps: int = _DEFAULT_FPS,
                 rotation_degrees: int = 0,
                 duration_s: float | None = None) -> None:
        self.path = path
        self.size = size
        self.fps = fps
        self.rotation_degrees = rotation_degrees
        self.duration_s = duration_s
        self.frames: list[RawFrame] = []

    def decode(self) -> list[RawFrame]:
        """Run ffmpeg, return the decoded frames."""
        if not self.path.exists():
            raise ThemeError(f"Video path does not exist: {self.path}")
        if not _ffmpeg_available():
            raise ThemeError(
                "ffmpeg not found on PATH — install via your package manager "
                "(e.g. 'dnf install ffmpeg' / 'apt install ffmpeg')"
            )

        w, h = self.size
        cmd: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        if self.rotation_degrees:
            cmd += ["-display_rotation", str(self.rotation_degrees)]
        if self.duration_s:
            cmd += ["-t", f"{self.duration_s:.3f}"]
        cmd += [
            "-i", str(self.path),
            "-r", str(self.fps),
            "-s", f"{w}x{h}",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
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

        frame_bytes = _FRAME_SIZE_RGB24(w, h)
        raw = proc.stdout
        if len(raw) % frame_bytes != 0:
            log.warning("VideoDecoder: output %d bytes is not a multiple of "
                        "frame_size %d — truncating tail",
                        len(raw), frame_bytes)
        count = len(raw) // frame_bytes

        self.frames = [
            RawFrame(data=raw[i * frame_bytes:(i + 1) * frame_bytes],
                     width=w, height=h)
            for i in range(count)
        ]
        log.info("VideoDecoder: decoded %d frame(s) at %dx%d from %s",
                 count, w, h, self.path.name)
        return self.frames


def _ffmpeg_available() -> bool:
    """Quick check whether ffmpeg is on PATH."""
    for dir_ in os.get_exec_path():
        if (Path(dir_) / "ffmpeg").exists():
            return True
        if (Path(dir_) / "ffmpeg.exe").exists():
            return True
    return False


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
        self.frames: list[RawFrame] = []
        self.timestamps: list[int] = []
        self.delays: list[int] = []
        self.fps: int = _DEFAULT_FPS

    def decode(self) -> list[RawFrame]:
        """Read header + JPEG payloads, decode each, return frames."""
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

            for _ in range(frame_count):
                (size,) = struct.unpack_from("<i", data, pos)
                pos += 4
                if size <= 0 or pos + size > len(data):
                    raise ThemeError(
                        f".zt frame size out of range or truncated at frame "
                        f"{len(self.frames)}: size={size} pos={pos} "
                        f"file_len={len(data)}"
                    )
                jpeg = data[pos:pos + size]
                pos += size
                self.frames.append(self._decode_jpeg(jpeg))
        except struct.error as e:
            raise ThemeError(f".zt parse error in {self.path}: {e}") from e

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

    def _decode_jpeg(self, jpeg: bytes) -> RawFrame:
        """One ffmpeg run per JPEG → scaled RGB24 frame."""
        w, h = self.size
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "jpeg_pipe", "-i", "pipe:0",
            "-vf", f"scale={w}:{h}",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "pipe:1",
        ]
        try:
            proc = subprocess.run(
                cmd, input=jpeg, capture_output=True,
                timeout=10, check=False,
            )
        except FileNotFoundError as e:
            raise ThemeError("ffmpeg not found on PATH") from e

        if proc.returncode != 0 or not proc.stdout:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            log.warning(
                "ZtDecoder: JPEG decode failed (%s) — substituting blank frame",
                stderr[:120] or "no stderr",
            )
            return RawFrame(data=bytes(w * h * 3), width=w, height=h)

        return RawFrame(data=proc.stdout[:w * h * 3], width=w, height=h)


# =========================================================================
# MediaService — playback state on top of the decoder
# =========================================================================


@dataclass
class Playback:
    """Current playback cursor for a video-backed theme."""
    frames: list[RawFrame]
    fps: int = _DEFAULT_FPS
    cursor: int = 0
    paused: bool = False
    loop: bool = True

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def current(self) -> RawFrame | None:
        return self.frames[self.cursor] if self.frames else None

    def advance(self) -> RawFrame | None:
        """Return the current frame and advance the cursor.

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
                   size: tuple[int, int],
                   fps: int = _DEFAULT_FPS,
                   rotation_degrees: int = 0,
                   duration_s: float | None = None) -> Playback:
        """Decode a video / .zt animation for a device, replacing any previous playback.

        Dispatches by suffix: ``.zt`` → :class:`ZtDecoder`, anything else
        through ``ffmpeg`` via :class:`VideoDecoder`.  ``.zt`` carries its
        own per-frame timing so we honour the decoder's derived ``fps``
        when the caller takes the default.
        """
        log.info(
            "load_video: key=%s path=%s size=%dx%d fps=%d rotate=%d",
            device_key, path, size[0], size[1], fps, rotation_degrees,
        )
        if path.suffix.lower() == ".zt":
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
