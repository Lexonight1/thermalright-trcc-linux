"""The trimmer's preview: one background ffmpeg, as the C#'s UCVideoCut.

Both skins ran one ffmpeg per 41 ms tick on the GUI thread (70-290 ms a grab,
measured), so previewing froze the window.  ``ClipPreview`` decodes the range
once in the background and hands back finished frames.  Pure pytest: no Qt.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from trcc.core import toolchain
from trcc.core.models import FitMode, VideoExportRequest
from trcc.ui.presentation.clip_preview import ClipPreview

needs_ffmpeg = pytest.mark.skipif(
    not (toolchain.present("ffmpeg") and toolchain.present("ffprobe")),
    reason="ffmpeg/ffprobe not on PATH",
)

BOX = (160, 160)


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


def _drain(preview: ClipPreview, seconds: float) -> list[bytes]:
    frames: list[bytes] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (data := preview.poll()) is not None:
            frames.append(data)
        time.sleep(0.005)
    return frames


@needs_ffmpeg
def test_play_decodes_the_range_once_at_the_chosen_rate(clip: Path) -> None:
    preview = ClipPreview()
    preview.load(clip)
    started = time.monotonic()
    preview.play(_req(clip, 0, 1000, fps=15), BOX)
    assert time.monotonic() - started < 0.05, "play() must not wait for ffmpeg"

    frames = _drain(preview, 3.0)
    preview.stop()
    distinct = list(dict.fromkeys(frames))
    # 1 s at 15 fps: ~15 distinct frames, then it loops back to the first.
    assert 15 <= len(distinct) <= 17
    assert len(frames) > len(distinct), "a finished preview did not loop"
    assert frames[len(distinct)] == frames[0]


@needs_ffmpeg
def test_a_still_is_one_frame_then_the_preview_is_idle(clip: Path) -> None:
    preview = ClipPreview()
    preview.load(clip)
    preview.still(_req(clip, 500, 501), BOX)
    frames = _drain(preview, 2.0)
    assert len(frames) == 1
    assert frames[0][:2] == b"\xff\xd8"
    assert not preview.active


@needs_ffmpeg
def test_the_preview_is_composed_as_export_encodes_it(clip: Path) -> None:
    """Fit WIDTH on a 4:3 clip into a square letterboxes: the exporter's own
    chain, so the preview cannot disagree with the export (#291)."""
    from PySide6.QtGui import QImage

    preview = ClipPreview()
    preview.load(clip)
    preview.still(_req(clip, 0, 1, fit_mode=FitMode.WIDTH), BOX)
    (jpeg,) = _drain(preview, 2.0)
    img = QImage.fromData(jpeg)
    assert (img.width(), img.height()) == BOX
    top = img.pixelColor(80, 2)
    assert max(top.red(), top.green(), top.blue()) < 24, "no letterbox bar"


@needs_ffmpeg
def test_stop_kills_ffmpeg_and_removes_its_files(clip: Path) -> None:
    preview = ClipPreview()
    preview.load(clip)
    preview.play(_req(clip, 0, 2000, fps=24), BOX)
    proc, folder = preview._proc, preview._dir
    assert proc is not None and folder is not None
    preview.stop()
    assert proc.poll() is not None
    assert not folder.exists()
    assert not preview.active


def test_a_frame_still_being_written_is_not_read(tmp_path: Path) -> None:
    """Complete = the next file exists, or ffmpeg has exited."""

    class _Proc:
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

    preview = ClipPreview()
    proc = _Proc()
    preview._proc, preview._dir = proc, tmp_path  # type: ignore[assignment]
    (tmp_path / "00001.jpg").write_bytes(b"half")
    assert preview.poll() is None
    (tmp_path / "00002.jpg").write_bytes(b"next")
    assert preview.poll() == b"half"
    assert preview.poll() is None
    proc.returncode = 0
    assert preview.poll() == b"next"
    preview._dir = None


def test_ffmpeg_that_writes_nothing_ends_the_preview(tmp_path: Path) -> None:
    class _Done:
        returncode = 234

        def poll(self) -> int:
            return self.returncode

    preview = ClipPreview()
    preview._proc, preview._dir = _Done(), tmp_path  # type: ignore[assignment]
    assert preview.poll() is None
    assert not preview.active


@needs_ffmpeg
def test_a_drag_lands_on_the_newest_still_only(clip: Path) -> None:
    """Stills asked for while one is in flight collapse to the newest: one
    grab running, one waiting, never a pile of processes."""
    from PySide6.QtGui import QImage

    preview = ClipPreview()
    preview.load(clip)
    started: list[int] = []
    original = preview._start

    def spy(req, box, *, still):
        started.append(req.start_ms)
        original(req, box, still=still)

    preview._start = spy  # type: ignore[method-assign]
    for ms in range(0, 1900, 100):
        preview.still(_req(clip, ms, ms + 1), BOX)
    frames = _drain(preview, 3.0)
    assert started == [0, 1800]
    assert len(frames) == 2
    assert not preview.active
    assert not QImage.fromData(frames[-1]).isNull()
