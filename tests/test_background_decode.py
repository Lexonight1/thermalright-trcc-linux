"""#264: a video starts playing on its first frames while the rest decodes.

``PlayVideo`` decoded every frame before it returned, and the gui dispatches
it on its UI thread: a 4K video froze the window for ~9 s.  Measured on a real
30 s 4K clip after this change: ``load_video`` returns in 0.47 s (was 2.21 s),
the decode finishing in the background at 2.15 s.

A fake ffmpeg here writes some frames, then blocks until the test lets it go,
so "returned before the decode finished" is a fact, not a race.
"""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any

import pytest

from trcc.core.errors import ThemeError
from trcc.services import media as media_module
from trcc.services.media import MediaService, Playback, _JpegFrames

from .test_video_playback import _encoded_frame


def _jpeg(shade: int) -> bytes:
    return _encoded_frame(0xFF000000 | shade, 8, 8)


class _Pipe:
    """ffmpeg's stdout: *first* now, *rest* once ``gate`` opens, then EOF."""

    def __init__(self, first: bytes, rest: bytes, gate: threading.Event) -> None:
        self._parts = [first, rest]
        self._gate = gate
        self.killed = False

    def read1(self, _size: int) -> bytes:
        if not self._parts:
            return b""
        if len(self._parts) == 1:
            self._gate.wait(5)
            if self.killed:
                self._parts.clear()
                return b""
        return self._parts.pop(0)

    def close(self) -> None:
        return None


class _GatedFfmpeg:
    """Popen for ffmpeg: *first* frames at once, *rest* when ``release()`` is
    called, then exit *code*.  ``terminate`` ends it like a killed process."""

    def __init__(self, first: list[bytes], rest: list[bytes], code: int = 0,
                 stderr: bytes = b"") -> None:
        self._first, self._rest, self._code, self._stderr = first, rest, code, stderr
        self.gate = threading.Event()
        self.pipes: list[_Pipe] = []
        self.terminated = 0

    def release(self) -> None:
        self.gate.set()

    def __call__(self, cmd: list[str], **_: Any) -> Any:
        pipe = _Pipe(b"".join(self._first), b"".join(self._rest), self.gate)
        self.pipes.append(pipe)
        ffmpeg = self

        class _Proc:
            stdout = pipe
            stderr = io.BytesIO(ffmpeg._stderr)

            def poll(self) -> int | None:
                return None if not ffmpeg.gate.is_set() and not pipe.killed else ffmpeg._code

            def wait(self, timeout: float | None = None) -> int:
                return -15 if pipe.killed else ffmpeg._code

            def terminate(self) -> None:
                ffmpeg.terminated += 1
                pipe.killed = True
                ffmpeg.gate.set()

            def kill(self) -> None:
                self.terminate()

        return _Proc()


@pytest.fixture
def clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A video file, ffmpeg and ffprobe present, a 30-frame duration probed."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    monkeypatch.setattr("trcc.core.toolchain.present", lambda tool: True)
    monkeypatch.setattr(media_module, "_probe_video_duration", lambda p: 2.0)   # 2 s @ 15 fps
    return path


def _ffmpeg(monkeypatch: pytest.MonkeyPatch, ffmpeg: _GatedFfmpeg) -> _GatedFfmpeg:
    monkeypatch.setattr(media_module.subprocess, "Popen", ffmpeg)
    return ffmpeg


# ── the splitter: a frame may straddle two reads ───────────────────────────


@pytest.mark.parametrize("cut", [1, 2, 3, 10, -2, -1])
def test_a_frame_cut_anywhere_comes_out_whole(cut: int) -> None:
    """Including between the ff and d9 of its EOI, and the ff and d8 of the
    next SOI.  MUTATION CHECK: drop the kept trailing ff -- a cut SOI is lost."""
    one, two = _jpeg(0x10), _jpeg(0x20)
    stream = one + two
    at = len(one) + cut                 # -1: between the ff and d9 of its EOI; 1: of the next SOI
    splitter = _JpegFrames()
    assert splitter.feed(stream[:at]) + splitter.feed(stream[at:]) == [one, two]
    assert splitter.pending == 0


# ── the playback: holds at the decoded end until it is complete ────────────


def test_a_video_still_decoding_holds_instead_of_wrapping() -> None:
    """Wrapping at the decoded end would restart a video half-read.

    MUTATION CHECK: wrap regardless of ``complete`` -- the cursor goes to 0."""
    pb = Playback(frames=[b"a", b"b"], complete=False, expected_frames=30)
    pb.advance()
    pb.advance()
    assert pb.cursor == 1                       # held on the last decoded frame
    pb.frames.append(b"c")
    pb.advance()
    assert pb.cursor == 2
    pb.complete = True
    pb.advance()
    assert pb.cursor == 0                       # now it really is the end


def test_the_length_is_the_expected_one_until_the_decode_completes() -> None:
    """A progress bar and clock show the real length from the first frame.

    MUTATION CHECK: report len(frames) while decoding -- 2, not 30."""
    pb = Playback(frames=[b"a", b"b"], complete=False, expected_frames=30)
    assert pb.frame_count == 30
    pb.expected_frames = 1                      # an estimate below what is here
    assert pb.frame_count == 2
    pb.complete = True
    assert pb.frame_count == 2


# ── load_video: returns on the first frames ────────────────────────────────


def test_load_video_returns_on_the_first_frames_and_the_rest_follows(
    clip: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION CHECK: wait for the whole decode -- this never returns before
    release(), and the fixture's 5 s gate times the read out."""
    ffmpeg = _ffmpeg(monkeypatch, _GatedFfmpeg(
        first=[_jpeg(1), _jpeg(2)], rest=[_jpeg(3), _jpeg(4), _jpeg(5)]))
    media = MediaService()
    pb = media.load_video("k", clip, size=(8, 8), fps=15)
    assert (len(pb.frames), pb.complete, pb.frame_count) == (2, False, 30)
    ffmpeg.release()
    media._decodes["k"].wait_done(5)
    assert (len(pb.frames), pb.complete, pb.frame_count) == (5, True, 5)


def test_a_video_that_gives_no_frame_fails_as_before(
    clip: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffmpeg failing before any frame: ThemeError, PlayVideo's error path."""
    ffmpeg = _ffmpeg(monkeypatch, _GatedFfmpeg(first=[], rest=[], code=1,
                                               stderr=b"Invalid data found\n"))
    ffmpeg.release()
    with pytest.raises(ThemeError, match="ffmpeg decode failed: ffmpeg exit 1: Invalid data"):
        MediaService().load_video("k", clip, size=(8, 8), fps=15)


def test_a_decode_that_fails_partway_keeps_its_frames_and_says_so_once(
    clip: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel keeps playing what decoded; the user hears why, once.

    MUTATION CHECK: never call on_error -- heard == []."""
    ffmpeg = _ffmpeg(monkeypatch, _GatedFfmpeg(
        first=[_jpeg(1), _jpeg(2)], rest=[_jpeg(3)], code=1, stderr=b"corrupt packet\n"))
    heard: list[str] = []
    media = MediaService()
    pb = media.load_video("k", clip, size=(8, 8), fps=15, on_error=heard.append)
    ffmpeg.release()
    media._decodes["k"].wait_done(5)
    assert (len(pb.frames), pb.complete) == (3, True)
    assert heard == ["clip.mp4: decoding stopped after 3 frame(s), which keep playing -- "
                     "ffmpeg exit 1: corrupt packet"]


def test_a_new_video_cancels_the_old_decode_and_unload_cancels_its_own(
    clip: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No stray ffmpeg keeps filling a playback nobody plays.

    MUTATION CHECK: drop the cancel in _replace_decode -- terminated == 0."""
    old = _ffmpeg(monkeypatch, _GatedFfmpeg(first=[_jpeg(1), _jpeg(2)], rest=[_jpeg(3)]))
    media = MediaService()
    media.load_video("k", clip, size=(8, 8), fps=15)
    new = _ffmpeg(monkeypatch, _GatedFfmpeg(first=[_jpeg(4), _jpeg(5)], rest=[_jpeg(6)]))
    media.load_video("k", clip, size=(8, 8), fps=15)
    assert (old.terminated, new.terminated) == (1, 0)
    media.unload("k")
    assert new.terminated == 1


def test_a_load_that_fails_leaves_the_playing_video_decoding(
    clip: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old decode is cancelled only when a new playback replaces it.

    MUTATION CHECK: cancel at the start of load_video -- the old one dies."""
    old = _ffmpeg(monkeypatch, _GatedFfmpeg(first=[_jpeg(1), _jpeg(2)], rest=[_jpeg(3)]))
    media = MediaService()
    pb = media.load_video("k", clip, size=(8, 8), fps=15)
    broken = _ffmpeg(monkeypatch, _GatedFfmpeg(first=[], rest=[], code=1))
    broken.release()
    with pytest.raises(ThemeError):
        media.load_video("k", clip, size=(8, 8), fps=15)
    assert old.terminated == 0 and media.playback("k") is pb
    old.release()
    media._decodes["k"].wait_done(5)
    assert len(pb.frames) == 3
