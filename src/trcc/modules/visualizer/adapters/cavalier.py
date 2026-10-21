"""Cavalier's drawing modes, as TRCC audio visuals.

Cavalier is the Linux desktop's audio visualizer (Flathub, built on CAVA).
It draws CAVA's bars eleven ways: Wave, Levels, Particles, Bars, Spine and
Splitter along the bottom, and Wave, Levels, Particles, Bars and Spine round
a circle; mirrored if asked.  Each is one TRCC visual here -- a class in the
registry, so the gallery, its tiles, its sliders and every UI pick it up --
sharing one engine: :class:`~trcc.adapters.visual._cava.Cava`, CAVA's own,
with the monstercat smoothing and the bar order Cavalier asks it for.

Drawn with Qt (``QPainterPath``, as Cavalier draws with Skia's ``SKPath``),
CPU only -- every OS.

**Credit.**  Ported from Cavalier by Fyodor Sobolev
(https://github.com/NickvisionApps/Cavalier, MIT), at commit
435c33919ab4765b4b06caea3d6ecf2cc5d3d1a6:

* ``NickvisionCavalier.Shared/Models/Renderer.cs`` -- ``Draw`` (the mirror
  halves, the picture clipped into the drawn shapes) and the eleven
  ``Draw...Box`` / ``Draw...Circle`` functions: their geometry, the cubic
  curve through the bars with its neighbour gradients, the ten-step levels,
  the eleven-step particles, the spine's centred squares, the splitter's
  alternating steps, the circles' inner radius;
* ``CAVA.cs`` -- the CAVA settings Cavalier runs (stereo, monstercat, noise
  reduction 0.77) and its ``ReverseOrder`` of each channel's bars;
* ``Configuration.cs`` / ``ColorProfile.cs`` -- the defaults: six bar pairs,
  item spacing 0.1, roundness 0.5, inner radius 0.5, filled shapes,
  foreground ``#3584e4`` on ``#242424``.

Modified 2026-10-13 for TRCC: ported from C# and SkiaSharp to Python and
Qt; the bottom-up direction and its top-down mirror only (the panel's own
rotation turns the picture); the colour is a hue slider over Cavalier's
blue; and -- TRCC's own addition, not Cavalier's -- the circle modes show
the user's centre picture inside the circle.  Distributed as part of TRCC
under GPL-3.0-or-later, keeping the notice below.

    Copyright (c) 2023 Fyodor Sobolev

    Permission is hereby granted, free of charge, to any person obtaining a
    copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to permit
    persons to whom the Software is furnished to do so, subject to the
    following conditions:

    The above copyright notice and this permission notice shall be included
    in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
    OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
    MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
    NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
    DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
    OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
    USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np

from trcc.core.logs import per_frame
from trcc.core.models import SOUND_CHANNELS, SOUND_RATE

from ..core.models import VisualAdjustment, VisualInfo
from ._cava import Cava, monstercat
from ._painted import PaintedVisual

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: Cavalier's colours: ``ColorProfile``'s foreground ``#3584e4`` -- as a hue
#: the slider turns, keeping its saturation and value -- on ``#242424``.
_HUE, _SATURATION, _VALUE = 213.0, 0.768, 0.894
#: CAVA as Cavalier runs it (``CAVA.cs``).
_NOISE_REDUCTION = 0.77
_LOW_HZ, _HIGH_HZ = 50, 10000
#: 16-bit samples: the range cava's linear scaling (its EQ) expects.
_SAMPLE_SCALE = 32768.0


@dataclass(frozen=True, slots=True)
class _Look:
    """How the items are drawn -- Cavalier's ``Configuration`` values the
    sliders set."""
    spacing: float = 0.1          # ItemsOffset
    roundness: float = 0.5        # ItemsRoundness
    inner_radius: float = 0.5     # InnerRadius


def _flip(enabled: bool, size: float, value: float) -> float:
    """Renderer.FlipCoord: clamp to the area, mirrored when *enabled*."""
    frame_log.debug("_flip: %s %.1f", enabled, value)
    clamped = max(0.0, min(value, size))
    return size - clamped if enabled else clamped


def _round_rect(path: Any, x: float, y: float, w: float, h: float,
                rx: float, ry: float) -> None:
    """SKPath.AddRoundRect -- Qt wants the radii within the rectangle."""
    from PySide6.QtCore import QRectF
    frame_log.debug("_round_rect: %.1fx%.1f", w, h)
    path.addRoundedRect(QRectF(x, y, w, h), max(0.0, min(rx, w / 2)),
                        max(0.0, min(ry, h / 2)))


# ── the box modes: a path, filled -- and the picture clipped into it ─────

def wave_box(sample: np.ndarray, x: float, y: float, w: float, h: float,
             up: bool, look: _Look) -> Any:
    """DrawWaveBox: a cubic curve through the bars, filled down to the edge."""
    from PySide6.QtGui import QPainterPath
    frame_log.debug("wave_box: %d bar(s)", sample.size)
    count = sample.size
    step = w / (count - 1)
    flip = not up
    points = [(step * i, h * (1 - float(sample[i]))) for i in range(count)]
    slopes = [points[min(i + 1, count - 1)][1] - points[max(i - 1, 0)][1]
              for i in range(count)]
    slopes = [s if i in (0, count - 1) else s / 2 for i, s in enumerate(slopes)]
    path = QPainterPath()
    path.moveTo(x + points[0][0], y + _flip(flip, h, points[0][1]))
    for i in range(count - 1):
        path.cubicTo(
            x + points[i][0] + step * 0.5,
            y + _flip(flip, h, points[i][1] + slopes[i] * 0.5),
            x + points[i + 1][0] - step * 0.5,
            y + _flip(flip, h, points[i + 1][1] - slopes[i + 1] * 0.5),
            x + points[i + 1][0], y + _flip(flip, h, points[i + 1][1]))
    path.lineTo(x + w, y + _flip(flip, h, h))
    path.lineTo(x, y + _flip(flip, h, h))
    path.closeSubpath()
    return path


def levels_box(sample: np.ndarray, x: float, y: float, w: float, h: float,
               up: bool, look: _Look) -> Any:
    """DrawLevelsBox: ten rounded blocks a column, lit to the bar's height."""
    from PySide6.QtGui import QPainterPath
    frame_log.debug("levels_box: %d bar(s)", sample.size)
    step = w / sample.size
    item_w = step * (1 - look.spacing * 2)
    item_h = h / 10 * (1 - look.spacing * 2)
    path = QPainterPath()
    for i, value in enumerate(sample):
        for j in range(math.floor(float(value) * 10)):
            row = 9 - j if up else j
            _round_rect(path, x + step * (i + look.spacing),
                        y + h / 10 * row + h / 10 * look.spacing, item_w, item_h,
                        item_w / 2 * look.roundness, item_h / 2 * look.roundness)
    return path


def particles_box(sample: np.ndarray, x: float, y: float, w: float, h: float,
                  up: bool, look: _Look) -> Any:
    """DrawParticlesBox: one rounded block a column, at the bar's height."""
    from PySide6.QtGui import QPainterPath
    frame_log.debug("particles_box: %d bar(s)", sample.size)
    step = w / sample.size
    item_w = step * (1 - look.spacing * 2)
    item_h = h / 11 * (1 - look.spacing * 2)
    path = QPainterPath()
    for i, value in enumerate(sample):
        height = float(value) if not up else 1 - float(value)
        _round_rect(path, x + step * (i + look.spacing),
                    y + h / 11 * 10 * height + h / 11 * look.spacing, item_w,
                    item_h, item_w / 2 * look.roundness,
                    item_h / 2 * look.roundness)
    return path


def bars_box(sample: np.ndarray, x: float, y: float, w: float, h: float,
             up: bool, look: _Look) -> Any:
    """DrawBarsBox: a bar a column, from the edge to its height."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath
    frame_log.debug("bars_box: %d bar(s)", sample.size)
    step = w / sample.size
    path = QPainterPath()
    for i, value in enumerate(sample):
        if value == 0:
            continue
        left = x + step * (i + look.spacing)
        width = step * (1 - look.spacing * 2)
        top, bottom = ((y + h * (1 - float(value)), y + h + 1) if up
                       else (y - 1, y + h * float(value) + 1))
        path.addRect(QRectF(left, top, width, bottom - top))
    return path


def spine_box(sample: np.ndarray, x: float, y: float, w: float, h: float,
              up: bool, look: _Look) -> Any:
    """DrawSpineBox: a rounded square a column on the middle line, as big
    as the bar (Cavalier's own ``(1 - size) / 2`` offset kept)."""
    from PySide6.QtGui import QPainterPath
    frame_log.debug("spine_box: %d bar(s)", sample.size)
    step = w / sample.size
    item = step * (1 - look.spacing * 2)
    path = QPainterPath()
    for i, value in enumerate(sample):
        if value == 0:
            continue
        size = item * float(value)
        _round_rect(path, x + step * (i + 0.5) + (1 - size) / 2,
                    y + h / 2 - size / 2, size, size, size / 2 * look.roundness,
                    size / 2 * look.roundness)
    return path


def splitter_box(sample: np.ndarray, x: float, y: float, w: float, h: float,
                 up: bool, look: _Look) -> Any:
    """DrawSplitterBox: steps alternating above and below the middle line,
    filled to the edge."""
    from PySide6.QtGui import QPainterPath
    frame_log.debug("splitter_box: %d bar(s)", sample.size)
    step = w / sample.size
    orient = -1 if up else 1
    path = QPainterPath()
    path.moveTo(x, y + h / 2 * (1 + float(sample[0]) * orient))
    for i, value in enumerate(sample):
        side = orient if i % 2 == 0 else -orient
        if i > 0:
            path.lineTo(x + step * i, y + h / 2)
        path.lineTo(x + step * i, y + h / 2 * (1 + float(value) * side))
        path.lineTo(x + step * (i + 1), y + h / 2 * (1 + float(value) * side))
        if i < sample.size - 1:
            path.lineTo(x + step * (i + 1), y + h / 2)
    edge = y + h if up else y
    path.lineTo(x + w, edge)
    path.lineTo(x, edge)
    path.closeSubpath()
    return path


# ── the circle modes: drawn round the middle, each item turned ───────────

def _radii(w: float, h: float, look: _Look) -> tuple[float, float]:
    full = min(w, h) / 2
    frame_log.debug("_radii: full %.1f inner %.2f", full, look.inner_radius)
    return full, full * look.inner_radius


def _on_ring(ring: tuple[float, float, float, float, int], value: float,
             slot: float) -> tuple[float, float]:
    """A point of the wave circle: *slot* of the ring's count round from
    six o'clock, *value* of the way from the inner radius to the edge."""
    cx, cy, inner, span, count = ring
    angle = math.pi / 2 + math.pi * 2 * slot / count
    radius = inner + span * value
    frame_log.debug("_on_ring: slot %.1f r %.1f", slot, radius)
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def wave_circle(painter: Any, sample: np.ndarray, x: float, y: float, w: float,
                h: float, look: _Look, color: Any) -> None:
    """DrawWaveCircle: a closed curve round the circle; the ring between the
    inner radius and the edge, inside the curve."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QPainterPath, QPen
    frame_log.debug("wave_circle: %d bar(s)", sample.size)
    full, inner = _radii(w, h, look)
    span, count = full - inner, sample.size
    ring = (x + w / 2, y + h / 2, inner, span, count)
    path = QPainterPath()
    path.moveTo(*_on_ring(ring, float(sample[0]), 0))
    for i in range(count):
        here, there = float(sample[i]), float(sample[(i + 1) % count])
        path.cubicTo(*_on_ring(ring, here, i + 0.5), *_on_ring(ring, there, i + 0.5),
                     *_on_ring(ring, there, i + 1 if i < count - 1 else 0))
    path.closeSubpath()
    painter.save()
    painter.setClipPath(path)
    painter.setPen(QPen(color, span))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QPointF(x + w / 2, y + h / 2), inner + span / 2,
                        inner + span / 2)
    painter.restore()


def _turned(painter: Any, x: float, y: float, w: float, h: float, i: int,
            count: int) -> None:
    """Into item *i*'s frame: the middle, turned to its slot."""
    frame_log.debug("_turned: %d of %d", i, count)
    painter.save()
    painter.translate(x + w / 2, y + h / 2)
    painter.rotate(math.degrees(2 * math.pi * (i + 0.5) / count))


def levels_circle(painter: Any, sample: np.ndarray, x: float, y: float,
                  w: float, h: float, look: _Look, color: Any) -> None:
    """DrawLevelsCircle: ten blocks out from the inner radius, per bar."""
    from PySide6.QtGui import QPainterPath
    frame_log.debug("levels_circle: %d bar(s)", sample.size)
    full, inner = _radii(w, h, look)
    bar = 2 * math.pi * inner / sample.size
    ring = (full - inner) / 10
    for i, value in enumerate(sample):
        _turned(painter, x, y, w, h, i, sample.size)
        path = QPainterPath()
        for j in range(math.floor(float(value) * 10)):
            _round_rect(path, -bar * (1 - look.spacing * 2) / 2,
                        inner + ring * j + ring * look.spacing,
                        bar * (1 - look.spacing * 2), ring * (1 - look.spacing * 2),
                        bar * (1 - look.spacing) * look.roundness,
                        ring * (1 - look.spacing) * look.roundness)
        painter.fillPath(path, color)
        painter.restore()


def particles_circle(painter: Any, sample: np.ndarray, x: float, y: float,
                     w: float, h: float, look: _Look, color: Any) -> None:
    """DrawParticlesCircle: one block per bar, as far out as it is loud."""
    from PySide6.QtGui import QPainterPath
    frame_log.debug("particles_circle: %d bar(s)", sample.size)
    full, inner = _radii(w, h, look)
    bar = 2 * math.pi * inner / sample.size
    ring = (full - inner) / 10
    for i, value in enumerate(sample):
        _turned(painter, x, y, w, h, i, sample.size)
        path = QPainterPath()
        _round_rect(path, -bar * (1 - look.spacing * 2) / 2,
                    inner + ring * 9 * float(value) + ring * look.spacing,
                    bar * (1 - look.spacing * 2), ring * (1 - look.spacing * 2),
                    bar * (1 - look.spacing) * look.roundness,
                    ring * (1 - look.spacing) * look.roundness)
        painter.fillPath(path, color)
        painter.restore()


def bars_circle(painter: Any, sample: np.ndarray, x: float, y: float, w: float,
                h: float, look: _Look, color: Any) -> None:
    """DrawBarsCircle: bars out from the inner radius."""
    from PySide6.QtCore import QRectF
    frame_log.debug("bars_circle: %d bar(s)", sample.size)
    full, inner = _radii(w, h, look)
    bar = 2 * math.pi * inner / sample.size
    for i, value in enumerate(sample):
        _turned(painter, x, y, w, h, i, sample.size)
        painter.fillRect(QRectF(-bar * (1 - look.spacing * 2) / 2, inner,
                                bar * (1 - look.spacing * 2),
                                (full - inner) * float(value) + 1), color)
        painter.restore()


def spine_circle(painter: Any, sample: np.ndarray, x: float, y: float, w: float,
                 h: float, look: _Look, color: Any) -> None:
    """DrawSpineCircle: a rounded square per bar on the inner radius, as big
    as the bar."""
    from PySide6.QtGui import QPainterPath
    frame_log.debug("spine_circle: %d bar(s)", sample.size)
    _full, inner = _radii(w, h, look)
    bar = 2 * math.pi * inner / sample.size
    item = bar * (1 - look.spacing * 2)
    for i, value in enumerate(sample):
        size = item * float(value)
        _turned(painter, x, y, w, h, i, sample.size)
        path = QPainterPath()
        _round_rect(path, -bar * (1 - look.spacing * 2) / 2 * float(value),
                    inner - size / 2, size, size, size / 2 * look.roundness,
                    size / 2 * look.roundness)
        painter.fillPath(path, color)
        painter.restore()


# ── the visuals ─────────────────────────────────────────────────────────

_BARS = VisualAdjustment("bars", "Bar pairs", 2.0, 30.0, 6.0)
_COLOUR = VisualAdjustment("colour", "Colour", 0.0, 359.0, _HUE)
_MIRROR = VisualAdjustment("mirror", "Mirror (off, full, split)", 0.0, 2.0, 0.0)
_SPACING = VisualAdjustment("spacing", "Spacing", 0.0, 0.4, 0.1)
_ROUNDNESS = VisualAdjustment("roundness", "Roundness", 0.0, 1.0, 0.5)
_INNER = VisualAdjustment("inner_radius", "Inner radius", 0.1, 0.9, 0.5)


def _info(kind: str, title: str, *extra: VisualAdjustment) -> VisualInfo:
    """A Cavalier mode's gallery entry: bars, colour and mirror, and *extra*."""
    log.debug("_info: %s", kind)
    return VisualInfo(kind=kind, title=title, category="Cavalier",
                      uses_center=True,
                      adjustments=(_BARS, _COLOUR, _MIRROR, *extra))


class CavalierVisual(PaintedVisual):
    """One of Cavalier's modes: CAVA's bars, drawn by ``SHAPE``.  A box
    shape returns a path this fills (and clips the picture into); a circle
    shape paints itself round the middle."""

    INFO: ClassVar[VisualInfo]
    SHAPE: ClassVar[Callable[..., Any]]
    CIRCLE: ClassVar[bool] = False
    RANK = 10                     # after TRCC Pipes, before MilkDrop
    BACKGROUND = (0x24, 0x24, 0x24)

    def __init__(self) -> None:
        log.debug("%s.__init__", type(self).__name__)
        super().__init__()
        self._pairs = 0
        self._cava: Cava | None = None
        self._sample = np.zeros(2)
        self._look = _Look()
        self._color: Any = None
        self._mirror = 0

    def adjust(self, settings: Mapping[str, float]) -> None:
        """The sliders: each at *settings*' value, else its default."""
        from PySide6.QtGui import QColor
        values = {a.name: a.default for a in self.INFO.adjustments}
        values.update(settings)
        log.info("%s.adjust: %s", type(self).__name__, values)
        pairs = max(1, round(values["bars"]))
        if pairs != self._pairs:
            self._pairs = pairs
            self._cava = Cava(pairs, SOUND_RATE, SOUND_CHANNELS,
                              noise_reduction=_NOISE_REDUCTION,
                              low_cut_off=_LOW_HZ, high_cut_off=_HIGH_HZ)
            self._sample = np.zeros(pairs * 2)
        self._color = QColor.fromHsvF((values["colour"] % 360) / 360,
                                      _SATURATION, _VALUE)
        self._mirror = round(values["mirror"])
        self._look = _Look(spacing=values.get("spacing", 0.1),
                           roundness=values.get("roundness", 0.5),
                           inner_radius=values.get("inner_radius", 0.5))

    def feed(self, pcm: bytes) -> None:
        """CAVA's bars, smoothed by monstercat, in Cavalier's order: each
        channel low to high from the outside in -- bass at both edges."""
        samples = np.frombuffer(pcm, dtype="<f4")
        frame_log.debug("%s.feed: %d sample(s)", type(self).__name__, samples.size)
        if self._cava is None or samples.size == 0:
            return
        out = self._cava.execute(samples.astype(np.float64) * _SAMPLE_SCALE)
        pairs = self._pairs
        left = monstercat(out[:pairs].copy())
        right = monstercat(out[pairs:].copy())
        # cava puts the left channel's bass in the middle; Cavalier's
        # ReverseOrder turns each half round: bass at the outer edges.
        self._sample = np.concatenate([left, right[::-1]])

    def _paint(self, painter: Any) -> None:
        """Each mirrored half (or the whole), in its shape."""
        frame_log.debug("%s._paint", type(self).__name__)
        for sample, y, height, up in self._halves():
            self._draw(painter, sample, float(y), float(height), up)

    def _bass(self) -> float:
        """How hard the bass hits now: its bars sit at both outer edges."""
        bass = float(max(self._sample[0], self._sample[-1]))
        frame_log.debug("%s._bass: %.2f", type(self).__name__, bass)
        return bass

    def _halves(self) -> list[tuple[np.ndarray, float, float, bool]]:
        """Renderer.Draw's mirror: off -- the whole height; full -- the bars
        up in the top half and hanging in the bottom; split channels -- the
        left channel up top, the right below."""
        sample, h = self._sample, self._h
        frame_log.debug("%s._halves: mirror %d", type(self).__name__, self._mirror)
        if self._mirror == 1:
            return [(sample, 0, h / 2, True), (sample, h / 2, h / 2, False)]
        if self._mirror == 2:
            half = sample.size // 2
            return [(sample[:half], 0, h / 2, True),
                    (sample[half:][::-1], h / 2, h / 2, False)]
        return [(sample, 0, h, True)]

    def _draw(self, painter: Any, sample: np.ndarray, y: float, height: float,
              up: bool) -> None:
        """One half (or the whole): the shape, and the picture in it."""
        frame_log.debug("%s._draw: y %.0f h %.0f", type(self).__name__, y, height)
        if sample.size < 2:
            return
        if self.CIRCLE:
            type(self).SHAPE(painter, sample, 0.0, y, float(self._w), height,
                             self._look, self._color)
            # TRCC's addition: the user's picture inside the inner circle.
            _full, inner = _radii(float(self._w), height, self._look)
            self._picture_in_circle(painter, self._w / 2, y + height / 2, inner)
            return
        path = type(self).SHAPE(sample, 0.0, y, float(self._w), height, up,
                                self._look)
        painter.fillPath(path, self._color)
        if self._picture is not None:
            self._burning.append(path)
            painter.save()
            painter.setClipPath(path)
            self._draw_picture(painter, 0.0, 0.0, float(self._w), float(self._h))
            painter.restore()

    def close(self) -> None:
        log.info("%s.close", type(self).__name__)
        super().close()
        self._cava = None


class CavalierWave(CavalierVisual, key="cavalier-wave"):
    INFO = _info("cavalier-wave", "Wave")
    SHAPE = staticmethod(wave_box)


class CavalierLevels(CavalierVisual, key="cavalier-levels"):
    INFO = _info("cavalier-levels", "Levels", _SPACING, _ROUNDNESS)
    SHAPE = staticmethod(levels_box)


class CavalierParticles(CavalierVisual, key="cavalier-particles"):
    INFO = _info("cavalier-particles", "Particles", _SPACING, _ROUNDNESS)
    SHAPE = staticmethod(particles_box)


class CavalierBars(CavalierVisual, key="cavalier-bars"):
    INFO = _info("cavalier-bars", "Bars", _SPACING)
    SHAPE = staticmethod(bars_box)


class CavalierSpine(CavalierVisual, key="cavalier-spine"):
    INFO = _info("cavalier-spine", "Spine", _SPACING, _ROUNDNESS)
    SHAPE = staticmethod(spine_box)


class CavalierSplitter(CavalierVisual, key="cavalier-splitter"):
    INFO = _info("cavalier-splitter", "Splitter")
    SHAPE = staticmethod(splitter_box)


class CavalierWaveCircle(CavalierVisual, key="cavalier-wave-circle"):
    INFO = _info("cavalier-wave-circle", "Wave Circle", _INNER)
    SHAPE = staticmethod(wave_circle)
    CIRCLE = True


class CavalierLevelsCircle(CavalierVisual, key="cavalier-levels-circle"):
    INFO = _info("cavalier-levels-circle", "Levels Circle", _SPACING,
                 _ROUNDNESS, _INNER)
    SHAPE = staticmethod(levels_circle)
    CIRCLE = True


class CavalierParticlesCircle(CavalierVisual, key="cavalier-particles-circle"):
    INFO = _info("cavalier-particles-circle", "Particles Circle", _SPACING,
                 _ROUNDNESS, _INNER)
    SHAPE = staticmethod(particles_circle)
    CIRCLE = True


class CavalierBarsCircle(CavalierVisual, key="cavalier-bars-circle"):
    INFO = _info("cavalier-bars-circle", "Bars Circle", _SPACING, _INNER)
    SHAPE = staticmethod(bars_circle)
    CIRCLE = True


class CavalierSpineCircle(CavalierVisual, key="cavalier-spine-circle"):
    INFO = _info("cavalier-spine-circle", "Spine Circle", _SPACING,
                 _ROUNDNESS, _INNER)
    SHAPE = staticmethod(spine_circle)
    CIRCLE = True
