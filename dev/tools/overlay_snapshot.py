#!/usr/bin/env python3
"""Pixel gate for the overlay renderer -- did a refactor change what is drawn?

Renders one fixed overlay through the real ``OverlayService`` and
``QtRenderer``: a text, a metric and a clock element, each once with every
style field given and once with none (so the renderer's own defaults are
drawn too).  It saves the PNG and its hash, so a change to the draw path can
be proved invisible rather than eyeballed::

    PYTHONPATH=src python3 dev/tools/overlay_snapshot.py before
    # ...change the renderer...
    PYTHONPATH=src python3 dev/tools/overlay_snapshot.py after
    PYTHONPATH=src python3 dev/tools/overlay_snapshot.py compare

``compare`` exits 0 when the two PNGs are byte-identical, 1 when they differ,
and says how many pixels moved.  Snapshots land in ``dev/.panel_snapshots/``
(git-ignored scratch), beside ``panel_snapshot``'s.
"""
from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "dev" / ".panel_snapshots"
log = logging.getLogger(__name__)

#: Every style field given -- each kind drawn as a theme would draw it.
_STYLED = {"color": "#ff8800", "size": 22, "bold": True, "italic": False,
           "name": "Microsoft YaHei"}
_ELEMENTS = [
    {"type": "text", "x": 80, "y": 10, "text": "Styled text", **_STYLED},
    {"type": "metric", "x": 80, "y": 60, "metric": "cpu:temp",
     "format": "{value}°C", **_STYLED},
    {"type": "clock", "x": 80, "y": 110, "source": "time", "format": "%H:%M",
     **_STYLED},
    # No style fields at all: what the renderer falls back to.
    {"type": "text", "x": 240, "y": 10, "text": "Bare text"},
    {"type": "metric", "x": 240, "y": 60, "metric": "cpu:temp"},
    {"type": "clock", "x": 240, "y": 110, "source": "date"},
    {"type": "clock", "x": 240, "y": 160, "source": "weekday"},
]


def render() -> bytes:
    """The fixed overlay on a black 320x320 base, as PNG bytes."""
    from datetime import datetime

    from trcc.adapters.render.qt import QtRenderer
    from trcc.services._clock import compute_clock
    from trcc.services.overlay import OverlayService

    renderer = QtRenderer()
    base = renderer.create_surface(320, 320, color=(0, 0, 0))
    frame = OverlayService(renderer).render(
        base, {"overlay_enabled": True, "elements": _ELEMENTS},
        {"cpu:temp": 47.0},
        # A fixed moment, so the picture never depends on when it was taken.
        compute_clock("en", now=datetime(2026, 11, 5, 12, 34)))
    log.info("render: %d element(s)", len(_ELEMENTS))
    return renderer.encode_png(frame)


def _differing_pixels(a: Path, b: Path) -> int:
    from PySide6.QtGui import QImage

    one, two = QImage(str(a)), QImage(str(b))
    log.debug("_differing_pixels: %s vs %s", a.name, b.name)
    if one.size() != two.size():
        return one.width() * one.height()
    return sum(one.pixel(x, y) != two.pixel(x, y)
               for y in range(one.height()) for x in range(one.width()))


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING)
    mode = argv[0] if argv else ""
    log.info("main: %s", mode)
    OUT.mkdir(parents=True, exist_ok=True)
    if mode in ("before", "after"):
        png = render()
        path = OUT / f"overlay-{mode}.png"
        path.write_bytes(png)
        print(f"{mode}: {hashlib.sha256(png).hexdigest()[:16]}  {path}")
        return 0
    if mode == "compare":
        before, after = OUT / "overlay-before.png", OUT / "overlay-after.png"
        if before.read_bytes() == after.read_bytes():
            print("compare: identical")
            return 0
        print(f"compare: DIFFERENT -- {_differing_pixels(before, after)} "
              f"pixel(s) changed")
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
