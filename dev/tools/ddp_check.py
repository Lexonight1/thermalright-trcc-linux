#!/usr/bin/env python3
"""Prove OpenRGB drives a cooler end to end, with no hardware and no OpenRGB
(#160, #333).

A real App on the mock platform with one cooler, the session started, so RGB
Lighting opens the cooler's own DDP port (on a free port, never 4048 -- your
OpenRGB may be there), and ``ddp_send.py`` -- OpenRGB's packets -- in a
separate process.  The cooler is left in its OWN mode (Rainbow): nothing is
switched on for OpenRGB.  Then:

  * every lit LED i shows the sender's pixel i (not a zone repeating the
    start of the frame -- the bug this tool was written to catch);
  * the colours move between renders;
  * once the sender stops, the cooler's own mode is back within LIVE_FOR_S.

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
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--pm", type=int, default=16,
                    help="the cooler's PM byte (16 = PA120, 1 = AX120)")
    ap.add_argument("--leds", type=int, default=128,
                    help="pixels the sender sends (more than the cooler has)")
    args = ap.parse_args()
    # After sys.path is set: the repo's own packages and the sender beside us.
    from ddp_send import rainbow

    import trcc.modules.rgb.app as rgb_app
    from tests.mock_platform import MockPlatform
    from trcc.app import App
    from trcc.core.commands import RenderLed, SetLedBrightness, SetLedMode
    from trcc.core.led_models import LEDMode
    from trcc.modules.rgb.adapters.ddp import DdpPort
    from trcc.modules.rgb.services.openrgb_lights import LIVE_FOR_S

    class _FreePort(DdpPort):
        """The module's own port, on one the OS picks -- never 4048."""

        def __init__(self, port: int) -> None:
            super().__init__(0)

    rgb_app.DdpPort = _FreePort
    with tempfile.TemporaryDirectory(prefix="trcc-ddp-check-") as root:
        app = App(MockPlatform([{"type": "led", "vid": "0416", "pid": "8001",
                                 "pm": args.pm}], Path(root)))
        try:
            app.start_session()
            module = app.modules.root("rgb")
            assert isinstance(module, rgb_app.RgbModule), module
            lights = module.lights
            addresses = lights.addresses()
            if not addresses:
                print("no cooler port opened -- is RGB Lighting on?")
                return 1
            cooler, port, error = addresses[0]
            print(f"cooler {cooler.key}: {cooler.led_count} LEDs, "
                  f"127.0.0.1:{port} {error}")
            app.dispatch(SetLedMode(key=_KEY, mode=LEDMode.RAINBOW))
            app.dispatch(SetLedBrightness(key=_KEY, percent=100))
            own = app.dispatch(RenderLed(key=_KEY)).colors
            sender = subprocess.Popen(
                [sys.executable, str(_SENDER), "--port", str(port),
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
            time.sleep(LIVE_FOR_S + 0.3)
            after = app.dispatch(RenderLed(key=_KEY)).colors
            matches_after = max(sum(c == tuple(rainbow(args.leds, p)[i * 3:i * 3 + 3])
                                    for i, c in enumerate(after) if c != (0, 0, 0))
                                for p in phases)
            back = len(after) == len(own) and matches_after < len(lit)
            ok.append(_check("own mode back", back,
                             f"{LIVE_FOR_S + 0.3:.1f} s after the sender stopped"))
        finally:
            app.close()
    print("OK" if all(ok) else "FAILED")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
