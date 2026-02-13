"""
Media player and animation support for TRCC Linux.

Video/animation frame extraction (FFmpeg) and Theme.zt playback.
"""

from __future__ import annotations

import io
import logging
import os
import struct
import subprocess
from abc import ABC, abstractmethod

from PIL import Image

log = logging.getLogger(__name__)


def _check_ffmpeg() -> bool:
    """Check if ffmpeg is available in PATH."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


FFMPEG_AVAILABLE = _check_ffmpeg()


class AbstractMediaPlayer(ABC):
    """Base class for frame-based media players.

    Provides common playback state and frame navigation logic
    shared by GIF, video, and Theme.zt animation players.
    """

    def __init__(self) -> None:
        self.frames: list[Image.Image] = []
        self.frame_count: int = 0
        self.current_frame: int = 0
        self.playing: bool = False
        self.loop: bool = True

    def play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def stop(self) -> None:
        self.playing = False
        self.current_frame = 0

    def reset(self) -> None:
        self.current_frame = 0

    def is_playing(self) -> bool:
        return self.playing

    def get_current_frame(self) -> Image.Image | None:
        if 0 <= self.current_frame < len(self.frames):
            return self.frames[self.current_frame]
        return self.frames[0] if self.frames else None

    def next_frame(self) -> Image.Image | None:
        self.current_frame += 1
        if self.current_frame >= self.frame_count:
            if self.loop:
                self.current_frame = 0
            else:
                self.current_frame = self.frame_count - 1
                self.playing = False
        return self.get_current_frame()

    @abstractmethod
    def get_delay(self) -> int:
        """Get delay for current frame in milliseconds."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Release resources."""
        ...


class VideoPlayer(AbstractMediaPlayer):
    """Video player — decodes all frames via FFmpeg pipe to memory."""

    def __init__(self, video_path: str, target_size: tuple[int, int] = (320, 320)) -> None:
        if not FFMPEG_AVAILABLE:
            raise RuntimeError(
                "FFmpeg not available. Install: sudo dnf install ffmpeg"
            )
        super().__init__()
        self.video_path = video_path
        self.target_size = target_size
        self.fps: int = 16  # Windows: originalImageHz = 16

        self._load_via_pipe()

    def _load_via_pipe(self) -> None:
        """Decode all frames through FFmpeg pipe."""
        w, h = self.target_size
        result = subprocess.run([
            'ffmpeg', '-i', self.video_path,
            '-r', str(self.fps),
            '-vf', f'scale={w}:{h}',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-loglevel', 'error', 'pipe:1',
        ], capture_output=True, timeout=300)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr.decode()[:200]}")

        raw = result.stdout
        frame_size = w * h * 3
        for i in range(0, len(raw), frame_size):
            chunk = raw[i:i + frame_size]
            if len(chunk) < frame_size:
                break
            self.frames.append(Image.frombytes('RGB', (w, h), chunk))

        self.frame_count = len(self.frames)

    def get_delay(self) -> int:
        return int(1000 / self.fps)

    def close(self) -> None:
        self.frames = []

    @staticmethod
    def extract_frames(
        video_path: str,
        output_dir: str,
        target_size: tuple[int, int] = (320, 320),
        max_frames: int | None = None,
    ) -> int:
        """Extract video frames to PNG files via FFmpeg."""
        if not FFMPEG_AVAILABLE:
            log.warning("FFmpeg not available for video extraction")
            return 0

        os.makedirs(output_dir, exist_ok=True)
        w, h = target_size

        cmd = [
            'ffmpeg', '-i', video_path, '-y',
            '-vf', f'scale={w}:{h}',
        ]
        if max_frames:
            cmd.extend(['-vframes', str(max_frames)])
        cmd.extend(['-f', 'image2', os.path.join(output_dir, 'frame_%04d.png')])

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=600)
            if result.returncode != 0:
                log.error("FFmpeg error: %s", result.stderr.decode()[:200])
                return 0
        except subprocess.TimeoutExpired:
            log.error("FFmpeg timed out")
            return 0
        except Exception:
            log.exception("FFmpeg failed")
            return 0

        extracted = len([
            f for f in os.listdir(output_dir)
            if f.startswith('frame_') and f.endswith('.png')
        ])
        log.info("Extracted %d frames to %s", extracted, output_dir)
        return extracted


class ThemeZtPlayer(AbstractMediaPlayer):
    """Plays Theme.zt animation files.

    Theme.zt format (Windows UCVideoCut.BmpToThemeFile):
    - byte: 0xDC magic (220)
    - int32: frame_count
    - int32[frame_count]: timestamps in ms
    - for each frame: int32 size + JPEG bytes
    """

    def __init__(self, zt_path: str, target_size: tuple[int, int] | None = None) -> None:
        super().__init__()
        self.zt_path = zt_path
        self.target_size = target_size
        self.timestamps: list[int] = []
        self.delays: list[int] = []

        with open(zt_path, 'rb') as f:
            magic = struct.unpack('B', f.read(1))[0]
            if magic != 0xDC:
                raise ValueError(f"Invalid Theme.zt magic: 0x{magic:02X}, expected 0xDC")

            frame_count = struct.unpack('<i', f.read(4))[0]

            for _ in range(frame_count):
                self.timestamps.append(struct.unpack('<i', f.read(4))[0])

            for _ in range(frame_count):
                size = struct.unpack('<i', f.read(4))[0]
                img = Image.open(io.BytesIO(f.read(size)))
                if target_size and img.size != target_size:
                    img = img.resize(target_size, Image.Resampling.LANCZOS)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                self.frames.append(img)

        self.frame_count = len(self.frames)

        # Calculate delays from timestamps
        for i in range(len(self.timestamps)):
            if i < len(self.timestamps) - 1:
                delay = self.timestamps[i + 1] - self.timestamps[i]
            else:
                delay = self.delays[-1] if self.delays else 42  # ~24fps default
            self.delays.append(max(1, delay))

    def get_current_frame(self) -> Image.Image | None:
        """Get current frame (copy to prevent mutation)."""
        if 0 <= self.current_frame < len(self.frames):
            return self.frames[self.current_frame].copy()
        return None

    def get_delay(self) -> int:
        if self.current_frame < len(self.delays):
            return self.delays[self.current_frame]
        return 42

    def seek(self, position: float) -> None:
        """Seek to position (0.0-1.0)."""
        position = max(0.0, min(1.0, position))
        self.current_frame = int(position * (len(self.frames) - 1))

    def get_progress(self) -> int:
        """Get current playback progress (0-100)."""
        if len(self.frames) <= 1:
            return 0
        return int((self.current_frame / (len(self.frames) - 1)) * 100)

    def close(self) -> None:
        for frame in self.frames:
            if hasattr(frame, 'close'):
                frame.close()
        self.frames = []


# Backward-compat aliases (dead code, kept for external imports)
GIFAnimator = VideoPlayer
GIFThemeLoader = VideoPlayer
