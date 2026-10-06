"""Device orientation geometry — toolkit-free, for the LCD View.

:func:`rotated_lcd_size` gives the post-rotation LCD buffer size and the
is-rotated flag the theme/mask catalogs and the preview key off.

It used to live inline in ``LCDHandler`` (``_sync_rotation_state``),
hand-rolling the orientation swap that core's ``oriented_resolution`` already
owns.  Lifting it leaves the handler a thin View and makes the rule
unit-testable with no Qt.  (Its sibling, the composed preview size, went to
``DisplayService.composed_canvas_size``; the Protocol typing it here was left
behind until 2026-10-06.)
"""
from __future__ import annotations

import logging

from ...core.models import oriented_resolution

log = logging.getLogger(__name__)


def rotated_lcd_size(
    canvas_size: tuple[int, int], orientation: int,
) -> tuple[bool, tuple[int, int]]:
    """``(is_rotated, post-rotation lcd size)`` for a user orientation.

    The GUI caches this off the device's pre-rotation canvas: at 90/270 the LCD
    buffer is the canvas with width/height swapped (``oriented_resolution`` —
    the single source of the swap), and ``is_rotated`` drives the theme/mask
    catalog + preview portrait selection.  Square panels swap to themselves, so
    only non-square panels actually change.
    """
    log.debug("rotated_lcd_size: canvas_size=%s orientation=%s", canvas_size, orientation)
    return orientation in (90, 270), oriented_resolution(canvas_size, orientation)
