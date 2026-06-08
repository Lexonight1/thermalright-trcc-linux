"""View↔PM binding seam — the one place pytest-qt earns its keep.

The bulk of the overlay logic is tested Qt-free in ``test_overlay_model.py``.
This file verifies only the thin seam: that ``OverlayGridPanel`` (the View)
delegates to its ``OverlayModel`` and still emits the same Qt signals its
consumers (``uc_theme_setting``, the handler, the window) rely on.  This is
exactly what ``qtbot.waitSignal`` is for — proving a real Qt signal fires.
"""
from __future__ import annotations

from trcc.core.models import OverlayElementConfig, OverlayMode
from trcc.ui.gui.overlay_grid import OverlayGridPanel


def _cfg(text: str) -> OverlayElementConfig:
    return OverlayElementConfig(mode=OverlayMode.CUSTOM, text=text, x=10, y=20)


def test_add_element_emits_elements_changed_and_updates_model(qtbot) -> None:
    panel = OverlayGridPanel()
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.elements_changed, timeout=1000):
        panel.add_element(_cfg("a"))
    assert len(panel.get_all_configs()) == 1
    assert panel.get_selected_config().text == "a"


def test_toggle_off_emits_and_serializes_empty(qtbot) -> None:
    panel = OverlayGridPanel()
    qtbot.addWidget(panel)
    panel.add_element(_cfg("a"))
    with qtbot.waitSignal(panel.toggle_changed, timeout=1000) as sig:
        panel._on_toggle(False)        # simulate the toggle button click
    assert sig.args == [False]
    assert panel.overlay_enabled is False
    assert panel.to_overlay_config() == {}      # disabled → renderer draws nothing
