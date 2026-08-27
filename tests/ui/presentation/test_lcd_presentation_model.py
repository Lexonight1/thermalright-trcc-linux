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


def test_activation_flag_defaults() -> None:
    """B3: the model owns the activation/view-lifecycle flags at their defaults."""
    pm = LcdPresentationModel("0402:3922")
    assert pm.ui_active is False
    assert pm.configured is False
    assert pm.background_active is False
    assert pm.brightness_level == 100
    assert pm.split_mode == 0
    assert pm.ldd_is_split is False


def test_activation_flags_are_per_instance() -> None:
    a = LcdPresentationModel("0402:3922")
    b = LcdPresentationModel("87ad:70db")
    a.ui_active = True
    a.configured = True
    a.brightness_level = 40
    assert (b.ui_active, b.configured, b.brightness_level) == (False, False, 100)


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


# ── Video math (B5) ───────────────────────────────────────────────────


def test_video_interval_ms() -> None:
    pm = LcdPresentationModel("87ad:70db")
    assert pm.video_interval_ms(None) == 33      # no playback → 30fps fallback
    assert pm.video_interval_ms(0) == 33          # fps 0 → 30fps fallback
    assert pm.video_interval_ms(30) == 33
    assert pm.video_interval_ms(60) == 16
    assert pm.video_interval_ms(1000) == 1        # clamped to >= 1ms


def test_seek_frame_clamps() -> None:
    pm = LcdPresentationModel("87ad:70db")
    assert pm.seek_frame(0.0, 100) == 0
    assert pm.seek_frame(0.5, 100) == 50
    assert pm.seek_frame(1.0, 100) == 99          # last valid index, not 100
    assert pm.seek_frame(-0.2, 100) == 0          # below range clamps to 0
    assert pm.seek_frame(2.0, 100) == 99          # above range clamps to last


def test_progress_fraction() -> None:
    pm = LcdPresentationModel("87ad:70db")
    assert pm.progress_fraction(0, 100) == 0.0
    assert pm.progress_fraction(50, 100) == 0.5
    assert pm.progress_fraction(5, 0) == 0.0       # no frames → 0.0, no ZeroDiv


# ── Split-mode policy (B4) ────────────────────────────────────────────


def test_apply_split_mode_split_capable_panel() -> None:
    """A split-capable resolution: ldd_is_split True, dispatch the chosen mode."""
    pm = LcdPresentationModel("87ad:70db")
    mode = pm.apply_split_mode(3, (1920, 462))      # a SPLIT_MODE resolution
    assert pm.ldd_is_split is True
    assert pm.split_mode == 3
    assert mode == 3


def test_apply_split_mode_defaults_to_two_when_unset() -> None:
    pm = LcdPresentationModel("87ad:70db")
    mode = pm.apply_split_mode(0, (1280, 480))       # persisted 0 → default 2
    assert pm.split_mode == 2
    assert pm.ldd_is_split is True
    assert mode == 2


def test_apply_split_mode_non_split_panel_dispatches_zero() -> None:
    """A non-split resolution: ldd_is_split False, dispatch mode 0."""
    pm = LcdPresentationModel("0402:3922")
    mode = pm.apply_split_mode(3, (320, 320))         # not a split resolution
    assert pm.ldd_is_split is False
    assert pm.split_mode == 3                          # still recorded
    assert mode == 0                                   # but 0 is sent


class _FakeComposer:
    def __init__(self, result: tuple[int, int]) -> None:
        self._result = result
        self.calls = 0

    def composed_canvas_size(
        self, info: Any, theme: Any, profile: Any, orientation: int,
    ) -> tuple[int, int]:
        self.calls += 1
        return self._result


# ``preview_size`` moved off the model and became the ``PreviewSize`` Query
# (the View was gathering four domain objects to feed it).  Its cases live
# in ``tests/test_preview_size.py``, including the non-square swap these
# two could not show.
