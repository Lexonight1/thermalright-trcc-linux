#!/usr/bin/env python3
"""What a built-in effect on the motherboard's headers costs the App.

Runs a real App over the scripted copy of the maintainer's ASUS board (no
USB), gives all three ARGB headers *leds* LEDs and the effect named, lets it
run for *seconds*, and prints how many frames reached the board.  Wrap it in
``perf stat -e instructions:u`` and divide by the frames -- CPU% means nothing
on a box whose clock moves with load (CLAUDE.md, "Never measure CPU with
CPU%").  ``none`` runs the same App with no effect, for the baseline.

Run:  PYTHONPATH=src:. perf stat -e instructions:u \\
          python3.12 dev/tools/board_effect_cost.py rainbow --seconds 10
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _mock_bootstrap  # noqa: F401 -- puts src/ + repo on sys.path
from tests.mock_platform import MockPlatform, ScriptedRgbHardware

log = logging.getLogger("board_effect_cost")


def main() -> int:
    parser = argparse.ArgumentParser(description="Cost of a header effect.")
    parser.add_argument("effect", help="a RamEffect value, or none")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--leds", type=int, default=12)
    args = parser.parse_args()

    from trcc.app import App
    from trcc.core.commands import ScanRgbLights, SetAuraEffect, SetAuraLeds
    from trcc.core.models import RamEffect

    with tempfile.TemporaryDirectory() as tmp:
        platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], Path(tmp))
        platform.rgb = ScriptedRgbHardware()
        app = App(platform)
        app.dispatch(ScanRgbLights())
        headers = tuple(f"aura/argb{n}" for n in (1, 2, 3))
        for n in (1, 2, 3):
            app.dispatch(SetAuraLeds(header=n, leds=args.leds))
        if args.effect != "none":
            result = app.dispatch(SetAuraEffect(
                effect=RamEffect(args.effect), refs=headers,
                colors=((255, 32, 64), (32, 128, 255))))
            if not result.ok:
                print(f"refused: {result.message}")
                return 1
        platform.aura.writes.clear()
        time.sleep(args.seconds)
        frames = len(platform.aura.reports(0x40))
        app.close()
    print(f"{args.effect}: {frames} direct packet(s) in {args.seconds:.0f} s "
          f"({frames / args.seconds:.1f}/s) on 3 header(s) of {args.leds} LEDs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
