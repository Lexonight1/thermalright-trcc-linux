"""View↔PM binding seam — the one place pytest-qt earns its keep.

The bulk of the overlay logic is tested Qt-free in ``test_overlay_model.py``.
This file verifies only the thin seam: that ``OverlayGridPanel`` (the View)
delegates to its ``OverlayModel`` and still emits the same Qt signals its
consumers (``uc_theme_setting``, the handler, the window) rely on.  This is
exactly what ``qtbot.waitSignal`` is for — proving a real Qt signal fires.
"""
from __future__ import annotations

from typing import Any

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


def _fit(qtbot, text: str) -> tuple[float, tuple[str, ...]]:
    """The value row's point size and lines for *text*, as a tile draws it."""
    from trcc.ui.gui.overlay_element import OverlayElementWidget

    tile = OverlayElementWidget(0)
    qtbot.addWidget(tile)
    font, lines = tile._fit_value(text)
    return font.pointSizeF(), lines


def _width(qtbot, text: str, size: float) -> int:
    from PySide6.QtGui import QFont, QFontMetrics

    from trcc.ui.gui.overlay_element import OverlayElementWidget

    font = QFont(OverlayElementWidget._CARD_FONT)
    font.setPointSizeF(size)
    return QFontMetrics(font).horizontalAdvance(text)


def test_a_tile_shrinks_a_long_date_onto_one_line(qtbot) -> None:
    """"2026/10/27" was cut off at both ends of the 56 px row; it shows
    whole, smaller, centered.  MUTATION CHECK: never shrink."""
    from trcc.ui.gui.overlay_element import OverlayElementWidget

    full, row = OverlayElementWidget._CARD_FONT.pointSizeF(), OverlayElementWidget._VALUE_ROW[2]
    for date in ("2026/10/27", "27/10/2026"):
        size, lines = _fit(qtbot, date)
        assert (lines, size < full, _width(qtbot, date, size) <= row) == ((date,), True, True)


def test_a_time_stays_on_one_line(qtbot) -> None:
    """"12:59 PM" is half a point too wide: it shrinks, it does not become
    "12:59" over "PM".  MUTATION CHECK: try two lines before shrinking."""
    from trcc.ui.gui.overlay_element import OverlayElementWidget

    size, lines = _fit(qtbot, "12:59 PM")
    assert lines == ("12:59 PM",) and size >= OverlayElementWidget._ONE_LINE_MIN


def test_words_that_do_not_fit_take_two_lines(qtbot) -> None:
    """His rule: words that do not fit go on two lines -- at full size here,
    not shrunk to the minimum.  MUTATION CHECK: drop the two-line step."""
    from trcc.ui.gui.overlay_element import OverlayElementWidget

    assert _fit(qtbot, "Memory Clock") == (
        OverlayElementWidget._CARD_FONT.pointSizeF(), ("Memory", "Clock"))


def test_a_long_text_takes_two_lines_never_more(qtbot) -> None:
    """Too long even at the smallest size: still two lines, centered -- Qt's
    own word wrap gave three."""
    from trcc.ui.gui.overlay_element import OverlayElementWidget

    size, lines = _fit(qtbot, "Thermalright Peerless Assassin")
    assert (size, lines) == (OverlayElementWidget._MIN_POINT,
                             ("Thermalright", "Peerless Assassin"))


def test_a_tile_keeps_a_short_value_at_full_size(qtbot) -> None:
    from trcc.ui.gui.overlay_element import OverlayElementWidget

    assert _fit(qtbot, "10/27") == (OverlayElementWidget._CARD_FONT.pointSizeF(), ("10/27",))


def test_the_weekday_tile_speaks_the_windows_language(qtbot, monkeypatch) -> None:
    """It drew English whatever the language: the window's language now
    reaches every tile, and the weekday is the panel's, in it.  MUTATION
    CHECK: drop the grid's set_language loop."""
    import trcc.ui.gui.overlay_element as tile_module
    from trcc.core.models import OverlayElementConfig, OverlayMode
    from trcc.ui.gui.overlay_grid import OverlayGridPanel

    asked: list[Any] = []
    real = tile_module.format_metric

    def spy(metric: str, value: float, **kw: Any) -> str:
        if metric == "weekday":
            asked.append(kw.get("lang"))
        return real(metric, value, **kw)

    monkeypatch.setattr(tile_module, "format_metric", spy)
    grid = OverlayGridPanel()
    qtbot.addWidget(grid)
    grid.add_element(OverlayElementConfig(mode=OverlayMode.WEEKDAY))
    grid.set_language("de")
    cell = grid._cells[0]
    cell.show()
    cell.grab()
    assert asked and set(asked) == {"de"}
