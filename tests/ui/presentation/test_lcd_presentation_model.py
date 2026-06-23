"""Pure tests for LcdPresentationModel (no Qt).

B1 establishes the Qt-free coordination model + relocates the DeviceState cache.
These pin the value-object defaults + that the model is constructed App-free /
Qt-free (it imports nothing but stdlib here — a QApplication is never created).
"""
from __future__ import annotations

from pathlib import Path

from trcc.ui.presentation.lcd_presentation_model import (
    DeviceState,
    LcdPresentationModel,
)


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
