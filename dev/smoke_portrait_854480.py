#!/usr/bin/env python3
"""Smoke: #136 portrait compose on the 854x480 panel, via the restored mock.

Proves — through the *real* App + DisplayService driven on scripted USB (no
hardware) — that:

  * a portrait-authored theme (config width<height, 480x854) composes at
    portrait dims on a non-square ``rotate=True`` panel, and the GUI
    preview-bezel size (``DisplayService.composed_canvas_size``) matches
    480x854 — #136 phases 2-3; and
  * a landscape theme (the stock Theme1) stays 854x480 — no regression.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python3.12 dev/smoke_portrait_854480.py
Gate: prints ``PASS`` and exits 0 when both assertions hold.
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

    from trcc._boot import trcc_next
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, LoadTheme

    app = cast(App, trcc_next(platform=cast(Any, platform), renderer=QtRenderer()))

    # Attach + connect the scripted 854x480 panel through the real adapters.
    app.attach(_VID, _PID)
    app.dispatch(ConnectDevice(key=_KEY))
    device = app.devices[_KEY]
    info, profile = device.info, device.profile
    print(f"connected {_KEY}: profile.resolution={profile.resolution} "
          f"rotate={profile.rotate}")
    assert profile.resolution == (854, 480), profile.resolution

    portrait_dir = _seed_theme("PortraitTest", 480, 854)
    landscape_dir = _seed_theme("LandscapeTest", 854, 480)

    # ── Portrait theme → 480x854 compose + preview bezel ──────────────────
    app.dispatch(LoadTheme(key=_KEY, path=portrait_dir))
    portrait = app.active_themes[_KEY]
    p_canvas = app.display.composed_canvas_size(info, portrait, profile, 0)
    print(f"portrait theme: resolution={portrait.resolution} "
          f"composed_canvas_size={p_canvas}")

    # ── Landscape theme → 854x480 (no regression) ─────────────────────────
    app.dispatch(LoadTheme(key=_KEY, path=landscape_dir))
    landscape = app.active_themes[_KEY]
    l_canvas = app.display.composed_canvas_size(info, landscape, profile, 0)
    print(f"landscape theme: resolution={landscape.resolution} "
          f"composed_canvas_size={l_canvas}")

    app.close()

    ok = p_canvas == (480, 854) and l_canvas == (854, 480)
    if ok:
        print("PASS — #136 portrait compose verified: portrait→480x854, "
              "landscape→854x480")
        return 0
    print(f"FAIL — expected portrait (480,854)/landscape (854,480), "
          f"got {p_canvas}/{l_canvas}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
