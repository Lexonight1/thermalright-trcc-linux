"""LedPanelModel — the per-style LED panel composition (toolkit-free).

Two layers:
* the model's per-style flags are correct (pure pytest, no Qt);
* the model matches the C# truth — cross-checked against the audit parser
  (``dev/tools/audit_csharp._led_panel_composition``) whenever the decompile is
  present, so a vendor change surfaces here instead of silently drifting.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.core.led_models import LED_STYLES, LEGACY_STYLE_ID
from trcc.ui.presentation.led_panel import led_panel_for

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "dev" / "tools"))
# See test_lcd_panel_model.py — the decompile location lives in ONE place
# (`core.csharp.DECOMPILE_ROOT`), never spelled in a test.
from audit_csharp import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    DECOMPILE_ROOT,
    _led_panel_composition,
)


def test_lc1_shows_memory_not_gauges() -> None:
    p = led_panel_for(4)
    assert p.show_memory_panel and not p.show_sensor_gauges
    assert not p.show_disk_panel and not p.show_clock_panel


def test_lf11_shows_disk_not_gauges() -> None:
    p = led_panel_for(10)
    assert p.show_disk_panel and not p.show_sensor_gauges
    assert not p.show_memory_panel and not p.show_clock_panel


def test_lc2_shows_clock_plus_gauges() -> None:
    p = led_panel_for(9)
    assert p.show_clock_panel and p.show_sensor_gauges
    assert not p.show_memory_panel and not p.show_disk_panel


@pytest.mark.parametrize(
    "style", [s for s in LED_STYLES if LEGACY_STYLE_ID[s] not in (4, 9, 10)],
    ids=lambda s: s.name,
)
def test_plain_styles_show_only_gauges(style) -> None:
    p = led_panel_for(LEGACY_STYLE_ID[style])
    assert p.show_sensor_gauges
    assert not (p.show_memory_panel or p.show_disk_panel or p.show_clock_panel)


def test_exactly_one_device_subpanel_per_style() -> None:
    """memory / disk / clock are mutually exclusive across every style."""
    for style in LED_STYLES:
        p = led_panel_for(LEGACY_STYLE_ID[style])
        assert sum((p.show_memory_panel, p.show_disk_panel,
                    p.show_clock_panel)) <= 1


@pytest.mark.skipif(not DECOMPILE_ROOT.exists(),
                    reason=f"C# decompile not present at {DECOMPILE_ROOT} "
                           f"(run `ilspycmd -p <exe>`, or set TRCC_DECOMPILE)")
def test_model_matches_csharp_audit() -> None:
    """Each C# FormLEDInit block's sections match led_panel_for(style).

    MUTATION CHECK -- set ``show_disk_panel=False`` in ``led_panel_for``.
    MEASURED 2026-08-18 against the real 2.1.6 tree: **1 failed**.
    """
    rows = _led_panel_composition(DECOMPILE_ROOT)
    assert rows, "audit parsed no LED panel blocks"
    for row in rows:
        style = row["style"] if row["style"] is not None else 1
        p = led_panel_for(style)
        assert p.show_sensor_gauges == row["sensors"], (style, row)
        assert p.show_memory_panel == row["memory"], (style, row)
        assert p.show_disk_panel == row["disk"], (style, row)
        assert p.show_clock_panel == row["week"], (style, row)
