"""OverlayService — clock element dispatch.

Verifies that ``type: "clock"`` elements route to ``_draw_clock`` and draw
the frame's moment in their OWN pattern, as the C# does per element.  The
clock dict comes from the real producer, ``compute_clock``, so this file
cannot drift from it the way hand-built ``{"time": "14:58"}`` dicts did.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from trcc.core.models import RawFrame
from trcc.core.ports import Renderer
from trcc.services._clock import compute_clock
from trcc.services.overlay import OverlayService

#: Wednesday 2026-05-20 14:58 — the frame's moment in every test here.
_CLOCK = compute_clock("en", now=datetime(2026, 5, 20, 14, 58, 30))


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

    def surface_nbytes(self, surface: Any) -> int:
        return surface.w * surface.h * 4

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        return _Surface(width, height)

    def rotate(self, surface: Any, degrees: int) -> Any:
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

    def to_raw_rgb24(self, surface):
        # The inverse the port now requires.  Test doubles carry no pixels,
        # so this reports the surface's DIMENSIONS with blank bytes — enough
        # for a caller that only needs a correctly-sized RawFrame.
        w, h = self.surface_size(surface)
        return RawFrame(data=bytes(w * h * 3), width=w, height=h)

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
        clock=_CLOCK,
    )

    assert rec.drawn == [(10, 20, "14:58", "#ffaa00", 32, False, False)]


def test_date_element_draws_in_its_own_format() -> None:
    """The DC stores the format the theme was designed for (e.g. MM/dd); the
    cutover discarded it and forced ``yyyy/MM/dd`` — reported with
    screenshots.  Every pattern draws as the element says, the default one
    included: there is no global format to defer to."""
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([
            {"type": "clock", "source": "date", "format": "%m/%d", "x": 0, "y": 0},
            {"type": "clock", "source": "date", "format": "%Y/%m/%d", "x": 0, "y": 30},
        ]),
        sensors={},
        clock=_CLOCK,
    )

    assert [d[2] for d in rec.drawn] == ["05/20", "2026/05/20"]


def test_a_12h_time_element_draws_the_csharps_hh_mm_tt() -> None:
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([{"type": "clock", "source": "time", "format": "%I:%M %p",
                  "x": 0, "y": 0}]),
        sensors={},
        clock=_CLOCK,
    )

    assert rec.drawn[0][2] == "02:58 PM"


def test_an_element_without_a_pattern_draws_the_default() -> None:
    """No strftime pattern (the metric default "{value}", an older layout)."""
    rec = _DrawRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)

    service.render(
        base,
        _config([
            {"type": "clock", "source": "date", "format": "{value}", "x": 0, "y": 0},
        ]),
        sensors={},
        clock=_CLOCK,
    )

    assert rec.drawn[0][2] == "2026/05/20"


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
        clock=_CLOCK,
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
        clock=_CLOCK,
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
        clock=_CLOCK,
    )

    texts = [d[2] for d in rec.drawn]
    assert texts == ["14:58", "67"]
