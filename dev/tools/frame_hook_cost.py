#!/usr/bin/env python3
"""What each sent frame costs the App's own listeners -- on the mock, zero
hardware.

Every frame a panel is sent publishes ``FrameSent``; whatever the App hears it
with runs once per frame, forever.  RGB Lighting used to hear every frame in
the App itself (``App._follow_frame``), following or not; as a module it
watches the panels' pictures only while a device leads (``host.frames``).
This prices that hook: instructions per ``FrameSent``, counted with
``perf stat -e instructions:u`` and normalised by the frames published -- the
work counted independently, never CPU%% (memory
``project_cpu_regression_is_logging``).

    PYTHONPATH=src python3.12 dev/tools/frame_hook_cost.py
    git worktree add --detach /tmp/base <ref>
    PYTHONPATH=src python3.12 dev/tools/frame_hook_cost.py --against /tmp/base/src

Each arm runs in its own process: once publishing nothing, once publishing
``--frames`` frames; the difference over the frames is the cost per frame.
``--follow`` first sets RGB to follow the panel (OpenRGB, refused on the
stand-in, so nothing is reached), so the follower samples too.  No window,
no device: a ``MockPlatform`` in a throwaway folder.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_KEY = "0402:3922"

#: The arm itself, run as ``python -c`` under perf with the tree on the path.
_ARM = r'''
import os, sys, tempfile
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TRCC_DAEMON"] = "0"
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from PySide6.QtGui import QColor, QImage
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.events import FrameSent
frames, follow = int(sys.argv[3]), sys.argv[4] == "1"
app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}],
                       Path(tempfile.mkdtemp())), renderer=QtRenderer())
if follow:
    try:
        from trcc.modules.rgb.core import SetRgbFollow
        from trcc.modules.rgb.core.models import RgbFollowMode
    except ImportError:
        from trcc.core.commands import SetRgbFollow
        from trcc.core.models import RgbFollowMode
    app.dispatch(SetRgbFollow(mode=RgbFollowMode.OPENRGB, source="0402:3922"))
surface = QImage(320, 320, QImage.Format.Format_ARGB32)
surface.fill(QColor(200, 40, 40))
for _ in range(frames):
    app.events.publish(FrameSent(key="0402:3922", bytes_sent=1, surface=surface))
app.close()
'''


def instructions(src: Path, frames: int, follow: bool) -> int:
    """User-space instructions of one arm, summed over every core type."""
    out = subprocess.run(
        ["perf", "stat", "-x,", "-e", "instructions:u", sys.executable, "-c", _ARM,
         str(src), str(src.parent), str(frames), "1" if follow else "0"],
        capture_output=True, text=True, check=True, env={**os.environ, "PYTHONPATH": ""})
    return sum(int(line.split(",")[0]) for line in out.stderr.splitlines()
               if "instructions" in line and line.split(",")[0].isdigit())


def per_frame(src: Path, frames: int, follow: bool, reps: int) -> float:
    """Instructions per published frame: the median difference of *reps*
    pairs of runs (frames, none)."""
    costs = sorted((instructions(src, frames, follow) - instructions(src, 0, follow))
                   / frames for _ in range(reps))
    return costs[len(costs) // 2]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--against", type=Path, help="another tree's src/ to compare")
    ap.add_argument("--frames", type=int, default=200000)
    ap.add_argument("--reps", type=int, default=5)
    args = ap.parse_args(argv)
    trees = {"this tree": _REPO / "src"}
    if args.against:
        trees["against"] = args.against.resolve()
    os.chdir(tempfile.mkdtemp(prefix="trcc-frame-hook-"))
    for follow in (False, True):
        label = "following an LCD" if follow else "not following"
        for name, src in trees.items():
            cost = per_frame(src, args.frames, follow, args.reps)
            print(f"  {label:18s} {name:10s} {cost:10.0f} instructions per frame")
    return 0


if __name__ == "__main__":
    sys.exit(main())
