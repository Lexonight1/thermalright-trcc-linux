"""LedZoneModel — pure-Python tests (NO Qt, NO QApplication).

Locks the zone/carousel interaction rules that used to live in UCLedControl
with the QPushButton checked-state as the model: radio-select, carousel
multi-select, the last-zone guard, and the styles-2/7 select-all special case.
"""
from __future__ import annotations

from trcc.ui.presentation.led_zone_model import LedZoneModel, ZoneEmit


def _model(zone_count: int, select_all: bool = False) -> LedZoneModel:
    m = LedZoneModel()
    m.configure(zone_count, select_all)
    return m


# ── configure ────────────────────────────────────────────────────────────


def test_configure_selects_zone0_carousel_off() -> None:
    m = _model(4)
    assert m.enabled == [True, False, False, False]
    assert m.selected == 0
    assert m.carousel is False
    assert m.zone_count == 4


# ── radio select ─────────────────────────────────────────────────────────


def test_radio_click_selects_and_emits() -> None:
    m = _model(4)
    emit = m.click_zone(2)
    assert m.selected == 2
    assert m.enabled == [False, False, True, False]
    assert emit == ZoneEmit("zone_selected", 2)


def test_click_out_of_range_is_noop() -> None:
    m = _model(2)
    assert m.click_zone(5) is None
    assert m.enabled == [True, False]


# ── carousel multi-select + last-zone guard ──────────────────────────────


def test_carousel_on_leaves_enabled_unchanged() -> None:
    m = _model(4)
    emit = m.toggle_carousel(True)
    assert m.carousel is True
    assert m.enabled == [True, False, False, False]
    assert emit == ZoneEmit("carousel", on=True)


def test_carousel_toggle_zone_on_then_off() -> None:
    m = _model(4)
    m.toggle_carousel(True)
    assert m.click_zone(1) == ZoneEmit("carousel_zone", 1, True)
    assert m.enabled == [True, True, False, False]
    assert m.click_zone(0) == ZoneEmit("carousel_zone", 0, False)
    assert m.enabled == [False, True, False, False]


def test_carousel_last_zone_cannot_be_disabled() -> None:
    m = _model(4)
    m.toggle_carousel(True)            # only zone 0 enabled
    emit = m.click_zone(0)            # try to turn the last one off
    assert emit is None              # guard: nothing emitted
    assert m.enabled == [True, False, False, False]   # kept on


def test_carousel_off_collapses_to_selected() -> None:
    m = _model(4)
    m.click_zone(2)                   # radio-select zone 2
    m.toggle_carousel(True)
    m.click_zone(0)                   # multi-select adds zone 0 → [T,F,T,F]
    assert m.enabled == [True, False, True, False]
    m.toggle_carousel(False)         # collapse back to the selected zone
    assert m.enabled == [False, False, True, False]


# ── select-all style (2 / 7) ─────────────────────────────────────────────


def test_select_all_style_carousel_enables_all() -> None:
    m = _model(4, select_all=True)
    emit = m.toggle_carousel(True)
    assert m.enabled == [True, True, True, True]
    assert emit == ZoneEmit("carousel", on=True)


def test_select_all_style_click_ignored_while_carousel_on() -> None:
    m = _model(4, select_all=True)
    m.toggle_carousel(True)
    assert m.click_zone(2) is None
    assert m.enabled == [True, True, True, True]


def test_select_all_style_carousel_off_collapses_to_selected() -> None:
    m = _model(4, select_all=True)
    m.click_zone(3)                   # selected = 3
    m.toggle_carousel(True)
    m.toggle_carousel(False)
    assert m.enabled == [False, False, False, True]


# ── load_sync (restore) ──────────────────────────────────────────────────


def test_load_sync_restores_carousel_and_zone_flags() -> None:
    m = _model(4)
    m.load_sync(True, [True, True, False, True])
    assert m.carousel is True
    assert m.enabled == [True, True, False, True]
