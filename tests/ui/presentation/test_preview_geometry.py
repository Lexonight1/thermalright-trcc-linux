"""Pure tests for preview-geometry resolution (no Qt).

Pins the compose-vs-fallback rule that used to be inline in
``LCDHandler._composed_preview_size``: defer to the DisplayService's composed
canvas when a device + theme are present, else swap the cached canvas for the
user orientation via ``oriented_resolution``.
"""
from __future__ import annotations

from typing import Any, cast

from trcc.core.models import ProductInfo, Theme
from trcc.core.protocol import DeviceProfile
from trcc.ui.presentation.preview_geometry import (
    rotated_lcd_size,
)

# Opaque stand-ins — the resolver only checks None-ness and forwards them to the
# composer, so their identity is all that matters (the fake display ignores them).
_INFO = cast(ProductInfo, object())
_THEME = cast(Theme, object())
_PROFILE = cast(DeviceProfile, object())


class _FakeDisplay:
    """Records the composed_canvas_size call and returns a sentinel size."""

    def __init__(self, result: tuple[int, int]) -> None:
        self._result = result
        self.calls: list[tuple[Any, Any, Any, int]] = []

    def composed_canvas_size(self, info: Any, theme: Any, profile: Any,
                             orientation: int) -> tuple[int, int]:
        self.calls.append((info, theme, profile, orientation))
        return self._result


# The ``composed_preview_size`` cases moved to
# ``tests/test_preview_size.py`` when that rule became the ``PreviewSize``
# Query — the gui was gathering four domain objects to compute it.


# ── rotated_lcd_size — (is_rotated, post-rotation lcd size) ───────────


def test_rotated_lcd_size_landscape_unchanged() -> None:
    assert rotated_lcd_size((854, 480), 0) == (False, (854, 480))
    assert rotated_lcd_size((854, 480), 180) == (False, (854, 480))


def test_rotated_lcd_size_portrait_swaps_and_flags() -> None:
    assert rotated_lcd_size((854, 480), 90) == (True, (480, 854))
    assert rotated_lcd_size((854, 480), 270) == (True, (480, 854))


def test_rotated_lcd_size_square_flags_rotated_but_size_unchanged() -> None:
    """A square panel at 90/270 is flagged rotated but swaps to itself."""
    assert rotated_lcd_size((320, 320), 90) == (True, (320, 320))
    assert rotated_lcd_size((320, 320), 0) == (False, (320, 320))
