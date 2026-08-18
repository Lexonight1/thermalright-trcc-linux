"""LedDisplayModel — the per-style display-selector behaviour (toolkit-free).

Three layers:
* the model is internally sane (labels match page count; ZONE/NONE carry none);
* it agrees with the ``led_segment`` render — PAGE page-counts equal the
  display's ``phase_count``, ZONE slot-counts equal its ``zone_led_map`` length,
  and only ZONE styles carry a ``zone_led_map``;
* the ZONE classification matches the C# truth — cross-checked against the
  audit parser (``dev/tools/audit_csharp._led_zone_styles``, the
  ``ucColor1Delegate`` gate) whenever the decompile is present.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.core.led_models import LEGACY_STYLE_ID, STYLE_BY_LEGACY_ID
from trcc.services.led_segment import get_display
from trcc.ui.presentation.led_display import (
    LedSelector,
    led_display_for,
)

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "dev" / "tools"))
# See test_lcd_panel_model.py — the decompile location lives in ONE place
# (`core.csharp.DECOMPILE_ROOT`), never spelled in a test.
from audit_csharp import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    DECOMPILE_ROOT,
    _led_zone_styles,
)

_ALL_IDS = sorted(LEGACY_STYLE_ID.values())


@pytest.mark.parametrize("sid", _ALL_IDS)
def test_model_internally_consistent(sid) -> None:
    m = led_display_for(sid)
    if m.selector is LedSelector.PAGE:
        assert m.slot_count >= 1
        assert len(m.page_labels) == m.slot_count
    else:  # ZONE / NONE carry no page labels
        assert m.page_labels == ()
        if m.selector is LedSelector.NONE:
            assert m.slot_count == 0


@pytest.mark.parametrize("sid", _ALL_IDS)
def test_model_agrees_with_render(sid) -> None:
    """PAGE counts == phase_count; ZONE counts == zone_led_map length; only
    ZONE styles carry a zone_led_map."""
    m = led_display_for(sid)
    display = get_display(STYLE_BY_LEGACY_ID[sid])

    if m.selector is LedSelector.PAGE:
        assert display is not None, f"style {sid} PAGE but no render display"
        assert display.zone_led_map is None, f"style {sid} PAGE but has zone_led_map"
        assert m.slot_count == display.phase_count
    elif m.selector is LedSelector.ZONE:
        assert display is not None and display.zone_led_map is not None, \
            f"style {sid} ZONE but no zone_led_map"
        assert m.slot_count == len(display.zone_led_map)


def test_only_pa120_lf10_are_zone() -> None:
    """The render's zone_led_map styles are exactly the model's ZONE styles."""
    zone_ids = {sid for sid in _ALL_IDS
                if led_display_for(sid).selector is LedSelector.ZONE}
    render_zone_ids = {
        sid for sid in _ALL_IDS
        if (d := get_display(STYLE_BY_LEGACY_ID[sid])) is not None
        and d.zone_led_map is not None
    }
    assert zone_ids == render_zone_ids


def test_clock_and_solid_are_none() -> None:
    assert led_display_for(9).selector is LedSelector.NONE   # LC2 clock
    assert led_display_for(12).selector is LedSelector.NONE  # LF13 solid


@pytest.mark.skipif(not DECOMPILE_ROOT.exists(),
                    reason=f"C# decompile not present at {DECOMPILE_ROOT} "
                           f"(run `ilspycmd -p <exe>`, or set TRCC_DECOMPILE)")
def test_zone_styles_match_csharp_audit() -> None:
    """Model's ZONE styles == the C# ucColor1Delegate per-zone-colour gate.

    MUTATION CHECK -- flip style 7 (LF10) from ZONE to PAGE in ``_MODELS``.
    MEASURED 2026-08-18 against the real 2.1.6 tree: **1 failed**.
    """
    cs_zone = _led_zone_styles(DECOMPILE_ROOT)
    assert cs_zone, "audit parsed no ZONE styles from ucColor1Delegate"
    model_zone = {sid for sid in _ALL_IDS
                  if led_display_for(sid).selector is LedSelector.ZONE}
    assert model_zone == cs_zone
