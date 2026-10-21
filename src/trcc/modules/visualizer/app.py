"""The Audio Visualizer's root: what the computer is playing, drawn as a
panel's background.

Built when the module is switched on.  It holds the visuals this computer can
draw -- each bound to the OS's OpenGL context (``host.gpu.surface``) -- and
plays them through its host: a :class:`VisualReader` per panel, shown as that
panel's background source, the choice kept in the panel's own settings for
this module.  Its Commands (``core/commands.py``) run against this class, and
themes save and load its visuals through :class:`VisualBackground`.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any

from trcc.adapters.modules import BaseModule
from trcc.core.errors import DeviceNotFoundError
from trcc.core.events import BackgroundChanged, ErrorOccurred
from trcc.core.ports import BackgroundKind, ModuleHost

from .adapters import VISUALS
from .core.events import VisualHearingChanged
from .core.models import VISUAL_FPS, VisualInfo, VisualSpec
from .core.ports import VisualEngine, ranked_visuals
from .core.results import VisualResult
from .services.visual_stream import VisualReader

log = logging.getLogger(__name__)

#: The theme background refs it draws -- ``module.json`` declares the same.
PREFIX = "visual_background"
#: Its presets' folder, in TRCC's data folder (where it has always been).
PRESETS = "visuals"

#: One panel's choice: which visual, its preset, its slider values.
Choice = tuple[str, str, dict[str, float]]


class VisualizerModule(BaseModule, key="visualizer"):
    """The visuals this computer can draw, and the panels drawing them."""

    title = "Audio Visualizer"
    summary = "Draws what your computer is playing as a panel's background."

    def __init__(self) -> None:
        log.debug("VisualizerModule.__init__")
        #: Each visual as this computer draws it, by kind -- while it is on.
        self._visuals: dict[str, type[VisualEngine]] = {}

    # ── switching ──────────────────────────────────────────────────────

    def _enable(self, host: ModuleHost) -> None:
        """Bind every visual to this OS's context, and let themes carry one."""
        surface = host.gpu.surface()
        self._visuals = {kind: host.gpu.substitute(cls.on_surface(surface))
                         for kind, cls in VISUALS.items()}
        log.info("visualizer: %d visual(s) on %s", len(self._visuals),
                 getattr(surface, "__name__", None))
        host.themes.add_kind(VisualBackground(self))

    def _disable(self, host: ModuleHost) -> None:
        """Its panels are ended by the host (each keeping its choice)."""
        log.info("visualizer: off")
        self._visuals = {}

    # ── what it can draw ───────────────────────────────────────────────

    def catalog(self) -> tuple[VisualInfo, ...]:
        """Every visual this computer can draw, in gallery order."""
        infos = ranked_visuals(self._visuals.values())
        log.debug("visualizer.catalog: %s", [i.kind for i in infos])
        return infos

    def visual(self, kind: str) -> type[VisualEngine]:
        """The visual *kind*, as this computer draws it; ``OSError`` for none."""
        log.debug("visualizer.visual: %s", kind)
        if kind not in self._visuals:
            raise OSError(f"no audio visual {kind!r} on this computer")
        return self._visuals[kind]

    def presets_dir(self) -> Path:
        """Where the preset files are."""
        folder = self.host.files.data_root() / PRESETS
        log.debug("visualizer.presets_dir: %s", folder)
        return folder

    def resolve(self, kind: str, preset: str
                ) -> tuple[VisualInfo | None, Path | None, str]:
        """The visual *kind* names and its *preset* file: ``(info, path,
        problem)``, *problem* "" when both are there."""
        info = next((v for v in self.catalog() if v.kind == kind), None)
        path = (self.visual(kind).preset(self.presets_dir(), preset)
                if info is not None and info.uses_presets else None)
        problem = (f"no audio visual {kind!r} on this computer" if info is None
                   else f"no preset {preset!r} in the visuals' folder"
                   if info.uses_presets and path is None
                   else self.visual(kind).problem())
        log.debug("visualizer.resolve: %s %r -> %s %r", kind, preset, path, problem)
        return info, path, problem

    def sound_problem(self) -> str:
        """Why this computer's sound cannot be heard -- every visual's."""
        problem = self.host.sound.problem()
        log.debug("visualizer.sound_problem: %r", problem)
        return problem

    # ── each panel's choice, and the centre picture ────────────────────

    def choice(self, key: str) -> Choice | None:
        """What panel *key* chose to draw, while its background is this
        module's -- None otherwise, or when the saved choice is unreadable."""
        if not self.host.background.draws(key):
            log.debug("visualizer.choice: %s draws something else", key)
            return None
        return _choice(self.host.settings.panel(key), key)

    def picture(self) -> tuple[Path | None, bool, float]:
        """The centre picture: its file, whether it is shown, its fire (none
        while hidden)."""
        saved = self.host.settings.app()
        center = saved.get("center")
        path = Path(center) if isinstance(center, str) and center else None
        shown = bool(saved.get("center_shown", True))
        # The fire belongs to the picture: none while it is hidden (kept).
        fire = _fraction(saved.get("center_fire", 0.0)) if shown else 0.0
        log.debug("visualizer.picture: %s shown=%s fire=%.2f", path, shown, fire)
        return path, shown, fire

    def set_picture(self, center: str | None = None, *, change_center: bool = False,
                    shown: bool | None = None, fire: float | None = None) -> None:
        """Change the centre picture -- each part only when given.  A picture
        just picked is shown."""
        log.info("visualizer.set_picture: center=%r(%s) shown=%s fire=%s",
                 center, change_center, shown, fire)
        saved = self.host.settings.app()
        if change_center:
            saved["center"] = center
            if center:
                saved["center_shown"] = True
        if shown is not None:
            saved["center_shown"] = shown
        if fire is not None:
            saved["center_fire"] = _fraction(fire)
        self.host.settings.save_app(saved)

    def _picture_args(self) -> dict[str, Any]:
        """The centre picture as a visual takes it."""
        center, shown, fire = self.picture()
        log.debug("visualizer._picture_args: %s shown=%s", center, shown)
        return {"center": center if shown else None, "center_fire": fire}

    # ── playing ────────────────────────────────────────────────────────

    def reader(self, key: str) -> VisualReader | None:
        """The reader drawing panel *key*, if it draws one of ours."""
        source = self.host.background.source(key) if self.host.background.draws(key) else None
        found = source if isinstance(source, VisualReader) else None
        log.debug("visualizer.reader: %s -> %s", key, found is not None)
        return found

    def play(self, key: str, kind: str, preset: str = "",
             sliders: Mapping[str, float] | None = None,
             command: str = "PlayVisual") -> VisualResult:
        """Draw panel *key*'s background with the visual *kind*: whatever it
        showed stops, and the choice is saved.  Refused before anything
        changes -- and said where the user is looking -- for an unknown
        visual, a missing preset, a panel that has not reported its size, or
        a computer whose sound cannot be heard."""
        sliders = dict(sliders or {})
        log.info("visualizer.play: %s <- %s %r %s", key, kind, preset, sliders)
        info, path, problem = self.resolve(kind, preset)
        try:
            size = self.host.panels.size(key)
        except DeviceNotFoundError as e:
            return self._refuse(command, key, preset, str(e))
        if not problem and size is None:
            problem = "the panel has not reported its size yet"
        if not problem:
            problem = self.sound_problem()
        if problem or info is None or size is None:
            return self._refuse(command, key, preset, problem)
        spec = VisualSpec(kind, size[0], size[1], preset=path, settings=sliders,
                          **self._picture_args())
        self.host.settings.save_panel(
            key, {"visual": kind, "preset": preset, "settings": sliders})
        reader = VisualReader(spec, self.host.sound.input, self.visual(kind),
                              self.host.sound.worker_thread,
                              on_hearing=partial(self._heard, key))
        self.host.background.show(key, reader, VISUAL_FPS)
        log.info("visualizer.play: %s playing %s", key, preset or kind)
        return VisualResult(ok=True, key=key, preset=preset, playing=True,
                            message=f"{info.title} on {key}")

    def adjust(self, key: str, sliders: Mapping[str, float]) -> VisualResult:
        """New slider values for panel *key*'s visual -- live, kept within
        each slider's range, saved; a name it has no slider for is refused."""
        log.info("visualizer.adjust: %s %s", key, dict(sliders))
        chosen = self.choice(key)
        info = next((v for v in self.catalog()
                     if chosen is not None and v.kind == chosen[0]), None)
        if chosen is None or info is None:
            return self._refuse("SetVisualSettings", key, "",
                                f"no audio visual plays on {key}")
        kind, preset, saved = chosen
        ranges = {a.name: a for a in info.adjustments}
        if unknown := sorted(set(sliders) - set(ranges)):
            return self._refuse("SetVisualSettings", key, preset,
                                f"{info.title} has no slider {', '.join(unknown)}")
        values = {**saved, **{name: min(max(float(value), ranges[name].minimum),
                                        ranges[name].maximum)
                              for name, value in sliders.items()}}
        self.host.settings.save_panel(
            key, {"visual": kind, "preset": preset, "settings": values})
        reader = self.reader(key)
        if reader is not None:
            reader.adjust(values)
        # Every UI follows the panel's background -- these are its sliders.
        self.host.publish(BackgroundChanged(key=key, path=""))
        return VisualResult(ok=True, key=key, preset=preset,
                            playing=reader is not None,
                            message=f"{info.title}: " + ", ".join(
                                f"{n} {v:g}" for n, v in values.items()))

    def resume(self, key: str) -> None:
        """Draw panel *key*'s saved choice again, if it has one."""
        chosen = self.choice(key)
        log.info("visualizer.resume: %s -> %s", key, chosen)
        if chosen is not None:
            self.play(key, *chosen, command="resume")

    def restart_centred(self, command: str) -> None:
        """Every panel playing a visual that shows the picture starts it
        again, so a change to the picture shows at once, sliders kept."""
        centred = {v.kind for v in self.catalog() if v.uses_center}
        for key in self.host.panels.attached():
            chosen = self.choice(key)
            if chosen is not None and chosen[0] in centred and self.reader(key):
                log.info("%s: %s plays %s -- starting it again", command, key,
                         chosen[0])
                self.play(key, *chosen, command=command)

    def _heard(self, key: str, hearing: bool) -> None:
        """Panel *key*'s visual started -- or stopped -- hearing anything."""
        log.info("visualizer: %s %s", key, "hears sound" if hearing
                 else "hears nothing")
        self.host.publish(VisualHearingChanged(key=key, hearing=hearing))

    def _refuse(self, command: str, key: str, preset: str,
                problem: str) -> VisualResult:
        """*command* refused before anything changed -- said where the user
        is looking: every open UI hears the ``ErrorOccurred``."""
        log.warning("%s: %s refused: %s", command, key, problem)
        self.host.publish(ErrorOccurred(message=problem, kind="visual", key=key))
        return VisualResult(ok=False, key=key, preset=preset, message=problem)


class VisualBackground(BackgroundKind):
    """A visual as a theme's background: ``visual_background/<id>``, whose
    folder holds the visual, its preset and its sliders."""

    prefix = PREFIX

    def __init__(self, module: VisualizerModule) -> None:
        log.debug("VisualBackground.__init__")
        self._module = module

    def play(self, key: str, payload: Mapping[str, Any]) -> VisualResult:
        """A theme with a visual background was loaded on panel *key*."""
        log.info("VisualBackground.play: %s %s", key, dict(payload))
        chosen = _choice(payload, key)
        if chosen is None:
            return VisualResult(ok=False, key=key,
                                message="the theme's audio visual is unreadable")
        return self._module.play(key, *chosen, command="LoadTheme")

    def resume(self, key: str) -> None:
        log.debug("VisualBackground.resume: %s", key)
        self._module.resume(key)

    def snapshot(self, key: str) -> dict[str, Any] | None:
        """What a theme saved now from panel *key* keeps."""
        chosen = self._module.choice(key)
        log.debug("VisualBackground.snapshot: %s -> %s", key, chosen)
        if chosen is None:
            return None
        kind, preset, sliders = chosen
        return {"visual": kind, "preset": preset, "settings": sliders}


def _choice(data: Mapping[str, Any], key: str) -> Choice | None:
    """A saved choice -- a panel's or a theme's -- or None when unreadable."""
    kind, preset = data.get("visual"), data.get("preset", "")
    sliders = data.get("settings", {})
    if not (isinstance(kind, str) and kind and isinstance(preset, str)
            and isinstance(sliders, Mapping)):
        log.debug("_choice: %s has none (%r %r)", key, kind, preset)
        return None
    try:
        values = {str(n): float(v) for n, v in sliders.items()}
    except (TypeError, ValueError):
        log.warning("_choice: %s has unreadable sliders %r", key, sliders)
        return None
    return kind, preset, values


def _fraction(value: Any) -> float:
    """*value* as 0 to 1; 0 when it is not a number."""
    try:
        found = min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        found = 0.0
    log.debug("_fraction: %r -> %.2f", value, found)
    return found
