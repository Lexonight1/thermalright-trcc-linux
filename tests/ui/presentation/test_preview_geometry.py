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
from trcc.ui.presentation.preview_geometry import composed_preview_size

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


def test_composed_path_defers_to_display() -> None:
    """Device + theme + profile present → the DisplayService composes the size."""
    display = _FakeDisplay((480, 854))

    size = composed_preview_size(
        display, info=_INFO, theme=_THEME, profile=_PROFILE,
        orientation=90, canvas_size=(854, 480),
    )

    assert size == (480, 854)
    assert len(display.calls) == 1            # display did the work
    assert display.calls[0][3] == 90          # orientation forwarded


def test_fallback_swaps_canvas_for_portrait_orientation() -> None:
    """No theme → cached canvas swapped for 90/270 (oriented_resolution), and
    the display is NOT consulted."""
    display = _FakeDisplay((0, 0))

    size = composed_preview_size(
        display, info=_INFO, theme=None, profile=_PROFILE,
        orientation=270, canvas_size=(854, 480),
    )

    assert size == (480, 854)                 # swapped
    assert display.calls == []                # fallback, display untouched


def test_fallback_landscape_keeps_canvas() -> None:
    """No device (pre-handshake) at landscape orientation → canvas unchanged."""
    display = _FakeDisplay((0, 0))

    size = composed_preview_size(
        display, info=None, theme=None, profile=None,
        orientation=0, canvas_size=(854, 480),
    )

    assert size == (854, 480)
    assert display.calls == []


def test_fallback_square_panel_unaffected_by_orientation() -> None:
    """A square canvas with no theme is identical at every orientation."""
    display = _FakeDisplay((0, 0))

    for deg in (0, 90, 180, 270):
        assert composed_preview_size(
            display, info=None, theme=None, profile=None,
            orientation=deg, canvas_size=(320, 320),
        ) == (320, 320)
