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


def test_add_element_emits_the_new_cell_with_its_id(qtbot) -> None:
    panel = OverlayGridPanel()
    qtbot.addWidget(panel)
    with qtbot.waitSignal(panel.element_added, timeout=1000) as sig:
        panel.add_element(_cfg("a"))
    (added,) = sig.args
    assert added is panel.get_selected_config()
    assert added.text == "a" and added.id, "the App needs an id to file it under"


def test_delete_element_emits_the_removed_cell(qtbot) -> None:
    """The removed cell, not its index: the id is what DeleteOverlayElement
    takes, and the index no longer names anything once the cell is gone."""
    panel = OverlayGridPanel()
    qtbot.addWidget(panel)
    panel.add_element(_cfg("a"))
    panel.add_element(_cfg("b"))
    first = panel.get_all_configs()[0]
    with qtbot.waitSignal(panel.element_deleted, timeout=1000) as sig:
        panel.delete_element(0)
    assert sig.args == [first]
    assert [c.text for c in panel.get_all_configs()] == ["b"]


def test_toggle_off_emits_and_keeps_the_elements(qtbot) -> None:
    panel = OverlayGridPanel()
    qtbot.addWidget(panel)
    panel.add_element(_cfg("a"))
    with qtbot.waitSignal(panel.toggle_changed, timeout=1000) as sig:
        panel._on_toggle(False)        # simulate the toggle button click
    assert sig.args == [False]
    assert panel.overlay_enabled is False
    assert len(panel.get_all_configs()) == 1, "off hides; it deletes nothing"


def test_a_refill_that_changes_nothing_logs_nothing(qtbot, caplog) -> None:
    """The grid refills after every change any UI makes; each cell used to
    log its selection state every time — 42 lines per drag move."""
    import logging

    panel = OverlayGridPanel()
    qtbot.addWidget(panel)
    name = "trcc.ui.gui.overlay_element"
    with caplog.at_level(logging.DEBUG, logger=name):
        panel.load_configs([_cfg("a"), _cfg("b")])
        panel.load_configs([_cfg("a"), _cfg("b")])
        quiet = [r for r in caplog.records if "set_selected" in r.getMessage()]
        panel.select_element(1)
        loud = [r for r in caplog.records if "set_selected" in r.getMessage()]
    assert quiet == [], "an unchanged selection still logged"
    assert [r.getMessage() for r in loud] == [
        "OverlayElementWidget.set_selected: index=1 False → True"]
