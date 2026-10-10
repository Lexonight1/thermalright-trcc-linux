"""An LCD's picture as the colours of the lights that follow it (#160).

Each light is a column of LEDs, so it takes a thin strip of the picture: with
two lights, the left edge and the right edge, as TV ambient lighting does;
with more, strips spread evenly from edge to edge; with one, the whole width.
Each strip is cut into one region per LED, top to bottom, and each region
gives one colour -- its brightest pixels (``VIVID``) or its average
(``SMOOTH``).

The picture's colours are made for a screen, which is gamma-encoded: a value
of 32 means far less light than an eighth of full.  An LED gives out exactly
the share it is sent, so a deep red with a little green and blue in it came
out pinkish white on Corsair RAM.  ``for_leds`` undoes the encoding first.

Measured on the maintainer's Vengeance DDR5 (2026-10-10) against a red nebula
video: a 16-px edge strip of a 320-px panel, the brightest tenth, gamma 2.2.

Read from the ``Renderer.raw_argb32`` bytes with numpy, and bounded: a region
is read at most ``SAMPLES`` pixels across and down whatever the panel's size.
"""
from __future__ import annotations

import logging
import sys

import numpy as np

from ..core.logs import per_frame
from ..core.models import FollowColors

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

Rgb = tuple[int, int, int]

#: A strip's width, as a share of the picture's: 16 px of a 320-px panel.
STRIP_SHARE = 0.05
#: The share of a region's pixels -- the brightest -- that VIVID averages.
VIVID_SHARE = 0.10
#: Pixels read across and down one region, at most.
SAMPLES = (16, 32)
#: The screen's gamma, undone before a colour reaches an LED.
GAMMA = 2.2

#: Byte offsets of red, green and blue in one ARGB32 pixel as Qt lays it out
#: in memory -- the native 32-bit word 0xAARRGGBB.
_RGB_AT = [2, 1, 0] if sys.byteorder == "little" else [1, 2, 3]
_LED_LEVELS = tuple(round(255 * (v / 255) ** GAMMA) for v in range(256))


def strip_spans(width: int, columns: int) -> tuple[tuple[int, int], ...]:
    """Each column's ``(x, width)`` in a picture *width* wide."""
    log.debug("strip_spans: width=%d columns=%d", width, columns)
    if columns <= 1:
        return ((0, width),)
    strip = max(1, round(width * STRIP_SHARE))
    return tuple((round(i * (width - strip) / (columns - 1)), strip)
                 for i in range(columns))


def frame_columns(pixels: bytes, width: int, height: int, stride: int,
                  columns: int, rows: int, how: FollowColors
                  ) -> tuple[tuple[Rgb, ...], ...]:
    """The picture as *columns* columns of *rows* colours, top to bottom.

    *pixels* are ARGB32, *stride* bytes to a line.  Screen colours: pass them
    through ``for_leds`` before an LED shows them.
    """
    frame_log.debug("frame_columns: %dx%d %d column(s) %s", width, height,
                    columns, how.value)
    lines = np.frombuffer(pixels, dtype=np.uint8,
                          count=stride * height).reshape(height, stride)
    picture = lines[:, :4 * width].reshape(height, width, 4)     # a view
    pick = _brightest if how is FollowColors.VIVID else _average
    return tuple(
        tuple((r, g, b) for r, g, b in
              pick(regions(picture[:, x:x + span], rows)).tolist())
        for x, span in strip_spans(width, columns))


def regions(strip: np.ndarray, rows: int) -> np.ndarray:
    """A strip's *rows* regions, top to bottom: ``(rows, n, 3)`` red, green
    and blue, each region read on a grid of at most ``SAMPLES`` pixels."""
    height, span = strip.shape[:2]
    tall = max(1, height // rows)
    frame_log.debug("regions: %dx%d -> %d of %d px", span, height, rows, tall)
    ys = (np.arange(rows)[:, None] * height // rows
          + np.arange(0, tall, -(-tall // SAMPLES[1]))[None, :])
    xs = np.arange(0, span, -(-span // SAMPLES[0]))
    picked = strip[np.minimum(ys, height - 1)[:, :, None], xs[None, None, :]]
    return picked[..., _RGB_AT].reshape(rows, -1, 3).astype(np.int32)


def for_leds(colors: tuple[Rgb, ...]) -> tuple[Rgb, ...]:
    """Screen colours as the light an LED must give out to look the same."""
    frame_log.debug("for_leds: %d colour(s)", len(colors))
    levels = _LED_LEVELS
    return tuple((levels[r], levels[g], levels[b]) for r, g, b in colors)


def _average(regions_: np.ndarray) -> np.ndarray:
    """Each region's mean colour: ``(rows, 3)``."""
    frame_log.debug("_average: %s", regions_.shape)
    return regions_.sum(axis=1) // max(1, regions_.shape[1])


def _brightest(regions_: np.ndarray) -> np.ndarray:
    """Each region's brightest ``VIVID_SHARE`` of pixels, by r + g + b,
    averaged: ``(rows, 3)``.  Ties stay in reading order, as a stable sort
    keeps them."""
    frame_log.debug("_brightest: %s", regions_.shape)
    keep = max(1, round(regions_.shape[1] * VIVID_SHARE))
    order = np.argsort(-regions_.sum(axis=2), axis=1, kind="stable")[:, :keep]
    return np.take_along_axis(regions_, order[:, :, None], axis=1).sum(
        axis=1) // keep
