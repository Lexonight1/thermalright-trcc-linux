"""OverlayModel — pure-Python Presentation Model tests (NO Qt, NO QApplication).

This is the interaction logic that used to be fused into ``OverlayGridPanel``
(a ``QFrame``) and could only be exercised by constructing a widget.  Extracting
it into :class:`trcc.ui.presentation.overlay_model.OverlayModel` makes it
testable as plain data — these tests import no Qt and create no QApplication.
"""
from __future__ import annotations

from dataclasses import replace

from trcc.core.models import OverlayElementConfig, OverlayMode
from trcc.ui.presentation.overlay_model import MAX_ELEMENTS, OverlayModel
from trcc.ui.presentation.overlay_serialization import (
    configs_to_next_elements,
    configs_to_overlay_config,
    overlay_config_to_configs,
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
    assert m.selected_config is b


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
    assert m.selected_index == -1      # selection cleared
    # load copies — mutating the source element must not touch the model
    src[0].text = "MUTATED"
    assert m.config_at(0).text == "0"


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


# ── Serialization gating + round-trip (shared free functions) ────────────


def test_disabled_model_serializes_to_empty_overlay_config() -> None:
    m = OverlayModel()
    m.add(_cfg(text="a"))
    m.set_enabled(False)
    assert configs_to_overlay_config(m.all_configs(), m.enabled) == {}


def test_overlay_config_round_trip_preserves_text_and_clock_elements() -> None:
    """CUSTOM + TIME survive configs_to_overlay_config → overlay_config_to_configs.

    (HARDWARE also round-trips now that the write side emits canonical DC ids —
    see ``test_hardware_metric_round_trips_via_dc_id``.)
    """
    original = [
        _cfg(mode=OverlayMode.CUSTOM, text="cpu", x=5, y=6,
             color="#ABCDEF", font_size=42, font_style=1),
        OverlayElementConfig(mode=OverlayMode.TIME, mode_sub=1, x=70, y=80),
    ]
    serialized = configs_to_overlay_config([replace(c) for c in original], True)
    restored = overlay_config_to_configs(serialized)

    assert len(restored) == 2
    custom, clock = restored
    assert custom.mode is OverlayMode.CUSTOM
    assert custom.text == "cpu"
    assert (custom.x, custom.y) == (5, 6)
    assert custom.color == "#ABCDEF"
    assert custom.font_size == 42
    assert custom.font_style == 1
    assert clock.mode is OverlayMode.TIME
    assert clock.mode_sub == 1
    assert (clock.x, clock.y) == (70, 80)


def test_editor_load_maps_next_id_hardware_metric_back_to_main_sub() -> None:
    """The editor-load path consumes the THEME/DC shape (next/ ids like
    ``cpu:temp``) and resolves it back to ``(main, sub)`` so the metric
    element re-enters the grid (overlay_grid.py:316 lesson)."""
    theme_shape = {
        "cpu:temp": {"x": 70, "y": 80, "enabled": True, "metric": "cpu:temp",
                     "show_unit": True, "font": {"size": 24, "style": "regular"}},
    }
    restored = overlay_config_to_configs(theme_shape)
    assert len(restored) == 1
    hardware = restored[0]
    assert hardware.mode is OverlayMode.HARDWARE
    assert (hardware.main_count, hardware.sub_count) == (0, 1)
    assert (hardware.x, hardware.y) == (70, 80)


def test_hardware_metric_round_trips_via_dc_id() -> None:
    """A HARDWARE element survives configs_to_overlay_config →
    overlay_config_to_configs: the write side emits the canonical DC id
    (``cpu:temp``) so the read side resolves it back to ``(main, sub)`` instead
    of dropping it (the former underscore-vs-colon vocabulary mismatch)."""
    original = OverlayElementConfig(mode=OverlayMode.HARDWARE,
                                    main_count=0, sub_count=1, x=12, y=34)
    serialized = configs_to_overlay_config([replace(original)], True)
    entry = next(iter(serialized.values()))
    assert entry["metric"] == "cpu:temp"          # canonical DC id, not cpu_temp

    restored = overlay_config_to_configs(serialized)
    assert len(restored) == 1                      # element survives, not dropped
    assert restored[0].mode is OverlayMode.HARDWARE
    assert (restored[0].main_count, restored[0].sub_count) == (0, 1)
    assert (restored[0].x, restored[0].y) == (12, 34)


# ── button0 unit-switch (mode_sub ↔ show_unit) ───────────────────────────


def test_next_elements_carry_show_unit_from_mode_sub() -> None:
    """The Command-bus path (SetOverlayConfig) carries button0 as show_unit —
    the C# unit-switch: mode_sub 1 → draw the unit, 0 → bare number."""
    shown = OverlayElementConfig(mode=OverlayMode.HARDWARE, mode_sub=1,
                                 main_count=0, sub_count=1)
    hidden = OverlayElementConfig(mode=OverlayMode.HARDWARE, mode_sub=0,
                                  main_count=0, sub_count=1)
    out = configs_to_next_elements([shown, hidden])
    assert out[0]["type"] == "metric" and out[0]["show_unit"] is True
    assert out[1]["show_unit"] is False


def test_configs_to_overlay_config_emits_show_unit() -> None:
    """Editor config → keyed dict carries button0 as ``show_unit`` (both states)."""
    for mode_sub in (0, 1):
        cfg = OverlayElementConfig(mode=OverlayMode.HARDWARE, mode_sub=mode_sub,
                                   main_count=1, sub_count=2)
        entry = next(iter(configs_to_overlay_config([cfg], True).values()))
        assert entry["show_unit"] is (mode_sub == 1)


def test_overlay_config_to_configs_reads_show_unit_into_mode_sub() -> None:
    """Keyed dict → editor config maps ``show_unit`` back to mode_sub 1/0."""
    for show_unit, expected in ((True, 1), (False, 0)):
        theme_shape = {
            "cpu:temp": {"x": 5, "y": 6, "enabled": True, "metric": "cpu:temp",
                         "show_unit": show_unit,
                         "font": {"size": 24, "style": "regular"}},
        }
        restored = overlay_config_to_configs(theme_shape)
        assert restored[0].mode_sub == expected
