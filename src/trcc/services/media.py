"""MediaService — video decoding + playback state.

VideoDecoder pipes ffmpeg → raw RGB24 frames (in-memory, no BMP-to-disk
like legacy Windows did).  MediaService owns playback state (current
frame, cursor, fps) so DisplayService can ask for "the frame at time t".

ffmpeg is a runtime dependency.  If it isn't on PATH, decode() raises
ThemeError with a user-facing install hint.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

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
                 duration_s: Optional[float] = None) -> None:
        self.path = path
        self.size = size
        self.fps = fps
        self.rotation_degrees = rotation_degrees
        self.duration_s = duration_s
        self.frames: List[RawFrame] = []

    def decode(self) -> List[RawFrame]:
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
# MediaService — playback state on top of the decoder
# =========================================================================


@dataclass
class Playback:
    """Current playback cursor for a video-backed theme."""
    frames: List[RawFrame]
    fps: int = _DEFAULT_FPS
    cursor: int = 0

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def current(self) -> Optional[RawFrame]:
        return self.frames[self.cursor] if self.frames else None

    def advance(self) -> Optional[RawFrame]:
        """Return the current frame and advance the cursor (wraps)."""
        if not self.frames:
            return None
        frame = self.frames[self.cursor]
        self.cursor = (self.cursor + 1) % len(self.frames)
        return frame

    def reset(self) -> None:
        self.cursor = 0


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
                   duration_s: Optional[float] = None) -> Playback:
        """Decode a video for a device, replacing any previous playback."""
        decoder = VideoDecoder(
            path=path, size=size, fps=fps,
            rotation_degrees=rotation_degrees,
            duration_s=duration_s,
        )
        decoder.decode()
        playback = Playback(frames=decoder.frames, fps=fps)
        self._playbacks[device_key] = playback
        return playback

    def playback(self, device_key: str) -> Optional[Playback]:
        return self._playbacks.get(device_key)

    def unload(self, device_key: str) -> None:
        """Drop a playback, freeing its frame buffers."""
        self._playbacks.pop(device_key, None)
