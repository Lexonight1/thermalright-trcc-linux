"""Video/animation playback service.

Pure Python (FFmpeg via media_player decoders), no Qt dependencies.
Owns all playback state — decoders are pure frame sources.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..core.models import PlaybackState, VideoState

log = logging.getLogger(__name__)


class MediaService:
    """Video/animation lifecycle: load, play, pause, stop, advance."""

    # LCD send interval: send every Nth frame.
    LCD_SEND_INTERVAL = 1

    def __init__(self, video_decoder_cls: Any = None,
                 zt_decoder_cls: Any = None) -> None:
        self._video_decoder_cls = video_decoder_cls
        self._zt_decoder_cls = zt_decoder_cls
        self._state = VideoState()
        self._frames: list[Any] = []
        self._delays: list[int] = []  # Per-frame delays (ms), for .zt files
        self._source_path: Path | None = None
        self._target_size: tuple[int, int] | None = None
        self._fit_mode: str = 'fill'  # 'fill', 'width', 'height'
        self._decoder: Any = None
        self._frame_counter = 0
        self._progress_counter = 0

    def _get_decoders(self) -> tuple[Any, Any]:
        """Return (VideoDecoder, ThemeZtDecoder) classes (must be injected)."""
        if self._video_decoder_cls is None or self._zt_decoder_cls is None:
            raise RuntimeError(
                "MediaService requires video_decoder_cls and zt_decoder_cls. "
                "Use ControllerBuilder to wire dependencies.")
        return self._video_decoder_cls, self._zt_decoder_cls

    # ── Target size ──────────────────────────────────────────────────

    def set_target_size(self, width: int, height: int) -> None:
        self._target_size = (width, height)

    def set_fit_mode(self, mode: str) -> bool:
        """Set video fit mode and re-decode if a video is loaded.

        C# UCBoFangQiKongZhi: buttonTPJCW (width-fit) / buttonTPJCH (height-fit).
        Returns True if frames were reloaded.
        """
        if mode not in ('fill', 'width', 'height'):
            mode = 'fill'
        self._fit_mode = mode
        # Re-decode if a video source exists (.zt is pre-rendered, skip)
        if (self._source_path and self._source_path.exists()
                and self._source_path.suffix.lower() != '.zt'):
            current_frame = self._state.current_frame
            was_playing = self.is_playing
            if self.load(self._source_path):
                self._state.current_frame = min(
                    current_frame, max(0, self._state.total_frames - 1))
                if was_playing:
                    self.play()
                return True
        return False

    @property
    def fit_mode(self) -> str:
        return self._fit_mode

    # ── Load ─────────────────────────────────────────────────────────

    def load(self, path: Path, preload: bool = True) -> bool:
        """Load video/animation file (.mp4, .gif, .zt).

        Returns True if loaded successfully.
        """
        if self._target_size is None:
            raise RuntimeError(
                "MediaService.set_target_size() must be called before loading media")
        self.stop()
        self._source_path = path
        self._frames.clear()
        self._delays.clear()

        try:
            VideoDecoder, ThemeZtDecoder = self._get_decoders()

            suffix = path.suffix.lower()
            if suffix == '.zt':
                try:
                    self._decoder = ThemeZtDecoder(str(path), self._target_size)
                    self._delays = list(self._decoder.delays)
                except ValueError:
                    # Not a valid .zt archive (e.g. MP4 renamed to .zt by
                    # older save code) — fall back to video decoder
                    self._decoder = VideoDecoder(
                        str(path), self._target_size, fit_mode=self._fit_mode)
            else:
                self._decoder = VideoDecoder(
                    str(path), self._target_size, fit_mode=self._fit_mode)

            self._state.total_frames = self._decoder.frame_count
            self._state.fps = self._decoder.fps if self._decoder.fps > 0 else 16
            self._state.current_frame = 0
            self._state.state = PlaybackState.STOPPED

            if preload:
                self._frames = list(self._decoder.frames)

            return True
        except Exception as e:
            log.error("Failed to load video: %s", e)
            return False

    # ── Playback control ─────────────────────────────────────────────

    def play(self) -> None:
        if self._frames:
            self._state.state = PlaybackState.PLAYING
            self._frame_counter = 0

    def pause(self) -> None:
        self._state.state = PlaybackState.PAUSED

    def stop(self) -> None:
        self._state.state = PlaybackState.STOPPED
        self._state.current_frame = 0

    def toggle(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def seek(self, percent: float) -> None:
        if self._state.total_frames > 0:
            frame = int((percent / 100) * self._state.total_frames)
            self._state.current_frame = max(
                0, min(frame, self._state.total_frames - 1))

    # ── Frame access ─────────────────────────────────────────────────

    def get_frame(self, index: int | None = None) -> Any | None:
        """Get frame at index (or current frame)."""
        if index is None:
            index = self._state.current_frame
        if 0 <= index < len(self._frames):
            return self._frames[index]
        return None

    def advance_frame(self) -> Any | None:
        """Advance to next frame and return it.

        Returns native surface (QImage) or None if not playing.
        """
        if self._state.state != PlaybackState.PLAYING:
            return None

        frame = self.get_frame()

        self._state.current_frame += 1
        if self._state.current_frame >= self._state.total_frames:
            if self._state.loop:
                self._state.current_frame = 0
            else:
                self._state.state = PlaybackState.STOPPED

        return frame

    def tick(self) -> tuple[Any | None, bool, tuple[float, str, str] | None]:
        """Called by timer to advance one frame.

        Returns:
            (frame, should_send_to_lcd, progress_tuple_or_none)
        """
        if not self.is_playing:
            return None, False, None

        frame = self.advance_frame()
        if not frame:
            return None, False, None

        # Progress update at ~2fps (every 8th frame)
        progress_info = None
        self._progress_counter += 1
        if self._progress_counter >= 8:
            self._progress_counter = 0
            progress_info = (
                self._state.progress,
                self._state.current_time_str,
                self._state.total_time_str,
            )

        # LCD send with frame skipping
        should_send = False
        self._frame_counter += 1
        if self._frame_counter >= self.LCD_SEND_INTERVAL:
            self._frame_counter = 0
            should_send = True

        return frame, should_send, progress_info

    # ── Properties ───────────────────────────────────────────────────

    @property
    def is_playing(self) -> bool:
        return self._state.state == PlaybackState.PLAYING

    @property
    def frame_interval_ms(self) -> int:
        return self._state.frame_interval_ms

    @property
    def source_path(self) -> Path | None:
        return self._source_path

    @property
    def current_time_str(self) -> str:
        return self._state.current_time_str

    @property
    def total_time_str(self) -> str:
        return self._state.total_time_str

    @property
    def has_frames(self) -> bool:
        return bool(self._frames)

    @property
    def state(self) -> VideoState:
        return self._state

