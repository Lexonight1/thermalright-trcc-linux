"""Tests for services/media.py — video/animation playback service.

Covers:
- Construction and strict DI
- set_target_size / set_fit_mode
- load() — MP4, GIF, .zt, .zt fallback to video decoder
- Playback state machine: play, pause, stop, toggle, seek
- Frame access: get_frame, advance_frame (looping, stop-at-end)
- tick() — frame counter, progress counter, LCD send interval
- Properties: is_playing, frame_interval_ms, source_path, has_frames
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trcc.legacy.core.models import PlaybackState
from trcc.legacy.services.media import MediaService


def _make_decoder(frame_count: int = 10, fps: float = 30.0,
                  frames: list | None = None,
                  delays: list | None = None):
    """Create a mock decoder class that returns a mock instance."""
    decoder = MagicMock()
    decoder.frame_count = frame_count
    decoder.fps = fps
    decoder.frames = frames if frames is not None else [f"frame_{i}" for i in range(frame_count)]
    if delays is not None:
        decoder.delays = delays
    return decoder


def _make_service(frame_count: int = 10, fps: float = 30.0) -> MediaService:
    """Create a MediaService with mock decoders pre-wired."""
    from trcc.legacy.core.models import FBL_PROFILES
    video_cls = MagicMock(return_value=_make_decoder(frame_count, fps))
    zt_cls = MagicMock(return_value=_make_decoder(frame_count, fps, delays=[33] * frame_count))
    svc = MediaService(video_decoder_cls=video_cls, zt_decoder_cls=zt_cls)
    p = FBL_PROFILES[100]
    svc.set_target_size(p.width, p.height)
    return svc


# =========================================================================
# Construction
# =========================================================================


class TestConstruction:
    def test_default_state(self):
        svc = _make_service()
        assert svc.is_playing is False
        assert svc.source_path is None
        assert svc.has_frames is False
        assert svc.fit_mode == 'fill'

    def test_missing_decoders_raises(self):
        svc = MediaService()
        with pytest.raises(RuntimeError, match="requires"):
            svc._get_decoders()


# =========================================================================
# Target size and fit mode
# =========================================================================


class TestTargetAndFit:
    def test_set_target_size(self):
        svc = _make_service()
        svc.set_target_size(480, 480)
        assert svc._target_size == (480, 480)

    def test_set_fit_mode_valid(self):
        svc = _make_service()
        svc.set_fit_mode('width')
        assert svc.fit_mode == 'width'

    def test_set_fit_mode_invalid_defaults_to_fill(self):
        svc = _make_service()
        svc.set_fit_mode('invalid')
        assert svc.fit_mode == 'fill'

    def test_set_fit_mode_reloads_if_video_loaded(self, tmp_path):
        svc = _make_service()
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake")
        svc.load(video_file)
        svc.play()
        reloaded = svc.set_fit_mode('height')
        assert reloaded is True

    def test_set_fit_mode_skips_zt(self, tmp_path):
        svc = _make_service()
        zt_file = tmp_path / "test.zt"
        zt_file.write_bytes(b"fake")
        svc.load(zt_file)
        reloaded = svc.set_fit_mode('width')
        assert reloaded is False


# =========================================================================
# Load
# =========================================================================


class TestLoad:
    def test_load_mp4(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        assert svc.load(f) is True
        assert svc.has_frames is True
        assert svc.source_path == f
        assert svc.state.total_frames == 10
        assert svc.state.fps == 30.0

    def test_load_zt(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "theme.zt"
        f.write_bytes(b"fake")
        assert svc.load(f) is True
        assert svc.has_frames is True

    def test_load_zt_fallback_to_video(self, tmp_path):
        """If .zt decoder raises ValueError, falls back to video decoder."""
        from trcc.legacy.core.models import FBL_PROFILES
        video_cls = MagicMock(return_value=_make_decoder(5, 24.0))
        zt_cls = MagicMock(side_effect=ValueError("not a valid zt"))
        svc = MediaService(video_decoder_cls=video_cls, zt_decoder_cls=zt_cls)
        p = FBL_PROFILES[100]
        svc.set_target_size(p.width, p.height)
        f = tmp_path / "renamed.zt"
        f.write_bytes(b"fake")
        assert svc.load(f) is True
        video_cls.assert_called_once()

    def test_load_stops_previous(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        assert svc.is_playing
        svc.load(f)  # Loading new stops old
        assert svc.is_playing is False

    def test_load_failure_returns_false(self, tmp_path):
        from trcc.legacy.core.models import FBL_PROFILES
        video_cls = MagicMock(side_effect=RuntimeError("decode error"))
        zt_cls = MagicMock()
        svc = MediaService(video_decoder_cls=video_cls, zt_decoder_cls=zt_cls)
        p = FBL_PROFILES[100]
        svc.set_target_size(p.width, p.height)
        f = tmp_path / "bad.mp4"
        f.write_bytes(b"fake")
        assert svc.load(f) is False

    def test_load_zero_fps_defaults_to_16(self, tmp_path):
        from trcc.legacy.core.models import FBL_PROFILES
        video_cls = MagicMock(return_value=_make_decoder(5, 0))
        zt_cls = MagicMock()
        svc = MediaService(video_decoder_cls=video_cls, zt_decoder_cls=zt_cls)
        p = FBL_PROFILES[100]
        svc.set_target_size(p.width, p.height)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        assert svc.state.fps == 16

    def test_load_without_preload(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f, preload=False)
        assert svc.has_frames is False  # Frames not loaded


# =========================================================================
# Playback state machine
# =========================================================================


class TestPlayback:
    def test_play(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        assert svc.is_playing is True

    def test_play_without_frames_stays_stopped(self):
        svc = _make_service()
        svc.play()
        assert svc.is_playing is False

    def test_pause(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        svc.pause()
        assert svc.state.state == PlaybackState.PAUSED

    def test_stop_resets_frame(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        svc._state.current_frame = 5
        svc.stop()
        assert svc.state.state == PlaybackState.STOPPED
        assert svc.state.current_frame == 0

    def test_toggle_play_pause(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.toggle()
        assert svc.is_playing is True
        svc.toggle()
        assert svc.state.state == PlaybackState.PAUSED

    def test_seek_to_percent(self, tmp_path):
        svc = _make_service(frame_count=100)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.seek(50.0)
        assert svc.state.current_frame == 50

    def test_seek_clamps_to_bounds(self, tmp_path):
        svc = _make_service(frame_count=10)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.seek(200.0)
        assert svc.state.current_frame == 9  # Clamped to max
        svc.seek(-10.0)
        assert svc.state.current_frame == 0  # Clamped to min

    def test_seek_with_no_frames(self):
        svc = _make_service()
        svc.seek(50.0)  # No crash, just no-op
        assert svc.state.current_frame == 0


# =========================================================================
# Frame access
# =========================================================================


class TestFrameAccess:
    def test_get_frame_current(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        frame = svc.get_frame()
        assert frame == "frame_0"

    def test_get_frame_by_index(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        assert svc.get_frame(5) == "frame_5"

    def test_get_frame_out_of_range(self, tmp_path):
        svc = _make_service(frame_count=3)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        assert svc.get_frame(99) is None

    def test_advance_frame_loops(self, tmp_path):
        svc = _make_service(frame_count=3)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        svc._state.current_frame = 2  # Last frame
        frame = svc.advance_frame()
        assert frame == "frame_2"
        assert svc.state.current_frame == 0  # Looped

    def test_advance_frame_stops_when_no_loop(self, tmp_path):
        svc = _make_service(frame_count=3)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        svc._state.loop = False
        svc._state.current_frame = 2
        svc.advance_frame()
        assert svc.state.state == PlaybackState.STOPPED

    def test_advance_frame_not_playing(self):
        svc = _make_service()
        assert svc.advance_frame() is None


# =========================================================================
# tick()
# =========================================================================


class TestTick:
    def test_tick_not_playing_returns_none(self):
        svc = _make_service()
        frame, should_send, progress = svc.tick()
        assert frame is None
        assert should_send is False
        assert progress is None

    def test_tick_returns_frame_and_send(self, tmp_path):
        svc = _make_service()
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        frame, should_send, _ = svc.tick()
        assert frame == "frame_0"
        assert should_send is True  # First tick always sends

    def test_tick_progress_every_8_frames(self, tmp_path):
        svc = _make_service(frame_count=20)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        progress_reported = False
        for _ in range(8):
            _, _, progress = svc.tick()
            if progress is not None:
                progress_reported = True
        assert progress_reported

    def test_tick_lcd_send_interval(self, tmp_path):
        svc = _make_service()
        svc.LCD_SEND_INTERVAL = 3
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        svc.play()
        sends = []
        for _ in range(6):
            _, should_send, _ = svc.tick()
            sends.append(should_send)
        # Should send every 3rd frame
        assert sends == [False, False, True, False, False, True]


# =========================================================================
# Properties
# =========================================================================


class TestProperties:
    def test_frame_interval_ms(self, tmp_path):
        svc = _make_service(fps=30.0)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        assert svc.frame_interval_ms == svc.state.frame_interval_ms

    def test_time_strings(self, tmp_path):
        svc = _make_service(frame_count=300, fps=30.0)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"fake")
        svc.load(f)
        assert svc.current_time_str == "00:00"
        assert svc.total_time_str == "00:10"  # 300 frames / 30 fps = 10s
