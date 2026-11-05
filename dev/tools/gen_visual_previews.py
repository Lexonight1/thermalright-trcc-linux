"""Draw each built-in audio visual's preview, once, into the module's assets.

    PYTHONPATH=src python3.12 dev/tools/gen_visual_previews.py           # missing only
    PYTHONPATH=src python3.12 dev/tools/gen_visual_previews.py --all     # redraw every one

The Visualizer's gallery tiles and a visual theme's own ``00.png`` are
preview pictures.  They were drawn on every gallery open -- 25 ms a visual,
measured 2026-11-05 -- so they are drawn here instead, once, and shipped:
``src/trcc/modules/visualizer/assets/previews/<kind>.jpg``.  Run it when a
visual is added or its drawing changes; ``tests/module_visualizer`` fails while
one is missing.

Drawn as the gallery always drew them -- ``snapshot()``, the visual fed bursts
of noise -- through the module's own engine binding (its OpenGL context), at
``SIZE`` square, with NO centre picture: a shipped preview cannot carry the
user's (nor Bob -- the SubGenius Foundation's, all rights reserved).  A visual
whose presets ship (KarmaViz, ``PRESETS_SHIP``) gets one per preset in
``previews/<kind>/``; MilkDrop's presets are the user's own files, drawn on the
user's computer once and kept there.
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

log = logging.getLogger("gen_visual_previews")

#: Square, and large enough that fitting it to a 1600x720 panel stays sharp-ish.
SIZE = 512
#: JPEG quality: as PNG, KarmaViz's noisy gradients made 19 previews 2.8 MB.
QUALITY = 90


def main(argv: list[str]) -> int:
    """Draw every missing preview (every one with ``--all``)."""
    logging.basicConfig(level=logging.WARNING)
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.modules.visualizer.app import PREVIEWS, VisualizerModule, preview_file
    from trcc.modules.visualizer.core.models import VisualSpec
    from trcc.modules.visualizer.services.visual_stream import snapshot

    from tests.mock_platform import MockPlatform

    redraw = "--all" in argv
    log.info("main: redraw=%s into %s", redraw, PREVIEWS)
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}],
                           Path(tempfile.mkdtemp())), renderer=QtRenderer())
    try:
        module = cast(VisualizerModule, app.modules.root("visualizer"))
        for info in module.catalog():
            engine: Any = module.visual(info.kind)
            if info.uses_presets and not engine.PRESETS_SHIP:
                print(f"  skip   {info.kind:28} its presets are the user's own files")
                continue
            presets = engine.presets(module.presets_dir()) if info.uses_presets else ("",)
            for preset in presets:
                out = preview_file(info.kind, preset)
                label = f"{info.kind} {preset}".strip()
                if out.exists() and not redraw:
                    print(f"  kept   {label:44} {out.stat().st_size} bytes")
                    continue
                path = engine.preset(module.presets_dir(), preset) if preset else None
                frame = snapshot(VisualSpec(info.kind, SIZE, SIZE, preset=path), engine())
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(app.renderer.encode_jpeg(
                    app.renderer.from_raw_rgb24(frame), QUALITY))
                print(f"  drawn  {label:44} {out.stat().st_size} bytes")
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
