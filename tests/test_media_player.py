"""Tests for media_player – video animation and Theme.zt playback."""

import io
import os
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from trcc.media_player import (
    GIFAnimator,
    GIFThemeLoader,
    ThemeZtPlayer,
    VideoPlayer,
    _check_ffmpeg,
)


def _make_theme_zt(frames=4, size=(8, 8), quality=50):
    """Create a minimal Theme.zt binary file. Returns path."""
    fd, path = tempfile.mkstemp(suffix='.zt')
    os.close(fd)

    jpeg_blobs = []
    for i in range(frames):
        img = Image.new('RGB', size, color=(0, i * 60, 0))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality)
        jpeg_blobs.append(buf.getvalue())

    # Timestamps: 0, 42, 84, ...
    timestamps = [i * 42 for i in range(frames)]

    with open(path, 'wb') as f:
        f.write(struct.pack('B', 0xDC))           # magic
        f.write(struct.pack('<i', frames))          # frame_count
        for ts in timestamps:
            f.write(struct.pack('<i', ts))          # timestamps
        for blob in jpeg_blobs:
            f.write(struct.pack('<i', len(blob)))   # size
            f.write(blob)                           # JPEG data

    return path


def _make_preloaded_player(frame_count=5, fps=16, size=(320, 320)):
    """Create a VideoPlayer with mocked internals for pure-logic testing."""
    with patch.object(VideoPlayer, '__init__', lambda self, *a, **kw: None):
        player = VideoPlayer.__new__(VideoPlayer)
    player.video_path = '/fake/video.mp4'
    player.target_size = size
    player.fps = fps
    player.current_frame = 0
    player.playing = False
    player.loop = True
    player.frames = [Image.new('RGB', size, (i * 50, 0, 0))
                     for i in range(frame_count)]
    player.frame_count = frame_count
    return player


# -- Backward-compat aliases ------------------------------------------------

class TestBackwardCompatAliases(unittest.TestCase):
    """GIFAnimator and GIFThemeLoader are aliases for VideoPlayer."""

    def test_gif_animator_is_video_player(self):
        self.assertIs(GIFAnimator, VideoPlayer)

    def test_gif_theme_loader_is_video_player(self):
        self.assertIs(GIFThemeLoader, VideoPlayer)


# -- AbstractMediaPlayer (via VideoPlayer preloaded) -----------------------

class TestAbstractMediaPlayer(unittest.TestCase):
    """Core playback logic inherited by VideoPlayer from AbstractMediaPlayer."""

    def test_get_current_frame(self):
        p = _make_preloaded_player()
        frame = p.get_current_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.size, (320, 320))

    def test_get_current_frame_out_of_range(self):
        p = _make_preloaded_player()
        p.current_frame = 999
        frame = p.get_current_frame()
        self.assertEqual(frame, p.frames[0])

    def test_get_current_frame_empty(self):
        p = _make_preloaded_player(frame_count=0)
        p.frames = []
        self.assertIsNone(p.get_current_frame())

    def test_next_frame_advances(self):
        p = _make_preloaded_player()
        p.next_frame()
        self.assertEqual(p.current_frame, 1)

    def test_next_frame_returns_image(self):
        p = _make_preloaded_player()
        frame = p.next_frame()
        self.assertIsInstance(frame, Image.Image)

    def test_next_frame_loops(self):
        p = _make_preloaded_player(frame_count=3)
        for _ in range(3):
            p.next_frame()
        self.assertEqual(p.current_frame, 0)

    def test_next_frame_stops_no_loop(self):
        p = _make_preloaded_player(frame_count=3)
        p.loop = False
        for _ in range(10):
            p.next_frame()
        self.assertEqual(p.current_frame, 2)
        self.assertFalse(p.playing)

    def test_play_pause_stop(self):
        p = _make_preloaded_player()
        self.assertFalse(p.is_playing())
        p.play()
        self.assertTrue(p.is_playing())
        p.pause()
        self.assertFalse(p.is_playing())
        p.stop()
        self.assertEqual(p.current_frame, 0)
        self.assertFalse(p.playing)

    def test_reset(self):
        p = _make_preloaded_player()
        p.current_frame = 4
        p.reset()
        self.assertEqual(p.current_frame, 0)


# -- VideoPlayer -----------------------------------------------------------

class TestVideoPlayerPreloaded(unittest.TestCase):
    """VideoPlayer-specific logic with preloaded frames."""

    def test_get_delay(self):
        p = _make_preloaded_player(fps=16)
        self.assertEqual(p.get_delay(), 62)  # 1000/16 = 62.5 -> int = 62

    def test_close_clears_frames(self):
        p = _make_preloaded_player()
        self.assertTrue(len(p.frames) > 0)
        p.close()
        self.assertEqual(len(p.frames), 0)


class TestVideoPlayerInit(unittest.TestCase):
    """VideoPlayer __init__ error paths."""

    @patch('trcc.media_player.FFMPEG_AVAILABLE', False)
    def test_raises_without_ffmpeg(self):
        with self.assertRaises(RuntimeError):
            VideoPlayer('/fake/video.mp4')


class TestVideoPlayerLoadViaPipe(unittest.TestCase):
    """Cover VideoPlayer.__init__ -> _load_via_pipe with mocked subprocess."""

    @patch('trcc.media_player.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_success(self, mock_run):
        """FFmpeg pipe returns raw RGB frames -> frames loaded."""
        w, h = 8, 8
        frame_size = w * h * 3
        # 3 frames of raw RGB data
        raw_data = bytes(range(256))[:frame_size] * 3

        mock_run.return_value = MagicMock(returncode=0, stdout=raw_data)

        player = VideoPlayer('/fake/video.mp4', target_size=(w, h))
        self.assertEqual(player.frame_count, 3)
        self.assertEqual(len(player.frames), 3)
        self.assertEqual(player.frames[0].size, (w, h))
        self.assertEqual(player.fps, 16)
        player.close()

    @patch('trcc.media_player.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_partial_frame_ignored(self, mock_run):
        """Incomplete trailing frame data is dropped."""
        w, h = 4, 4
        frame_size = w * h * 3
        # 1 full frame + partial
        raw_data = b'\x00' * frame_size + b'\xFF' * 10

        mock_run.return_value = MagicMock(returncode=0, stdout=raw_data)

        player = VideoPlayer('/fake/vid.mp4', target_size=(w, h))
        self.assertEqual(player.frame_count, 1)
        player.close()

    @patch('trcc.media_player.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_ffmpeg_failure(self, mock_run):
        """FFmpeg returns non-zero -> RuntimeError."""
        mock_run.return_value = MagicMock(returncode=1, stderr=b'error msg', stdout=b'')
        with self.assertRaises(RuntimeError):
            VideoPlayer('/fake/vid.mp4')

    @patch('trcc.media_player.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_ffmpeg_timeout(self, mock_run):
        """FFmpeg times out -> propagates TimeoutExpired."""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired('ffmpeg', 300)
        with self.assertRaises(sp.TimeoutExpired):
            VideoPlayer('/fake/vid.mp4')

    @patch('trcc.media_player.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_empty_output(self, mock_run):
        """FFmpeg returns success but no output -> 0 frames."""
        mock_run.return_value = MagicMock(returncode=0, stdout=b'')
        player = VideoPlayer('/fake/vid.mp4', target_size=(8, 8))
        self.assertEqual(player.frame_count, 0)
        self.assertEqual(len(player.frames), 0)
        player.close()


# -- VideoPlayer.extract_frames --------------------------------------------

class TestExtractFrames(unittest.TestCase):
    """Cover VideoPlayer.extract_frames static method."""

    @patch('trcc.media_player.FFMPEG_AVAILABLE', False)
    def test_no_ffmpeg_returns_zero(self):
        result = VideoPlayer.extract_frames('/fake.mp4', '/tmp/out')
        self.assertEqual(result, 0)

    @patch('subprocess.run')
    def test_success_counts_frames(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as outdir:
            # Create some fake frame files
            for i in range(5):
                open(os.path.join(outdir, f'frame_{i+1:04d}.png'), 'w').close()

            result = VideoPlayer.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320))
            self.assertEqual(result, 5)

    @patch('subprocess.run')
    def test_with_max_frames(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as outdir:
            VideoPlayer.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320), max_frames=10)
            cmd = mock_run.call_args[0][0]
            self.assertIn('-vframes', cmd)
            self.assertIn('10', cmd)

    @patch('subprocess.run')
    def test_ffmpeg_error_returns_zero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr=b'error')

        with tempfile.TemporaryDirectory() as outdir:
            result = VideoPlayer.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320))
            self.assertEqual(result, 0)

    @patch('subprocess.run', side_effect=Exception("ffmpeg crashed"))
    def test_ffmpeg_exception_returns_zero(self, _):
        with tempfile.TemporaryDirectory() as outdir:
            result = VideoPlayer.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320))
            self.assertEqual(result, 0)

    @patch('subprocess.run')
    def test_ffmpeg_timeout_returns_zero(self, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired('ffmpeg', 600)

        with tempfile.TemporaryDirectory() as outdir:
            result = VideoPlayer.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320))
            self.assertEqual(result, 0)


# -- _check_ffmpeg ----------------------------------------------------------

class TestCheckFfmpeg(unittest.TestCase):

    @patch('subprocess.run')
    def test_ffmpeg_available(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(_check_ffmpeg())

    @patch('subprocess.run', side_effect=FileNotFoundError)
    def test_ffmpeg_not_found(self, _):
        self.assertFalse(_check_ffmpeg())


# -- FFMPEG_AVAILABLE constant ----------------------------------------------

class TestFfmpegAvailableConstant(unittest.TestCase):

    def test_ffmpeg_available_is_bool(self):
        from trcc.media_player import FFMPEG_AVAILABLE
        self.assertIsInstance(FFMPEG_AVAILABLE, bool)


# -- ThemeZtPlayer ----------------------------------------------------------

class TestThemeZtPlayer(unittest.TestCase):
    """Theme.zt binary animation player."""

    def setUp(self):
        self.path = _make_theme_zt(frames=4, size=(8, 8))
        self.player = ThemeZtPlayer(self.path)

    def tearDown(self):
        self.player.close()
        os.unlink(self.path)

    def test_frame_count(self):
        self.assertEqual(self.player.frame_count, 4)

    def test_timestamps(self):
        self.assertEqual(self.player.timestamps, [0, 42, 84, 126])

    def test_delays_computed(self):
        # [42, 42, 42, 42] -- last frame reuses previous delay
        self.assertEqual(self.player.delays, [42, 42, 42, 42])

    def test_frames_are_rgb(self):
        for frame in self.player.frames:
            self.assertEqual(frame.mode, 'RGB')

    def test_play_pause_stop(self):
        self.assertFalse(self.player.is_playing())
        self.player.play()
        self.assertTrue(self.player.is_playing())
        self.player.pause()
        self.assertFalse(self.player.is_playing())
        self.player.stop()
        self.assertEqual(self.player.current_frame, 0)

    def test_next_frame_loops(self):
        for _ in range(4):
            self.player.next_frame()
        self.assertEqual(self.player.current_frame, 0)

    def test_next_frame_stops_no_loop(self):
        self.player.loop = False
        for _ in range(10):
            self.player.next_frame()
        self.assertEqual(self.player.current_frame, 3)
        self.assertFalse(self.player.playing)

    def test_seek_clamps(self):
        self.player.seek(-1)
        self.assertEqual(self.player.current_frame, 0)
        self.player.seek(0.5)
        self.assertEqual(self.player.current_frame, 1)
        self.player.seek(5.0)
        self.assertEqual(self.player.current_frame, 3)

    def test_get_progress(self):
        self.assertEqual(self.player.get_progress(), 0)
        self.player.current_frame = 3
        self.assertEqual(self.player.get_progress(), 100)

    def test_get_current_frame_returns_copy(self):
        f1 = self.player.get_current_frame()
        f2 = self.player.get_current_frame()
        self.assertIsNot(f1, f2)  # .copy() each time

    def test_get_delay(self):
        self.assertEqual(self.player.get_delay(), 42)

    def test_invalid_magic_raises(self):
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)
        with open(path, 'wb') as f:
            f.write(b'\x00\x00\x00\x00\x00')
        with self.assertRaises(ValueError):
            ThemeZtPlayer(path)
        os.unlink(path)

    def test_resize_on_load(self):
        player = ThemeZtPlayer(self.path, target_size=(4, 4))
        self.assertEqual(player.frames[0].size, (4, 4))
        player.close()


# -- ThemeZtPlayer edge cases -----------------------------------------------

class TestThemeZtPlayerEdge(unittest.TestCase):

    def test_single_frame_delay(self):
        """Single frame -> delay defaults to 42."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        img = Image.new('RGB', (8, 8), 'red')
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        jpeg_data = buf.getvalue()

        with open(path, 'wb') as f:
            f.write(struct.pack('B', 0xDC))
            f.write(struct.pack('<i', 1))       # 1 frame
            f.write(struct.pack('<i', 0))       # timestamp 0
            f.write(struct.pack('<i', len(jpeg_data)))
            f.write(jpeg_data)

        try:
            player = ThemeZtPlayer(path)
            self.assertEqual(player.delays, [42])  # single frame default
            player.close()
        finally:
            os.unlink(path)

    def test_get_current_frame_out_of_range_returns_none(self):
        """current_frame out of range -> None."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        img = Image.new('RGB', (4, 4))
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        jpeg_data = buf.getvalue()

        with open(path, 'wb') as f:
            f.write(struct.pack('B', 0xDC))
            f.write(struct.pack('<i', 1))
            f.write(struct.pack('<i', 0))
            f.write(struct.pack('<i', len(jpeg_data)))
            f.write(jpeg_data)

        try:
            player = ThemeZtPlayer(path)
            player.current_frame = 999
            self.assertIsNone(player.get_current_frame())
            player.close()
        finally:
            os.unlink(path)

    def test_get_delay_out_of_range(self):
        """current_frame beyond delays -> 42."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        img = Image.new('RGB', (4, 4))
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        jpeg_data = buf.getvalue()

        with open(path, 'wb') as f:
            f.write(struct.pack('B', 0xDC))
            f.write(struct.pack('<i', 1))
            f.write(struct.pack('<i', 0))
            f.write(struct.pack('<i', len(jpeg_data)))
            f.write(jpeg_data)

        try:
            player = ThemeZtPlayer(path)
            player.current_frame = 999
            self.assertEqual(player.get_delay(), 42)
            player.close()
        finally:
            os.unlink(path)

    def test_get_progress_single_frame(self):
        """Single frame -> progress=0."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        img = Image.new('RGB', (4, 4))
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        jpeg_data = buf.getvalue()

        with open(path, 'wb') as f:
            f.write(struct.pack('B', 0xDC))
            f.write(struct.pack('<i', 1))
            f.write(struct.pack('<i', 0))
            f.write(struct.pack('<i', len(jpeg_data)))
            f.write(jpeg_data)

        try:
            player = ThemeZtPlayer(path)
            self.assertEqual(player.get_progress(), 0)
            player.close()
        finally:
            os.unlink(path)

    def test_close_clears_frames(self):
        """close() releases all frames."""
        path = _make_theme_zt(frames=2)
        try:
            player = ThemeZtPlayer(path)
            self.assertTrue(len(player.frames) > 0)
            player.close()
            self.assertEqual(len(player.frames), 0)
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
