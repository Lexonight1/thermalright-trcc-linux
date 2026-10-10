"""An LCD's picture as the colours of the lights that follow it -- the pure
sampler, and its agreement with the Qt renderer's real pixels."""
from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from trcc.core.models import FollowColors
from trcc.services.follow_colors import (
    SAMPLES,
    for_leds,
    frame_columns,
    regions,
    strip_spans,
)

RED, YELLOW, BLACK = (150, 0, 10), (255, 220, 0), (0, 0, 0)


def _frame(width: int, height: int,
           paint: Callable[[int, int], tuple[int, int, int]]) -> tuple[bytes, int]:
    """ARGB32 bytes in the host's native order, as Qt lays them out."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            r, g, b = paint(x, y)
            out += (0xFF000000 | r << 16 | g << 8 | b).to_bytes(4, sys.byteorder)
    return bytes(out), width * 4


def test_two_lights_take_the_two_edges_and_one_the_whole_width() -> None:
    assert strip_spans(320, 2) == ((0, 16), (304, 16))
    assert strip_spans(320, 3) == ((0, 16), (152, 16), (304, 16))
    assert strip_spans(320, 1) == ((0, 320),)


def test_each_led_takes_its_own_region_top_to_bottom() -> None:
    # Left edge red over black, right edge black over red.
    pixels, stride = _frame(40, 20, lambda x, y: (
        (RED if y < 10 else BLACK) if x < 20 else (BLACK if y < 10 else RED)))
    left, right = frame_columns(pixels, 40, 20, stride, 2, 2,
                                FollowColors.SMOOTH)
    assert left == (RED, BLACK) and right == (BLACK, RED)


def test_vivid_keeps_a_small_bright_star_that_smooth_drowns() -> None:
    # A 5x5 yellow star: 25 of the region's 512 pixels.  VIVID averages the
    # brightest 51 -- the 25 star pixels among them -- SMOOTH all 512.
    pixels, stride = _frame(16, 32, lambda x, y: (
        YELLOW if 5 <= x <= 9 and 13 <= y <= 17 else RED))
    (smooth,), = frame_columns(pixels, 16, 32, stride, 1, 1,
                               FollowColors.SMOOTH)
    (vivid,), = frame_columns(pixels, 16, 32, stride, 1, 1, FollowColors.VIVID)
    assert smooth[1] == 25 * 220 // 512    # 10: lost in the average
    assert vivid[1] == 25 * 220 // 51      # 107: kept among the brightest


def test_a_big_panel_is_read_on_a_bounded_grid() -> None:
    """A 1600x720 strip is 80 px wide and 72 px a region: read on a grid of
    at most 16 x 32, whatever the panel."""
    import numpy as np
    pixels, stride = _frame(1600, 720, lambda x, y: RED)
    picture = np.frombuffer(pixels, dtype=np.uint8).reshape(720, 1600, 4)
    (x, span), _ = strip_spans(1600, 2)
    sampled = regions(picture[:, x:x + span], 10)
    assert sampled.shape == (10, 16 * 24, 3)         # 80/5 across, 72/3 down
    assert sampled.shape[1] <= SAMPLES[0] * SAMPLES[1]
    assert frame_columns(pixels, 1600, 720, stride, 2, 10,
                         FollowColors.VIVID) == ((RED,) * 10,) * 2


def test_screen_colours_are_ungamma_d_for_the_leds() -> None:
    # The pair compared on the maintainer's sticks: (82, 1, 1) looked like
    # the video's dark red; (152, 18, 23) sent raw looked pinkish white.
    assert for_leds(((152, 18, 23), (0, 0, 0), (255, 255, 255))) == (
        (82, 1, 1), (0, 0, 0), (255, 255, 255))


@pytest.mark.parametrize("rgb", [(152, 18, 23), (0, 128, 255), (255, 0, 0)])
def test_the_qt_renderers_pixels_are_read_in_the_right_order(
        rgb: tuple[int, int, int]) -> None:
    from PySide6.QtGui import QColor, QImage

    from trcc.adapters.render.qt import QtRenderer
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor(*rgb))
    pixels, width, height, stride = QtRenderer().raw_argb32(image)
    assert frame_columns(pixels, width, height, stride, 2, 10,
                         FollowColors.SMOOTH) == ((rgb,) * 10,) * 2
