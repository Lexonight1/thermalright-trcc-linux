"""Pure tests for LcdPresentationModel (no Qt).

B1 establishes the Qt-free coordination model + relocates the DeviceState cache.
These pin the value-object defaults + that the model is constructed App-free /
Qt-free (it imports nothing but stdlib here — a QApplication is never created).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from trcc.core.models import ProductInfo, Theme
from trcc.core.protocol import DeviceProfile
from trcc.ui.presentation.lcd_presentation_model import (
    DeviceState,
    LcdPresentationModel,
)

# Opaque stand-ins — the model only checks None-ness + forwards them.
_INFO = cast(ProductInfo, object())
_THEME = cast(Theme, object())
_PROFILE = cast(DeviceProfile, object())


def test_device_state_defaults() -> None:
    s = DeviceState()
    assert s.canvas_size == (0, 0)
    assert s.lcd_size == (0, 0)
    assert s.is_rotated is False
    assert s.overlay_enabled is False
    assert s.current_theme_path is None
    assert s.last_metrics is None


def test_model_holds_key_and_fresh_state() -> None:
    pm = LcdPresentationModel("0402:3922")
    assert pm.device_key == "0402:3922"
    assert isinstance(pm.state, DeviceState)
    assert pm.state.canvas_size == (0, 0)


def test_state_is_mutable_per_instance() -> None:
    """The View writes the cache through the model; instances don't share state."""
    a = LcdPresentationModel("0402:3922")
    b = LcdPresentationModel("87ad:70db")
    a.state.canvas_size = (854, 480)
    a.state.is_rotated = True
    a.state.current_theme_path = Path("/themes/Theme1")

    assert a.state.canvas_size == (854, 480)
    assert b.state.canvas_size == (0, 0)        # independent
    assert b.state.is_rotated is False


# ── Geometry (B2) ─────────────────────────────────────────────────────


def test_set_canvas_seeds_both_sizes() -> None:
    pm = LcdPresentationModel("0402:3922")
    pm.set_canvas(854, 480)
    assert pm.state.canvas_size == (854, 480)
    assert pm.state.lcd_size == (854, 480)      # seeded equal pre-rotation
    assert pm.state.is_rotated is False


def test_apply_rotation_portrait_swaps_lcd_size_and_flags() -> None:
    pm = LcdPresentationModel("0402:3922")
    pm.set_canvas(854, 480)

    pm.apply_rotation(90)
    assert pm.state.is_rotated is True
    assert pm.state.lcd_size == (480, 854)      # swapped
    assert pm.state.canvas_size == (854, 480)   # canvas unchanged

    pm.apply_rotation(0)
    assert pm.state.is_rotated is False
    assert pm.state.lcd_size == (854, 480)       # restored


class _FakeComposer:
    def __init__(self, result: tuple[int, int]) -> None:
        self._result = result
        self.calls = 0

    def composed_canvas_size(
        self, info: Any, theme: Any, profile: Any, orientation: int,
    ) -> tuple[int, int]:
        self.calls += 1
        return self._result


def test_preview_size_uses_cached_canvas_in_fallback() -> None:
    """No theme → the model swaps its OWN cached canvas (composer untouched)."""
    pm = LcdPresentationModel("0402:3922")
    pm.set_canvas(854, 480)
    composer = _FakeComposer((0, 0))

    size = pm.preview_size(
        composer, info=None, theme=None, profile=None, orientation=90,
    )
    assert size == (480, 854)
    assert composer.calls == 0


def test_preview_size_defers_to_composer_when_themed() -> None:
    pm = LcdPresentationModel("0402:3922")
    pm.set_canvas(854, 480)
    composer = _FakeComposer((480, 854))

    size = pm.preview_size(
        composer, info=_INFO, theme=_THEME, profile=_PROFILE, orientation=90,
    )
    assert size == (480, 854)
    assert composer.calls == 1
