#!/usr/bin/env python3
"""Quit a GUI while background work is still drawing in Qt -- does it crash?

In-process (Windows, ``TRCC_DAEMON=0``) a ``dispatch_in_background`` worker
runs the Command itself, Qt drawing included: a Visualizer tile is
``QtRenderer.encode_png``.  This asks for every visual's tile four times over
on a mock App, quits the event loop *quit_ms* after it starts -- the way a GUI
quits -- and reports how many of *runs* processes died by a signal.

Measured 2026-11-10: quit at 0 ms crashed 8 runs in 8 (SIGSEGV) before
``qt_background.drain``; 0 in 8 after.  At 300 ms the tiles were done: 0 in 8
both ways -- so quit at 0 is the regime that runs hot.

    PYTHONPATH=src python3 dev/tools/quit_mid_work.py              # 8 runs, quit at 0 ms
    PYTHONPATH=src python3 dev/tools/quit_mid_work.py --runs 20 --quit-ms 50
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: One process: the mock App, the tiles asked for, the quit.
_CHILD = r'''
import sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
qt = QApplication(sys.argv[:1])
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.modules.visualizer.core.commands import ListVisuals, VisualTile
from trcc.ui.qt_background import dispatch_in_background

class Sink(QWidget):
    def got(self, result): pass

app = App(MockPlatform([], Path(tempfile.mkdtemp())), renderer=QtRenderer())
sink = Sink()
for kind in [v.kind for v in app.dispatch(ListVisuals()).visuals] * 4:
    dispatch_in_background(app, VisualTile(visual=kind, size=480), sink.got)
QTimer.singleShot(int(sys.argv[1]), qt.quit)
sys.exit(qt.exec())
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Quit a GUI while background work draws in Qt.")
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--quit-ms", type=int, default=0)
    args = parser.parse_args()
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "TRCC_DAEMON": "0",
           "PYTHONPATH": str(REPO / "src")}
    codes = [subprocess.run([sys.executable, "-c", _CHILD, str(args.quit_ms)],
                            cwd=REPO, env=env, capture_output=True,
                            timeout=120).returncode for _ in range(args.runs)]
    crashed = sum(code < 0 for code in codes)
    print(f"quit at {args.quit_ms} ms: {crashed} of {args.runs} crashed  {codes}")
    return 1 if crashed else 0


if __name__ == "__main__":
    raise SystemExit(main())
