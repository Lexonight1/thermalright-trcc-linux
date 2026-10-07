"""OverlayModel — pure-Python Presentation Model tests (NO Qt, NO QApplication).

This is the interaction logic that used to be fused into ``OverlayGridPanel``
(a ``QFrame``) and could only be exercised by constructing a widget.  Extracting
it into :class:`trcc.ui.presentation.overlay_model.OverlayModel` makes it
testable as plain data — these tests import no Qt and create no QApplication.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from trcc.core.models import OverlayElementConfig, OverlayMode
from trcc.core.results import OverlayElementEntry
from trcc.ui.presentation.overlay_model import MAX_ELEMENTS, OverlayModel
from trcc.ui.presentation.overlay_serialization import (
    config_fields,
    entries_to_configs,
)


def _cfg(**kw) -> OverlayElementConfig:
    """A CUSTOM-text element by default — simplest round-trippable shape."""
    base = dict(mode=OverlayMode.CUSTOM, text="hello", x=10, y=20)
    base.update(kw)
    return OverlayElementConfig(**base)


# ── Construction / defaults ──────────────────────────────────────────────


def test_new_model_is_empty_enabled_unselected() -> None:
    m = OverlayModel()
    assert len(m) == 0
    assert m.enabled is True
    assert m.selected_index == -1
    assert m.selected_config is None
    assert m.all_configs() == []


# ── Add + selection ──────────────────────────────────────────────────────


def test_add_appends_and_selects_new_element() -> None:
    m = OverlayModel()
    a, b = _cfg(text="a"), _cfg(text="b")
    assert m.add(a) is True
    assert m.add(b) is True
    assert len(m) == 2
    assert m.selected_index == 1          # newest selected
    # A copy with its own id — never the caller's object.
    assert m.selected_config is not b
    assert m.selected_config == replace(b, id=m.selected_config.id)


def test_add_gives_every_element_its_own_id_even_from_one_object() -> None:
    """The sensor sidebar hands the SAME config each time its row is clicked;
    two adds must be two elements, not one object in two cells."""
    m = OverlayModel()
    shared = _cfg(text="cpu")
    m.add(shared)
    m.add(shared)
    first, second = m.all_configs()
    assert first is not second
    assert first.id and second.id and first.id != second.id
    assert shared.id == "", "add must not write into the caller's object"


def test_add_refuses_beyond_max_elements() -> None:
    m = OverlayModel()
    for i in range(MAX_ELEMENTS):
        assert m.add(_cfg(text=str(i))) is True
    assert len(m) == MAX_ELEMENTS
    assert m.add(_cfg(text="overflow")) is False   # refused, no append
    assert len(m) == MAX_ELEMENTS


def test_select_in_range_sets_index_out_of_range_clears() -> None:
    m = OverlayModel()
    m.add(_cfg(text="a"))
    m.add(_cfg(text="b"))
    assert m.select(0) is m.config_at(0)
    assert m.selected_index == 0
    assert m.select(99) is None            # out of range clears selection
    assert m.selected_index == -1


# ── Delete + the selection-fixup that lived at overlay_grid.py:186-187 ────


def test_delete_clamps_selection_to_new_last_index() -> None:
    m = OverlayModel()
    for c in ("a", "b", "c"):
        m.add(_cfg(text=c))
    # selected is index 2 (last added); delete it → clamp to new last (1)
    assert m.delete(2) is True
    assert len(m) == 2
    assert m.selected_index == 1


def test_delete_last_remaining_sets_selection_to_minus_one() -> None:
    m = OverlayModel()
    m.add(_cfg(text="only"))
    assert m.selected_index == 0
    assert m.delete(0) is True
    assert len(m) == 0
    assert m.selected_index == -1


def test_delete_out_of_range_is_noop_false() -> None:
    m = OverlayModel()
    m.add(_cfg(text="a"))
    assert m.delete(5) is False
    assert len(m) == 1


# ── Update ───────────────────────────────────────────────────────────────


def test_update_replaces_in_range_only() -> None:
    m = OverlayModel()
    m.add(_cfg(text="a"))
    replacement = _cfg(text="z", x=99)
    assert m.update(0, replacement) is True
    assert m.config_at(0) is replacement
    assert m.update(3, replacement) is False


# ── load / clear ─────────────────────────────────────────────────────────


def test_load_copies_caps_and_clears_selection() -> None:
    m = OverlayModel()
    m.add(_cfg(text="pre"))           # establishes a selection to clear
    src = [_cfg(text=str(i)) for i in range(MAX_ELEMENTS + 5)]
    m.load(src)
    assert len(m) == MAX_ELEMENTS      # capped
    assert m.selected_index == -1      # the selected one is gone → cleared
    # load copies — mutating the source element must not touch the model
    src[0].text = "MUTATED"
    assert m.config_at(0).text == "0"


def test_load_keeps_the_selected_element_selected_by_id() -> None:
    """The grid reloads after every change, its own drag included; losing the
    selection there left the next drag move with nothing to move."""
    m = OverlayModel()
    m.load([_cfg(id="a", text="a"), _cfg(id="b", text="b")])
    m.select(1)

    m.load([_cfg(id="new", text="n"), _cfg(id="a", text="a"),
            _cfg(id="b", text="b", x=77)])

    assert m.selected_index == 2
    assert m.selected_config.x == 77, "the reloaded values, at the new place"


def test_load_does_not_match_cells_that_have_no_id() -> None:
    """An id-less selection must not latch onto the first id-less cell."""
    m = OverlayModel()
    m.load([_cfg(text="a")])
    m.select(0)
    m.load([_cfg(text="other")])
    assert m.selected_index == -1


def test_clear_empties_and_resets_selection() -> None:
    m = OverlayModel()
    m.add(_cfg(text="a"))
    m.clear()
    assert len(m) == 0
    assert m.selected_index == -1


# ── find_nearest (geometry) ──────────────────────────────────────────────


def test_find_nearest_returns_closest_by_squared_distance() -> None:
    m = OverlayModel()
    m.add(_cfg(text="far", x=500, y=500))
    m.add(_cfg(text="near", x=12, y=22))
    assert m.find_nearest(10, 20) == 1


def test_find_nearest_empty_returns_minus_one() -> None:
    assert OverlayModel().find_nearest(0, 0) == -1


# ── Cell ↔ App element (shared free functions) ───────────────────────────


def _entry(cfg: OverlayElementConfig) -> OverlayElementEntry:
    """What the App hands back for a cell it was given."""
    return OverlayElementEntry(id=cfg.id or "el_x", **config_fields(cfg))


def test_config_fields_carry_show_unit_from_mode_sub() -> None:
    """button0, the C# unit-switch: mode_sub 1 → draw the unit, 0 → bare."""
    for mode_sub, shown in ((1, True), (0, False)):
        cfg = OverlayElementConfig(mode=OverlayMode.HARDWARE, mode_sub=mode_sub,
                                   main_count=0, sub_count=1)
        fields = config_fields(cfg)
        assert fields is not None
        assert fields["type"] == "metric"
        assert fields["metric"] == "cpu:temp"
        assert fields["show_unit"] is shown


def test_a_pair_the_dc_table_cannot_name_gives_no_element() -> None:
    """No sensor id → nothing the App could draw; refused, never invented."""
    cfg = OverlayElementConfig(mode=OverlayMode.HARDWARE,
                               main_count=99, sub_count=77)
    assert config_fields(cfg) is None


def test_a_metric_the_dc_table_cannot_name_still_gets_a_cell() -> None:
    """#223 #259 #310: such an element was left out of the gui grid, so the gui
    could neither show nor edit what qtgui, the CLI and the API placed.  It
    gets a cell by its sensor id, and an edit sends that id back with the
    format trcc's own table gives it."""
    shown = OverlayElementEntry(id="t", type="text", text="hi")
    board = OverlayElementEntry(id="m", type="metric",
                                metric="board:nct6798_auxtin1:temp", show_unit=True)

    configs = entries_to_configs([shown, board])
    fields = config_fields(configs[1])

    assert [(c.id, c.metric) for c in configs] == [
        ("t", ""), ("m", "board:nct6798_auxtin1:temp")]
    assert fields is not None
    assert (fields["metric"], fields["format"], fields["show_unit"]) == (
        "board:nct6798_auxtin1:temp", "{value:.0f}°C", True)


def _element(**kw):
    """An element with every field set to something distinctive."""
    cfg = OverlayElementConfig(
        id="el_1234", x=13, y=27, color="#ABCDEF", font_size=24, font_style=1,
        font_name="Noto Sans",
    )
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


@pytest.mark.parametrize("built", [
    _element(mode=OverlayMode.TIME, mode_sub=1),
    _element(mode=OverlayMode.DATE, mode_sub=2),
    _element(mode=OverlayMode.DATE, mode_sub=4),
    _element(mode=OverlayMode.WEEKDAY),
    _element(mode=OverlayMode.CUSTOM, text="hello"),
    _element(mode=OverlayMode.CUSTOM, text="it", font_style=2),
    _element(mode=OverlayMode.HARDWARE, main_count=1, sub_count=1, mode_sub=1),
    _element(mode=OverlayMode.HARDWARE, main_count=0, sub_count=1, mode_sub=0),
], ids=["time-12h", "date-dmy", "date-dm", "weekday", "custom", "italic",
        "hw-unit", "hw-bare"])
def test_a_cell_round_trips_through_the_app_element(built) -> None:
    """Cell → App element → cell must return what went in, for EVERY mode.

    The two directions dispatch on different things — one on ``OverlayMode``,
    one on ``type``/``source``/``metric`` — so nothing but this forces them to
    agree.  The id is part of it: it is how an edit finds its element.
    """
    back = entries_to_configs([_entry(built)])

    assert len(back) == 1, f"{built.mode.name} cell dropped on the way back"
    assert back[0] == built


def test_a_format_outside_the_table_shows_the_default_button() -> None:
    """The cell cannot hold ``%H:%M:%S``; it shows the table's first entry.
    That is display only — an edit sends changed fields, so the element keeps
    its own pattern (driven in test_gui_overlay_switch_keeps_the_layout)."""
    entry = OverlayElementEntry(id="c", type="clock", source="time",
                                format="%H:%M:%S")
    (cell,) = entries_to_configs([entry])
    assert (cell.mode, cell.mode_sub) == (OverlayMode.TIME, 0)
