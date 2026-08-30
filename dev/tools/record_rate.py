"""How many log records does ONE rendered frame write?  Measured, at default -v.

The burn-down's ratchet counts SILENT functions.  This measures the opposite
defect, which nothing counted: a function on the frame path that logs through
the ORDINARY logger instead of ``core.logs.per_frame`` writes a record EVERY
frame.  The file floor is DEBUG at every rung by design -- ``trcc report`` is
the whole diagnosis for hardware we do not own -- so those records are written
even with no ``-v`` at all.  Cost is paid, and the one-shot lines a report is
actually read for get scrolled out of the 1 MB tail.

That shape was 82-90%% of the CPU regression since v9.9.2.  It is also easy to
re-create by accident while ADDING coverage, which is how it came back.

Run at the verbosity a user runs::

    PYTHONPATH=src python3.12 dev/tools/record_rate.py            # static theme
    PYTHONPATH=src python3.12 dev/tools/record_rate.py --video X  # advancing video

Needs a real device.  The answer should be **0.00 records/frame**: everything
per-frame belongs to the ``trcc.frame`` family, which is silent by default.
Any non-zero row names a call site to move onto ``per_frame(__name__)``.

Reads the file the app actually writes, not a captured handler -- the question
is what a reporter's log contains, and only the file can answer that.

The panel updates far more slowly than the loop renders and holds the last frame
after the run exits -- same flat-out drive as ``frame_profile``, where 200 built
frames produced 4 raw writes.  Neither is a bug.
"""
from __future__ import annotations

import argparse
import collections
import logging
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trcc._boot import trcc
from trcc.adapters.infra.logging import configure_logging
from trcc.core.commands import (
    ConnectDevice,
    DiscoverDevices,
    LoadTheme,
    PlayVideo,
    RenderAndSend,
)
from trcc.core.logs import levels_for

log = logging.getLogger(__name__)

#: ``2026-08-30T11:55:17 DEBUG   trcc.services.display:Foo.bar:329: msg``
_RECORD = re.compile(r"^\S+ (\w+)\s+(\S+?):(\S+?):(\d+):")

WARMUP = 20


def measure(frames: int, video: Path | None) -> tuple[int, collections.Counter]:
    """Render *frames* at DEFAULT verbosity; return (frames, records by site)."""
    app = trcc()
    ladder = levels_for(0)                      # what a user runs: no -v at all
    # A DEDICATED file, not the app's own.  Sharing it meant starting from a
    # byte offset into a log that prior runs had already grown near the 1 MB
    # cap -- the rotation then fired mid-measurement, the offset pointed past
    # the end of the new file, and the tool reported a confident 0.00
    # records/frame for a video run that was in fact the noisiest.  The cap is
    # also lifted here, and rotation is asserted against below.
    tmp = tempfile.mkdtemp(prefix="trcc-record-rate-")
    log_file = Path(tmp) / "trcc.log"
    # ``level`` is the ROOT level and therefore the FILE's; ``stderr_level`` is
    # the terminal's.  Passing the terminal level as ``level`` sets the root to
    # WARNING, suppresses every DEBUG record everywhere, and makes this tool
    # report a confident 0.00 no matter what the code does -- the same false
    # negative, by a second route.  Both are spelled out because this tool's
    # answer is only worth anything if it cannot fake a pass.
    configure_logging(log_file, level=ladder.file,
                      stderr_level=ladder.terminal,
                      per_frame=ladder.per_frame,
                      max_bytes=1_000_000_000, latest_max_bytes=1_000_000_000)

    keys = [d.key for d in getattr(app.dispatch(DiscoverDevices()), "devices", [])]
    if not keys:
        raise SystemExit("no device attached — this needs real hardware")
    key = keys[0]
    if not app.dispatch(ConnectDevice(key=key)).ok:
        raise SystemExit("connect failed")
    theme = app.platform.paths().data_dir() / "theme320320" / "Theme1"
    if not app.dispatch(LoadTheme(key=key, path=theme)).ok:
        raise SystemExit("theme load failed")

    playback = None
    if video is not None:
        if not app.dispatch(PlayVideo(key=key, path=video)).ok:
            raise SystemExit("play failed")
        playback = app.media.playback(key)

    def tick() -> None:
        if playback is not None:
            playback.advance()
        app.dispatch(RenderAndSend(key=key))

    for _ in range(WARMUP):    # connect + first-frame lines are one-shot, not
        tick()                 # per-frame, so they must not be counted

    # A byte offset, not a line count: the file is appended to.
    before = log_file.stat().st_size if log_file.exists() else 0
    for _ in range(frames):
        tick()

    after = log_file.stat().st_size if log_file.exists() else 0
    if after < before or any(log_file.with_suffix(f".log.{i}").exists()
                             for i in range(1, 6)):
        raise SystemExit(
            "the log ROTATED mid-measurement — every count below the mark is "
            "lost and a 0.00 here would be a false pass, not a clean result")

    by_site: collections.Counter[str] = collections.Counter()
    with log_file.open("r", errors="replace") as fh:
        fh.seek(before)
        for line in fh:
            m = _RECORD.match(line)
            if m is not None:
                level, module, func, lineno = m.groups()
                by_site[f"{module}:{func}:{lineno} [{level}]"] += 1
    return frames, by_site


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--video", type=Path, default=None)
    args = ap.parse_args()

    frames, by_site = measure(args.frames, args.video)
    total = sum(by_site.values())
    workload = "video" if args.video else "static theme"
    print(f"# {total} record(s) over {frames} frames · {workload} · default -v")
    print(f"# {total / frames:.2f} records/frame  (target: 0.00)")
    for site, n in by_site.most_common():
        print(f"{n / frames:9.3f}  {site}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
