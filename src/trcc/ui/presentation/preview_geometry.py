"""Preview + device orientation geometry — toolkit-free, for the LCD View.

The orientation-driven sizes a device View needs are pure geometry:

    theme (the DisplayService's composed canvas when a theme + profile resolve,
    else the cached canvas swapped for the user orientation, #136).
  * :func:`rotated_lcd_size` — the post-rotation LCD buffer size + the
    is-rotated flag the theme/mask catalogs and preview key off.

Both used to live inline in ``LCDHandler`` (``_composed_preview_size`` /
``_sync_rotation_state``), each hand-rolling the orientation swap that core's
``oriented_resolution`` already owns.  Lifting them leaves the handler a thin
View and makes the rules unit-testable with no Qt — routed through the single
``oriented_resolution`` helper.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from ...core.models import oriented_resolution

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ...core.models import ProductInfo, Theme
    from ...core.protocol import DeviceProfile


class CanvasComposer(Protocol):
    """The one DisplayService method this resolver needs (ISP).

    Typing against the narrow shape — not the whole ``DisplayService`` — is
    what lets the rule be exercised with a trivial fake and no Qt/renderer.
    """

    def composed_canvas_size(
        self, info: ProductInfo, theme: Theme, profile: DeviceProfile | None,
        orientation: int,
    ) -> tuple[int, int]: ...


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
    return orientation in (90, 270), oriented_resolution(canvas_size, orientation)
