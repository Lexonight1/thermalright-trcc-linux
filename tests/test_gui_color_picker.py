"""The overlay colour editor's inline picker -- the C#'s UCColorB + UCColorC.

The first commit put a click target that opened a modal QColorDialog where
the C# has a hue strip driving a colour square; a 2026-02-17 audit called
them "NOT NEEDED" and the note was deleted the next day.  The maths here is
the C#'s, in its own integer arithmetic.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt

from trcc.ui.gui.color_and_add_panels import (
    ColorPickerPanel,
    color_square_rgb,
    hue_strip_rgb,
)

# UCColorB: 158 px wide, an 8 px thumb -> 150 px of travel, 25 per segment.
W, THUMB = 158, 8


@pytest.mark.parametrize(("center_x", "rgb"), [
    (4, (255, 0, 0)),              # left end: red
    (4 + 25, (255, 255, 0)),       # end of segment 0: yellow
    (4 + 50, (0, 255, 0)),         # green
    (4 + 75, (0, 255, 255)),       # cyan
    (4 + 100, (0, 0, 255)),        # blue
    (4 + 125, (255, 0, 255)),      # magenta
    (4 + 150, (255, 0, 0)),        # right end: red again
    (4 + 10, (255, 102, 0)),       # 255 * 10 // 25 = 102, as the C# ints
])
def test_the_hue_strip_is_the_csharps_six_segments(center_x, rgb) -> None:
    assert hue_strip_rgb(center_x, W, THUMB) == rgb


def test_the_square_fades_to_white_across_and_black_down() -> None:
    red = (255, 0, 0)
    assert color_square_rgb(red, 0, 0, 206, 128) == (255, 0, 0)
    assert color_square_rgb(red, 205, 0, 206, 128) == (255, 255, 255)
    assert color_square_rgb(red, 0, 127, 206, 128) == (0, 0, 0)
    # Truncating at each step, as ColorToBitmap's (int) casts do.
    assert color_square_rgb((0, 255, 255), 56, 36, 206, 128) == (49, 182, 182)


def test_the_hue_strip_rebases_the_square_and_a_square_pick_does_not(qtbot) -> None:
    panel = ColorPickerPanel()
    qtbot.addWidget(panel)
    picked: list[tuple[int, int, int]] = []
    panel.color_changed.connect(lambda r, g, b: picked.append((r, g, b)))

    qtbot.mouseClick(panel.hue_strip, Qt.MouseButton.LeftButton,
                     pos=QPoint(79, 9))
    assert picked[-1] == (0, 255, 255)
    assert panel.color_square._base == (0, 255, 255)

    qtbot.mouseClick(panel.color_square, Qt.MouseButton.LeftButton,
                     pos=QPoint(60, 40))
    assert picked[-1] == (49, 182, 182)
    assert panel.color_square._base == (0, 255, 255), "a pick moved the hue"
    assert (panel.r_input.text(), panel.g_input.text(),
            panel.b_input.text()) == ("49", "182", "182")


def test_a_colour_set_from_outside_becomes_the_squares_hue(qtbot) -> None:
    panel = ColorPickerPanel()
    qtbot.addWidget(panel)
    panel.set_color(10, 20, 30)
    assert panel.color_square._base == (10, 20, 30)
