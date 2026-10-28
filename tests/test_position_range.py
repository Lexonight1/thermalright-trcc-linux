"""The X / Y inputs follow the canvas, not a fixed 480."""
from __future__ import annotations

from trcc.ui.gui.color_and_add_panels import ColorPickerPanel


def test_position_range_follows_canvas(qtbot):
    panel = ColorPickerPanel()
    qtbot.addWidget(panel)
    panel.set_position_range(462, 1920)
    panel.set_position(34, 1690)
    assert (panel.x_spin.maximum(), panel.y_spin.maximum()) == (462, 1920)
    assert (panel.x_spin.value(), panel.y_spin.value()) == (34, 1690)
