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


def _build_uc_led_control() -> Any:
    from trcc.ui.gui.uc_led_control import UCLedControl
    # Style 1, the segment panel of #333's screenshot: its mode-button row sits
    # over the six tiles baked into the background image.
    panel = UCLedControl(language="en")
    panel.initialize(1, zone_count=1)
    return panel


#: Built windows and Apps stay alive until the snapshot is taken.
_KEEP: list[Any] = []


def _build_uc_theme_setting(edited: bool) -> Any:
    """The gui's device Settings panel, fed the way the window feeds it.

    Its state reaches it through the window (Queries the window and its LCD
    handler read), and moving that path is exactly what a conversion to the
    shared ``ThemeSettings`` presentation does -- so the panel is grabbed out
    of a real ``TRCCApp`` over a mock panel, never built bare.  *edited*: two
    overlay elements and the mask hidden, so the grid, the element editor and
    the mask box show values rather than defaults.
    """
    import tempfile
    sys.path.insert(0, str(REPO))
    from tests.conftest import show_a_theme
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import (
        AddOverlayElement,
        ConnectDevice,
        EnableOverlay,
        SetMaskVisible,
    )
    from trcc.ui.gui.trcc_app import TRCCApp

    key = "0402:3922"
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}],
                           Path(tempfile.mkdtemp())), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=key)).ok
    show_a_theme(app, key)
    if edited:
        for command in (AddOverlayElement(key=key, type="text", x=40, y=60, text="Hello",
                                          color="#ff8800", size=28, element_id="a"),
                        # Not a clock: its tile shows the time of day, so a
                        # before and an after a minute apart would differ.
                        AddOverlayElement(key=key, type="text", x=120, y=200,
                                          text="World", element_id="b"),
                        EnableOverlay(key=key, enabled=True),
                        SetMaskVisible(key=key, visible=False)):
            assert app.dispatch(command).ok, command
    window = TRCCApp(app=app)
    window.replay_initial_devices()
    _KEEP.extend((app, window))
    return window.uc_theme_setting


def _tile_pngs(folder: Path, names: list[str]) -> dict[str, Path]:
    """One flat-coloured PNG per name, the colour fixed by its position."""
    from PySide6.QtGui import QColor, QImage
    folder.mkdir(parents=True, exist_ok=True)
    made = {}
    for i, name in enumerate(names):
        image = QImage(160, 160, QImage.Format.Format_RGB32)
        image.fill(QColor(40 + 30 * i, 200 - 20 * i, 90 + 15 * i))
        made[name] = folder / f"{name}.png"
        image.save(str(made[name]))
    return made


#: Seven tiles -- one row and a half -- a long name among them, which the
#: tile shortens.
_TILE_NAMES = ["Theme1", "Theme2", "Theme3", "A very long theme name", "Theme5",
               "Theme6", "Theme7"]


def _build_uc_theme_local(filled: bool) -> Any:
    """The gui's Local themes tab: seven tiles, the third current."""
    import tempfile

    from trcc.core.results import ThemeListEntry
    from trcc.ui.gui.uc_theme_local import UCThemeLocal

    panel = UCThemeLocal()
    pngs = _tile_pngs(Path(tempfile.mkdtemp()), _TILE_NAMES) if filled else {}
    panel.set_themes([ThemeListEntry(name=name, resolution=(320, 320),
                                     path=str(png.parent / name), preview=str(png),
                                     origin="user" if i % 2 else "shipped")
                      for i, (name, png) in enumerate(pngs.items())])
    if filled:
        panel.show_current_theme(pngs["Theme3"].parent / "Theme3")
    _KEEP.append(panel)
    return panel


def _build_uc_theme_web(filled: bool) -> Any:
    """The gui's Cloud backgrounds tab: previews, the downloaded ones solid,
    the rest dashed."""
    import tempfile

    from trcc.ui.gui.uc_theme_web import UCThemeWeb

    panel = UCThemeWeb()
    if filled:
        folder = Path(tempfile.mkdtemp())
        _tile_pngs(folder, ["a001", "a002", "b001", "b002", "c001", "c002"])
        (folder / "a002.mp4").write_bytes(b"")          # downloaded
    panel.set_web_directory(folder if filled else None)
    _KEEP.append(panel)
    return panel


class _Masks:
    """A bus that answers ``ListMasks`` with fixed masks -- nothing else."""

    def __init__(self, masks: list[Any]) -> None:
        self._masks = masks

    def dispatch(self, _command: Any) -> Any:
        from types import SimpleNamespace
        return SimpleNamespace(ok=True, message="", directory="", masks=self._masks)


def _build_uc_theme_mask(filled: bool) -> Any:
    """The gui's Cloud masks tab: five masks, one the user's own."""
    import tempfile
    from types import SimpleNamespace

    from trcc.ui.gui.uc_theme_mask import UCThemeMask

    pngs = (_tile_pngs(Path(tempfile.mkdtemp()), ["000a", "000b", "000c", "000d", "mine"])
            if filled else {})
    masks = [SimpleNamespace(name=name, path=str(png.parent), preview=str(png),
                             is_custom=name == "mine") for name, png in pngs.items()]
    bus: Any = _Masks(masks)
    panel = UCThemeMask(app=bus)
    panel.set_resolution("320x320")
    panel.set_mask_directory(Path(tempfile.mkdtemp()))
    _KEEP.append(panel)
    return panel


def _build_uc_visuals() -> Any:
    """The Audio Visualizer's gui tab, out of a real ``TRCCApp`` over a mock
    panel: its gallery listed and every tile the App draws arrived."""
    import tempfile
    sys.path.insert(0, str(REPO))
    from PySide6.QtWidgets import QApplication

    from tests.conftest import show_a_theme
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice
    from trcc.ui.gui.trcc_app import TRCCApp

    key = "0402:3922"
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}],
                           Path(tempfile.mkdtemp())), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=key)).ok
    show_a_theme(app, key)
    window = TRCCApp(app=app)
    window.replay_initial_devices()
    page: Any = window._module_pages["visualizer"]
    page.refresh()
    _settle(QApplication.instance(), page)
    _KEEP.extend((app, window))
    return page


def _settle(qt: Any, page: Any, seconds: float = 60.0) -> None:
    """Run the event loop until *page* has every tile: its listing arrived
    and nothing waits in its tile queue.

    Not "until the picture stops changing": the tiles scrolled out of view
    change nothing on it, so that stopped while they were still being drawn
    -- on a worker thread, in Qt -- and the process exited under them: a
    segfault in 4 runs of 8 (2026-11-10), from the tool, not the page.
    """
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end and not (page.item_widgets and not page._tiles._waiting):
        qt.processEvents()
        time.sleep(0.05)
    for _ in range(5):              # the last tile's delivery and paint
        qt.processEvents()
        time.sleep(0.05)
    if page._tiles._waiting:
        raise SystemExit(f"uc_visuals: {len(page._tiles._waiting)} tile(s) still "
                         f"drawing after {seconds:g} s")


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
    "uc_led_control": _build_uc_led_control,
    "uc_theme_setting": lambda: _build_uc_theme_setting(edited=False),
    "uc_theme_setting_edited": lambda: _build_uc_theme_setting(edited=True),
    # The tile grid every theme tab and the Audio Visualizer stand on.
    "uc_theme_local": lambda: _build_uc_theme_local(filled=True),
    "uc_theme_local_empty": lambda: _build_uc_theme_local(filled=False),
    "uc_theme_web": lambda: _build_uc_theme_web(filled=True),
    "uc_theme_web_empty": lambda: _build_uc_theme_web(filled=False),
    "uc_theme_mask": lambda: _build_uc_theme_mask(filled=True),
    "uc_theme_mask_empty": lambda: _build_uc_theme_mask(filled=False),
    "uc_visuals": _build_uc_visuals,
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
