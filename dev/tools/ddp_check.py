#!/usr/bin/env python3
"""Prove the OpenRGB LED mode end to end, with no hardware and no OpenRGB (#160).

A real App on the mock platform with one cooler, put in the OpenRGB mode
through the Command every UI sends, its real DDP listener on a free port,
and ``ddp_send.py`` -- OpenRGB's packets -- in a separate process.  Then:

  * every lit LED i shows the sender's pixel i (not a zone repeating the
    start of the frame -- the bug this tool was written to catch);
  * the colours move between renders;
  * leaving the mode closes the listener.

    PYTHONPATH=src python3 dev/tools/ddp_check.py              # PA120, 4 zones
    PYTHONPATH=src python3 dev/tools/ddp_check.py --pm 1       # AX120, digits

Exit 0 when all three hold, 1 otherwise.  Nothing touches real USB: the
cooler is ``tests/mock_platform``'s scripted one, its data under a temp dir.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_KEY = "0416:8001"
_SENDER = Path(__file__).resolve().parent / "ddp_send.py"


def _check(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pm", type=int, default=16,
                    help="the cooler's PM byte (16 = PA120, 1 = AX120)")
    ap.add_argument("--leds", type=int, default=128,
                    help="pixels the sender sends (more than the cooler has)")
    args = ap.parse_args()
    # After sys.path is set: the repo's own packages and the sender beside us.
    from ddp_send import rainbow

    from tests.mock_platform import MockPlatform
    from trcc.adapters.infra.ddp_receiver import DdpReceiver
    from trcc.app import App
    from trcc.core.commands import (
        ConnectDevice,
        RenderLed,
        SetLedBrightness,
        SetLedMode,
    )
    from trcc.core.led_models import LEDMode

    with tempfile.TemporaryDirectory(prefix="trcc-ddp-check-") as root:
        receiver = DdpReceiver(port=0)
        app = App(MockPlatform([{"type": "led", "vid": "0416", "pid": "8001",
                                 "pm": args.pm}], Path(root)),
                  led_stream=receiver)
        try:
            print(f"connect: {app.dispatch(ConnectDevice(key=_KEY)).message}")
            app.dispatch(SetLedMode(key=_KEY, mode=LEDMode.OPENRGB))
            app.dispatch(SetLedBrightness(key=_KEY, percent=100))
            app.led_animation_loop.tick()                    # opens the listener
            print(f"listener: 127.0.0.1:{receiver.port}")
            sender = subprocess.Popen(
                [sys.executable, str(_SENDER), "--port", str(receiver.port),
                 "--leds", str(args.leds), "--fps", "30", "--seconds", "2"],
                stdout=subprocess.DEVNULL)
            time.sleep(0.6)
            first = app.dispatch(RenderLed(key=_KEY)).colors
            time.sleep(0.6)
            second = app.dispatch(RenderLed(key=_KEY)).colors
            sender.wait(timeout=10)

            # Pixel i of SOME rainbow phase: LED i's hue must sit at i/leds.
            lit = [(i, c) for i, c in enumerate(second) if c != (0, 0, 0)]
            phases = [n / 120 for n in range(400)]
            matches = max(sum(c == tuple(rainbow(args.leds, p)[i * 3:i * 3 + 3])
                              for i, c in lit) for p in phases)
            ok = [
                _check("LED i = pixel i", bool(lit) and matches == len(lit),
                       f"{matches}/{len(lit)} lit LEDs match one sent frame, "
                       f"{len({c for _i, c in lit})} distinct colours"),
                _check("moving", first != second, "colours changed between renders"),
            ]
            app.dispatch(SetLedMode(key=_KEY, mode=LEDMode.RAINBOW))
            app.led_animation_loop.tick()
            ok.append(_check("listener closes", not receiver.listening,
                             "after leaving the OpenRGB mode"))
        finally:
            app.close()
    print("OK" if all(ok) else "FAILED")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
