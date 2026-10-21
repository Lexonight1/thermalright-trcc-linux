"""TRCC Pipes: the Windows 3D Pipes screen saver, as a stereo's equaliser.

Eight pipes run out from the centre picture like the spokes of a
wheel, one per frequency band, every 360 / 8 = 45 degrees -- the bass at
twelve o'clock, round through the mids to the treble.  Each reaches from the
centre toward the edge as far as its band is loud, a peak marker hangs at its
highest reach and sinks back as an old graphic equaliser's did, and the beat
swells every pipe.  When the music goes quiet the round ends: new materials,
and the wheel leans a new way.

**Lit the way the screen saver lit its pipes**, from its source -- the OpenGL
sample Microsoft published in the Windows NT 4.0 SDK
(``MSTOOLS/SAMPLES/OPENGL/SCRSAVE/PIPES`` and ``COMMON/MATERIAL.C``):

* its sixteen "good" materials, ambient / diffuse / specular / shininess, one
  per pipe -- the table it took from SGI's GLAUX teapots demo;
* its one white directional light at (90, 90, 150), light ambient 0.1 over a
  global ambient of 1.0, viewer at infinity -- OpenGL's fixed-function sum;
* its proportions: pipe radius 1 in a 7-unit cell, ball joints and caps
  sqrt(2) x the pipe, a 90-degree perspective from 75 units back for a scene
  56 units across -- so the near pipes are big and the far ones small;
* its turn of the scene by 9.73156 degrees at every reset -- here the
  direction the wheel leans.

The numbers are taken; no code is.  The wheel, the bands, the peaks, the beat
and the centre picture are TRCC's.  What to draw is worked out here, on the
CPU; the drawing -- every tube, ball, flame and the picture -- is OpenGL's
(``_pipes_gl``), on the context the platform names: every OS with OpenGL 3,
the graphics card's or a software one.

The centre picture is the user's -- anything; none until they pick
one.  It rocks toward the viewer and away, swells with the bass, and with
Fire up it burns.
"""
from __future__ import annotations

import logging
import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from trcc.adapters.visual._gl import GlSurface
from trcc.core.logs import per_frame
from trcc.core.models import SOUND_CHANNELS, SOUND_RATE, RawFrame

from ..core.models import VISUAL_FPS, VisualAdjustment, VisualInfo, VisualSpec
from ._base import GlVisual
from ._fire import RISE, STEP, burned_picture, keep
from ._pipes_gl import SHAPE_FIELDS, PipesPainter

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_GRID = 13                    # cells per side
_BANDS = 8                    # one pipe per frequency band: few, so each is big
_BASS_HZ = 180.0              # the bands below this are the bass, for the beat
_RING = 1.5                   # where the pipes start: right at the centre
_ZENITH_CELLS = 6.2           # how far out a pipe reaches at full volume, in cells
STEPS = 14                   # spheres per cell of pipe
_ROLL = 20 * VISUAL_FPS       # frames, at least, between new rounds
_FFT = 8192                   # FFT size: 5.4 Hz bins, so the 40-57 Hz band has some
_LIFT = 2.0                   # a hit this far over the band's average is the zenith
_BUILD = STEP / 7.0          # most a pipe grows in a frame: built out, not shot
_FALL = 1 - keep(0.88)       # how fast a pipe falls back toward the centre
_ELBOW = math.radians(45.0)   # each pipe turns this far at its one elbow ...
_BEND = (0.3, 0.5)            # ... somewhere in this part of its length
_LEAN = math.radians(30.0)    # how far the whole wheel leans, a new way each round
_PIPE_HEAT = 0.15             # how hot a pipe's flames are, the picture's being 1
_PIPE_GLOW = 0.3              # how far a pipe turns to embers, the picture's being 1

# ── the screen saver's numbers (NT 4.0 SDK, SCRSAVE/PIPES) ─────────────────
_RADIUS = 1.0 / 7.0           # STATE.CXX radius 1.0 in VIEW.CXX divSize 7.0
_BALL = math.sqrt(2.0)        # NSTATE.CXX: joints and caps are ROOT_TWO*radius
_VIEW = math.radians(90.0)    # VIEW.CXX persp.viewAngle
_DISTANCE = 75.0 / 56.0       # VIEW.CXX zTrans -75 over the half of 16 x 7
_TURN = math.radians(9.73156)  # VIEW.CXX IncrementSceneRotation
#: STATE.CXX GLInit: light 0 is white, directional, from (90, 90, 150) in eye
#: space -- y flipped here, since the sprites count rows down the screen.
_LIGHT = np.array([90.0, -90.0, 150.0], dtype=np.float32)
_LIGHT /= np.linalg.norm(_LIGHT)
_HALF = _LIGHT + np.array([0.0, 0.0, 1.0], dtype=np.float32)   # viewer at infinity
_HALF /= np.linalg.norm(_HALF)
#: The global ambient 1.0 plus light 0's own 0.1, both falling on a material's
#: ambient colour.
_AMBIENT = 1.1


@dataclass(frozen=True, slots=True)
class _Material:
    """One of the screen saver's materials, as fixed-function OpenGL lights
    it: ``ambient + diffuse * N.L + specular * (N.H) ** shininess``."""
    name: str
    ambient: np.ndarray
    diffuse: np.ndarray
    specular: np.ndarray
    shininess: float


def _material(name: str, ka: tuple[float, float, float],
              kd: tuple[float, float, float], ks: tuple[float, float, float],
              spec_exp: float) -> _Material:
    """A row of MATERIAL.C's ``teaMaterialData``, ready to light."""
    log.debug("_material: %s", name)
    return _Material(name, np.array(ka, dtype=np.float32) * _AMBIENT,
                     np.array(kd, dtype=np.float32),
                     np.array(ks, dtype=np.float32), spec_exp * 128.0)


#: MATERIAL.C's ``goodMaterials``, in its order, with its numbers.
_MATERIALS = tuple(_material(*row) for row in (
    ("emerald", (0.0215, 0.1745, 0.0215), (0.07568, 0.61424, 0.07568),
     (0.633, 0.727811, 0.633), 0.6),
    ("jade", (0.135, 0.2225, 0.1575), (0.54, 0.89, 0.63),
     (0.316228, 0.316228, 0.316228), 0.1),
    ("pearl", (0.25, 0.20725, 0.20725), (1.0, 0.829, 0.829),
     (0.296648, 0.296648, 0.296648), 0.088),
    ("ruby", (0.1745, 0.01175, 0.01175), (0.61424, 0.04136, 0.04136),
     (0.727811, 0.626959, 0.626959), 0.6),
    ("turquoise", (0.1, 0.18725, 0.1745), (0.396, 0.74151, 0.69102),
     (0.297254, 0.30829, 0.306678), 0.1),
    ("brass", (0.329412, 0.223529, 0.027451), (0.780392, 0.568627, 0.113725),
     (0.992157, 0.941176, 0.807843), 0.21794872),
    ("bronze", (0.2125, 0.1275, 0.054), (0.714, 0.4284, 0.18144),
     (0.393548, 0.271906, 0.166721), 0.2),
    ("copper", (0.19125, 0.0735, 0.0225), (0.7038, 0.27048, 0.0828),
     (0.256777, 0.137622, 0.086014), 0.1),
    ("gold", (0.24725, 0.1995, 0.0745), (0.75164, 0.60648, 0.22648),
     (0.628281, 0.555802, 0.366065), 0.4),
    ("silver", (0.19225, 0.19225, 0.19225), (0.50754, 0.50754, 0.50754),
     (0.508273, 0.508273, 0.508273), 0.4),
    ("cyan plastic", (0.0, 0.1, 0.06), (0.0, 0.50980392, 0.50980392),
     (0.50196078, 0.50196078, 0.50196078), 0.25),
    ("white plastic", (0.0, 0.0, 0.0), (0.55, 0.55, 0.55),
     (0.7, 0.7, 0.7), 0.25),
    ("yellow plastic", (0.0, 0.0, 0.0), (0.5, 0.5, 0.0),
     (0.6, 0.6, 0.5), 0.25),
    ("cyan rubber", (0.0, 0.05, 0.05), (0.4, 0.5, 0.5),
     (0.04, 0.7, 0.7), 0.078125),
    ("green rubber", (0.0, 0.05, 0.0), (0.4, 0.5, 0.4),
     (0.04, 0.7, 0.04), 0.078125),
    ("white rubber", (0.05, 0.05, 0.05), (0.5, 0.5, 0.5),
     (0.7, 0.7, 0.7), 0.078125),
))
#: The peak markers: the table's white plastic, whatever the pipe is made of
#: -- by its place in the table, as the painter knows the materials.
_PEAK = 11



def band_edges() -> np.ndarray:
    """The frequency bands, spaced like pitch: 40 Hz to 14 kHz."""
    log.debug("band_edges: %d bands", _BANDS)
    return np.geomspace(40.0, 14000.0, _BANDS + 1)


class _Pipe:
    """One pipe: its band, its material (its place in ``_MATERIALS``), its
    path from the centre out as
    screen points, and the index of its elbow.  ``reach`` is how much of the
    path the music shows now."""

    def __init__(self, band: int, material: int,
                 points: np.ndarray, elbow: int) -> None:
        log.debug("_Pipe: band %d, %s, %d point(s), elbow at %d", band,
                  _MATERIALS[material].name, len(points), elbow)
        self.band, self.material = band, material
        self.points, self.elbow = points, elbow
        self.reach = 0.0
        self.peak = 0.0          # the old graphic EQ's peak marker


class TrccPipes(GlVisual, key="trcc-pipes"):
    """TRCC Pipes -- the first visual in the gallery."""

    RANK = 0
    INFO = VisualInfo(
        kind="trcc-pipes", title="TRCC Pipes", category="TRCC",
        uses_center=True,
        adjustments=(
            VisualAdjustment("growth", "Growth", 0.3, 3.0, 1.0),
            VisualAdjustment("thickness", "Thickness", 0.5, 4.0, 2.8),
            VisualAdjustment("center_size", "Centre picture", 0.0, 0.5, 0.3),
        ))

    @classmethod
    def picture_problem(cls, path: Path) -> str:
        """A centre picture must be an image Qt can read."""
        from PySide6.QtGui import QImage
        problem = ("" if path.is_file() and not QImage(str(path)).isNull()
                   else f"no picture at {path}")
        log.info("TrccPipes.picture_problem: %s -> %r", path, problem)
        return problem

    def __init__(self) -> None:
        log.debug("TrccPipes.__init__")
        self._surface: GlSurface | None = None
        self._painter: PipesPainter | None = None
        self._edges = band_edges()
        self._bins: dict[int, list[tuple[int, int]]] = {}
        self._band_peak = np.full(_BANDS, 1e-3)
        self._bands = np.zeros(_BANDS)
        self._band_avg = np.zeros(_BANDS)
        self._bass_bands = max(1, int(np.count_nonzero(self._edges[1:] <= _BASS_HZ)))
        self._bass = self._beat = 0.0
        self._peak_bass = 1e-3
        self._tick = 0.0
        self._turn = random.uniform(0.0, 2 * math.pi)
        self._w = self._h = 0
        self._center: Any = None          # the picture, as loaded
        self._shown: Any = None           # ... as drawn: red-hot with Fire up
        self._growth = self._thickness = 1.0
        self._center_size = 0.3
        self._fire = 0.0

    # ── VisualEngine ─────────────────────────────────────────────────

    def open(self, spec: VisualSpec) -> None:
        log.info("TrccPipes.open: %dx%d centre=%s settings=%s", spec.width,
                 spec.height, spec.center, dict(spec.settings))
        self._w, self._h = spec.width, spec.height
        self.adjust(spec.settings)
        picture = spec.center
        if picture is not None and picture.is_file():
            from PySide6.QtGui import QImage
            image = QImage(str(picture))
            if image.isNull():
                log.warning("TrccPipes.open: %s is not a picture", picture)
            else:
                self._center = image
        # The fire is the picture's: no picture, no fire (the user's call).
        self._fire = spec.center_fire if self._center is not None else 0.0
        self._surface = surface = self._open_surface()
        try:
            self._painter = PipesPainter(
                surface, self._w, self._h,
                [(m.ambient, m.diffuse, m.specular, m.shininess)
                 for m in _MATERIALS],
                _LIGHT, _HALF, _GRID / 2 * _DISTANCE + _GRID,
                (RISE, STEP, _PIPE_HEAT))
        except OSError:
            self.close()
            raise
        self._prepare_center()
        self._new_round()

    def adjust(self, settings: Mapping[str, float]) -> None:
        """The sliders: each at *settings*' value, else its default."""
        values = {a.name: a.default for a in self.INFO.adjustments}
        values.update(settings)
        log.info("TrccPipes.adjust: %s", values)
        self._growth = float(values["growth"])
        self._thickness = float(values["thickness"])
        self._center_size = float(values["center_size"])

    def feed(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype="<f4")
        frame_log.debug("TrccPipes.feed: %d sample(s)", samples.size)
        if samples.size < SOUND_CHANNELS * 64:
            return
        mono = samples[: samples.size - samples.size % SOUND_CHANNELS]
        mono = mono.reshape(-1, SOUND_CHANNELS).mean(axis=1)
        size = max(_FFT, mono.size)
        spectrum = np.abs(np.fft.rfft(mono, size))
        levels = np.zeros(_BANDS)
        for i, (low, high) in enumerate(self._band_bins(size)):
            if high > low:
                levels[i] = float(spectrum[low:high].mean())
        # Each band against its own recent peak, so the treble is not
        # drowned by the bass and a quiet song still grows pipes.
        self._band_peak = np.maximum(levels, self._band_peak * keep(0.996))
        held = keep(0.55)
        self._bands = (held * self._bands
                       + (1 - held) * levels / np.maximum(self._band_peak, 1e-6))
        # ... and its recent average, about a second: a pipe goes out when
        # its band rises over that -- a hit -- not merely because it is loud.
        usual = keep(0.96)
        self._band_avg = usual * self._band_avg + (1 - usual) * self._bands
        bass = float(levels[:self._bass_bands].mean())
        self._peak_bass = max(bass, self._peak_bass * keep(0.995))
        bass_now = bass / self._peak_bass
        self._beat = (max(bass_now - self._bass, 0.0) * 2.5
                      + self._beat * keep(0.8))
        kept = keep(0.6)
        self._bass = kept * self._bass + (1 - kept) * bass_now

    def render(self) -> RawFrame:
        frame_log.debug("TrccPipes.render: frame %d", self._frame)
        if self._painter is None:
            raise OSError("TRCC Pipes is not open")
        self._frame += 1
        self._follow_the_music()
        beat = min(self._beat, 1.0)
        placed = (self._center_placement()
                  if self._shown is not None and self._center_size > 0 else None)
        data = self._painter.paint(
            self._shapes(_RADIUS * self._thickness * (1.0 + 0.45 * beat)),
            gain=1.0 + 0.35 * beat, glow=self._fire * _PIPE_GLOW,
            fire=self._fire, bass=self._bass, placed=placed)
        return RawFrame(data=data, width=self._w, height=self._h)

    def close(self) -> None:
        log.info("TrccPipes.close: open=%s", self._surface is not None)
        if self._painter is not None:
            self._painter.close()
            self._painter = None
        if self._surface is not None:
            self._surface.close()
            self._surface = None
        self._center = self._shown = None

    # ── the screen saver ─────────────────────────────────────────────

    def _new_round(self) -> None:
        """New materials -- eight of the sixteen, dealt one to a pipe, as the screen
        saver picked one at random per pipe -- and the wheel leaning a new
        way, turned on as the screen saver turned its scene each reset."""
        self._frame = self._rolled = getattr(self, "_frame", 0)
        w, h = self._w, self._h
        self._turn += _TURN
        axis = np.array([math.cos(self._turn), math.sin(self._turn), 0.0])
        cross = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]],
                          [-axis[1], axis[0], 0.0]])
        self._rot = (math.cos(_LEAN) * np.eye(3) + math.sin(_LEAN) * cross
                     + (1 - math.cos(_LEAN)) * np.outer(axis, axis)
                     ).astype(np.float32)
        self._dist = _GRID / 2 * _DISTANCE
        self._focal = min(w, h) / 2 / math.tan(_VIEW / 2)
        materials = random.sample(range(len(_MATERIALS)), _BANDS)
        self._pipes = [self._spoke(band, materials[band])
                       for band in range(_BANDS)]
        log.info("TrccPipes: new round, leaning toward %.0f deg, %s",
                 math.degrees(self._turn) % 360,
                 [_MATERIALS[m].name for m in materials])

    def _spoke(self, band: int, material: int) -> _Pipe:
        """A pipe from the centre out at its band's angle -- 360 / 8 =
        every 45 degrees, the bass at twelve o'clock -- straight, then one
        elbow of 45 degrees left or right at half its length or less, then
        straight to the zenith; as screen points, with the elbow's index."""
        frame_log.debug("_spoke: band %d", band)
        mid = (_GRID - 1) / 2
        angle = band / _BANDS * 2 * math.pi - math.pi / 2
        turned = angle + random.choice((-1, 1)) * _ELBOW
        along = np.array([math.cos(angle), -math.sin(angle), 0.0], dtype=np.float32)
        after = np.array([math.cos(turned), -math.sin(turned), 0.0], dtype=np.float32)
        start = np.full(3, mid, dtype=np.float32) + along * _RING
        length = _ZENITH_CELLS - _RING
        elbow = round(random.uniform(*_BEND) * length * STEPS)
        corner = start + along * (elbow / STEPS)
        points = [start + along * (i / STEPS) if i <= elbow
                  else corner + after * ((i - elbow) / STEPS)
                  for i in range(int(length * STEPS) + 1)]
        projected = np.array([self._project(p) for p in points], dtype=np.float32)
        return _Pipe(band, material, projected, elbow)

    def _band_bins(self, size: int) -> list[tuple[int, int]]:
        """Each band's FFT bins for an FFT of *size*, as ``[low, high)`` --
        the bins at or above its lower edge and below its upper.  Worked out
        once per size: scanning all 4,097 bins per band every frame was 0.77
        M instructions a frame (measured 2026-10-13)."""
        if size not in self._bins:
            hz = np.fft.rfftfreq(size, 1.0 / SOUND_RATE)
            cuts = np.searchsorted(hz, self._edges, side="left")
            self._bins[size] = list(pairwise(cuts.tolist()))
            log.debug("_band_bins: %d -> %s", size, self._bins[size])
        frame_log.debug("_band_bins: %d", size)
        return self._bins[size]

    def _follow_the_music(self) -> None:
        """Each pipe reaches out as far as its band rises over its own
        average -- built out from the centre a piece at a time, as the screen
        saver laid its pipes, then falling back.  A new round comes in a
        quiet moment, no more often than every ``_ROLL`` frames."""
        frame_log.debug("_follow_the_music")
        for pipe in self._pipes:
            now = float(self._bands[pipe.band])
            usual = float(self._band_avg[pipe.band])
            lift = (now - usual) / max(1.0 - usual, 0.15)
            target = min(max(lift * _LIFT * self._growth, 0.0), 1.0)
            if target > pipe.reach:
                pipe.reach = min(target, pipe.reach + _BUILD)
            else:
                pipe.reach += (target - pipe.reach) * _FALL
            # The peak marker hangs at the highest reach, then sinks back --
            # as an old stereo's graphic equaliser did.
            pipe.peak = max(pipe.reach, pipe.peak - 0.012 * STEP)
        quiet = all(p.reach < 0.12 for p in self._pipes)
        if quiet and self._frame - self._rolled > _ROLL:
            self._new_round()

    def _project(self, point: np.ndarray) -> tuple[float, float, float]:
        frame_log.debug("_project")
        q = self._rot @ (point - (_GRID - 1) / 2.0)
        z = float(q[2]) + self._dist
        return (self._w / 2 + self._focal * float(q[0]) / z,
                self._h / 2 - self._focal * float(q[1]) / z, z)

    def _shapes(self, radius: float) -> np.ndarray:
        """This frame's shapes for the painter, ``SHAPE_FIELDS`` floats a
        row: every pipe as far as it reaches, *radius* thick -- a tube to
        the elbow and one past it, the screen saver's balls on the root, the
        elbow and the tip, and a ball for the peak marker.  Nearer shapes
        hide farther ones; at a tie the one listed first wins."""
        frame_log.debug("_shapes: %d pipe(s), radius %.3f", len(self._pipes),
                        radius)
        rows: list[tuple[float, ...]] = []
        for pipe in self._pipes:
            last = len(pipe.points) - 1
            tip = round(pipe.reach * last)
            bend = min(pipe.elbow, tip)
            self._tube(rows, pipe.points[0], pipe.points[bend], radius,
                       pipe.material)
            if tip > pipe.elbow:
                self._tube(rows, pipe.points[pipe.elbow], pipe.points[tip],
                           radius, pipe.material)
            for j in {0, bend, tip}:
                self._ball(rows, pipe.points[j], radius * _BALL, pipe.material)
            top = round(pipe.peak * last)
            if top > tip:
                self._ball(rows, pipe.points[top], radius * _BALL, _PEAK)
        return np.array(rows, dtype=np.float32).reshape(-1, SHAPE_FIELDS)

    def _tube(self, rows: list[tuple[float, ...]], start: np.ndarray,
              end: np.ndarray, radius: float, material: int) -> None:
        """A straight pipe from *start* to *end* (screen x, y and depth) --
        one cylinder, as the screen saver's ``PIPE_OBJECT``, its near end
        wider than its far end."""
        x0, y0, z0 = (float(v) for v in start)
        x1, y1, z1 = (float(v) for v in end)
        frame_log.debug("_tube: %.0f,%.0f -> %.0f,%.0f", x0, y0, x1, y1)
        if math.hypot(x1 - x0, y1 - y0) < 0.5:
            return
        r0, r1 = self._focal * radius / z0, self._focal * radius / z1
        pad = max(r0, r1) + 1.0
        rows.append((x0, y0, z0, x1, y1, z1, r0, r1, radius, 0.0, material,
                     min(x0, x1) - pad, min(y0, y1) - pad,
                     max(x0, x1) + pad, max(y0, y1) + pad))

    def _ball(self, rows: list[tuple[float, ...]], point: np.ndarray,
              radius: float, material: int) -> None:
        """One lit sphere, centred on a whole pixel, at least one across."""
        sx, sy, z = (float(v) for v in point)
        px = max(1, round(self._focal * radius / z))
        cx, cy = float(round(sx)), float(round(sy))
        frame_log.debug("_ball: %.0f,%.0f r%d", cx, cy, px)
        rows.append((cx, cy, z, cx, cy, z, px, px, radius, 1.0, material,
                     cx - px - 1, cy - px - 1, cx + px + 1, cy + px + 1))

    def _center_placement(self) -> tuple[float, float]:
        """This frame's place for the picture, in the middle: rocking toward
        the viewer and away -- how far it is squeezed across -- and swelling
        with the bass, its size in pixels."""
        self._tick += 1.6 / VISUAL_FPS
        tilt = 0.35 + 0.65 * abs(math.cos(self._tick))
        size = (min(self._w, self._h) * self._center_size
                * (1.0 + 0.22 * min(self._bass, 1.5)))
        frame_log.debug("_center_placement: tilt %.2f size %.0f", tilt, size)
        return tilt, size

    def _prepare_center(self) -> None:
        """The picture as it is drawn: red-hot as far as Fire is up --
        worked out when the picture or the Fire slider changes, never per
        frame."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        if self._center is None or self._painter is None:
            log.debug("_prepare_center: no picture")
            self._shown = None
            return
        self._shown = burned_picture(self._center, self._fire)
        # Drawn square, at most the panel's short side: scaled once, smoothly,
        # so the card never shrinks a big photo by sampling it.
        side = min(self._w, self._h)
        square = self._shown.scaled(
            side, side, Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation).convertToFormat(
            QImage.Format.Format_RGBA8888)
        rows = np.frombuffer(square.constBits(), dtype=np.uint8).reshape(
            side, square.bytesPerLine())[:, :side * 4]
        self._painter.show_picture(rows.tobytes(), side, side)
        log.debug("_prepare_center: fire %.2f, %dpx", self._fire, side)

