#!/usr/bin/env python3
"""Whole-frame gate for the render pipeline -- did a change move one byte?

Drives every frame builder in ``DisplayService`` the way production calls it
(``device.info`` + the live ``device.profile``) on mock panels through the
real ``QtRenderer``, and records a hash of the WIRE bytes and of the PREVIEW
for each case:

* panels: a square SCSI 320x320, an 854x480 that rotates (87ad:70db PM 11
  SUB 5), a 1600x720 with the split overlay (87ad:70db PM 64);
* every display angle (0 / 90 / 180 / 270);
* the theme render (image + mask + text and metric elements), its preview,
  background modes colour and transparent, a playing video, a user and a
  program override image, the mask hidden, a portrait theme, split modes
  1-3, a cast frame with an audio spectrum, the game frame, a solid colour
  and a pushed image.

Refactors of the compose step must leave every hash unchanged::

    PYTHONPATH=src python3 dev/tools/frame_snapshot.py record before
    # ...change the pipeline...
    PYTHONPATH=src python3 dev/tools/frame_snapshot.py record after
    PYTHONPATH=src python3 dev/tools/frame_snapshot.py compare before after

``compare`` exits 0 when every case matches and 1 naming each case that moved.
Snapshots land in ``dev/.panel_snapshots/`` (git-ignored scratch).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import tempfile
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "dev" / ".panel_snapshots"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))
log = logging.getLogger(__name__)

#: (name, MockPlatform spec, device key, canvas size)
_PANELS = [
    ("scsi320", {"vid": "0402", "pid": "3922", "fbl": 100}, "0402:3922", (320, 320)),
    ("bulk854", {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 11, "sub": 5},
     "87ad:70db", (854, 480)),
    ("split1600", {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 64, "sub": 0},
     "87ad:70db", (1600, 720)),
]
_ANGLES = (0, 90, 180, 270)
_SENSORS = {"cpu:temp": 47.0, "cpu:load": 12.0}
_ELEMENTS = [
    {"type": "text", "x": 60, "y": 30, "text": "Snapshot", "color": "#ffcc00", "size": 20},
    {"type": "metric", "x": 60, "y": 70, "metric": "cpu:temp", "format": "{value}°C",
     "color": "#00ccff", "size": 24},
]


def _image(path: Path, size: tuple[int, int], rgb: tuple[int, int, int],
           marker: bool = True) -> Path:
    """A solid picture with a contrasting corner block, so a misplaced or
    rotated draw changes pixels a solid fill would hide."""
    from PySide6.QtGui import QColor, QImage

    img = QImage(*size, QImage.Format.Format_RGB888)
    img.fill(QColor(*rgb))
    if marker:
        for x in range(size[0] // 4):
            for y in range(size[1] // 6):
                img.setPixelColor(x, y, QColor(255 - rgb[0], 255 - rgb[1], 255 - rgb[2]))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path)), path
    log.debug("_image: %s %s", path.name, size)
    return path


def _mask(path: Path, size: tuple[int, int]) -> Path:
    """A mask: transparent with an opaque band across the middle."""
    from PySide6.QtGui import QColor, QImage

    img = QImage(*size, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    for x in range(size[0]):
        for y in range(size[1] * 2 // 5, size[1] * 3 // 5):
            img.setPixelColor(x, y, QColor(40, 200, 40, 200))
    assert img.save(str(path)), path
    log.debug("_mask: %s", path)
    return path


def _theme(root: Path, name: str, size: tuple[int, int]) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "trcc.json").write_text(json.dumps(
        {"name": name, "width": size[0], "height": size[1], "elements": _ELEMENTS}),
        encoding="utf-8")
    _image(folder / "00.png", size, (0, 64, 128))
    _mask(folder / "01.png", size)
    log.debug("_theme: %s %s", name, size)
    return folder


def _jpeg(size: tuple[int, int], rgb: tuple[int, int, int]) -> bytes:
    """A solid frame, encoded by the production JPEG encoder."""
    from PySide6.QtGui import QColor, QImage

    from trcc.adapters.render.qt import QtRenderer

    img = QImage(*size, QImage.Format.Format_RGB888)
    img.fill(QColor(*rgb))
    log.debug("_jpeg: %s %s", size, rgb)
    return QtRenderer().encode_jpeg(img, 95)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _cases(app: Any, key: str, size: tuple[int, int], root: Path,
           split: bool) -> Iterator[tuple[str, Callable[[], dict[str, str]]]]:
    """Every case for one panel at its current angle: (name, run)."""
    from trcc.core.models import RawFrame
    from trcc.services.media import Playback

    device = app.devices[key]
    display = app.display
    s = app.settings.for_device(key)
    landscape = _theme(root / "themes", f"land{size[0]}", size)
    portrait = _theme(root / "themes", f"port{size[0]}", (size[1], size[0]))
    user_bg = _image(app.platform.paths().user_content_dir() / "bg" / "user.png",
                     (size[0] // 2, size[1]), (180, 40, 40))
    program_bg = _image(root / "program" / "prog.png", size, (40, 180, 40))
    pushed = _image(root / "pushed.png", size, (90, 90, 200))

    def fresh() -> None:
        s.background_mode, s.mask_visible, s.background_path = "theme", True, None
        s.split_mode = 0
        app.media._playbacks.pop(key, None)
        display.invalidate(key)

    def frame(theme_dir: Path) -> dict[str, str]:
        theme = app.themes.load(theme_dir)
        wire = display.build_frame(info=device.info, theme=theme,
                                   sensors=_SENSORS, profile=device.profile)
        preview = display.build_preview_surface(info=device.info, theme=theme,
                                                sensors=_SENSORS,
                                                profile=device.profile)
        return {"wire": _digest(wire),
                "preview": _digest(app.renderer.encode_png(preview))}

    def setting(**fields: Any) -> Callable[[], dict[str, str]]:
        def run() -> dict[str, str]:
            fresh()
            for name, value in fields.items():
                setattr(s, name, value)
            return frame(landscape)
        return run

    def video() -> dict[str, str]:
        fresh()
        app.media._playbacks[key] = Playback(
            frames=[_jpeg(size, (200, 100, 0)), _jpeg(size, (0, 100, 200))], fps=15)
        return frame(landscape)

    def portrait_theme() -> dict[str, str]:
        fresh()
        return frame(portrait)

    def cast() -> dict[str, str]:
        fresh()
        grab = RawFrame(data=bytes([30, 60, 90]) * (size[0] * size[1]),
                        width=size[0], height=size[1])
        wire = display.build_screencast_frame(
            info=device.info, frame=grab, theme=app.themes.load(landscape),
            sensors=_SENSORS, spectrum=tuple(i / 16 for i in range(16)),
            profile=device.profile)
        return {"wire": _digest(wire), "preview": _preview(app, key)}

    def game() -> dict[str, str]:
        fresh()
        wire = display.build_game_frame(info=device.info,
                                        theme=app.themes.load(landscape),
                                        sensors=_SENSORS, profile=device.profile)
        return {"wire": _digest(wire), "preview": _preview(app, key)}

    def solid() -> dict[str, str]:
        fresh()
        wire = display.build_solid_color_frame(info=device.info, color=(255, 0, 128),
                                               profile=device.profile)
        return {"wire": _digest(wire), "preview": _preview(app, key)}

    def image() -> dict[str, str]:
        fresh()
        wire = display.build_image_frame(info=device.info, path=pushed,
                                         profile=device.profile)
        return {"wire": _digest(wire), "preview": _preview(app, key)}

    yield "theme", setting()
    yield "mode-color", setting(background_mode="color", overlay_background=(20, 120, 220))
    yield "mode-transparent", setting(background_mode="transparent")
    yield "mask-hidden", setting(mask_visible=False)
    yield "override-user", setting(background_path=str(user_bg))
    yield "override-program", setting(background_path=str(program_bg))
    yield "video", video
    yield "portrait-theme", portrait_theme
    if split:
        for mode in (1, 2, 3):
            yield f"split-{mode}", setting(split_mode=mode)
    yield "cast+spectrum", cast
    yield "game", game
    yield "solid", solid
    yield "image", image


def _preview(app: Any, key: str) -> str:
    surface = app.display.rendered_surface(key)
    return _digest(app.renderer.encode_png(surface)) if surface is not None else "none"


def record() -> dict[str, dict[str, str]]:
    """Every case on every panel at every angle: case id -> hashes."""
    import trcc.services.display as display_module
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.services._clock import compute_clock
    from tests.mock_platform import MockPlatform

    def fixed_clock(language: str = "en", **_: Any) -> dict[str, str]:
        return compute_clock(language, now=datetime(2026, 11, 5, 12, 34))

    display_module.compute_clock = fixed_clock      # one moment, every run
    out: dict[str, dict[str, str]] = {}
    for name, spec, key, size in _PANELS:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = App(platform=MockPlatform([spec], root), renderer=QtRenderer())
            try:
                app.discover_and_connect()
                for angle in _ANGLES:
                    app.settings.for_device(key).orientation = angle
                    case_root = root / f"a{angle}"
                    for case, run in _cases(app, key, size, case_root,
                                            split=name == "split1600"):
                        out[f"{name}/{angle}/{case}"] = run()
            finally:
                app.close()
        log.info("record: %s done", name)
    return out


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING)
    log.info("main: %s", argv)
    OUT.mkdir(parents=True, exist_ok=True)
    if len(argv) == 2 and argv[0] == "record":
        hashes = record()
        path = OUT / f"frames-{argv[1]}.json"
        path.write_text(json.dumps(hashes, indent=1, sort_keys=True))
        print(f"record: {len(hashes)} case(s) -> {path}")
        return 0
    if len(argv) == 3 and argv[0] == "compare":
        a, b = (json.loads((OUT / f"frames-{n}.json").read_text()) for n in argv[1:])
        moved = sorted(c for c in a.keys() | b.keys() if a.get(c) != b.get(c))
        for case in moved:
            print(f"  MOVED {case}: {a.get(case)} -> {b.get(case)}")
        print(f"compare: {len(a)} case(s), {len(moved)} moved")
        return 1 if moved else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
