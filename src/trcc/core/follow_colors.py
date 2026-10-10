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

Pure Python over the ``Renderer.raw_argb32`` bytes, and bounded: a region is
read at most ``SAMPLES`` pixels across and down whatever the panel's size.
"""
from __future__ import annotations

import logging
import math
import sys

from .logs import per_frame
from .models import FollowColors

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
_RGB_AT = (2, 1, 0) if sys.byteorder == "little" else (1, 2, 3)
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
    pick = _brightest if how is FollowColors.VIVID else _average
    out = []
    for x, span in strip_spans(width, columns):
        out.append(tuple(
            pick(_region(pixels, stride, x, span, height * r // rows,
                         height * (r + 1) // rows))
            for r in range(rows)))
    return tuple(out)


def for_leds(colors: tuple[Rgb, ...]) -> tuple[Rgb, ...]:
    """Screen colours as the light an LED must give out to look the same."""
    frame_log.debug("for_leds: %d colour(s)", len(colors))
    levels = _LED_LEVELS
    return tuple((levels[r], levels[g], levels[b]) for r, g, b in colors)


def _region(pixels: bytes, stride: int, x: int, width: int, top: int,
            bottom: int) -> list[Rgb]:
    """The pixels of one region, read on a grid of at most ``SAMPLES``."""
    frame_log.debug("_region: x=%d w=%d y=%d-%d", x, width, top, bottom)
    step_x = max(1, math.ceil(width / SAMPLES[0]))
    step_y = max(1, math.ceil((bottom - top) / SAMPLES[1]))
    r_at, g_at, b_at = _RGB_AT
    step = 4 * step_x
    found: list[Rgb] = []
    for y in range(top, max(bottom, top + 1), step_y):
        line = pixels[y * stride + 4 * x:y * stride + 4 * (x + width)]
        found.extend(zip(line[r_at::step], line[g_at::step], line[b_at::step],
                         strict=True))
    return found


def _average(region: list[Rgb]) -> Rgb:
    frame_log.debug("_average: %d", len(region))
    n = len(region) or 1
    return (sum(p[0] for p in region) // n, sum(p[1] for p in region) // n,
            sum(p[2] for p in region) // n)


def _brightest(region: list[Rgb]) -> Rgb:
    frame_log.debug("_brightest: %d", len(region))
    keep = max(1, round(len(region) * VIVID_SHARE))
    return _average(sorted(region, key=sum, reverse=True)[:keep])
