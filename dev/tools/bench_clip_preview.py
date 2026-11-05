"""What one trimmer-preview tick costs, today against two ways through the App.

    PYTHONPATH=src python3.12 dev/tools/bench_clip_preview.py [VIDEO]

The trimmer's timer asks for the next preview frame every 41 ms.  Today the
window runs that ffmpeg itself (``ClipPreview``) and reads a JPEG file per
tick.  Moving the ffmpeg into the App (ffmpeg port, increment 5) makes each
tick a round trip over the daemon socket, carrying either:

* **bytes**  -- the JPEG itself, base64 in the JSON line (``ipc`` encodes any
  ``bytes`` field that way), or
* **name**   -- only which file is ready; the window reads it from disk, as
  ``frame_share`` does for the panel preview.

and, since increment 5 landed, **as built**: the real ``PreviewClip`` and
``TickClipPreview`` while the App's own ffmpeg plays the clip -- the cost a
window pays per tick now (ffmpeg's own CPU is a child process, not in it).

All three are timed on the SAME real frames -- the ones the preview makes
from *VIDEO* at the gui's 480x500 box -- through a real ``IPCServer`` and
``AppProxy`` on a private socket.  Server and client share this process, so
``process_time`` is the CPU of BOTH ends per tick: what the user's machine
pays, not just one side.  Wall time is the latency the window waits.
"""
from __future__ import annotations

import logging
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.conftest import FakePlatform
from trcc import ipc
from trcc.adapters.media.ffmpeg import FfmpegTool
from trcc.app import App
from trcc.core.commands import PreviewClip, StopClipPreview, TickClipPreview
from trcc.core.commands._base import Query
from trcc.core.models import VideoExportRequest
from trcc.core.results import Result
from trcc.proxy import AppProxy
from trcc.services.clip_preview import ClipPreviews

log = logging.getLogger("bench_clip_preview")

BOX = (480, 500)            # the gui trimmer's preview (uc_video_cut.PREVIEW_W/H)
TICKS = 300


@dataclass(frozen=True, slots=True)
class BenchFrameResult(Result):
    """One preview frame: its bytes, or only its name."""
    image: bytes = field(default=b"", repr=False)
    name: str = ""


#: The frames the App would hold, by index -- filled before the server starts.
_FRAMES: list[Path] = []


@dataclass(frozen=True, slots=True)
class BenchFrameBytes(Query[BenchFrameResult]):
    """Frame *index*, its JPEG carried over the socket."""
    index: int

    def execute(self, app: App) -> BenchFrameResult:
        path = _FRAMES[self.index % len(_FRAMES)]
        return BenchFrameResult(ok=True, image=path.read_bytes())


@dataclass(frozen=True, slots=True)
class BenchFrameName(Query[BenchFrameResult]):
    """Frame *index*, only its file name carried over the socket."""
    index: int

    def execute(self, app: App) -> BenchFrameResult:
        return BenchFrameResult(ok=True, name=str(_FRAMES[self.index % len(_FRAMES)]))


def _frames(video: Path, out: Path) -> list[Path]:
    """The real preview frames: the App's preview playing 4 s of *video*."""
    previews = ClipPreviews(video_tool=FfmpegTool())
    previews.play("frames", VideoExportRequest(source=video, start_ms=0, end_ms=4000,
                                               target_w=320, target_h=320, fps=24), BOX)
    got: list[bytes] = []
    deadline = time.monotonic() + 60
    while len(got) < 96 and time.monotonic() < deadline:
        if (data := previews.tick("frames").image) is not None:
            got.append(data)
        else:
            time.sleep(0.005)
    previews.stop("frames")
    paths = []
    for i, data in enumerate(got):
        (path := out / f"{i:05d}.jpg").write_bytes(data)
        paths.append(path)
    log.info("_frames: %d frame(s) from %s", len(paths), video.name)
    return paths


def _time(label: str, tick) -> tuple[float, float]:
    """Median wall ms and mean CPU ms (both ends) of *tick* over TICKS calls."""
    walls: list[float] = []
    cpu0 = time.process_time()
    for i in range(TICKS):
        t = time.perf_counter()
        tick(i)
        walls.append((time.perf_counter() - t) * 1000)
    cpu = (time.process_time() - cpu0) * 1000 / TICKS
    wall = statistics.median(walls)
    print(f"  {label:44} wall {wall:7.3f} ms (p95 {sorted(walls)[int(TICKS * .95)]:7.3f})"
          f"   cpu {cpu:7.3f} ms/tick")
    return wall, cpu


def main(argv: list[str]) -> int:
    """Make the frames, then time the three ways."""
    logging.basicConfig(level=logging.WARNING)
    video = Path(argv[0]) if argv else ROOT / "dev" / "assets" / "missing.mp4"
    if not video.is_file():
        print(f"no video at {video}: pass one")
        return 2
    work = Path(tempfile.mkdtemp(prefix="trcc-bench-preview-"))
    _FRAMES.extend(_frames(video, work))
    sizes = [p.stat().st_size for p in _FRAMES]
    print(f"{video.name}: {len(_FRAMES)} real preview frames at {BOX[0]}x{BOX[1]}, "
          f"JPEG {min(sizes) // 1024}-{max(sizes) // 1024} KB "
          f"(median {statistics.median(sizes) / 1024:.0f} KB)")

    ipc.register_types(sys.modules[__name__])   # the bench's own Query + Result
    sock = Path(f"/tmp/trcc-bench-{id(work):x}.sock")
    ipc.socket_path = lambda: sock          # a socket of this bench's own
    app = App(FakePlatform(work / "home"))
    server = ipc.IPCServer(app)
    server.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while not ipc.daemon_running():
        time.sleep(0.01)
    proxy = AppProxy(timeout=5.0)

    def today(i: int) -> None:          # ClipPreview.poll: 2 stats + a read
        path = _FRAMES[i % len(_FRAMES)]
        path.is_file()
        path.with_name(f"{i + 1:05d}.jpg").is_file()
        path.read_bytes()

    def via_bytes(i: int) -> None:
        proxy.dispatch(BenchFrameBytes(index=i)).image

    def via_name(i: int) -> None:
        Path(proxy.dispatch(BenchFrameName(index=i)).name).read_bytes()

    shown: list[int] = []

    def as_built(_i: int) -> None:
        if proxy.dispatch(TickClipPreview(token="bench")).image:
            shown.append(1)

    try:
        print(f"per tick, {TICKS} ticks (the timer fires every 41 ms):")
        _time("today: the window reads the file", today)
        _time("App, bytes: JPEG as base64 over the socket", via_bytes)
        _time("App, name: the file name, window reads it", via_name)
        proxy.dispatch(PreviewClip(token="bench", path=video, start_ms=0, end_ms=10_000,
                                   box_w=BOX[0], box_h=BOX[1], target_w=320,
                                   target_h=320, fps=24))
        time.sleep(0.5)                 # let it write ahead, as a window's first ticks do
        _time("as built: TickClipPreview (App's ffmpeg)", as_built)
        proxy.dispatch(StopClipPreview(token="bench"))
        print(f"  ({len(shown)} of {TICKS} ticks carried a frame)")
    finally:
        proxy.close()
        server.shutdown()
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
