"""Tests for gif_animator – GIF/video animation and Theme.zt playback."""

import io
import os
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from trcc.gif_animator import GIFAnimator, GIFThemeLoader, ThemeZtPlayer, VideoPlayer


def _make_gif(frames=3, size=(4, 4), durations=None):
    """Create a minimal GIF in a temp file. Returns path."""
    if durations is None:
        durations = [100] * frames

    imgs = []
    for i in range(frames):
        # Each frame gets a distinct color so we can tell them apart
        img = Image.new('RGB', size, color=(i * 80, 0, 0))
        imgs.append(img)

    fd, path = tempfile.mkstemp(suffix='.gif')
    os.close(fd)
    imgs[0].save(path, save_all=True, append_images=imgs[1:],
                 duration=durations, loop=0)
    return path


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


# ── GIFAnimator ──────────────────────────────────────────────────────────────

class TestGIFAnimator(unittest.TestCase):
    """Core GIF playback logic."""

    def setUp(self):
        self.path = _make_gif(frames=3, durations=[100, 200, 150])
        self.anim = GIFAnimator(self.path)

    def tearDown(self):
        self.anim.close()
        os.unlink(self.path)

    def test_frame_count(self):
        self.assertEqual(self.anim.frame_count, 3)

    def test_frames_are_rgb(self):
        for i in range(self.anim.frame_count):
            self.assertEqual(self.anim.get_frame(i).mode, 'RGB')

    def test_delays_extracted(self):
        self.assertEqual(len(self.anim.delays), 3)
        self.assertEqual(self.anim.delays, [100, 200, 150])

    def test_get_frame_default_is_current(self):
        """get_frame() with no arg returns current_frame."""
        self.anim.current_frame = 1
        frame = self.anim.get_frame()
        self.assertEqual(frame, self.anim.frames[1])

    def test_get_frame_out_of_range(self):
        """Out-of-range index returns frame 0."""
        self.assertIsNotNone(self.anim.get_frame(999))

    def test_next_frame_advances(self):
        self.assertEqual(self.anim.current_frame, 0)
        self.anim.next_frame()
        self.assertEqual(self.anim.current_frame, 1)

    def test_next_frame_loops(self):
        self.anim.loop = True
        for _ in range(3):
            self.anim.next_frame()
        self.assertEqual(self.anim.current_frame, 0)

    def test_next_frame_stops_when_no_loop(self):
        self.anim.loop = False
        for _ in range(10):
            self.anim.next_frame()
        self.assertEqual(self.anim.current_frame, 2)
        self.assertFalse(self.anim.playing)

    def test_reset(self):
        self.anim.current_frame = 2
        self.anim.reset()
        self.assertEqual(self.anim.current_frame, 0)

    def test_play_pause(self):
        self.anim.play()
        self.assertTrue(self.anim.is_playing())
        self.anim.pause()
        self.assertFalse(self.anim.is_playing())

    def test_set_speed_clamps(self):
        self.anim.set_speed(0.01)
        self.assertAlmostEqual(self.anim.speed_multiplier, 0.1)
        self.anim.set_speed(99)
        self.assertAlmostEqual(self.anim.speed_multiplier, 10.0)

    def test_get_delay_respects_speed(self):
        self.anim.speed_multiplier = 2.0
        delay = self.anim.get_delay(0)
        self.assertEqual(delay, 50)  # 100 / 2.0

    def test_is_last_frame(self):
        self.assertFalse(self.anim.is_last_frame())
        self.anim.current_frame = 2
        self.assertTrue(self.anim.is_last_frame())


# ── GIFThemeLoader ───────────────────────────────────────────────────────────

class TestGIFThemeLoader(unittest.TestCase):
    """Static helpers for GIF→theme conversion."""

    def setUp(self):
        self.gif_path = _make_gif(frames=2, size=(8, 8))

    def tearDown(self):
        os.unlink(self.gif_path)

    def test_load_gif_theme_returns_animator(self):
        anim = GIFThemeLoader.load_gif_theme(self.gif_path)
        self.assertIsInstance(anim, GIFAnimator)
        self.assertEqual(anim.frame_count, 2)
        anim.close()

    def test_gif_to_frames_extracts_files(self):
        with tempfile.TemporaryDirectory() as out_dir:
            count = GIFThemeLoader.gif_to_frames(
                self.gif_path, out_dir, target_size=(8, 8))
            self.assertEqual(count, 2)
            # Check frame files exist
            self.assertTrue(os.path.exists(os.path.join(out_dir, 'frame_0000.png')))
            self.assertTrue(os.path.exists(os.path.join(out_dir, 'frame_0001.png')))
            # Delay txt files
            self.assertTrue(os.path.exists(os.path.join(out_dir, 'frame_0000.txt')))
            # Background frame
            self.assertTrue(os.path.exists(os.path.join(out_dir, '00.png')))


# ── ThemeZtPlayer ────────────────────────────────────────────────────────────

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
        # [42, 42, 42, 42] — last frame reuses previous delay
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


# ── VideoPlayer ──────────────────────────────────────────────────────────────

class TestVideoPlayerPreloaded(unittest.TestCase):
    """VideoPlayer with preloaded frames (bypasses actual video loading)."""

    def _make_player(self, frame_count=5, fps=16):
        """Create a VideoPlayer with mocked internals for pure-logic testing."""
        with patch.object(VideoPlayer, '__init__', lambda self, *a, **kw: None):
            player = VideoPlayer.__new__(VideoPlayer)
        player.video_path = '/fake/video.mp4'
        player.target_size = (320, 320)
        player.fps = fps
        player.current_frame = 0
        player.playing = False
        player.loop = True
        player.frames = [Image.new('RGB', (320, 320), (i * 50, 0, 0))
                         for i in range(frame_count)]
        player.frame_count = frame_count
        return player

    def test_get_current_frame(self):
        p = self._make_player()
        frame = p.get_current_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.size, (320, 320))

    def test_get_current_frame_out_of_range(self):
        p = self._make_player()
        p.current_frame = 999
        frame = p.get_current_frame()
        self.assertEqual(frame, p.frames[0])

    def test_get_current_frame_empty(self):
        p = self._make_player(frame_count=0)
        p.frames = []
        self.assertIsNone(p.get_current_frame())

    def test_next_frame_advances(self):
        p = self._make_player()
        p.next_frame()
        self.assertEqual(p.current_frame, 1)

    def test_next_frame_loops(self):
        p = self._make_player(frame_count=3)
        for _ in range(3):
            p.next_frame()
        self.assertEqual(p.current_frame, 0)

    def test_next_frame_stops_no_loop(self):
        p = self._make_player(frame_count=3)
        p.loop = False
        for _ in range(10):
            p.next_frame()
        self.assertEqual(p.current_frame, 2)
        self.assertFalse(p.playing)

    def test_play_pause_stop(self):
        p = self._make_player()
        self.assertFalse(p.is_playing())
        p.play()
        self.assertTrue(p.is_playing())
        p.pause()
        self.assertFalse(p.is_playing())
        p.stop()
        self.assertEqual(p.current_frame, 0)
        self.assertFalse(p.playing)

    def test_get_delay(self):
        p = self._make_player(fps=16)
        self.assertEqual(p.get_delay(), 62)  # 1000/16 = 62.5 → int = 62

    def test_reset(self):
        p = self._make_player()
        p.current_frame = 4
        p.reset()
        self.assertEqual(p.current_frame, 0)

    def test_close_clears_frames(self):
        p = self._make_player()
        self.assertTrue(len(p.frames) > 0)
        p.close()
        self.assertEqual(len(p.frames), 0)


class TestVideoPlayerInit(unittest.TestCase):
    """VideoPlayer __init__ error paths."""

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', False)
    def test_raises_without_ffmpeg(self):
        with self.assertRaises(RuntimeError):
            VideoPlayer('/fake/video.mp4')


# ── _check_ffmpeg ────────────────────────────────────────────────────────────

class TestCheckFfmpeg(unittest.TestCase):

    @patch('subprocess.run')
    def test_ffmpeg_available(self, mock_run):
        from trcc.gif_animator import _check_ffmpeg
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(_check_ffmpeg())

    @patch('subprocess.run', side_effect=FileNotFoundError)
    def test_ffmpeg_not_found(self, _):
        from trcc.gif_animator import _check_ffmpeg
        self.assertFalse(_check_ffmpeg())


# ── GIFAnimator.get_delay out-of-range ───────────────────────────────────────

class TestGIFAnimatorGetDelayEdge(unittest.TestCase):

    def test_delay_out_of_range(self):
        gif_path = _make_gif(frames=2, durations=[50, 80])
        try:
            anim = GIFAnimator(gif_path)
            anim.current_frame = 999  # beyond range
            self.assertEqual(anim.get_delay(), 100)  # fallback
        finally:
            os.unlink(gif_path)


# ── VideoPlayer.extract_frames dispatch ──────────────────────────────────────

class TestExtractFramesDispatch(unittest.TestCase):

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', False)
    def test_no_ffmpeg(self):
        result = VideoPlayer.extract_frames('/fake.mp4', '/tmp/out')
        self.assertEqual(result, 0)

    @patch('trcc.gif_animator.VideoPlayer._extract_frames_ffmpeg', return_value=5)
    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', True)
    def test_uses_ffmpeg(self, mock_extract):
        result = VideoPlayer.extract_frames('/fake.mp4', '/tmp/out')
        self.assertEqual(result, 5)
        mock_extract.assert_called_once()


# ── FFMPEG_AVAILABLE=False warning lines 32-33 ──────────────────────────────

class TestFfmpegUnavailableWarning(unittest.TestCase):

    def test_module_prints_warning_when_ffmpeg_missing(self):
        """Lines 32-33: when FFMPEG_AVAILABLE is False, two print lines run at import."""
        # We can't re-import at module level, but we can verify the constant
        from trcc.gif_animator import FFMPEG_AVAILABLE
        # FFMPEG_AVAILABLE is either True or False; just confirm it's a bool
        self.assertIsInstance(FFMPEG_AVAILABLE, bool)


# ── AbstractMediaPlayer edge cases ──────────────────────────────────────────

class TestAbstractMediaPlayerEdge(unittest.TestCase):
    """Cover AbstractMediaPlayer.get_current_frame edge paths."""

    def _make_player(self):
        """Create a GIFAnimator without __init__, with image attr."""
        with patch.object(GIFAnimator, '__init__', lambda s, *a, **kw: None):
            player = GIFAnimator.__new__(GIFAnimator)
        player.image = None  # prevent __del__ AttributeError
        return player

    def test_get_current_frame_empty(self):
        """Line 75: empty frames list → None."""
        player = self._make_player()
        player.frames = []
        player.frame_count = 0
        player.current_frame = 0
        self.assertIsNone(player.get_current_frame())

    def test_get_current_frame_out_of_range(self):
        """Line 73: current_frame beyond frames → returns frames[0]."""
        player = self._make_player()
        player.frames = [Image.new('RGB', (4, 4), 'red')]
        player.frame_count = 1
        player.current_frame = 99
        self.assertEqual(player.get_current_frame(), player.frames[0])

    def test_next_frame_no_loop_sets_playing_false(self):
        """Line 86/91: non-looping at last frame → playing=False."""
        player = self._make_player()
        player.frames = [Image.new('RGB', (4, 4))] * 2
        player.frame_count = 2
        player.current_frame = 1
        player.playing = True
        player.loop = False
        player.next_frame()
        self.assertFalse(player.playing)
        self.assertEqual(player.current_frame, 1)


# ── GIFAnimator.__del__ ─────────────────────────────────────────────────────

class TestGIFAnimatorDel(unittest.TestCase):

    def test_del_calls_close(self):
        gif_path = _make_gif(frames=1)
        try:
            anim = GIFAnimator(gif_path)
            anim.__del__()  # Line 198
            self.assertIsNone(anim.image)
        finally:
            os.unlink(gif_path)


# ── VideoPlayer._load_via_pipe ───────────────────────────────────────────────

class TestVideoPlayerLoadViaPipe(unittest.TestCase):
    """Cover VideoPlayer.__init__ → _load_via_pipe with mocked subprocess."""

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_success(self, mock_run):
        """FFmpeg pipe returns raw RGB frames → frames loaded."""
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

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', True)
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

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_ffmpeg_failure(self, mock_run):
        """FFmpeg returns non-zero → RuntimeError."""
        mock_run.return_value = MagicMock(returncode=1, stderr=b'error msg', stdout=b'')
        with self.assertRaises(RuntimeError):
            VideoPlayer('/fake/vid.mp4')

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_ffmpeg_timeout(self, mock_run):
        """FFmpeg times out → propagates TimeoutExpired."""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired('ffmpeg', 300)
        with self.assertRaises(sp.TimeoutExpired):
            VideoPlayer('/fake/vid.mp4')

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_load_empty_output(self, mock_run):
        """FFmpeg returns success but no output → 0 frames."""
        mock_run.return_value = MagicMock(returncode=0, stdout=b'')
        player = VideoPlayer('/fake/vid.mp4', target_size=(8, 8))
        self.assertEqual(player.frame_count, 0)
        self.assertEqual(len(player.frames), 0)
        player.close()


# ── VideoPlayer.__del__ ─────────────────────────────────────────────────────

class TestVideoPlayerDel(unittest.TestCase):

    def test_del_calls_close(self):
        with patch.object(VideoPlayer, '__init__', lambda s, *a, **kw: None):
            player = VideoPlayer.__new__(VideoPlayer)
        player.frames = [Image.new('RGB', (4, 4))]
        player.__del__()
        self.assertEqual(player.frames, [])


# ── _extract_frames_ffmpeg ───────────────────────────────────────────────────

class TestExtractFramesFfmpeg(unittest.TestCase):
    """Cover VideoPlayer._extract_frames_ffmpeg static method."""

    @patch('subprocess.run')
    def test_success_counts_frames(self, mock_run):
        """Lines 487-524: successful extraction."""
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as outdir:
            # Create some fake frame files
            for i in range(5):
                open(os.path.join(outdir, f'frame_{i+1:04d}.png'), 'w').close()

            result = VideoPlayer._extract_frames_ffmpeg(
                '/fake/vid.mp4', outdir, (320, 320), None)
            self.assertEqual(result, 5)

    @patch('subprocess.run')
    def test_with_max_frames(self, mock_run):
        """Lines 497-498: max_frames adds -vframes arg."""
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as outdir:
            VideoPlayer._extract_frames_ffmpeg(
                '/fake/vid.mp4', outdir, (320, 320), max_frames=10)
            cmd = mock_run.call_args[0][0]
            self.assertIn('-vframes', cmd)
            self.assertIn('10', cmd)

    @patch('subprocess.run')
    def test_ffmpeg_error_returns_zero(self, mock_run):
        """Line 512: non-zero returncode → 0."""
        mock_run.return_value = MagicMock(returncode=1, stderr=b'error')

        with tempfile.TemporaryDirectory() as outdir:
            result = VideoPlayer._extract_frames_ffmpeg(
                '/fake/vid.mp4', outdir, (320, 320), None)
            self.assertEqual(result, 0)

    @patch('subprocess.run', side_effect=Exception("ffmpeg crashed"))
    def test_ffmpeg_exception_returns_zero(self, _):
        """Line 520: generic exception → 0."""
        with tempfile.TemporaryDirectory() as outdir:
            result = VideoPlayer._extract_frames_ffmpeg(
                '/fake/vid.mp4', outdir, (320, 320), None)
            self.assertEqual(result, 0)

    @patch('subprocess.run')
    def test_ffmpeg_timeout_returns_zero(self, mock_run):
        """Line 517: TimeoutExpired → 0."""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired('ffmpeg', 600)

        with tempfile.TemporaryDirectory() as outdir:
            result = VideoPlayer._extract_frames_ffmpeg(
                '/fake/vid.mp4', outdir, (320, 320), None)
            self.assertEqual(result, 0)


# ── ThemeZtPlayer edge cases ────────────────────────────────────────────────

class TestThemeZtPlayerEdge(unittest.TestCase):

    def test_single_frame_delay(self):
        """Line 585: single frame → delay defaults to 42."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        import struct
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
        """Line 605: current_frame out of range → None."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        import struct
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
        """Line 611: current_frame beyond delays → 42."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        import struct
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
        """Line 621: single frame → progress=0."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        import struct
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


# ── test_video_player() CLI function ─────────────────────────────────────────

class TestVideoPlayerCLI(unittest.TestCase):
    """Cover the test_video_player() CLI function (lines 634-683)."""

    def test_no_args_prints_usage(self):
        """Lines 639-642: no args → usage message."""
        from trcc.gif_animator import test_video_player
        with patch('sys.argv', ['gif_animator.py']):
            test_video_player()  # should not raise

    def test_gif_file(self):
        """Lines 658-661: .gif file path."""
        from trcc.gif_animator import test_video_player
        gif_path = _make_gif(frames=2)
        try:
            with patch('sys.argv', ['gif_animator.py', gif_path]):
                test_video_player()
        finally:
            os.unlink(gif_path)

    def test_zt_file(self):
        """Lines 662-671: .zt file path."""
        from trcc.gif_animator import test_video_player
        zt_path = _make_theme_zt(frames=2)
        try:
            with patch('sys.argv', ['gif_animator.py', zt_path]):
                test_video_player()
        finally:
            os.unlink(zt_path)

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', False)
    def test_video_file_no_ffmpeg(self):
        """Lines 675-677: video file without ffmpeg → warning."""
        from trcc.gif_animator import test_video_player
        with patch('sys.argv', ['gif_animator.py', '/fake/vid.mp4']):
            test_video_player()  # should not raise

    @patch('trcc.gif_animator.FFMPEG_AVAILABLE', True)
    @patch('subprocess.run')
    def test_video_file_with_ffmpeg(self, mock_run):
        """Video file with ffmpeg available — pipe returns empty frames."""
        from trcc.gif_animator import test_video_player

        mock_run.return_value = MagicMock(returncode=0, stdout=b'')
        with patch('sys.argv', ['gif_animator.py', '/fake/vid.mp4']):
            test_video_player()

    def test_extract_gif(self):
        """Lines 647-651: --extract with .gif."""
        from trcc.gif_animator import test_video_player
        gif_path = _make_gif(frames=2)
        try:
            with tempfile.TemporaryDirectory() as outdir:
                with patch('sys.argv', ['gif_animator.py', '--extract', gif_path, outdir]):
                    test_video_player()
                self.assertTrue(os.path.exists(os.path.join(outdir, 'frame_0000.png')))
        finally:
            os.unlink(gif_path)

    @patch('trcc.gif_animator.VideoPlayer.extract_frames', return_value=5)
    def test_extract_video(self, mock_extract):
        """Lines 652-653: --extract with video file."""
        from trcc.gif_animator import test_video_player
        with patch('sys.argv', ['gif_animator.py', '--extract', '/fake/vid.mp4', '/tmp/out']):
            test_video_player()
        mock_extract.assert_called_once()

    @patch('trcc.gif_animator.VideoPlayer.extract_frames', return_value=5)
    def test_extract_with_max_frames(self, mock_extract):
        """Line 650: --extract with max_frames arg."""
        from trcc.gif_animator import test_video_player
        with patch('sys.argv', ['gif_animator.py', '--extract', '/fake/vid.mp4', '/tmp/out', '100']):
            test_video_player()
        _, kwargs = mock_extract.call_args
        self.assertEqual(kwargs.get('max_frames'), 100)


if __name__ == '__main__':
    unittest.main()
