"""OverlayService — clock element dispatch.

Verifies that ``type: "clock"`` elements route to ``_draw_clock`` and
resolve the right source from the pre-computed clock dict.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from trcc.core.ports import Renderer
from trcc.services.overlay import OverlayService


class _Surface:
    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h


class _DrawRecorder(Renderer):
    """Renderer that records draw_text calls; everything else is a no-op."""

    def __init__(self) -> None:
        self.drawn: list[tuple[int, int, str, str, int, bool, bool]] = []

    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        return _Surface(width, height)

    def open_image(self, path: Path) -> Any:
        return _Surface(100, 100)

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.w, surface.h)

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        return _Surface(width, height)

    def rotate(self, surface: Any, degrees: int) -> Any:
        return surface

    def flip_horizontal(self, surface: Any) -> Any:
        return surface

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        return surface

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False, family: str = "") -> None:
        self.drawn.append((x, y, text, color, size, bold, italic))

    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes:
        return b""

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        return b""

    def from_raw_rgb24(self, frame: Any) -> Any:
        return _Surface(100, 100)

    def decode_image(self, data: bytes) -> Any:
        return _Surface(100, 100)


def _config(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {"overlay_enabled": True, "elements": elements}


def test_clock_element_renders_resolved_time() -> None:
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([{
            "type": "clock", "source": "time",
            "x": 10, "y": 20,
            "color": "#ffaa00", "size": 32,
        }]),
        sensors={},
        clock={"time": "14:58", "date": "2026/05/20", "weekday": "WED"},
    )

    assert rec.drawn == [(10, 20, "14:58", "#ffaa00", 32, False, False)]


def test_date_element_honours_its_own_format_over_the_global() -> None:
    """A date element carrying the theme's strftime pattern renders with it.

    The DC stores the format the theme was designed for (e.g. MM/dd); the
    cutover discarded it and forced the global ``yyyy/MM/dd``.  Now the date
    element carries ``"%m/%d"`` and the renderer resolves it live, ignoring
    the (wrong-format) precomputed dict entry.  Reported with screenshots:
    full ``2026/06/05`` where ``MM/dd`` was intended.
    """
    from datetime import datetime

    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([
            {"type": "clock", "source": "date", "format": "%m/%d",
             "x": 0, "y": 0},
        ]),
        sensors={},
        clock={"date": "2026/06/05"},
    )

    assert rec.drawn[0][2] == datetime.now().strftime("%m/%d")
    assert rec.drawn[0][2] != "2026/06/05"


def test_date_element_with_default_pattern_follows_global() -> None:
    """A date element whose pattern is the DEFAULT ("%Y/%m/%d" ≡ yyyy/MM/dd) is
    treated as uncustomised → it follows the user's global date_format (the
    precomputed dict), so the settings-panel choice applies.

    The reconciliation of two reports: the sibling test keeps a DELIBERATE
    pattern (%m/%d); this keeps the GLOBAL pref winning for the standard format
    a user expects to control. (#format-prefs)
    """
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([
            {"type": "clock", "source": "date", "format": "%Y/%m/%d",
             "x": 0, "y": 0},
        ]),
        sensors={},
        clock={"date": "05/06/2026"},   # global pref (dd/MM/yyyy) already resolved
    )

    # Follows the global dict, NOT the element's default-equivalent pattern.
    assert rec.drawn[0][2] == "05/06/2026"


def test_date_element_without_pattern_uses_global_dict() -> None:
    """No real strftime pattern (e.g. the metric default "{value}") → fall
    back to the precomputed global clock dict, unchanged."""
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([
            {"type": "clock", "source": "date", "format": "{value}",
             "x": 0, "y": 0},
        ]),
        sensors={},
        clock={"date": "2026/06/05"},
    )

    assert rec.drawn[0][2] == "2026/06/05"


def test_clock_element_renders_resolved_date_and_weekday() -> None:
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([
            {"type": "clock", "source": "date",    "x": 0, "y": 0},
            {"type": "clock", "source": "weekday", "x": 0, "y": 30},
        ]),
        sensors={},
        clock={"time": "14:58", "date": "2026/05/20", "weekday": "WED"},
    )

    texts = [d[2] for d in rec.drawn]
    assert texts == ["2026/05/20", "WED"]


def test_clock_element_with_no_clock_dict_is_skipped() -> None:
    """Calling render() without a clock dict (or with empty) skips clock elements."""
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([{"type": "clock", "source": "time", "x": 0, "y": 0}]),
        sensors={},
        # clock omitted → None → empty dict inside; source resolves to ""
    )

    assert rec.drawn == []


def test_clock_unknown_source_is_skipped_silently() -> None:
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([{"type": "clock", "source": "century", "x": 0, "y": 0}]),
        sensors={},
        clock={"time": "14:58"},
    )

    assert rec.drawn == []


def test_clock_element_does_not_consume_sensor_dict() -> None:
    """Clock elements don't read from sensors — verify they're independent."""
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([
            {"type": "clock",  "source": "time",     "x": 0, "y": 0},
            {"type": "metric", "metric": "cpu_temp", "x": 0, "y": 30,
             "format": "{value:.0f}"},
        ]),
        sensors={"cpu_temp": 67.0},
        clock={"time": "14:58"},
    )

    texts = [d[2] for d in rec.drawn]
    assert texts == ["14:58", "67"]
