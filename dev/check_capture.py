#!/usr/bin/env python3
"""What does screen capture do on the desktop I am sitting in, right now?

Composes the SHIPPING chain for this session, reports it, and takes one real
frame.  Ten seconds on each desktop, instead of launching the GUI and driving
it by hand.

    PYTHONPATH=src python3.12 dev/check_capture.py

Written 2026-09-18, after a change that composes the capture chain per display
session (portal + one tool on Wayland, Qt's grab + the X11 tools on X11) left
the maintainer needing to log into GNOME and XFCE to see whether it still
worked.  This answers that without a GUI.

**What it proves and what it does not.**  It proves the session is detected
correctly, that a sane chain is composed, and that a frame with real pixels
comes back.  It does NOT drive the LCD, so a wire-level problem is out of
scope -- for that, run the app.

On Wayland the portal may ask for consent.  Approve it and the run continues;
a refusal is reported as a refusal, not a crash.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# A real windowing platform: QtNativeCapture declines outright when Qt is
# offscreen, which would make an X11 session look broken.
os.environ.pop("QT_QPA_PLATFORM", None)

from trcc.adapters.screencast import build_screen_capture
from trcc.adapters.system import current_platform
from trcc.core.models import SCREENCAST_TICK_S, DisplayServer, RawFrame
from trcc.core.ports import CaptureNotReady

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _describe(chain: object, depth: int = 0) -> list[str]:
    """The composed chain, one link per line."""
    from trcc.adapters.screencast.pipewire import PipeWireScreenCapture
    from trcc.adapters.screencast.qt import QtNativeCapture, ToolCapture

    pad = "   " * depth
    if isinstance(chain, PipeWireScreenCapture):
        return [f"{pad}PipeWireScreenCapture  (xdg-portal + PipeWire)",
                *_describe(chain._fallback, depth + 1)]
    if isinstance(chain, QtNativeCapture):
        row = f"{pad}QtNativeCapture        (QScreen.grabWindow)"
        if chain._then is None:
            return [row]
        return [row, *_describe(chain._then, depth + 1)]
    if isinstance(chain, ToolCapture):
        rows = [f"{pad}ToolCapture"]
        for tool in chain.tools:
            where = shutil.which(tool.name)
            mark = f"{GREEN}present{OFF}" if where else f"{YELLOW}not installed{OFF}"
            kind = "region" if tool.region else "full screen, cropped"
            rows.append(f"{pad}   {tool.name:18} {mark:24} {DIM}{kind}{OFF}")
        return rows
    return [f"{pad}{type(chain).__name__}"]


def _looks_blank(frame: RawFrame) -> bool:
    """A frame of one colour is what a refused capture returns."""
    data = frame.data
    step = max(3, (len(data) // 3 // 500) * 3)
    sample = {tuple(data[i:i + 3]) for i in range(0, len(data) - 2, step)}
    return len(sample) <= 1


def main() -> int:
    platform = current_platform()
    session = platform.display_session()

    print(f"\n{DIM}── session ──{OFF}")
    print(f"   server   {session.server.value}")
    print(f"   desktop  {', '.join(session.desktops) or '(unnamed)'}")
    for var in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
                "WAYLAND_DISPLAY", "DISPLAY"):
        print(f"   {DIM}{var:20} {os.environ.get(var, '') or '(unset)'}{OFF}")

    capture = build_screen_capture(session, Path.home() / ".trcc")
    print(f"\n{DIM}── the chain this session composes ──{OFF}")
    for line in _describe(capture):
        print(f"   {line}")

    if session.server is DisplayServer.WAYLAND:
        print(f"\n{DIM}   Wayland: the portal may ask to share your screen.")
        print(f"   Pick the SCREEN, not a region — a region is remembered and "
              f"silently crops every later run.{OFF}")

    print(f"\n{DIM}── one real frame ──{OFF}")
    region = (0, 0, 320, 320)
    deadline = time.monotonic() + 45
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            frame = capture.grab_region(*region)
        except CaptureNotReady:
            time.sleep(0.5)                 # consent pending; that is normal
            continue
        except OSError as e:
            print(f"   {RED}FAILED{OFF} after {attempts} attempt(s): {e}")
            return 1
        blank = _looks_blank(frame)
        mark = f"{YELLOW}ONE COLOUR — probably not a real capture{OFF}" if blank \
            else f"{GREEN}has real pixels{OFF}"
        print(f"   got {frame.width}x{frame.height}, "
              f"{len(frame.data)} bytes, {mark}")
        print(f"   {DIM}after {attempts} attempt(s){OFF}")
        stop = getattr(capture, "stop", None)
        if callable(stop):
            stop()
        print(f"\n   capture cadence: {1 / SCREENCAST_TICK_S:.1f} fps "
              f"({SCREENCAST_TICK_S * 1000:.0f} ms), from the C# oracle\n")
        return 1 if blank else 0

    print(f"   {RED}TIMED OUT{OFF} after 45 s — the portal was never approved")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
