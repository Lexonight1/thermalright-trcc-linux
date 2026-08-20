"""An element's own font must reach the renderer.

Both theme parsers have always written the element's font family into the
element dict under ``name`` -- ``services/_dc.py`` for DC themes,
``services/theme.py`` for the JSON/legacy shape -- and ``OverlayService``
never read it, while ``Renderer.draw_text`` had no parameter to receive it.
So every overlay drew in the renderer's theme default no matter what the DC
stored or the user picked in the settings panel.  It was invisible because
every shipped theme uses Microsoft YaHei, which is also the default.

That invisibility is why this file measures the ARGUMENT rather than pixels:
the only proof is what ``draw_text`` was handed.

MUTATION CHECK -- drop the family in ``OverlayService._element_family`` or stop
passing it at the three ``draw_text`` call sites, and
``test_each_element_type_carries_its_font`` must fail for all three types.  If
it still passes, this file is guarding nothing.
"""
from __future__ import annotations

from typing import Any

import pytest

from trcc.core.models import OverlayElement
from trcc.services.overlay import OverlayService

from .test_overlay_clock import _config, _DrawRecorder


class _FamilyRecorder(_DrawRecorder):
    """Records the family argument, which ``_DrawRecorder`` discards."""

    def __init__(self) -> None:
        super().__init__()
        self.families: list[str] = []

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False, family: str = "") -> None:
        super().draw_text(surface, x, y, text, color, size, bold, italic)
        self.families.append(family)


@pytest.mark.parametrize(("element", "label"), [
    ({"type": "text", "text": "hello", "x": 10, "y": 10}, "text"),
    ({"type": "metric", "metric": "cpu:temp", "x": 10, "y": 10}, "metric"),
    ({"type": "clock", "source": "time", "x": 10, "y": 10}, "clock"),
])
def test_each_element_type_carries_its_font(
    element: dict[str, Any], label: str,
) -> None:
    rec = _FamilyRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)
    service.render(
        base,
        _config([{**element, "name": "Comic Sans MS"}]),
        sensors={"cpu:temp": 42.0},
        clock={"time": "14:58", "date": "2026/05/20", "weekday": "WED"},
    )
    assert rec.families == ["Comic Sans MS"], (
        f"{label} element: the renderer was handed {rec.families!r} — the "
        "font in the theme/DC never reached draw_text, so this element drew "
        "in the theme default instead of its own font."
    )


def test_an_element_without_a_font_gets_the_renderer_default() -> None:
    """No font on the element → empty, which QtRenderer resolves to the theme
    family.  Elements that carry no font must keep behaving exactly as before.
    """
    rec = _FamilyRecorder()
    service = OverlayService(rec)
    base = rec.create_surface(320, 320)
    service.render(base, _config([
        {"type": "text", "text": "hi", "x": 1, "y": 1},
    ]), sensors={})
    assert rec.families == [""]


def test_a_user_edited_element_round_trips_its_font() -> None:
    """``OverlayElement`` serializes the family under ``name`` — the key both
    parsers write and the overlay serializer reads — so an edited element does
    not silently revert to the theme default.
    """
    el = OverlayElement(id="e1", type="text", text="x", font="Noto Sans")
    assert el.to_dict()["name"] == "Noto Sans"
    assert "name" not in OverlayElement(id="e2", type="text").to_dict()
