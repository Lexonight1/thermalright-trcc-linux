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


def test_set_themes_renders_entries_and_origin_drives_filter(qtbot) -> None:
    """The View renders ListThemes entries (no disk walk): ``origin`` maps to
    is_user (location-derived) and drives the user/default filter; ``preview``
    is the tile image. (#theme-collision)"""
    from trcc.core.results import ThemeListEntry

    panel = UCThemeLocal()
    qtbot.addWidget(panel)

    panel.set_themes([
        ThemeListEntry(name="MyMix", resolution=(320, 320), path="/u/MyMix",
                       preview="/u/MyMix/Theme.png", origin="user"),
        ThemeListEntry(name="Aurora", resolution=(320, 320), path="/s/Aurora",
                       preview="/s/Aurora/00.png", origin="shipped"),
    ])

    by_name = {i.name: i for i in panel._all_themes}
    assert by_name["MyMix"].is_user is True            # origin-derived, not name-based
    assert by_name["Aurora"].is_user is False
    assert by_name["MyMix"].thumbnail == "/u/MyMix/Theme.png"

    panel.filter_mode = panel.MODE_USER
    panel._render_filtered()
    shown = [w.item_info.name for w in panel.item_widgets
             if hasattr(w, "item_info")]
    assert shown == ["MyMix"]                          # only the user-origin theme
