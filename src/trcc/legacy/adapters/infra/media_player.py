"""Media frame decoders for TRCC Linux.

Pure infrastructure — decodes video/animation files into RawFrame lists.
No playback state (play/pause/stop/seek). That belongs in MediaService.
No framework deps (no PIL, no Qt) — raw RGB24 bytes only.

Decoders:
    VideoDecoder   — FFmpeg pipe → list[RawFrame] + fps
    ThemeZtDecoder — Theme.zt binary → list[RawFrame] + per-frame delays
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess

from trcc.legacy.core.platform import SUBPROCESS_NO_WINDOW as _NO_WINDOW
from trcc.legacy.core.ports import RawFrame

log = logging.getLogger(__name__)


class VideoDecoder:
    """Decode video frames via FFmpeg pipe. No playback state."""

    def __init__(self, video_path: str, target_size: tuple[int, int],
                 fit_mode: str = 'fill') -> None:
        self.frames: list[RawFrame] = []
        self.fps: int = 16  # Windows: originalImageHz = 16

        self._decode(video_path, target_size, fit_mode)

    def _decode(self, video_path: str, target_size: tuple[int, int],
                fit_mode: str = 'fill') -> None:
        """Decode all frames through FFmpeg pipe.

        fit_mode:
            'fill'   — stretch to fill LCD (current default)
            'width'  — scale width to LCD, letterbox/crop height (C# buttonTPJCW)
            'height' — scale height to LCD, letterbox/crop width (C# buttonTPJCH)
        """
        w, h = target_size
        if w <= 0 or h <= 0:
            raise ValueError(
                f"VideoDecoder: target_size {target_size} has a non-positive "
                "dimension — the device resolution wasn't determined (handshake "
                "may have failed to extract PM/SUB). Run `trcc report` and check "
                "the handshake section."
            )

        if fit_mode in ('width', 'height'):
            video_dims = self._probe_dimensions(video_path)
            if video_dims:
                vw, vh = video_dims
                if fit_mode == 'width':
                    scale_w, scale_h = w, max(2, int(vh * w / vw) & ~1)
                else:
                    scale_w, scale_h = max(2, int(vw * h / vh) & ~1), h
            else:
                scale_w, scale_h = w, h
        else:
            scale_w, scale_h = w, h

        result = subprocess.run([
            'ffmpeg', '-i', video_path,
            '-r', str(self.fps),
            '-vf', f'scale={scale_w}:{scale_h}',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-loglevel', 'error', 'pipe:1',
        ], capture_output=True, timeout=300, creationflags=_NO_WINDOW)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr.decode()[:200]}")

        raw = result.stdout
        frame_size = scale_w * scale_h * 3
        need_composite = fit_mode in ('width', 'height') and (scale_w != w or scale_h != h)

        for i in range(0, len(raw), frame_size):
            chunk = raw[i:i + frame_size]
            if len(chunk) < frame_size:
                break

            if need_composite:
                # Letterbox / center-crop onto black canvas — pure bytes
                canvas = bytearray(w * h * 3)  # black RGB24
                px = (w - scale_w) // 2
                py = (h - scale_h) // 2

                if scale_w > w or scale_h > h:
                    # Center-crop: frame overflows canvas
                    src_x = max(0, (scale_w - w) // 2)
                    src_y = max(0, (scale_h - h) // 2)
                    copy_w = min(w, scale_w - src_x)
                    copy_h = min(h, scale_h - src_y)
                    dst_x = max(0, px)
                    dst_y = max(0, py)
                    for row in range(copy_h):
                        src_off = ((src_y + row) * scale_w + src_x) * 3
                        dst_off = ((dst_y + row) * w + dst_x) * 3
                        canvas[dst_off:dst_off + copy_w * 3] = chunk[src_off:src_off + copy_w * 3]
                else:
                    # Letterbox: frame fits inside canvas
                    for row in range(scale_h):
                        src_off = row * scale_w * 3
                        dst_off = ((py + row) * w + px) * 3
                        canvas[dst_off:dst_off + scale_w * 3] = chunk[src_off:src_off + scale_w * 3]

                self.frames.append(RawFrame(bytes(canvas), w, h))
            else:
                self.frames.append(RawFrame(bytes(chunk), scale_w, scale_h))

    @staticmethod
    def _probe_dimensions(video_path: str) -> tuple[int, int] | None:
        """Get original video dimensions via ffprobe."""
        try:
            result = subprocess.run([
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'csv=p=0',
                video_path,
            ], capture_output=True, timeout=10, text=True, creationflags=_NO_WINDOW)
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(',')
                if len(parts) >= 2:
                    return int(parts[0]), int(parts[1])
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            log.debug("ffprobe video dimensions failed: %s", e)
        return None

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    def close(self) -> None:
        self.frames = []

    @staticmethod
    def extract_frames(
        video_path: str,
        output_dir: str,
        target_size: tuple[int, int],
        max_frames: int | None = None,
    ) -> int:
        """Extract video frames to PNG files via FFmpeg."""
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
            result = subprocess.run(cmd, capture_output=True, timeout=600,
                                    creationflags=_NO_WINDOW)
            if result.returncode != 0:
                log.error("FFmpeg error: %s", result.stderr.decode()[:200])
                return 0
        except subprocess.TimeoutExpired:
            log.error("FFmpeg timed out")
            return 0
        except (OSError, subprocess.SubprocessError) as e:
            log.error("FFmpeg failed: %s", e)
            return 0

        extracted = len([
            f for f in os.listdir(output_dir)
            if f.startswith('frame_') and f.endswith('.png')
        ])
        log.info("Extracted %d frames to %s", extracted, output_dir)
        return extracted


class ThemeZtDecoder:
    """Decode Theme.zt animation files. No playback state.

    Theme.zt format (Windows UCVideoCut.BmpToThemeFile):
    - byte: 0xDC magic (220)
    - int32: frame_count
    - int32[frame_count]: timestamps in ms
    - for each frame: int32 size + JPEG bytes
    """

    def __init__(self, zt_path: str, target_size: tuple[int, int]) -> None:
        self.frames: list[RawFrame] = []
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
                jpeg_bytes = f.read(size)
                frame = self._decode_jpeg(jpeg_bytes, target_size)
                self.frames.append(frame)

        # Calculate delays from timestamps
        for i in range(len(self.timestamps)):
            if i < len(self.timestamps) - 1:
                delay = self.timestamps[i + 1] - self.timestamps[i]
            else:
                delay = self.delays[-1] if self.delays else 42  # ~24fps default
            self.delays.append(max(1, delay))

    @staticmethod
    def _decode_jpeg(jpeg_bytes: bytes,
                     target_size: tuple[int, int]) -> RawFrame:
        """Decode JPEG bytes → RawFrame via ffmpeg pipe."""
        tw, th = target_size
        vf_args = ['-vf', f'scale={tw}:{th}']
        cmd = [
            'ffmpeg', '-f', 'jpeg_pipe', '-i', 'pipe:0',
            *vf_args,
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-v', 'error', 'pipe:1',
        ]
        try:
            result = subprocess.run(
                cmd, input=jpeg_bytes, capture_output=True,
                timeout=10, creationflags=_NO_WINDOW,
            )
            if result.returncode != 0 or not result.stdout:
                raise ValueError(f"ffmpeg decode failed: {result.stderr[:100]!r}")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            log.warning("ThemeZtDecoder: JPEG decode failed (%s), returning blank", exc)
            w, h = target_size
            return RawFrame(bytes(w * h * 3), w, h)

        w, h = target_size
        return RawFrame(result.stdout[:w * h * 3], w, h)

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def fps(self) -> float:
        """Average fps derived from delays."""
        if not self.delays:
            return 24.0
        avg_delay = sum(self.delays) / len(self.delays)
        return 1000.0 / avg_delay if avg_delay > 0 else 24.0

    def close(self) -> None:
        self.frames = []


def is_animated_gif(path: str | os.PathLike) -> bool:
    """Return True if the GIF at *path* has more than one frame (ffprobe)."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=nb_frames',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(path),
        ], capture_output=True, timeout=5, text=True, creationflags=_NO_WINDOW)
        if result.returncode == 0 and result.stdout.strip().isdigit():
            return int(result.stdout.strip()) > 1
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.debug("ffprobe gif frame-count failed for %s: %s", path, e)
    return False

