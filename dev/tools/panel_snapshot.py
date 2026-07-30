#!/usr/bin/env python3
"""Pixel gate for the PanelSpec conversion — did the render actually change?

Converting a panel from hand-written widget code to a ``PanelSpec`` must be
invisible to the user.  This renders a panel offscreen, hashes the PNG, and
dumps every child widget's geometry, so a conversion can be proved rather
than eyeballed:

    PYTHONPATH=src python3 dev/tools/panel_snapshot.py uc_device before
    # ...convert the panel...
    PYTHONPATH=src python3 dev/tools/panel_snapshot.py uc_device after
    PYTHONPATH=src python3 dev/tools/panel_snapshot.py uc_device compare

``compare`` exits 0 when the bytes match, 1 when they don't — and prints the
geometry diff, so a failure says WHICH widget moved instead of just "the
hash changed".

Snapshots land in ``dev/.panel_snapshots/`` (git-ignored scratch).  Panels
render against a fixed fake fleet so the result never depends on what is
plugged in.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "dev" / ".panel_snapshots"


def _fake_fleet() -> list[dict]:
    """A fixed two-device fleet — deterministic input for any device panel."""
    return [
        {"name": "Frozen Warframe", "key": "0402:3922",
         "vid": "0402", "pid": "3922"},
        {"name": "Bulk Panel", "key": "87ad:70db",
         "vid": "87ad", "pid": "70db"},
    ]


def _build_uc_device(devices: list[dict]) -> Any:
    from trcc.ui.gui.uc_device import UCDevice
    panel = UCDevice(detect_fn=lambda: devices)
    # Pin the per-OS hint: it is injected at runtime from the Platform port,
    # so leaving it live would make the snapshot host-dependent.
    panel.set_no_devices_hint("Connect a Thermalright\nLCD cooler via USB")
    return panel


def _build_uc_image_cut() -> Any:
    from trcc.ui.gui.uc_image_cut import UCImageCut
    return UCImageCut()


def _build_uc_preview() -> Any:
    from trcc.ui.gui.uc_preview import UCPreview
    # A fixed 320x320 LCD — the preview's layout keys on the panel size, so
    # pinning it keeps the snapshot independent of any attached device.
    return UCPreview(320, 320)


# Add a row per panel as it is converted.  Keep builders deterministic — no
# real device scan, no host-dependent strings.
#
# A panel gets one builder PER STATE, because a snapshot only covers what it
# renders: with devices present the empty-state labels are hidden, so moving
# them changed nothing and the gate passed a real regression.  Every state a
# panel can be in needs its own row, or the gate has a blind spot exactly
# where the code is least exercised.
PANELS: dict[str, Callable[[], Any]] = {
    "uc_device": lambda: _build_uc_device(_fake_fleet()),
    "uc_device_empty": lambda: _build_uc_device([]),
    "uc_image_cut": _build_uc_image_cut,
    "uc_preview": _build_uc_preview,
}


def _qt_app() -> Any:
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["-platform", "offscreen"])


def capture(name: str, label: str) -> int:
    if name not in PANELS:
        print(f"unknown panel {name!r} — known: {', '.join(sorted(PANELS))}")
        return 2
    app = _qt_app()
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    set_assets_dir(_PKG_ASSETS_DIR)

    panel = PANELS[name]()
    app.processEvents()

    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / f"{name}_{label}.png"
    pix = panel.grab()
    pix.save(str(png), "PNG")
    digest = hashlib.sha256(png.read_bytes()).hexdigest()

    geo = OUT / f"{name}_{label}.geom"
    geo.write_text(_geometry(panel), encoding="utf-8")

    print(f"{name} [{label}]: {pix.width()}x{pix.height()}")
    print(f"  sha256 {digest}")
    print(f"  geometry → {geo.relative_to(REPO)}")
    return 0


def _geometry(panel: Any) -> str:
    """Every child's type, rect and text — so a diff explains itself."""
    lines: list[str] = []
    for child in panel.findChildren(object):
        if not hasattr(child, "geometry"):
            continue
        try:
            g = child.geometry()
        except Exception:
            continue
        text = ""
        if hasattr(child, "text"):
            try:
                text = str(child.text())
            except Exception:
                text = ""
        lines.append(
            f"{type(child).__name__:16} "
            f"({g.x()},{g.y()},{g.width()},{g.height()}) {text!r}"
        )
    return "\n".join(sorted(lines))


def compare(name: str) -> int:
    before, after = OUT / f"{name}_before.png", OUT / f"{name}_after.png"
    if not before.exists() or not after.exists():
        print(f"need both snapshots — run 'before' and 'after' for {name}")
        return 2

    b = hashlib.sha256(before.read_bytes()).hexdigest()
    a = hashlib.sha256(after.read_bytes()).hexdigest()
    pixels_same = a == b

    # Geometry is checked even when the pixels match.  A HIDDEN widget that
    # moved renders identically today and is a real regression the moment the
    # panel enters the state that shows it — returning early on a hash match
    # let exactly that through.
    import difflib
    gb, ga = OUT / f"{name}_before.geom", OUT / f"{name}_after.geom"
    diff: list[str] = []
    if gb.exists() and ga.exists():
        diff = list(difflib.unified_diff(
            gb.read_text().splitlines(), ga.read_text().splitlines(),
            "before", "after", lineterm="", n=0,
        ))

    if pixels_same and not diff:
        print(f"{name}: IDENTICAL  {a}")
        return 0

    if pixels_same:
        print(f"{name}: pixels match but GEOMETRY MOVED — a widget that is "
              f"hidden in this state changed position:")
    else:
        print(f"{name}: DIFFERENT\n  before {b}\n  after  {a}")
        if not diff:
            print("  geometry is identical — pixels moved without layout "
                  "(styling, asset or paint-order change).")
    for line in diff[:40]:
        print(f"  {line}")
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    name = sys.argv[1]
    action = sys.argv[2] if len(sys.argv) > 2 else "before"
    if action == "compare":
        return compare(name)
    return capture(name, action)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "src"))
    raise SystemExit(main())
