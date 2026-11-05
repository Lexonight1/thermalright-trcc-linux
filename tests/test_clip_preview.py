"""The trimmer's preview: one background ffmpeg, as the C#'s UCVideoCut -- in the App.

Both skins ran one ffmpeg per 41 ms tick on the GUI thread (70-290 ms a grab,
measured), so previewing froze the window.  The preview decodes the range
once in the background and hands back finished frames.  It ran in each
window until 2026-11-05; the App runs it now (``ClipPreviews`` through the
``VideoTool``), and a window dispatches ``PreviewClip`` / ``TickClipPreview``
/ ``StopClipPreview``.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import cast

import pytest

from trcc.adapters.media.ffmpeg import FfmpegPreview, FfmpegTool
from trcc.core import toolchain
from trcc.core.models import FitMode, VideoExportRequest
from trcc.core.ports import PreviewRun
from trcc.services.clip_preview import ClipPreviews, PreviewFrame

needs_ffmpeg = pytest.mark.skipif(
    not (toolchain.present("ffmpeg") and toolchain.present("ffprobe")),
    reason="ffmpeg/ffprobe not on PATH",
)

BOX = (160, 160)
T = "window-1"


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """A real 2 s 320x240 test pattern."""
    out = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=duration=2:size=320x240:rate=30",
         "-pix_fmt", "yuv420p", str(out)],
        capture_output=True, check=True, timeout=120,
    )
    return out


def _req(clip: Path, start: int, end: int, **kw) -> VideoExportRequest:
    return VideoExportRequest(source=clip, start_ms=start, end_ms=end,
                              target_w=160, target_h=160, **kw)


def _drain(previews: ClipPreviews, done, token: str = T,
           timeout: float = 15.0) -> tuple[list[bytes], PreviewFrame]:
    """Tick until ``done(frames, last)`` -- ffmpeg's own pace, not a fixed
    window.  Returns what arrived and the last answer; the caller's asserts
    judge them, a timeout included."""
    frames: list[bytes] = []
    last = PreviewFrame(None, 0, True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not done(frames, last):
        last = previews.tick(token)
        if last.image is not None:
            frames.append(last.image)
        time.sleep(0.005)
    return frames, last


def _looped(frames: list[bytes], _last: PreviewFrame) -> bool:
    """The first frame came round again: one whole pass, then the loop."""
    return len(frames) > 1 and frames[-1] == frames[0]


def _idle(_frames: list[bytes], last: PreviewFrame) -> bool:
    return not last.active


@needs_ffmpeg
def test_play_decodes_the_range_once_at_the_chosen_rate(clip: Path) -> None:
    previews = ClipPreviews(video_tool=FfmpegTool())
    started = time.monotonic()
    previews.play(T, _req(clip, 0, 1000, fps=15), BOX)
    assert time.monotonic() - started < 0.5, "play() must not wait for ffmpeg"

    frames, _ = _drain(previews, _looped)
    previews.stop(T)
    distinct = list(dict.fromkeys(frames))
    # 1 s at 15 fps: ~15 distinct frames, then it loops back to the first.
    assert 15 <= len(distinct) <= 17
    assert len(frames) > len(distinct), "a finished preview did not loop"
    assert frames[len(distinct)] == frames[0]


@needs_ffmpeg
def test_a_still_is_one_frame_then_the_preview_is_idle(clip: Path) -> None:
    previews = ClipPreviews(video_tool=FfmpegTool())
    previews.still(T, _req(clip, 500, 501), BOX)
    frames, last = _drain(previews, _idle)
    assert len(frames) == 1
    assert frames[0][:2] == b"\xff\xd8"
    assert not last.active


@needs_ffmpeg
def test_the_preview_is_composed_as_export_encodes_it(clip: Path) -> None:
    """Fit WIDTH on a 4:3 clip into a square letterboxes: the exporter's own
    chain, so the preview cannot disagree with the export (#291)."""
    from PySide6.QtGui import QImage

    previews = ClipPreviews(video_tool=FfmpegTool())
    previews.still(T, _req(clip, 0, 1, fit_mode=FitMode.WIDTH), BOX)
    (jpeg,), _ = _drain(previews, _idle)
    img = QImage.fromData(jpeg)
    assert (img.width(), img.height()) == BOX
    top = img.pixelColor(80, 2)
    assert max(top.red(), top.green(), top.blue()) < 24, "no letterbox bar"


@needs_ffmpeg
def test_stop_kills_ffmpeg_and_removes_its_files(clip: Path) -> None:
    previews = ClipPreviews(video_tool=FfmpegTool())
    previews.play(T, _req(clip, 0, 2000, fps=24), BOX)
    run = previews._previews[T].run
    assert isinstance(run, FfmpegPreview)
    previews.stop(T)
    assert run._proc.poll() is not None
    assert not run._folder.exists()
    assert not previews.tick(T).active


class _Proc:
    """A stand-in preview ffmpeg: running until ``returncode`` is set."""
    pid = 1
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def wait(self) -> int | None:
        return self.returncode


def test_a_frame_still_being_written_is_not_read(tmp_path: Path) -> None:
    """Complete = the next file exists, or ffmpeg has exited."""
    proc = _Proc()
    run = FfmpegPreview(cast("subprocess.Popen[bytes]", proc), tmp_path)
    (tmp_path / "00001.jpg").write_bytes(b"half")
    assert run.frame(0) is None
    (tmp_path / "00002.jpg").write_bytes(b"next")
    assert run.frame(0) == b"half"
    assert run.frame(1) is None
    proc.returncode = 0
    assert run.frame(1) == b"next"


class _NothingWritten(PreviewRun):
    """A run whose ffmpeg exited without writing a frame."""

    def frame(self, index: int) -> bytes | None:
        return None

    @property
    def ended(self) -> bool:
        return True

    @property
    def exit_code(self) -> int | None:
        return 234

    def close(self) -> None:
        return None


class _Empty(FfmpegTool):
    def preview(self, request: VideoExportRequest, box: tuple[int, int],
                source_wh: tuple[int, int], *, still: bool) -> PreviewRun | None:
        return _NothingWritten()


def test_ffmpeg_that_writes_nothing_ends_the_preview(tmp_path: Path) -> None:
    previews = ClipPreviews(video_tool=_Empty())
    previews.play(T, _req(tmp_path / "x.mp4", 0, 1000), BOX)
    assert previews.tick(T) == PreviewFrame(None, 0, False)


class _Recording(FfmpegTool):
    """The real tool, noting where each run it starts begins."""

    def __init__(self) -> None:
        super().__init__()
        self.started: list[int] = []

    def preview(self, request: VideoExportRequest, box: tuple[int, int],
                source_wh: tuple[int, int], *, still: bool) -> PreviewRun | None:
        self.started.append(request.start_ms)
        return super().preview(request, box, source_wh, still=still)


@needs_ffmpeg
def test_a_drag_lands_on_the_newest_still_only(clip: Path) -> None:
    """Stills asked for while one is in flight collapse to the newest: one
    grab running, one waiting, never a pile of processes."""
    from PySide6.QtGui import QImage

    tool = _Recording()
    previews = ClipPreviews(video_tool=tool)
    for ms in range(0, 1900, 100):
        previews.still(T, _req(clip, ms, ms + 1), BOX)
    frames, last = _drain(previews, _idle)
    assert tool.started == [0, 1800]
    assert len(frames) == 2
    assert not last.active
    assert not QImage.fromData(frames[-1]).isNull()


@needs_ffmpeg
def test_two_windows_never_stop_each_others_preview(clip: Path) -> None:
    """Each window's preview is its own, by its token.

    MUTATION CHECK: key every preview by one name -- the second play stops
    the first, and the first window's ticks come back inactive."""
    previews = ClipPreviews(video_tool=FfmpegTool())
    previews.play("a", _req(clip, 0, 1000, fps=15), BOX)
    previews.play("b", _req(clip, 0, 1000, fps=15), BOX)
    previews.stop("b")
    frames, last = _drain(previews, _looped, token="a")
    previews.stop("a")
    assert len(frames) > 1 and last.active


@needs_ffmpeg
def test_a_window_previews_through_the_app(clip: Path, tmp_path: Path) -> None:
    """The whole path a trimmer takes: its ``ClipPreview`` dispatches
    ``PreviewClip`` and ``TickClipPreview`` on the App, and gets the JPEG back
    -- no ffmpeg in the window."""
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.ui.presentation.clip_preview import ClipPreview

    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    try:
        window = ClipPreview(app)
        window.still(_req(clip, 500, 501), BOX)
        assert window.active
        deadline = time.monotonic() + 15
        jpeg = None
        while jpeg is None and time.monotonic() < deadline:
            jpeg = window.poll()
            time.sleep(0.005)
        assert jpeg is not None and jpeg[:2] == b"\xff\xd8"
        window.poll()
        assert not window.active
    finally:
        app.close()


@needs_ffmpeg
def test_a_missing_clip_ends_the_preview_with_no_picture(tmp_path: Path) -> None:
    """ffmpeg cannot open it, exits with no frame, and the window goes idle."""
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.ui.presentation.clip_preview import ClipPreview

    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    try:
        window = ClipPreview(app)
        window.play(_req(tmp_path / "gone.mp4", 0, 1000), BOX)
        deadline = time.monotonic() + 15
        frames = []
        while window.active and time.monotonic() < deadline:
            frames.append(window.poll())
            time.sleep(0.005)
        assert not window.active and set(frames) == {None}
    finally:
        app.close()


@needs_ffmpeg
def test_closing_the_app_ends_every_preview(clip: Path, tmp_path: Path) -> None:
    """A window that never said stop leaves no ffmpeg behind the App.

    MUTATION CHECK: drop ``clip_previews.close()`` from ``App.close`` -- the
    preview's ffmpeg is still running."""
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.ui.presentation.clip_preview import ClipPreview

    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    ClipPreview(app).play(_req(clip, 0, 2000, fps=24), BOX)
    (preview,) = app.clip_previews._previews.values()
    run = preview.run
    assert isinstance(run, FfmpegPreview)
    app.close()
    assert run._proc.poll() is not None and not run._folder.exists()
