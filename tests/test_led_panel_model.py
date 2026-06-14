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
_DECOMPILE = Path("/tmp/trcc216_src/TRCC.decompiled.cs")


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


@pytest.mark.skipif(not _DECOMPILE.is_file(),
                    reason="C# decompile not present (run ilspycmd)")
def test_model_matches_csharp_audit() -> None:
    """Each C# FormLEDInit block's sections match led_panel_for(style)."""
    sys.path.insert(0, str(_REPO / "dev" / "tools"))
    from audit_csharp import _led_panel_composition  # type: ignore[import-not-found]

    rows = _led_panel_composition(_DECOMPILE)
    assert rows, "audit parsed no LED panel blocks"
    for row in rows:
        style = row["style"] if row["style"] is not None else 1
        p = led_panel_for(style)
        assert p.show_sensor_gauges == row["sensors"], (style, row)
        assert p.show_memory_panel == row["memory"], (style, row)
        assert p.show_disk_panel == row["disk"], (style, row)
        assert p.show_clock_panel == row["week"], (style, row)
