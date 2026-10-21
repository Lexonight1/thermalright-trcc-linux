"""The centre picture's fire: the picture red-hot, and flames rising off it.

It belongs to the picture, not to one visual -- the user's call: with the
picture on, a Fire slider; with it off, no fire.  Every visual that shows the
picture burns it the same way through :class:`Fire`.

The flames are the old demoscene fire: heat seeded where things burn rises
two rows a frame, spreads to its neighbours and cools, coloured black, red,
orange, yellow, white.  Worked at half resolution -- flames are blurry -- and
shown at full, a quarter of the work.  TRCC Pipes burns by the same rule
on the graphics card (``_pipes_gl``): a change here is a change there.

Its motion was tuned by eye at 30 frames a second; visuals draw at
``VISUAL_FPS`` (16), so a per-frame keep becomes ``keep ** STEP`` and a
per-frame step ``step * STEP`` (:func:`keep`).
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from trcc.core.logs import per_frame

from ..core.models import VISUAL_FPS

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

TUNED_FPS = 30
STEP = TUNED_FPS / VISUAL_FPS
#: Rows the heat rises a frame, at the fire's half size: two at 30 fps.
RISE = max(1, round(2 * STEP))
#: Embers: what a burning colour turns toward.
_HOT = np.array([1.0, 0.22, 0.05], dtype=np.float32)
#: The flames' colours by heat, 0-255: black, red, orange, yellow, white --
#: one table, so a frame looks colours up instead of working them out.
_LEVELS = np.linspace(0.0, 1.0, 256, dtype=np.float32)[:, None]
_PALETTE = (np.clip(np.concatenate([_LEVELS * 3, _LEVELS * 3 - 1,
                                    _LEVELS * 3 - 2], axis=1), 0, 1)
            * 255).astype(np.uint8)


def keep(kept: float) -> float:
    """What is kept of a value per frame now, given *kept* at 30 fps."""
    frame_log.debug("keep: %.4f at %d fps", kept, VISUAL_FPS)
    return kept ** STEP


def burn(rgb: np.ndarray, heat: float) -> np.ndarray:
    """*rgb* (floats 0-1) glowing red-hot as far as *heat* (0-1): its
    brightness kept, its colour turned to embers."""
    frame_log.debug("burn: %d pixel(s) at %.2f", rgb.size // 3, heat)
    glow = rgb.mean(axis=-1, keepdims=True) * 0.7 + 0.3
    return rgb * (1 - heat) + _HOT * glow * heat * 1.25


def burned_picture(picture: Any, heat: float) -> Any:
    """A red-hot copy of *picture* (a ``QImage``) -- worked out once, when
    the picture or the fire changes, never per frame."""
    from PySide6.QtGui import QImage
    if heat <= 0:
        log.debug("burned_picture: no fire -- as it is")
        return picture
    source = picture.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = source.width(), source.height()
    rgba = np.frombuffer(source.constBits(), dtype=np.uint8).reshape(
        h, source.bytesPerLine())[:, :w * 4].reshape(h, w, 4)
    hot = burn(rgba[..., :3] / np.float32(255), heat)
    pixels = np.ascontiguousarray(np.concatenate(
        [np.clip(hot * 255, 0, 255).astype(np.uint8), rgba[..., 3:]], axis=2))
    log.debug("burned_picture: %dx%d red-hot at %.2f", w, h, heat)
    return QImage(pixels.data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()


class Fire:
    """The flames of one visual: heat that persists from frame to frame."""

    def __init__(self, width: int, height: int) -> None:
        log.debug("Fire: %dx%d", width, height)
        self._w, self._h = width, height
        self._heat: np.ndarray | None = None

    @property
    def half(self) -> tuple[int, int]:
        """The fire's own size, (height, width): half the visual's."""
        frame_log.debug("Fire.half: of %dx%d", self._w, self._h)
        return (self._h + 1) // 2, (self._w + 1) // 2

    def flames(self, seed: np.ndarray, strength: float, bass: float) -> np.ndarray:
        """This frame's flames, full size, RGB bytes: heat added where
        *seed* (half size, 0-1) burns -- harder with the *bass* -- risen,
        spread and cooled, as bright as *strength*."""
        frame_log.debug("Fire.flames: strength %.2f bass %.2f", strength, bass)
        heat = self._heat
        if heat is None or heat.shape != seed.shape:
            heat = np.zeros_like(seed)
        flicker = np.random.random(seed.shape).astype(np.float32)
        heat = np.maximum(heat, seed * (0.6 + 0.4 * flicker)
                          * (0.7 + 0.5 * min(bass, 1.3)))
        rise = np.roll(heat, -RISE, axis=0)
        rise[-RISE:] = 0
        spread = (rise + np.roll(rise, 1, axis=1) + np.roll(rise, -1, axis=1)
                  + np.roll(rise, -1, axis=0)) / 4.0
        self._heat = heat = spread * (0.955 - 0.03 * flicker) ** STEP
        level = (np.clip(heat * (1.4 * strength), 0.0, 1.0) * 255).astype(np.uint8)
        colour = _PALETTE[level]
        return colour.repeat(2, axis=0).repeat(2, axis=1)[:self._h, :self._w]

    def seed_from(self, paths: list[Any]) -> np.ndarray:
        """Where shapes burn, at the fire's half size: *paths* (full-size
        ``QPainterPath``) filled, 0-1."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage, QPainter
        height, width = self.half
        mask = QImage(width, height, QImage.Format.Format_Alpha8)
        mask.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mask)
        painter.scale(0.5, 0.5)
        for path in paths:
            painter.fillPath(path, QColor(255, 255, 255))
        painter.end()
        alpha = np.frombuffer(mask.constBits(), dtype=np.uint8)
        frame_log.debug("Fire.seed_from: %d path(s)", len(paths))
        return alpha.reshape(height, mask.bytesPerLine())[:, :width] / np.float32(255)
