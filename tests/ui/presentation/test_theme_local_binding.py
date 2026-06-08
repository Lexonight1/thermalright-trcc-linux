"""View↔PM seam — UCThemeLocal slideshow public API (pytest-qt).

The handler used to restore slideshow UI by reaching into private attrs
(``local._lunbo_array = …``).  It now calls ``set_slideshow_state(...)``; this
verifies that public entry drives the panel's SlideshowModel + getters.
"""
from __future__ import annotations

from trcc.ui.gui.uc_theme_local import UCThemeLocal


def test_set_slideshow_state_drives_public_getters(qtbot) -> None:
    panel = UCThemeLocal()
    qtbot.addWidget(panel)

    panel.set_slideshow_state(["A", "B"], enabled=True, interval=5)

    assert panel.is_slideshow() is True
    assert panel.get_slideshow_interval() == 5
    assert panel.timer_input.text() == "5"


def test_set_slideshow_state_disabled_clears(qtbot) -> None:
    panel = UCThemeLocal()
    qtbot.addWidget(panel)

    panel.set_slideshow_state(["A"], enabled=True, interval=4)
    panel.set_slideshow_state([], enabled=False, interval=4)

    assert panel.is_slideshow() is False
    assert panel.get_slideshow_themes() == []      # no names → no resolved items
