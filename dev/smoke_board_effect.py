#!/usr/bin/env python3
"""Smoke: a built-in effect on a motherboard header, from the RGB page.

Drives the real RGB page of each window on a real App over the scripted
copy of the maintainer's ASUS board (``ScriptedRgbHardware`` -- no USB):
Find lights, give header 1 its 8 LEDs, pick Motherboard, Built-in effect,
tick header 1,
pick Static, press Apply -- then reads back what reached the board.  Then
Rainbow runs for ``--seconds`` and the frames the board got are counted.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python3.12 dev/smoke_board_effect.py [--seconds N]
Gate: prints ``PASS`` and exits 0 when, in both windows, Apply painted header
1's 8 LEDs the picked colour, saved nothing on the board (no 0x3F report),
and Rainbow sent a stream of changing frames.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mock_bootstrap  # noqa: F401 -- puts src/ + repo on sys.path
from tests.mock_platform import MockPlatform, ScriptedRgbHardware

log = logging.getLogger("smoke_board_effect")
_HEADER = "aura/argb1"
_LEDS = 8


def _wait(check: Any, timeout: float = 5.0) -> bool:
    """Let Qt run until *check* holds, or *timeout* passes."""
    from PySide6.QtWidgets import QApplication
    deadline = time.monotonic() + timeout
    while not check() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return bool(check())


def _painted(platform: MockPlatform, rgb: tuple[int, int, int]) -> bool:
    """Whether a direct packet painted header 1's LEDs *rgb*."""
    want = bytes([0x40, 0x80, 0, _LEDS]) + bytes(rgb * _LEDS)
    return any(p[:4 + 3 * _LEDS] == want for p in platform.aura.reports(0x40))


def _drive(skin: type, seconds: float) -> tuple[bool, str]:
    """One window: Static on header 1, then Rainbow for *seconds*."""
    from trcc.app import App
    from trcc.core.commands import SetRgbFollow
    from trcc.core.models import RamEffect, RgbFollowMode
    from trcc.ui.presentation.rgb_page import RgbDevice, RgbSource

    with tempfile.TemporaryDirectory() as tmp:
        platform = MockPlatform([{"vid": "0416", "pid": "8001", "pm": 1}], Path(tmp))
        platform.rgb = ScriptedRgbHardware()
        app = App(platform)
        view = skin(app)
        try:
            view.resize(1170, 630)
            view.show()
            view._on_find()
            if not _wait(lambda: not view._busy):
                return False, "Find never answered"
            view._on_leds(1, _LEDS)               # the header's LED box
            if not _wait(lambda: not view._busy):
                return False, "the LED count never answered"
            view._devices_column.buttons[RgbDevice.MOTHERBOARD].click()
            view._source_buttons[RgbSource.EFFECT].click()
            view._lights_column._on_light_toggled(_HEADER, True)
            view._effect._on_effect(view._effect._effect.findData(RamEffect.STATIC))
            colour = view.page.effect_picks.settings().colors[0]
            problem = view.page.problem
            if problem is not None:
                return False, f"Apply refused: {problem}"
            view._apply.click()
            if not _wait(lambda: _painted(platform, colour)):
                return False, f"header 1 never turned {colour}: {view._message.text()!r}"
            if platform.aura.reports(0x3F):
                return False, "something was saved on the board (0x3F)"
            view._effect._on_effect(view._effect._effect.findData(RamEffect.RAINBOW))
            view._apply.click()
            _wait(lambda: not view._busy)
            platform.aura.writes.clear()
            time.sleep(seconds)
            frames = [p for p in platform.aura.reports(0x40) if p[1] == 0x80]
            app.dispatch(SetRgbFollow(mode=RgbFollowMode.OFF))
        finally:
            view.close()
            app.close()
    rate = len(frames) / seconds
    if len({bytes(f[4:7]) for f in frames}) < 3:
        return False, f"Rainbow sent {len(frames)} frame(s), not a changing stream"
    return True, f"Static {colour} painted; Rainbow {rate:.1f} frames/s"


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    parser = argparse.ArgumentParser(description="Smoke: a built-in effect on a motherboard header.")
    parser.add_argument("--seconds", type=float, default=2.0,
                        help="how long Rainbow runs (default 2)")
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication
    _qt = QApplication.instance() or QApplication(sys.argv)  # kept alive for the run

    from trcc.ui.gui.uc_rgb import UCRgbPage
    from trcc.ui.qtgui.panels.rgb_panel import QtRgbPage

    ok = True
    for name, skin in (("gui", UCRgbPage), ("qtgui", QtRgbPage)):
        passed, detail = _drive(cast(type, skin), args.seconds)
        print(f"{name}: {'ok' if passed else 'FAIL'} -- {detail}")
        ok &= passed
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
