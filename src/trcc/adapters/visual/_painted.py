"""A visual painted with Qt on the CPU, with the user's centre picture.

What every painted visual shares, written once: the frame and its
background, the user's picture (covering an area, or in a circle), and the
picture's fire -- flames rising from wherever the visual says it burns.  A
visual says what to ``_paint`` each frame and how hard its bass hits.
Cavalier's modes and the Spike Circle are painted visuals; every OS draws
them, no OpenGL needed.
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, ClassVar

import numpy as np

from ...core.logs import per_frame
from ...core.models import RawFrame, VisualSpec
from ._base import BaseVisual
from ._fire import Fire, burned_picture

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class PaintedVisual(BaseVisual):
    """A frame painted over ``BACKGROUND``, the picture's fire behind it."""

    BACKGROUND: ClassVar[tuple[int, int, int]] = (0, 0, 0)

    def __init__(self) -> None:
        log.debug("%s.__init__", type(self).__name__)
        self._w = self._h = 0
        self._picture: Any = None
        self._fire = 0.0
        self._flames = Fire(0, 0)
        self._seed = np.zeros((0, 0), dtype=np.float32)
        #: The shapes the fire rises from this frame -- ``_paint`` adds them.
        self._burning: list[Any] = []

    def open(self, spec: VisualSpec) -> None:
        from PySide6.QtGui import QImage
        log.info("%s.open: %dx%d picture=%s settings=%s", type(self).__name__,
                 spec.width, spec.height, spec.center, dict(spec.settings))
        self._w, self._h = spec.width, spec.height
        self.adjust(spec.settings)
        if spec.center is not None and spec.center.is_file():
            image = QImage(str(spec.center))
            self._picture = None if image.isNull() else image
        # The fire is the picture's (the user's call): no picture, no fire.
        self._fire = spec.center_fire if self._picture is not None else 0.0
        if self._picture is not None:
            self._picture = burned_picture(self._picture, self._fire)
        self._flames = Fire(self._w, self._h)
        self._seed = np.zeros(self._flames.half, dtype=np.float32)

    def render(self) -> RawFrame:
        from PySide6.QtGui import QImage, QPainter
        frame_log.debug("%s.render", type(self).__name__)
        frame = np.empty((self._h, self._w, 3), dtype=np.uint8)
        frame[:] = self.BACKGROUND
        if self._fire > 0:
            # Behind the shapes: flames rising from where the picture was.
            np.maximum(frame, self._flames.flames(self._seed, self._fire,
                                                  self._bass()), out=frame)
        image = QImage(frame.data, self._w, self._h, 3 * self._w,
                       QImage.Format.Format_RGB888)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._burning = []
        self._paint(painter)
        painter.end()
        if self._fire > 0:
            self._seed = self._flames.seed_from(self._burning)
        return RawFrame(data=frame.tobytes(), width=self._w, height=self._h)

    @abstractmethod
    def _paint(self, painter: Any) -> None:
        """This frame's shapes, on *painter* (antialiased, the full frame)."""

    @abstractmethod
    def _bass(self) -> float:
        """How hard the bass hits now, 0 to 1 -- it feeds the fire."""

    def _picture_in_circle(self, painter: Any, cx: float, cy: float,
                           radius: float) -> None:
        """The user's picture inside the circle at (*cx*, *cy*) -- and the
        circle burns."""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainterPath
        if self._picture is None:
            frame_log.debug("_picture_in_circle: no picture")
            return
        circle = QPainterPath()
        circle.addEllipse(QPointF(cx, cy), radius, radius)
        self._burning.append(circle)
        painter.save()
        painter.setClipPath(circle)
        self._draw_picture(painter, cx - radius, cy - radius, 2 * radius,
                           2 * radius)
        painter.restore()
        frame_log.debug("_picture_in_circle: radius %.0f", radius)

    def _draw_picture(self, painter: Any, x: float, y: float, w: float,
                      h: float) -> None:
        """The picture covering the area, centred (scale = the larger of the
        two ratios)."""
        from PySide6.QtCore import QRectF
        picture = self._picture
        scale = max(w / picture.width(), h / picture.height())
        pw, ph = picture.width() * scale, picture.height() * scale
        frame_log.debug("_draw_picture: %.0fx%.0f", pw, ph)
        painter.drawImage(QRectF(x + (w - pw) / 2, y + (h - ph) / 2, pw, ph),
                          picture)

    def close(self) -> None:
        log.info("%s.close", type(self).__name__)
        self._picture = None
