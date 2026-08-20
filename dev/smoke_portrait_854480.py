#!/usr/bin/env python3
"""Smoke: canvas shape is ANGLE-driven on the 854x480 panel — the C# rule.

Driven through the *real* App + DisplayService on scripted USB (no hardware).

**Anchored to the oracle, not to our own output.**  Source of truth is
``dev/decompiler/AUDIT_LCD_PIPELINE.md`` master table, **WS-landscape** row
(854/960/800/1280, mode2 pm=9/11/10/12):

    Canvas 0/180 -> 90/270  =  WxH -> HxW

The theme's authored orientation does not appear in that column: the C# spends
the display angle purely on canvas SHAPE (``UCScreenImage.cs``
GenerateImage/SetMyUCScreenImage; every branch is ``angle == 0 || angle ==
180``) and draws content upright at raw coords either way.

What this proves that ``smoke_rotation_mask`` does not: it varies the THEME
(portrait-authored vs landscape-authored) and asserts the canvas is unchanged
by that -- the content-driven model this file used to encode.

  TEST 1  canvas   -- 854x480 at 0/180, 480x854 at 90/270, for BOTH themes.
  TEST 2  header   -- the wire frame always matches the resolution the header
                      declares, for both themes at every angle (the #262 class
                      of bug: a mismatched shape means the panel paints only
                      the overlap).

HISTORY: this smoke asserted ``portrait theme -> portrait canvas at angle 0``,
which the C# never did.  It was unanchored -- it encoded a belief, so nothing
could tell it apart from ``smoke_rotation_mask`` asserting the opposite.  It
failed silently for months because nothing ran it.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python3.12 dev/smoke_portrait_854480.py
Gate: prints ``PASS`` and exits 0 when every assertion holds.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import DEV_DATA, DEV_TRCC          # also puts src/ + repo on sys.path
from tests.mock_platform import MockPlatform

_KEY = "87ad:70db"          # the bulk panel, scripted to 854x480 (pm=11)
_VID, _PID = 0x87AD, 0x70DB

# Self-contained: build the fleet inline so the smoke doesn't depend on a local
# (untracked) dev/devices.json — it runs identically on a fresh clone / in CI.
_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db",
         "resolution": "854x480", "pm": 11, "sub": 5}


def _seed_theme(name: str, width: int, height: int) -> Path:
    """Seed a theme dir whose config declares ``width``x``height``.

    ``Theme.resolution`` comes from the config dims (not the image), so this is
    all that's needed to make a theme portrait- or landscape-authored.  Seeded
    locally so the smoke is reproducible on a fresh clone (no downloaded data).
    """
    from PySide6.QtGui import QColor, QImage

    theme_dir = DEV_DATA / "theme854480" / name
    theme_dir.mkdir(parents=True, exist_ok=True)

    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(QColor(20, 30, 60))
    img.save(str(theme_dir / "00.png"))

    config = {
        "name": name,
        "width": width, "height": height,      # ← drives Theme.resolution
        "overlay_enabled": True,
        "rotation": 0,
        "background_display": True,
        "transparent_display": False,
        "mask_visible": False,
        "mask_position": [width // 2, height // 2],
        "elements": [
            {"type": "clock", "source": "time", "x": 60, "y": height // 2,
             "name": "微软雅黑", "size": 48.0, "bold": True, "italic": False,
             "color": "#ffffff"},
        ],
    }
    (theme_dir / "trcc.json").write_text(json.dumps(config), encoding="utf-8")
    return theme_dir


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import logging

    from trcc.adapters.infra.logging import configure_logging
    platform = MockPlatform([_SPEC], DEV_TRCC)
    configure_logging(platform.paths().log_file(), level=logging.DEBUG)

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    from trcc._boot import trcc
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, LoadTheme

    app = cast(App, trcc(platform=cast(Any, platform), renderer=QtRenderer()))

    # Attach + connect the scripted 854x480 panel through the real adapters.
    app.attach(_VID, _PID)
    app.dispatch(ConnectDevice(key=_KEY))
    device = app.devices[_KEY]
    info, profile = device.info, device.profile
    assert profile is not None, "connected device must carry a DeviceProfile"
    print(f"connected {_KEY}: profile.resolution={profile.resolution} "
          f"rotate={profile.rotate}")
    assert profile.resolution == (854, 480), profile.resolution

    portrait_dir = _seed_theme("PortraitTest", 480, 854)
    landscape_dir = _seed_theme("LandscapeTest", 854, 480)

    # AUDIT_LCD_PIPELINE.md master table, WS-landscape row: WxH -> HxW.
    # Keyed by angle ONLY — the theme's authored orientation is not a term.
    expected_canvas = {0: (854, 480), 90: (480, 854),
                       180: (854, 480), 270: (480, 854)}
    header = profile.resolution
    failures: list[str] = []

    from trcc.adapters.device.bulk_lcd import jpeg_dimensions
    from trcc.core.commands import SetOrientation

    for label, theme_dir in (("portrait-authored 480x854", portrait_dir),
                             ("landscape-authored 854x480", landscape_dir)):
        app.dispatch(LoadTheme(key=_KEY, path=theme_dir))
        print(f"\n{label}: Theme.resolution="
              f"{app.active_themes[_KEY].resolution}")

        for degrees in (0, 90, 180, 270):
            app.dispatch(SetOrientation(key=_KEY, degrees=degrees))
            theme = app.active_themes[_KEY]

            # TEST 1 — canvas follows the angle, per the C# table.
            canvas = app.display.composed_canvas_size(
                info, theme, profile, degrees)
            want = expected_canvas[degrees]
            if canvas != want:
                failures.append(
                    f"{label} @ {degrees}deg: canvas {canvas}, C# says {want}")

            # TEST 2 — the frame matches the header it ships under (#262).
            payload = app.display.build_frame(info, theme, {}, profile=profile)
            dims = jpeg_dimensions(payload)
            if dims is not None and dims != header:
                failures.append(
                    f"{label} @ {degrees}deg: frame {dims} under a {header} "
                    "header — the panel paints only the overlap")

            print(f"  {degrees:>3}deg  canvas={canvas} (C# {want})  "
                  f"frame={dims} header={header}")

    app.close()

    if not failures:
        print("\nPASS — canvas is angle-driven for both theme orientations, "
              "and every frame matches its header")
        return 0
    print(f"\nFAIL — {len(failures)} divergence(s) from the C# oracle:")
    for f in failures:
        print(f"  * {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
