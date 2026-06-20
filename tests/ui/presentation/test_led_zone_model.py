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


# ── select-all style (PA120/LF10, 2/7) — independent multi-select ─────────
#
# Per the C# (FormLED button1_Click for nowLedStyle==2 + ucColor1Delegate
# gate 2||7): the zones are an independent multi-select — the colour applies
# to every selected zone.  There is NO radio mode; the "circulate" toggle is a
# select-all/edit-all overlay that leaves the underlying mask intact.


def test_select_all_click_is_multi_select_toggle_not_radio() -> None:
    """Buttons toggle independently with circulate OFF (the #192 regression)."""
    m = _model(4, select_all=True)            # zone 0 on by default
    assert m.click_zone(2) == ZoneEmit("carousel_zone", 2, True)
    assert m.enabled == [True, False, True, False]   # 0 kept, 2 added (not radio)
    assert m.click_zone(0) == ZoneEmit("carousel_zone", 0, False)
    assert m.enabled == [False, False, True, False]  # 0 removed


def test_select_all_last_zone_cannot_be_disabled() -> None:
    m = _model(4, select_all=True)            # only zone 0 on
    assert m.click_zone(0) is None            # guard: can't disable the last
    assert m.enabled == [True, False, False, False]


def test_select_all_overlay_shows_all_and_preserves_mask() -> None:
    """Circulate ON = edit-all overlay: display all active, mask untouched."""
    m = _model(4, select_all=True)
    m.click_zone(2)                           # mask = [T,F,T,F]
    assert m.enabled == [True, False, True, False]
    m.toggle_carousel(True)
    assert m.enabled == [True, False, True, False]        # mask preserved
    assert m.display_enabled == [True, True, True, True]  # overlay all-on
    assert m.click_zone(1) is None            # clicks ignored while overlay on
    assert m.enabled == [True, False, True, False]
    m.toggle_carousel(False)
    assert m.enabled == [True, False, True, False]        # selection restored
    assert m.display_enabled == [True, False, True, False]


def test_page_style_display_equals_enabled() -> None:
    """Non-select-all (page) styles: display mirrors the mask, no overlay."""
    m = _model(4)
    m.toggle_carousel(True)
    m.click_zone(1)
    assert m.display_enabled == m.enabled == [True, True, False, False]


# ── load_sync (restore) ──────────────────────────────────────────────────


def test_load_sync_restores_carousel_and_zone_flags() -> None:
    m = _model(4)
    m.load_sync(True, [True, True, False, True])
    assert m.carousel is True
    assert m.enabled == [True, True, False, True]
