"""Metric values render as BARE numbers — the unit comes from the theme art.

The Windows app strips ℃/℉/MHz/%/RPM off every sensor value before drawing it
(TRCC.cs: val.Replace(...) → Convert.ToInt32 → DrawString), because the unit
glyph is baked into the theme's background image, not the overlay.  Drawing
"42°C" over a baked-in "°C" double-prints the unit (#150/#203).  These lock the
strip so any theme source (fresh DC parse, cached trcc.json, user, cloud) draws
the bare number.
"""
from __future__ import annotations

from typing import Any

import pytest
from test_overlay_clock import _DrawRecorder  # the draw_text-capturing renderer

from trcc.services.overlay import OverlayService, _strip_metric_unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42°C", "42"),
        ("42°F", "42"),
        ("42℃", "42"),
        ("42℉", "42"),
        ("801 MHz", "801"),
        ("801MHz", "801"),
        ("13%", "13"),
        ("1200 RPM", "1200"),
        ("42", "42"),          # already bare — unchanged
        ("", ""),
    ],
)
def test_strip_metric_unit(raw: str, expected: str) -> None:
    assert _strip_metric_unit(raw) == expected


def _config(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {"overlay_enabled": True, "elements": elements}


def test_metric_element_draws_bare_number_despite_unit_format() -> None:
    """A metric whose format bakes in °C still draws just the number."""
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(854, 480)

    service.render(
        base,
        _config([{
            "type": "metric", "metric": "cpu:temp", "format": "{value:.0f}°C",
            "x": 130, "y": 370, "color": "#ffffff", "size": 36,
        }]),
        sensors={"cpu:temp": 42.0},
        clock={},
    )

    assert rec.drawn == [(130, 370, "42", "#ffffff", 36, False, False)]


def test_metric_freq_and_usage_also_bare() -> None:
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(854, 480)

    service.render(
        base,
        _config([
            {"type": "metric", "metric": "cpu:freq", "format": "{value:.0f} MHz",
             "x": 660, "y": 370, "color": "#ffffff", "size": 27},
            {"type": "metric", "metric": "cpu:usage", "format": "{value:.0f}%",
             "x": 370, "y": 370, "color": "#ffffff", "size": 27},
        ]),
        sensors={"cpu:freq": 801.0, "cpu:usage": 13.0},
        clock={},
    )

    assert [d[2] for d in rec.drawn] == ["801", "13"]
