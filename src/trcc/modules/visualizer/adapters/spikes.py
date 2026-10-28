"""TRCC Spike Circle: a thin white circle whose edge spikes with the music.

Quiet, it is a plain circle.  The music pushes its edge out into small
triangles all the way round -- one per band, the bass at twelve o'clock,
rising in pitch down both sides to the treble at six, the two halves a
mirror so the circle stays balanced however the music leans -- and the
circle itself swells a little on the bass.  It breathes rather than jumps:
the heights from the EQ base (:mod:`._spectrum`) are eased in over
``_RISE_S`` and let go over ``_FALL_S``.  The user's picture, if any, sits
inside the circle -- and burns there, with the fire.

Painted with Qt on the CPU (:class:`._painted.PaintedVisual`): every OS.
TRCC's own visual, 2026-10-14 -- the user asked for "a spike circle,
triangles in the circumference that would spike ... just thin white ...
not so prevalent, like breathing".
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from trcc.core.logs import per_frame
from trcc.core.models import SOUND_RATE
from trcc.core.spectrum import Spectrum

from ..core.models import VisualAdjustment, VisualInfo
from ._painted import PaintedVisual

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

#: The most of half the panel's short side the circle and its spikes fill.
_REACH = 0.97
#: Seconds the spikes take to ease out to the music, and to let go.
_RISE_S, _FALL_S = 0.12, 0.45
#: The same for the circle's breath on the bass: slower, like a breath.
_BREATH_IN_S, _BREATH_OUT_S = 0.25, 0.8


def _ease(shown: np.ndarray, target: np.ndarray, seconds: float, rise_s: float,
          fall_s: float) -> np.ndarray:
    """*shown* moved toward *target*: over *rise_s* going up, *fall_s* coming
    down -- per second, so the same at any frame rate."""
    frame_log.debug("_ease: %.3f s", seconds)
    rate = np.where(target > shown, 1 - math.exp(-seconds / rise_s),
                    1 - math.exp(-seconds / fall_s))
    return shown + (target - shown) * rate


class SpikeCircle(PaintedVisual, key="trcc-spikes"):
    """A circle that breathes with the music; see the module."""

    INFO = VisualInfo(
        kind="trcc-spikes", title="Spike Circle", category="TRCC",
        uses_center=True,
        adjustments=(
            VisualAdjustment("spikes", "Spikes", 24.0, 240.0, 90.0),
            VisualAdjustment("inner_radius", "Circle size", 0.2, 0.8, 0.6),
            VisualAdjustment("spike", "Spike size", 0.05, 0.6, 0.2),
            VisualAdjustment("breathing", "Breathing", 0.0, 0.2, 0.06),
            VisualAdjustment("line", "Line width", 0.5, 4.0, 1.5),
            VisualAdjustment("tint", "Tint (white to colour)", 0.0, 1.0, 0.0),
            VisualAdjustment("colour", "Colour", 0.0, 359.0, 190.0),
        ))
    RANK = 5                      # after TRCC Pipes, before Cavalier
    BACKGROUND = (0, 0, 0)

    def __init__(self) -> None:
        log.debug("SpikeCircle.__init__")
        super().__init__()
        self._spectrum: Spectrum | None = None
        self._count = 0
        self._inner, self._spike, self._breathing, self._line = 0.6, 0.2, 0.06, 1.5
        self._color: Any = None
        #: The heights as drawn, eased; and the circle's breath, 0 to 1.
        self._shown = np.zeros(0)
        self._breath = 0.0

    def adjust(self, settings: Mapping[str, float]) -> None:
        """The sliders; a new spike count starts a new analyzer."""
        from PySide6.QtGui import QColor
        values = {a.name: a.default for a in self.INFO.adjustments}
        values.update(settings)
        log.info("SpikeCircle.adjust: %s", values)
        count = max(2, round(values["spikes"] / 2)) * 2
        if count != self._count:
            self._count = count
            self._spectrum = Spectrum(count // 2, SOUND_RATE)
            self._shown = np.zeros(count // 2)
        self._inner, self._line = values["inner_radius"], values["line"]
        self._spike, self._breathing = values["spike"], values["breathing"]
        self._color = QColor.fromHsvF(values["colour"] % 360 / 360,
                                      min(max(values["tint"], 0.0), 1.0), 1.0)

    def feed(self, pcm: bytes) -> None:
        """Both channels, as one -- then eased into a breath."""
        samples = np.frombuffer(pcm, dtype="<f4")
        frame_log.debug("SpikeCircle.feed: %d sample(s)", samples.size)
        if self._spectrum is None or samples.size < 2:
            return
        stereo = samples[:samples.size // 2 * 2].reshape(-1, 2)
        self._spectrum.feed(stereo.mean(axis=1, dtype=np.float64))
        seconds = stereo.shape[0] / SOUND_RATE
        self._shown = _ease(self._shown, self._spectrum.heights, seconds,
                            _RISE_S, _FALL_S)
        self._breath = float(_ease(np.array(self._breath),
                                   np.array(self._spectrum.bass), seconds,
                                   _BREATH_IN_S, _BREATH_OUT_S))

    def _bass(self) -> float:
        frame_log.debug("SpikeCircle._bass: %.2f", self._breath)
        return self._breath

    def _paint(self, painter: Any) -> None:
        """The spiked circle, and the picture inside it."""
        frame_log.debug("SpikeCircle._paint: %d spike(s)", self._count)
        if self._spectrum is None:
            return
        cx, cy = self._w / 2, self._h / 2
        room = min(self._w, self._h) / 2 * _REACH
        inner = room * self._inner * (1 + self._breathing * self._breath)
        span = inner * self._spike
        if inner + span > room:                     # big sliders: still fits
            inner, span = (v * room / (inner + span) for v in (inner, span))
        self._picture_in_circle(painter, cx, cy, inner)
        self._draw_edge(painter, cx, cy, inner, span)

    def _draw_edge(self, painter: Any, cx: float, cy: float, inner: float,
                   span: float) -> None:
        """The circle's edge: from each corner on the circle out to its
        spike's tip and back to the next corner.  Drawn as separate lines --
        one stroked polygon of them cost four times as much (4.8 ms against
        1.2 at 1.5 px, 320x320)."""
        from PySide6.QtCore import QLineF, QPointF
        from PySide6.QtGui import QPen
        # Band b is spike b clockwise from the top, and its mirror back round.
        heights = np.concatenate([self._shown, self._shown[::-1]])
        turn = 2 * math.pi / self._count
        corners = -math.pi / 2 + turn * np.arange(self._count + 1)
        tips = corners[:-1] + turn / 2
        radius = inner + span * heights
        cx_, cy_ = cx + inner * np.cos(corners), cy + inner * np.sin(corners)
        tx, ty = cx + radius * np.cos(tips), cy + radius * np.sin(tips)
        lines = []
        for i in range(self._count):
            corner, tip = QPointF(cx_[i], cy_[i]), QPointF(tx[i], ty[i])
            lines += [QLineF(corner, tip), QLineF(tip, QPointF(cx_[i + 1], cy_[i + 1]))]
        painter.setPen(QPen(self._color, self._line))
        painter.drawLines(lines)
        frame_log.debug("SpikeCircle._draw_edge: longest %.0f px",
                        float(radius.max()) - inner)

    def close(self) -> None:
        log.info("SpikeCircle.close")
        super().close()
        self._spectrum = None
