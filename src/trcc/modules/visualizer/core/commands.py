"""The Audio Visualizer's Commands -- every UI's way in.

A visual is a key in the ``VISUALS`` registry; these Commands never name
one.  Each runs against the module's root (``app.py``), never the App, and
is refused by the App, in every UI, while the module is off.  Stopping a
panel's visual is the App's own ``ClearModuleSource``, which works with the
module off too (a decision of 2026-10-21: turning something off never needs
it on).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from trcc.core.commands._base import ModuleCommand, ModuleQuery
from trcc.core.models import Capability

from .models import VisualSpec
from .results import VisualResult, VisualsResult, VisualTileResult

if TYPE_CHECKING:
    from ..app import VisualizerModule

log = logging.getLogger(__name__)

#: The module these Commands belong to.
_KEY = "visualizer"
#: The largest gallery tile a client may ask for, in pixels a side.
MAX_TILE = 512


@dataclass(frozen=True, slots=True)
class ListVisuals(ModuleQuery[VisualsResult, "VisualizerModule"]):
    """Every audio visual this machine can draw, and the preset files for
    the ones that draw presets -- the Audio Visuals page's gallery.  With a
    *key*, also what that panel plays and its slider values, so a window
    shows the same truth every other UI does."""
    MODULE: ClassVar[str] = _KEY
    key: str = ""

    def run(self, module: VisualizerModule) -> VisualsResult:
        problem = module.sound_problem()
        base = module.presets_dir()
        visuals = tuple(
            replace(v, problem=problem or cls.problem(), presets=cls.presets(base))
            for v in module.catalog() for cls in (module.visual(v.kind),))
        center, shown, _fire = module.picture()
        chosen = module.choice(self.key) if self.key else None
        reader = module.reader(self.key) if self.key else None
        log.info("ListVisuals: %s, presets %s, centre %s, %s plays %s",
                 [v.kind for v in visuals],
                 {v.kind: len(v.presets) for v in visuals if v.uses_presets},
                 center, self.key or "-", chosen)
        kind, preset, sliders = chosen or ("", "", {})
        return VisualsResult(ok=True, visuals=visuals,
                             center=str(center or ""), center_shown=shown,
                             center_fire=float(module.host.settings.app().get(
                                 "center_fire", 0.0) or 0.0),
                             playing=kind, preset=preset, sliders=dict(sliders),
                             hearing=reader is not None and reader.hearing)


@dataclass(frozen=True, slots=True)
class PlayVisual(ModuleCommand[VisualResult, "VisualizerModule"]):
    """Draw the panel's background with the audio visual *visual* (a
    ``VISUALS`` key), from what is playing.  *preset* is the preset file,
    relative to the visuals' folder, for a visual that draws presets;
    *settings* its slider values by name.  Whatever played before stops
    first.  Refused before anything changes for an unknown visual, a
    missing preset, or a panel that has not reported its size."""
    MODULE: ClassVar[str] = _KEY
    USES_DEVICE: ClassVar[bool] = True
    REQUIRES: ClassVar[Capability | None] = Capability.BACKGROUND
    key: str
    visual: str
    preset: str = ""
    settings: dict[str, float] = field(default_factory=dict)

    def run(self, module: VisualizerModule) -> VisualResult:
        log.info("PlayVisual: %s <- %s %r %s", self.key, self.visual,
                 self.preset, self.settings)
        return module.play(self.key, self.visual, self.preset, self.settings)


@dataclass(frozen=True, slots=True)
class SetVisualSettings(ModuleCommand[VisualResult, "VisualizerModule"]):
    """New slider values for the panel's audio visual -- live: it keeps
    playing and takes them before its next picture.  Only the sliders named
    change; each is kept within its range; a name the visual has no slider
    for is refused.  Saved, so a restart plays them."""
    MODULE: ClassVar[str] = _KEY
    USES_DEVICE: ClassVar[bool] = True
    REQUIRES: ClassVar[Capability | None] = Capability.BACKGROUND
    key: str
    settings: dict[str, float] = field(default_factory=dict)

    def run(self, module: VisualizerModule) -> VisualResult:
        log.info("SetVisualSettings: %s %s", self.key, self.settings)
        return module.adjust(self.key, self.settings)


@dataclass(frozen=True, slots=True)
class SetVisualCenter(ModuleCommand[VisualsResult, "VisualizerModule"]):
    """The centre picture for audio visuals that show one -- any image the
    user picks; an empty *path* takes it away.  Every panel playing such a
    visual starts it again, so the picture shows at once."""
    MODULE: ClassVar[str] = _KEY
    path: str = ""

    def run(self, module: VisualizerModule) -> VisualsResult:
        log.info("SetVisualCenter: %r", self.path)
        problem = next((module.visual(v.kind).picture_problem(Path(self.path))
                        for v in module.catalog() if v.uses_center),
                       "") if self.path else ""
        if problem:
            log.warning("SetVisualCenter: refused -- %s", problem)
            return VisualsResult(ok=False, message=problem)
        module.set_picture(self.path or None, change_center=True)
        module.restart_centred("SetVisualCenter")
        listed = ListVisuals().run(module)
        return VisualsResult(ok=True, visuals=listed.visuals,
                             center=listed.center,
                             message=("centre picture set" if self.path
                                      else "no centre picture"))


@dataclass(frozen=True, slots=True)
class SetVisualPicture(ModuleCommand[VisualsResult, "VisualizerModule"]):
    """Switch the centre picture on or off -- it is kept while off, so on
    brings it back -- and set how hot it burns, 0 to 1 (the user's call:
    the fire belongs to the picture; no picture, no fire).  Each only when
    given.  Every panel playing a visual that shows it starts again."""
    MODULE: ClassVar[str] = _KEY
    shown: bool | None = None
    fire: float | None = None

    def run(self, module: VisualizerModule) -> VisualsResult:
        log.info("SetVisualPicture: shown=%s fire=%s", self.shown, self.fire)
        module.set_picture(shown=self.shown, fire=self.fire)
        module.restart_centred("SetVisualPicture")
        listed = ListVisuals().run(module)
        return replace(listed, message=(
            "centre picture off" if not listed.center_shown
            else f"centre picture on, fire {listed.center_fire:.2f}"))


@dataclass(frozen=True, slots=True)
class VisualTile(ModuleQuery[VisualTileResult, "VisualizerModule"]):
    """The gallery tile for *visual* (and its *preset*): what it draws for
    noise, *size* pixels a side, as PNG -- the same whatever is playing, so
    every UI's gallery shows each visual the same way."""
    MODULE: ClassVar[str] = _KEY
    visual: str
    preset: str = ""
    size: int = 120

    def run(self, module: VisualizerModule) -> VisualTileResult:
        from ..services.visual_stream import snapshot

        log.info("VisualTile: %s %r at %d", self.visual, self.preset, self.size)
        _info, preset, problem = module.resolve(self.visual, self.preset)
        if not 16 <= self.size <= MAX_TILE:
            problem = f"a tile is 16 to {MAX_TILE} pixels a side"
        if not problem:
            spec = VisualSpec(self.visual, self.size, self.size, preset=preset,
                              **module._picture_args())
            try:
                frame = snapshot(spec, module.visual(self.visual)())
            except OSError as e:
                problem = f"{self.visual} could not draw: {e}"
        if problem:
            log.warning("VisualTile: %s %r refused: %s", self.visual,
                        self.preset, problem)
            return VisualTileResult(ok=False, visual=self.visual,
                                    preset=self.preset, message=problem)
        png = module.host.files.encode_png(frame)
        return VisualTileResult(ok=True, visual=self.visual, preset=self.preset,
                                image=png, media_type="image/png",
                                message=f"{len(png)} bytes")
