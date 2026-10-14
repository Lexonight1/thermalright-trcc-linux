"""Audio visuals: what the computer is playing, drawn as a panel's background.

A visual is a key in the ``VISUALS`` registry; these Commands never name
one.  ``PlayVisual`` starts it on the screencast's chain -- a reader draws on
its own thread, ``VisualDriver`` takes the newest picture each tick
(``CaptureVisualFrame``), ``SendScreencastFrame`` puts the theme's overlay on
top -- exactly as a web stream runs.  The choice is a display source in
Settings, exclusive with the others, so another source taking the panel
ends the visual within a tick, and a restart resumes it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from ..errors import DeviceNotFoundError
from ..events import ErrorOccurred, VisualHearingChanged
from ..models import Capability, VisualInfo, VisualSpec
from ..ports import CaptureNotReady
from ..results import (
    ScreencastResult,
    VisualResult,
    VisualsResult,
    VisualTileResult,
)
from ._base import Command, Query
from .device import PlayVideo, SendScreencastFrame, StopVideo, _publish_background

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)

#: The largest gallery tile a client may ask for, in pixels a side.
MAX_TILE = 512


def _resolve(app: App, visual: str,
             preset: str) -> tuple[VisualInfo | None, Path | None, str]:
    """The visual *visual* names and its *preset* file: ``(info, path,
    problem)``, *problem* "" when both are there."""
    info = next((v for v in app.platform.visual_catalog()
                 if v.kind == visual), None)
    path = (app.platform.visual_class(visual).preset(
        app.platform.paths().visuals_dir(), preset)
        if info is not None and info.uses_presets else None)
    problem = (f"no audio visual {visual!r} on this computer" if info is None
               else f"no preset {preset!r} in the visuals' folder"
               if info.uses_presets and path is None
               else app.platform.visual_class(visual).problem())
    log.debug("_resolve: %s %r -> %s %r", visual, preset, path, problem)
    return info, path, problem


def _sound_problem(app: App) -> str:
    """Why this computer's sound cannot be heard -- "" when it can.  Every
    audio visual hears it, so this is every visual's problem."""
    made = type(app.platform).SOUND_INPUT
    problem = (f"TRCC cannot hear this computer's sound on "
               f"{app.platform.distro_name()} yet" if made is None
               else made.problem())
    log.debug("_sound_problem: %r", problem)
    return problem


def _picture(app: App) -> dict[str, Any]:
    """The centre picture as a visual takes it: the file -- or ``None``
    while switched off -- and its fire."""
    center, shown, fire = app.settings.visual_picture()
    log.debug("_picture: %s shown=%s fire=%.2f", center, shown, fire)
    return {"center": center if shown else None, "center_fire": fire}


def _restart_centred(app: App, command: str) -> None:
    """Every panel playing a visual that shows the picture starts it again,
    so a change to the picture shows at once, sliders kept."""
    centred = {v.kind for v in app.platform.visual_catalog() if v.uses_center}
    for key in list(app.devices):
        s = app.settings.for_device(key)
        if s.visual in centred and app.media.visual(key) is not None:
            log.info("%s: %s plays %s -- starting it again", command, key,
                     s.visual)
            app.dispatch(PlayVisual(key=key, visual=s.visual,
                                    preset=s.visual_preset,
                                    settings=dict(s.visual_settings)))


def _refused(app: App, command: str, key: str, preset: str,
             problem: str) -> VisualResult:
    """*command* refused before anything changed -- and said where the user
    is looking: every open UI hears the ``ErrorOccurred``."""
    log.warning("%s: %s refused: %s", command, key, problem)
    app.events.publish(ErrorOccurred(message=problem, kind="visual", key=key))
    return VisualResult(ok=False, key=key, preset=preset, message=problem)


@dataclass(frozen=True, slots=True)
class ListVisuals(Query[VisualsResult]):
    """Every audio visual this machine can draw, and the preset files for
    the ones that draw presets -- the Audio Visuals page's gallery.  With a
    *key*, also what that panel plays and its slider values, so a window
    shows the same truth every other UI does."""
    key: str = ""

    def execute(self, app: App) -> VisualsResult:
        problem = _sound_problem(app)
        base = app.platform.paths().visuals_dir()
        visuals = tuple(
            replace(v, problem=problem or cls.problem(), presets=cls.presets(base))
            for v in app.platform.visual_catalog()
            for cls in (app.platform.visual_class(v.kind),))
        center, shown, _fire = app.settings.visual_picture()
        s = app.settings.for_device(self.key) if self.key else None
        reader = app.media.visual(self.key) if self.key else None
        log.info("ListVisuals: %s, presets %s, centre %s, %s plays %s",
                 [v.kind for v in visuals],
                 {v.kind: len(v.presets) for v in visuals if v.uses_presets},
                 center, self.key or "-", s.visual if s else None)
        return VisualsResult(ok=True, visuals=visuals,
                             center=str(center or ""), center_shown=shown,
                             center_fire=app.settings.visual_center_fire(),
                             playing=(s.visual or "") if s else "",
                             preset=s.visual_preset if s else "",
                             sliders=dict(s.visual_settings) if s else {},
                             hearing=reader is not None and reader.hearing)


@dataclass(frozen=True, slots=True)
class PlayVisual(Command[VisualResult]):
    """Draw the panel's background with the audio visual *visual* (a
    ``VISUALS`` key), from what is playing.  *preset* is the preset file,
    relative to the visuals' folder, for a visual that draws presets;
    *settings* its slider values by name.  Whatever played before stops
    first.  Refused before anything changes for an unknown visual, a
    missing preset, or a panel that has not reported its size."""
    USES_DEVICE: ClassVar[bool] = True
    REQUIRES: ClassVar[Capability | None] = Capability.BACKGROUND
    key: str
    visual: str
    preset: str = ""
    settings: dict[str, float] = field(default_factory=dict)

    def execute(self, app: App) -> VisualResult:
        from ...services.visual_driver import VisualDriver

        log.info("PlayVisual: %s <- %s %r %s", self.key, self.visual,
                 self.preset, self.settings)
        info, preset, problem = _resolve(app, self.visual, self.preset)
        try:
            profile = app.get(self.key).profile
        except DeviceNotFoundError as e:
            return self._refuse(app, str(e))
        if not problem and profile is None:
            problem = "the panel has not reported its size yet"
        if not problem:
            problem = _sound_problem(app)
        if problem:
            return self._refuse(app, problem)
        assert info is not None and profile is not None
        width, height = profile.resolution
        spec = VisualSpec(self.visual, width, height, preset=preset,
                          settings=dict(self.settings), **_picture(app))
        app.dispatch(StopVideo(key=self.key))
        app.settings.set_visual(self.key, self.visual, self.preset,
                                self.settings)
        app.media.open_visual(self.key, spec, app.platform.sound_input,
                              app.platform.visual_class(self.visual),
                              app.platform.worker_thread_context)
        app.add_task(VisualDriver(app, self.key))
        _publish_background(app, self.key)
        log.info("PlayVisual: %s playing %s", self.key,
                 self.preset or self.visual)
        return VisualResult(ok=True, key=self.key, preset=self.preset,
                            playing=True,
                            message=f"{info.title} on {self.key}")

    def _refuse(self, app: App, problem: str) -> VisualResult:
        log.debug("PlayVisual._refuse: %s", self.visual)
        return _refused(app, "PlayVisual", self.key, self.preset, problem)


@dataclass(frozen=True, slots=True)
class StopVisual(Command[VisualResult]):
    """End the panel's audio visual: the theme's own background returns,
    its video playing again -- the App decides, no UI reloads a theme."""
    USES_DEVICE: ClassVar[bool] = True
    REQUIRES: ClassVar[Capability | None] = Capability.BACKGROUND
    key: str

    def execute(self, app: App) -> VisualResult:
        from ...services.visual_driver import task_key

        if app.settings.for_device(self.key).visual is None:
            log.info("StopVisual: %s has no audio visual", self.key)
            return VisualResult(ok=True, key=self.key,
                                message=f"no audio visual on {self.key}")
        log.info("StopVisual: ending %s's audio visual", self.key)
        app.media.close_visual(self.key)
        app.remove_task(task_key(self.key))
        app.settings.set_visual(self.key, None)
        theme = app.active_themes.get(self.key)
        video = app.themes.video_path(theme) if theme is not None else None
        if video is not None:
            app.dispatch(PlayVideo(key=self.key, path=video))
        _publish_background(app, self.key)
        return VisualResult(ok=True, key=self.key,
                            message=f"audio visual ended on {self.key}")


@dataclass(frozen=True, slots=True)
class SetVisualSettings(Command[VisualResult]):
    """New slider values for the panel's audio visual -- live: it keeps
    playing and takes them before its next picture.  Only the sliders named
    change; each is kept within its range; a name the visual has no slider
    for is refused.  Saved, so a restart plays them."""
    USES_DEVICE: ClassVar[bool] = True
    REQUIRES: ClassVar[Capability | None] = Capability.BACKGROUND
    key: str
    settings: dict[str, float] = field(default_factory=dict)

    def execute(self, app: App) -> VisualResult:
        log.info("SetVisualSettings: %s %s", self.key, self.settings)
        s = app.settings.for_device(self.key)
        info = next((v for v in app.platform.visual_catalog()
                     if v.kind == s.visual), None)
        if s.visual is None or info is None:
            return _refused(app, "SetVisualSettings", self.key, "",
                            f"no audio visual plays on {self.key}")
        sliders = {a.name: a for a in info.adjustments}
        if unknown := sorted(set(self.settings) - set(sliders)):
            return _refused(app, "SetVisualSettings", self.key,
                            s.visual_preset,
                            f"{info.title} has no slider {', '.join(unknown)}")
        values = {**s.visual_settings,
                  **{name: min(max(float(value), sliders[name].minimum),
                               sliders[name].maximum)
                     for name, value in self.settings.items()}}
        app.settings.set_visual(self.key, s.visual, s.visual_preset, values)
        reader = app.media.visual(self.key)
        if reader is not None:
            reader.adjust(values)
        _publish_background(app, self.key)
        return VisualResult(ok=True, key=self.key, preset=s.visual_preset,
                            playing=reader is not None,
                            message=f"{info.title}: "
                                    + ", ".join(f"{n} {v:g}"
                                                for n, v in values.items()))


@dataclass(frozen=True, slots=True)
class CaptureVisualFrame(Command[ScreencastResult]):
    """One tick of an audio visual: its newest picture, onto the panel.

    ``CaptureStreamFrame``'s twin.  It ends ITSELF when another source took
    the panel -- the display sources are exclusive in Settings, so the moment
    ``visual`` stops naming this reader's visual, the reader and the driver
    stop here, within a tick.
    """
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    key: str

    def execute(self, app: App) -> ScreencastResult:
        from ...services.visual_driver import task_key

        reader = app.media.visual(self.key)
        chosen = app.settings.for_device(self.key).visual
        if reader is None or chosen != reader.spec.kind:
            log.info("CaptureVisualFrame: %s's visual is no longer its "
                     "source -- ending it", self.key)
            app.media.close_visual(self.key)
            app.remove_task(task_key(self.key))
            return ScreencastResult(ok=True, key=self.key,
                                    message=f"visual on {self.key} ended")
        if self.key in app.held:
            log.debug("CaptureVisualFrame: %s held by a pushed image", self.key)
            return ScreencastResult(ok=True, key=self.key, message="held")
        try:
            frame = reader.latest()
        except CaptureNotReady as e:
            log.debug("CaptureVisualFrame: %s", e)
            return ScreencastResult(ok=True, key=self.key, message=str(e))
        except OSError as e:
            log.warning("CaptureVisualFrame: %s -- ending the audio visual", e)
            app.media.close_visual(self.key)
            app.remove_task(task_key(self.key))
            app.settings.set_visual(self.key, None)
            _publish_background(app, self.key)
            app.events.publish(ErrorOccurred(message=str(e), kind="video",
                                             key=self.key))
            return ScreencastResult(ok=False, key=self.key, message=str(e))
        if (hearing := reader.hearing) != reader.announced:
            reader.announced = hearing
            log.info("CaptureVisualFrame: %s %s", self.key,
                     "hears sound again" if hearing else "hears nothing")
            app.events.publish(VisualHearingChanged(key=self.key,
                                                    hearing=hearing))
        return app.dispatch(SendScreencastFrame(key=self.key, frame=frame))


@dataclass(frozen=True, slots=True)
class SetVisualCenter(Command[VisualsResult]):
    """The centre picture for audio visuals that show one -- any image the
    user picks; an empty *path* takes it away.  Every panel
    playing such a visual starts it again, so the picture shows at once."""
    path: str = ""

    def execute(self, app: App) -> VisualsResult:
        log.info("SetVisualCenter: %r", self.path)
        problem = next((app.platform.visual_class(v.kind)
                        .picture_problem(Path(self.path))
                        for v in app.platform.visual_catalog() if v.uses_center),
                       "") if self.path else ""
        if problem:
            log.warning("SetVisualCenter: refused -- %s", problem)
            return VisualsResult(ok=False, message=problem)
        app.settings.set_visual_center(self.path or None)
        _restart_centred(app, "SetVisualCenter")
        listed = app.dispatch(ListVisuals())
        return VisualsResult(ok=True, visuals=listed.visuals,
                             center=listed.center,
                             message=("centre picture set" if self.path
                                      else "no centre picture"))


@dataclass(frozen=True, slots=True)
class SetVisualPicture(Command[VisualsResult]):
    """Switch the centre picture on or off -- it is kept while off, so on
    brings it back -- and set how hot it burns, 0 to 1 (the user's call:
    the fire belongs to the picture; no picture, no fire).  Each only when
    given.  Every panel playing a visual that shows it starts again."""
    shown: bool | None = None
    fire: float | None = None

    def execute(self, app: App) -> VisualsResult:
        log.info("SetVisualPicture: shown=%s fire=%s", self.shown, self.fire)
        app.settings.set_visual_picture(shown=self.shown, fire=self.fire)
        _restart_centred(app, "SetVisualPicture")
        listed = app.dispatch(ListVisuals())
        return replace(listed, message=(
            "centre picture off" if not listed.center_shown
            else f"centre picture on, fire {listed.center_fire:.2f}"))


@dataclass(frozen=True, slots=True)
class VisualTile(Query[VisualTileResult]):
    """The gallery tile for *visual* (and its *preset*): what it draws for
    noise, *size* pixels a side, as PNG -- the same whatever is playing, so
    every UI's gallery shows each visual the same way."""
    visual: str
    preset: str = ""
    size: int = 120

    def execute(self, app: App) -> VisualTileResult:
        from ...services.visual_stream import snapshot

        log.info("VisualTile: %s %r at %d", self.visual, self.preset, self.size)
        _info, preset, problem = _resolve(app, self.visual, self.preset)
        if not 16 <= self.size <= MAX_TILE:
            problem = f"a tile is 16 to {MAX_TILE} pixels a side"
        if not problem:
            spec = VisualSpec(self.visual, self.size, self.size, preset=preset,
                              **_picture(app))
            try:
                frame = snapshot(spec, app.platform.visual_class(self.visual)())
            except OSError as e:
                problem = f"{self.visual} could not draw: {e}"
        if problem:
            log.warning("VisualTile: %s %r refused: %s", self.visual,
                        self.preset, problem)
            return VisualTileResult(ok=False, visual=self.visual,
                                    preset=self.preset, message=problem)
        png = app.renderer.encode_png(app.renderer.from_raw_rgb24(frame))
        return VisualTileResult(ok=True, visual=self.visual, preset=self.preset,
                                image=png, media_type="image/png",
                                message=f"{len(png)} bytes")
