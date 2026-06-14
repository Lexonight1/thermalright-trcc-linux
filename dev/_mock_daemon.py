#!/usr/bin/env python3
"""Mock-platform daemon for the daemon-mode smoke (``dev/smoke_daemon_gui.py``).

Runs the **real** daemon entry point — ``trcc.daemon.run_daemon`` — against a
scripted two-device ``MockPlatform`` fleet, so zero real hardware is needed.
The ONLY thing swapped vs a packaged ``trcc daemon`` is the platform: socket
binding (``ipc.socket_path()`` → ``$XDG_RUNTIME_DIR/trcc.sock``), the
``IPCServer``, signal handling and shutdown are all production code reached
through ``run_daemon``'s DI seam.  No hand-rolled bring-up, no monkeypatching
of ``ipc`` internals — that was the rot this rewrite removes.

The smoke harness spawns this subprocess with ``$XDG_RUNTIME_DIR`` pointed at a
throwaway dir, then talks to it with the real client helpers (``AppProxy`` /
``ipc`` / ``daemon.kill_daemon``).  ``SPECS`` is the single source of truth for
the fleet — the harness imports it to compute its expectations.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# The daemon owns USB directly and renders headless — same Qt knob the real
# daemon honours via the renderer's offscreen bootstrap.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import DEV_TRCC

# Two distinct-resolution LCD panels.  Their identity travels over IPC (TEST 1),
# then the bulk one is connected + dimmed over IPC (TESTs 2-3).  Kept here so
# the harness imports the SAME list it validates against.
SPECS: list[dict] = [
    {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 11},  # 854x480 bulk
    {"type": "lcd", "vid": "0416", "pid": "5302", "pm": 36},  # 240x240 scsi
]


def main() -> int:
    from trcc.adapters.infra.logging import configure_logging
    from trcc.daemon import run_daemon

    from tests.mock_platform import MockPlatform

    platform = MockPlatform(SPECS, DEV_TRCC)
    configure_logging(platform.paths().log_file(), level=logging.INFO)
    # renderer=None → run_daemon builds the offscreen QtRenderer, exactly as a
    # packaged ``trcc daemon`` does; we inject only the scripted platform.
    return run_daemon(platform=platform)


if __name__ == "__main__":
    sys.exit(main())
