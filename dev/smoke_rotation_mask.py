#!/usr/bin/env python3
"""Smoke: rotation reloads the active theme + mask (#136), command-driven.

Drives the **real** App + DisplayService on a scripted 854x480 non-square panel
(``rotate=True``) — no GUI window, no hardware — and verifies the rotation
behaviors against the current architecture:

  TEST 1  geometry   — ``DisplayService.composed_canvas_size`` swaps
                       854x480 ↔ 480x854 with user orientation (the GUI
                       preview-bezel contract); the rotated render path runs.
  TEST 2  theme      — on ``SetOrientation`` the active theme reloads from the
                       rotated-resolution dir (theme854480 → theme480854).
  TEST 3  mask       — on ``SetOrientation`` an active web/zt mask reloads to
                       the rotated-resolution variant (zt854480 → zt480854).

TESTs 2-3 exercise ``App._on_orientation_changed`` end-to-end through the real
``SetOrientation`` → ``OrientationChanged`` → observer → ``LoadTheme``/
``ApplyMask`` chain — the #136 behavior the cutover dropped and restored in
e6fba9a2.  (The old harness drove the GUI window via the removed
``Topic.DEVICE_LIST`` + ``LCDDevice`` facade; this command-driven rewrite needs
neither.)

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python3.12 dev/smoke_rotation_mask.py
Gate: prints PASS and exits 0 when every assertion holds.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import DEV_DATA, DEV_TRCC   # also puts src/ + repo on sys.path
from tests.mock_platform import MockPlatform

_KEY = "87ad:70db"
_VID, _PID = 0x87AD, 0x70DB
# The verified #136 panel: bulk, scripted to 854x480, rotate=True (same spec
# smoke_portrait_854480 uses).  Self-contained so the smoke runs on a fresh
# clone with no local dev/devices.json.
_SPEC = {"type": "lcd", "vid": "87ad", "pid": "70db",
         "resolution": "854x480", "pm": 11, "sub": 5}
_THEME = "Theme1"
_MASK_ID = "001a"

_FAILURES: list[str] = []


def _check(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        _FAILURES.append(label)
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def _seed_theme(name: str, width: int, height: int) -> Path:
    """Seed ``theme{width}{height}/<name>`` (00.png + config drives resolution)."""
    from PySide6.QtGui import QColor, QImage

    d = DEV_DATA / f"theme{width}{height}" / name
    d.mkdir(parents=True, exist_ok=True)
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(QColor(20, 30, 60))
    img.save(str(d / "00.png"))
    (d / "trcc.json").write_text(json.dumps({
        "name": name, "width": width, "height": height,
        "overlay_enabled": True, "rotation": 0, "background_display": True,
        "mask_visible": False, "mask_position": [width // 2, height // 2],
        "elements": [],
    }), encoding="utf-8")
    return d


def _seed_mask(mask_id: str, width: int, height: int) -> Path:
    """Seed ``web/zt{width}{height}/<mask_id>`` with a 01.png mask image."""
    from PySide6.QtGui import QColor, QImage

    d = DEV_DATA / "web" / f"zt{width}{height}" / mask_id
    d.mkdir(parents=True, exist_ok=True)
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 180))   # mostly-opaque, valid mask
    img.save(str(d / "01.png"))
    return d


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import logging

    from trcc.adapters.infra.logging import configure_logging
    platform = MockPlatform([_SPEC], DEV_TRCC)
    configure_logging(platform.paths().log_file(), level=logging.INFO)

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    from trcc._boot import trcc
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import (
        ApplyMask,
        ConnectDevice,
        LoadTheme,
        SetOrientation,
    )

    app = cast(App, trcc(platform=cast(Any, platform), renderer=QtRenderer()))
    app.attach(_VID, _PID)
    app.dispatch(ConnectDevice(key=_KEY))
    device = app.devices[_KEY]
    info, profile = device.info, device.profile
    print(f"connected {_KEY}: resolution={profile.resolution} rotate={profile.rotate}")
    assert profile.resolution == (854, 480), profile.resolution

    land_theme = _seed_theme(_THEME, 854, 480)
    app.dispatch(LoadTheme(key=_KEY, path=land_theme))
    theme = app.active_themes[_KEY]

    # ── TEST 1: geometry swap (pure contract — no observer needed) ─────────
    print("\nTEST 1: composed_canvas_size geometry")
    c0 = app.display.composed_canvas_size(info, theme, profile, 0)
    c90 = app.display.composed_canvas_size(info, theme, profile, 90)
    _check(c0 == (854, 480), "landscape orientation → (854, 480)", str(c0))
    _check(c90 == (480, 854), "90° orientation → (480, 854)", str(c90))
    surf = app.display.build_preview_surface(info, theme, {})
    _check(surf is not None, "rotated render path produces a preview surface")

    # ── TEST 2: theme reloads to the rotated-resolution dir on rotation ────
    print("\nTEST 2: theme reload on rotation")
    _seed_theme(_THEME, 480, 854)                       # portrait variant now exists
    app.dispatch(SetOrientation(key=_KEY, degrees=0))   # baseline (no-op reload)
    app.dispatch(SetOrientation(key=_KEY, degrees=90))  # → observer reloads theme
    loaded = app.active_themes[_KEY].path
    _check("theme480854" in str(loaded) and loaded.name == _THEME,
           "active theme reloaded from theme480854", str(loaded))

    # ── TEST 3: mask reloads to the rotated-resolution variant on rotation ─
    print("\nTEST 3: mask reload on rotation")
    land_mask = _seed_mask(_MASK_ID, 854, 480)
    _seed_mask(_MASK_ID, 480, 854)
    app.dispatch(SetOrientation(key=_KEY, degrees=0))   # back to landscape
    app.dispatch(ApplyMask(key=_KEY, path=land_mask))
    mp0 = app.settings.for_device(_KEY).mask_path or ""
    _check("zt854480" in mp0, "mask applied from landscape zt854480", mp0)
    app.dispatch(SetOrientation(key=_KEY, degrees=90))  # → observer reloads mask
    mp90 = app.settings.for_device(_KEY).mask_path or ""
    _check("zt480854" in mp90 and _MASK_ID in mp90,
           "mask reloaded to portrait zt480854 on rotation", mp90)

    app.close()

    if _FAILURES:
        print(f"\nFAIL: {len(_FAILURES)} assertion(s) failed: {_FAILURES}")
        return 1
    print("\nPASS — rotation reloads theme + mask; geometry swaps verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
