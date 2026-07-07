"""A metric's unit is drawn or hidden per-element via ``show_unit``.

The Windows unit-switch (``button0`` → ``myModeSub``) decides, per element,
whether the unit glyph is appended to the number: ``myModeSub == 1`` draws
"42°C", otherwise the bare "42" (the unit is baked into the theme art, and
drawing it again double-prints — #150/#203).  89% of shipped masks show the
unit; the 001-series (baked glyph) hide it.  ``show_unit`` carries that choice
through every theme source (DC parse, cached trcc.json, user, cloud), and the
global temperature unit (C/F) swaps the °C glyph when the unit is shown.
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


def _render(element: dict[str, Any], sensors: dict[str, float], **kw: Any) -> _DrawRecorder:
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(854, 480)
    service.render(base, _config([element]), sensors=sensors, clock={}, **kw)
    return rec


def test_show_unit_true_draws_the_unit() -> None:
    """A metric with ``show_unit`` draws number + unit (the 89% majority)."""
    rec = _render(
        {"type": "metric", "metric": "cpu:temp", "format": "{value:.0f}°C",
         "show_unit": True, "x": 130, "y": 370, "color": "#ffffff", "size": 36},
        {"cpu:temp": 42.0},
    )
    assert rec.drawn == [(130, 370, "42°C", "#ffffff", 36, False, False)]


def test_show_unit_false_draws_bare_number() -> None:
    """The 001-series masks bake the glyph into the art → bare number."""
    rec = _render(
        {"type": "metric", "metric": "cpu:temp", "format": "{value:.0f}°C",
         "show_unit": False, "x": 130, "y": 370, "color": "#ffffff", "size": 36},
        {"cpu:temp": 42.0},
    )
    assert rec.drawn == [(130, 370, "42", "#ffffff", 36, False, False)]


def test_show_unit_defaults_to_true_when_absent() -> None:
    """A metric dict with no ``show_unit`` shows the unit (majority default)."""
    rec = _render(
        {"type": "metric", "metric": "cpu:freq", "format": "{value:.0f} MHz",
         "x": 660, "y": 370, "color": "#ffffff", "size": 27},
        {"cpu:freq": 801.0},
    )
    assert rec.drawn[0][2] == "801 MHz"


def test_shown_temp_unit_swaps_glyph_for_fahrenheit() -> None:
    """With the global unit F and the unit shown, the °C glyph becomes °F."""
    rec = _render(
        {"type": "metric", "metric": "cpu:temp", "format": "{value:.0f}°C",
         "show_unit": True, "x": 130, "y": 370, "color": "#ffffff", "size": 36},
        {"cpu:temp": 107.0},   # already-Fahrenheit value from upstream
        temp_unit="F",
    )
    assert rec.drawn[0][2] == "107°F"


def test_hidden_unit_ignores_temp_unit() -> None:
    """A bare-number element stays bare regardless of the global unit."""
    rec = _render(
        {"type": "metric", "metric": "cpu:temp", "format": "{value:.0f}°C",
         "show_unit": False, "x": 130, "y": 370, "color": "#ffffff", "size": 36},
        {"cpu:temp": 107.0},
        temp_unit="F",
    )
    assert rec.drawn[0][2] == "107"
