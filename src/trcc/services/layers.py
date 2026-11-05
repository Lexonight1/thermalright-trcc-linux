"""Everything drawn on a panel is a :class:`Layer`.

A frame is a stack: a background at the bottom, then whatever is drawn over
it -- the mask, the text / metric / clock elements, an audio spectrum, the
widescreen split overlay.  Each of those is a child of one abstract class:
it says what it is (``key`` -- equal keys draw equal pixels) and how it puts
itself on the canvas below it (``draw``).

The DisplayService still decides WHICH layers a frame has and resolves what
each one needs (a mask file, the cached overlay surface, the split asset); a
layer only draws.  Rotation, brightness and the wire encode are not layers:
they turn the finished picture into what a panel receives, and stay in the
service's output step.

Increments L1-L3 of the Layer plan (memory: project_layer_abc_trace): the
mask, the overlay, the split overlay, the spectrum, and the two ways a
background is drawn -- a picture, a solid colour, or a live picture (a
screencast, a web stream, a module's visual).  WHICH picture (a
playing video, an override, the theme's own) is still chosen in one place,
``DisplayService._resolve_background`` with its one identity token: a layer
draws what it is given.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Hashable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.geometry import fit_rect_for_mode
from ..core.logs import per_frame
from ..core.models import FitMode, RawFrame, RenderContent
from ..core.ports import Renderer

log = logging.getLogger(__name__)
# Every layer draws on every frame sent: its lines are per-frame lines.
frame_log = per_frame(__name__)


class Layer(ABC):
    """One thing drawn on a panel's upright canvas, over what is below it."""

    @abstractmethod
    def key(self) -> Hashable:
        """What this layer shows -- two layers with equal keys draw the same
        pixels, so a key is what a cache of the drawn result is keyed on."""

    @abstractmethod
    def draw(self, canvas: Any, renderer: Renderer) -> Any:
        """*canvas* with this layer drawn on top."""


class BackgroundLayer(Layer):
    """The bottom of the stack: it MAKES the canvas, at the panel's size,
    rather than drawing onto one.  A canvas made first and then covered cost
    a whole panel-sized allocation per frame on a cast (~274 k instructions
    at 1600x720, measured) for pixels nobody saw."""

    @abstractmethod
    def canvas(self, size: tuple[int, int], renderer: Renderer) -> Any:
        """The canvas this background is, *size* pixels."""

    def draw(self, canvas: Any, renderer: Renderer) -> Any:
        """Over anything else, a background covers it."""
        frame_log.debug("%s.draw: covers the canvas", type(self).__name__)
        return self.canvas(renderer.surface_size(canvas), renderer)


@dataclass(frozen=True, slots=True)
class SolidColor(BackgroundLayer):
    """A background of one colour (the ``color`` background mode)."""

    rgb: tuple[int, int, int]

    def key(self) -> Hashable:
        frame_log.debug("SolidColor.key: %s", self.rgb)
        return self.rgb

    def canvas(self, size: tuple[int, int], renderer: Renderer) -> Any:
        frame_log.debug("SolidColor.canvas: %s %dx%d", self.rgb, *size)
        return renderer.create_surface(*size, color=(*self.rgb, 255))


@dataclass(frozen=True, slots=True)
class Picture(BackgroundLayer):
    """A background picture -- an image, or the current frame of a video.

    Drawn by where it came from, as the C# draws it: the user's own is fitted
    to the canvas by *fit_mode*; a program or cloud picture goes on native at
    (0, 0), or not at all when it is wider than the canvas
    (``Renderer.bg_fit``, ``UCScreenImage.cs:824-834``).  *identity* is the
    background token -- which picture, and which frame of it."""

    identity: Hashable
    surface: Any = field(compare=False, repr=False)
    is_user: bool
    fit_mode: FitMode

    def key(self) -> Hashable:
        frame_log.debug("Picture.key: %s", self.identity)
        return (self.identity, self.fit_mode)

    def canvas(self, size: tuple[int, int], renderer: Renderer) -> Any:
        """Black, with the picture on it -- the C# canvas starts black
        (UCScreenImage.cs:903) and a picture may not cover it."""
        canvas = renderer.create_surface(*size, color=(0, 0, 0, 255))
        if self.surface is None:
            frame_log.debug("Picture.canvas: no picture -- the canvas stays black")
            return canvas
        if not self.is_user:
            frame_log.debug("Picture.canvas: program picture, native-or-black")
            return renderer.bg_fit(canvas, RenderContent(self.surface, None))
        src = renderer.surface_size(self.surface)
        rect = fit_rect_for_mode(src, size, self.fit_mode)
        frame_log.debug("Picture.canvas: user picture %dx%d -> fit %s -> %dx%d at "
                        "(%d, %d)", *src, self.fit_mode.value, rect.width,
                        rect.height, rect.x, rect.y)
        fitted = renderer.resize(self.surface, rect.width, rect.height)
        return renderer.composite(canvas, fitted, position=(rect.x, rect.y))


@dataclass(frozen=True, slots=True, eq=False)
class LivePicture(BackgroundLayer):
    """A live picture's newest frame -- a screencast, a web stream, a
    module's visual -- stretched to the canvas: the C# sizes its ``GIFSize``
    to the panel and stretches the grab into it (FormCZTV.cs:3100-3557).

    Never equal to another: a live frame is not the same picture twice, so
    its key is the layer itself and no cache can hand an old frame back."""

    frame: RawFrame = field(repr=False)

    def key(self) -> Hashable:
        frame_log.debug("LivePicture.key")
        return self

    def canvas(self, size: tuple[int, int], renderer: Renderer) -> Any:
        """The frame itself is the canvas -- no black one made under it."""
        surface = renderer.from_raw_rgb24(self.frame)
        if renderer.surface_size(surface) != size:
            frame_log.debug("LivePicture.canvas: %dx%d stretched to %dx%d",
                            self.frame.width, self.frame.height, *size)
            surface = renderer.resize(surface, *size)
        return surface


@dataclass(frozen=True, slots=True)
class Mask(Layer):
    """The theme's mask (``01.png``) or the user's own, at *position*.
    *source* None: no mask -- hidden, or none resolved -- and the canvas is
    returned untouched (the C# draws one mask per frame, gated the same way:
    ``UCScreenImage.cs:1118`` / ``:1511``)."""

    source: Path | None
    position: tuple[int, int] = (0, 0)
    surface: Any = field(default=None, compare=False, repr=False)

    def key(self) -> Hashable:
        frame_log.debug("Mask.key: %s at %s", self.source, self.position)
        return (self.source, self.position)

    def draw(self, canvas: Any, renderer: Renderer) -> Any:
        if self.source is None or self.surface is None:
            frame_log.debug("Mask.draw: no mask composited")
            return canvas
        frame_log.debug("Mask.draw: %s (%dx%d) at (%d, %d)", self.source.name,
                        *renderer.surface_size(self.surface), *self.position)
        return renderer.composite(canvas, self.surface, position=self.position)


@dataclass(frozen=True, slots=True)
class Overlay(Layer):
    """The text, metric and clock elements, already drawn on a transparent
    surface -- the service keeps that surface while *overlay_key* holds,
    because readings change every couple of seconds and frames go out ~16
    times a second."""

    overlay_key: Hashable
    surface: Any = field(compare=False, repr=False)

    def key(self) -> Hashable:
        frame_log.debug("Overlay.key")
        return self.overlay_key

    def draw(self, canvas: Any, renderer: Renderer) -> Any:
        frame_log.debug("Overlay.draw")
        return renderer.composite(canvas, self.surface, position=(0, 0))


@dataclass(frozen=True, slots=True)
class Spectrum(Layer):
    """Audio bars over the picture -- the cast's sound, while it hears."""

    bands: tuple[float, ...]

    def key(self) -> Hashable:
        frame_log.debug("Spectrum.key: %d band(s)", len(self.bands))
        return self.bands

    def draw(self, canvas: Any, renderer: Renderer) -> Any:
        frame_log.debug("Spectrum.draw: %d band(s)", len(self.bands))
        return renderer.draw_spectrum(canvas, self.bands)


@dataclass(frozen=True, slots=True)
class Split(Layer):
    """The widescreen "Dynamic Island" overlay (1600x720): *asset* is the
    picture chosen for the split style and rotation, None when there is none
    -- the canvas is then returned untouched, as is a failed composite."""

    asset: str | None
    surface: Any = field(default=None, compare=False, repr=False)

    def key(self) -> Hashable:
        frame_log.debug("Split.key: %s", self.asset)
        return self.asset

    def draw(self, canvas: Any, renderer: Renderer) -> Any:
        if self.asset is None or self.surface is None:
            frame_log.debug("Split.draw: no asset")
            return canvas
        try:
            return renderer.composite(canvas, self.surface, position=(0, 0))
        except (OSError, ValueError, RuntimeError) as e:
            log.warning("Split.draw: %s composite failed: %s: %s",
                        self.asset, type(e).__name__, e)
            return canvas

