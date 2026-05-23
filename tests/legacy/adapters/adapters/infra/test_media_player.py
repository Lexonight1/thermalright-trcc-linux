"""Tests for media_player – video/animation frame decoders."""

import os
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from trcc.legacy.adapters.infra.media_player import (
    ThemeZtDecoder,
    VideoDecoder,
)
from trcc.legacy.core.models import FBL_PROFILES
from trcc.legacy.core.ports import RawFrame

_p320 = FBL_PROFILES[100]   # 320×320 canonical profile


def _make_theme_zt(frames=4, size=(8, 8), quality=50):
    """Create a minimal Theme.zt binary file. Returns path."""
    fd, path = tempfile.mkstemp(suffix='.zt')
    os.close(fd)

    w, h = size
    jpeg_blobs = []
    for i in range(frames):
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(0, i * 60, 0))
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPEG", quality)
        jpeg_blobs.append(bytes(buf.data()))

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


def _make_video_decoder(frame_count=5, fps=16, size=(320, 320)):
    """Create a VideoDecoder with mocked internals for testing."""
    with patch.object(VideoDecoder, '__init__', lambda self, *a, **kw: None):
        decoder = VideoDecoder.__new__(VideoDecoder)
    decoder.fps = fps
    w, h = size
    decoder.frames = [RawFrame(bytes(w * h * 3), w, h) for _ in range(frame_count)]
    return decoder


# -- Backward-compat aliases ------------------------------------------------

# -- VideoDecoder -----------------------------------------------------------

class TestVideoDecoderProperties(unittest.TestCase):
    """VideoDecoder with preloaded frames."""

    def test_frame_count(self):
        d = _make_video_decoder(frame_count=5)
        self.assertEqual(d.frame_count, 5)

    def test_close_clears_frames(self):
        d = _make_video_decoder()
        self.assertTrue(len(d.frames) > 0)
        d.close()
        self.assertEqual(len(d.frames), 0)

    def test_frames_are_raw_frames(self):
        d = _make_video_decoder()
        for frame in d.frames:
            self.assertIsInstance(frame, RawFrame)


class TestVideoDecoderDecode(unittest.TestCase):
    """Cover VideoDecoder.__init__ -> _decode with mocked subprocess."""

    @patch('subprocess.run')
    def test_decode_success(self, mock_run):
        """FFmpeg pipe returns raw RGB frames -> frames loaded."""
        w, h = 8, 8
        frame_size = w * h * 3
        # 3 frames of raw RGB data
        raw_data = bytes(range(256))[:frame_size] * 3

        mock_run.return_value = MagicMock(returncode=0, stdout=raw_data)

        decoder = VideoDecoder('/fake/video.mp4', target_size=(w, h))
        self.assertEqual(decoder.frame_count, 3)
        self.assertEqual(len(decoder.frames), 3)
        self.assertEqual((decoder.frames[0].width, decoder.frames[0].height), (w, h))
        self.assertEqual(decoder.fps, 16)
        decoder.close()

    @patch('subprocess.run')
    def test_decode_partial_frame_ignored(self, mock_run):
        """Incomplete trailing frame data is dropped."""
        w, h = 4, 4
        frame_size = w * h * 3
        # 1 full frame + partial
        raw_data = b'\x00' * frame_size + b'\xFF' * 10

        mock_run.return_value = MagicMock(returncode=0, stdout=raw_data)

        decoder = VideoDecoder('/fake/vid.mp4', target_size=(w, h))
        self.assertEqual(decoder.frame_count, 1)
        decoder.close()

    @patch('subprocess.run')
    def test_decode_ffmpeg_failure(self, mock_run):
        """FFmpeg returns non-zero -> RuntimeError."""
        mock_run.return_value = MagicMock(returncode=1, stderr=b'error msg', stdout=b'')
        with self.assertRaises(RuntimeError):
            VideoDecoder('/fake/vid.mp4', target_size=(320, 320))

    @patch('subprocess.run')
    def test_decode_ffmpeg_timeout(self, mock_run):
        """FFmpeg times out -> propagates TimeoutExpired."""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired('ffmpeg', 300)
        with self.assertRaises(sp.TimeoutExpired):
            VideoDecoder('/fake/vid.mp4', target_size=(320, 320))

    @patch('subprocess.run')
    def test_decode_empty_output(self, mock_run):
        """FFmpeg returns success but no output -> 0 frames."""
        mock_run.return_value = MagicMock(returncode=0, stdout=b'')
        decoder = VideoDecoder('/fake/vid.mp4', target_size=(8, 8))
        self.assertEqual(decoder.frame_count, 0)
        self.assertEqual(len(decoder.frames), 0)
        decoder.close()


# -- VideoDecoder.extract_frames --------------------------------------------

class TestExtractFrames(unittest.TestCase):
    """Cover VideoDecoder.extract_frames static method."""

    @patch('subprocess.run')
    def test_success_counts_frames(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as outdir:
            # Create some fake frame files
            for i in range(5):
                open(os.path.join(outdir, f'frame_{i+1:04d}.png'), 'w').close()

            result = VideoDecoder.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320))
            self.assertEqual(result, 5)

    @patch('subprocess.run')
    def test_with_max_frames(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as outdir:
            VideoDecoder.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320), max_frames=10)
            cmd = mock_run.call_args[0][0]
            self.assertIn('-vframes', cmd)
            self.assertIn('10', cmd)

    @patch('subprocess.run')
    def test_ffmpeg_error_returns_zero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr=b'error')

        with tempfile.TemporaryDirectory() as outdir:
            result = VideoDecoder.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320))
            self.assertEqual(result, 0)

    @patch('subprocess.run', side_effect=OSError("ffmpeg crashed"))
    def test_ffmpeg_exception_returns_zero(self, _):
        # Production catches (OSError, SubprocessError) — typical ffmpeg failure
        # modes — and returns 0. Bare Exception is intentionally not caught.
        with tempfile.TemporaryDirectory() as outdir:
            result = VideoDecoder.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320))
            self.assertEqual(result, 0)

    @patch('subprocess.run')
    def test_ffmpeg_timeout_returns_zero(self, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired('ffmpeg', 600)

        with tempfile.TemporaryDirectory() as outdir:
            result = VideoDecoder.extract_frames(
                '/fake/vid.mp4', outdir, (320, 320))
            self.assertEqual(result, 0)


# -- ThemeZtDecoder ---------------------------------------------------------

class TestThemeZtDecoder(unittest.TestCase):
    """Theme.zt binary animation decoder."""

    def setUp(self):
        self.path = _make_theme_zt(frames=4, size=(8, 8))
        self.decoder = ThemeZtDecoder(self.path, target_size=(8, 8))

    def tearDown(self):
        self.decoder.close()
        os.unlink(self.path)

    def test_frame_count(self):
        self.assertEqual(self.decoder.frame_count, 4)

    def test_timestamps(self):
        self.assertEqual(self.decoder.timestamps, [0, 42, 84, 126])

    def test_delays_computed(self):
        # [42, 42, 42, 42] -- last frame reuses previous delay
        self.assertEqual(self.decoder.delays, [42, 42, 42, 42])

    def test_frames_are_raw_frames(self):
        for frame in self.decoder.frames:
            self.assertIsInstance(frame, RawFrame)

    def test_fps_from_delays(self):
        # avg delay = 42ms → fps ≈ 23.8
        self.assertAlmostEqual(self.decoder.fps, 1000.0 / 42, places=1)

    def test_invalid_magic_raises(self):
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)
        with open(path, 'wb') as f:
            f.write(b'\x00\x00\x00\x00\x00')
        with self.assertRaises(ValueError):
            ThemeZtDecoder(path, target_size=(8, 8))
        os.unlink(path)

    def test_resize_on_load(self):
        decoder = ThemeZtDecoder(self.path, target_size=(4, 4))
        self.assertEqual((decoder.frames[0].width, decoder.frames[0].height), (4, 4))
        decoder.close()


# -- ThemeZtDecoder edge cases -----------------------------------------------

class TestThemeZtDecoderEdge(unittest.TestCase):

    def test_single_frame_delay(self):
        """Single frame -> delay defaults to 42."""
        fd, path = tempfile.mkstemp(suffix='.zt')
        os.close(fd)

        img = QImage(8, 8, QImage.Format.Format_RGB32)
        img.fill(QColor(255, 0, 0))
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPEG")
        jpeg_data = bytes(buf.data())

        with open(path, 'wb') as f:
            f.write(struct.pack('B', 0xDC))
            f.write(struct.pack('<i', 1))       # 1 frame
            f.write(struct.pack('<i', 0))       # timestamp 0
            f.write(struct.pack('<i', len(jpeg_data)))
            f.write(jpeg_data)

        try:
            decoder = ThemeZtDecoder(path, target_size=(8, 8))
            self.assertEqual(decoder.delays, [42])  # single frame default
            decoder.close()
        finally:
            os.unlink(path)

    def test_close_clears_frames(self):
        """close() releases all frames."""
        path = _make_theme_zt(frames=2)
        try:
            decoder = ThemeZtDecoder(path, target_size=(8, 8))
            self.assertTrue(len(decoder.frames) > 0)
            decoder.close()
            self.assertEqual(len(decoder.frames), 0)
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
