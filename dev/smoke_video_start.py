#!/usr/bin/env python3
"""Smoke: a video starts playing on its first frames, not its last (#264).

Drives the real App on scripted USB (no hardware) with real ffmpeg: connect the
mock 320x320 SCSI panel, dispatch ``PlayVideo`` on a 4K clip, and time the
dispatch -- the time a UI waits, since the gui sends it on its UI thread.  Then
the decode must finish in the background, and frames must reach the panel.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python3.12 dev/smoke_video_start.py [clip]
      (no clip: a 10 s 3840x2160 test pattern is made with ffmpeg in a temp dir)
Gate: prints ``PASS`` and exits 0 when the dispatch returned in under half the
time the whole decode took, the decode completed, and the panel was written.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import DEV_TRCC  # also puts src/ + repo on sys.path
from tests.mock_platform import MockPlatform

_SCSI = "0402:3922"   # 320x320
_SPECS = [{"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100}]


def _clip(argv: list[str], scratch: Path) -> Path:
    """The clip to play: the one named, else a fresh 4K test pattern."""
    if len(argv) > 1:
        return Path(argv[1])
    clip = scratch / "pattern4k.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=3840x2160:rate=30", "-t", "10",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    str(clip)], check=True)
    return clip


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import logging

    from trcc.adapters.infra.logging import configure_logging

    platform = MockPlatform(_SPECS, DEV_TRCC)
    configure_logging(platform.paths().log_file(), level=logging.INFO)

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    from trcc._boot import trcc
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, PlayVideo, TickDisplay

    with tempfile.TemporaryDirectory() as tmp:
        clip = _clip(sys.argv, Path(tmp))
        app = cast(App, trcc(platform=cast(Any, platform), renderer=QtRenderer()))
        try:
            assert app.dispatch(ConnectDevice(key=_SCSI)).ok, "connect"
            sent_before = len(app.devices[_SCSI]._transport.sent)
            began = time.perf_counter()
            result = app.dispatch(PlayVideo(key=_SCSI, path=clip))
            returned = time.perf_counter() - began
            assert result.ok, result.message
            playback = app.media.playback(_SCSI)
            assert playback is not None
            at_return = (len(playback.frames), playback.frame_count, playback.complete)
            for _ in range(5):
                app.dispatch(TickDisplay(key=_SCSI))
            while not playback.complete and time.perf_counter() - began < 120:
                time.sleep(0.05)
            finished = time.perf_counter() - began
            sent = len(app.devices[_SCSI]._transport.sent) - sent_before
        finally:
            app.close()

    print(f"PlayVideo returned after {returned:.2f} s with {at_return[0]} frame(s) decoded "
          f"of ~{at_return[1]} (complete={at_return[2]})")
    print(f"decode finished after   {finished:.2f} s: {len(playback.frames)} frame(s)")
    print(f"panel writes during the run: {sent}")
    ok = returned < finished / 2 and playback.complete and sent > 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
