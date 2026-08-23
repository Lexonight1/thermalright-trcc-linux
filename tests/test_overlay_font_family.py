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

from pathlib import Path
from typing import Any

import pytest

from trcc.core.models import OverlayElement
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings

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
    parsers write and the renderer resolves.
    """
    el = OverlayElement(id="e1", type="text", text="x", font="Noto Sans")
    assert el.to_dict()["name"] == "Noto Sans"
    assert "name" not in OverlayElement(id="e2", type="text").to_dict()


@pytest.mark.parametrize("key", ["font", "name"])
def test_from_dict_reads_the_family_under_either_key(key: str) -> None:
    """Two writers, two keys, and both are real.

    ``asdict`` persists the dataclass FIELD name (``font``) into trcc.json;
    ``to_dict`` and both theme parsers use ``name``.  ``from_dict`` is the one
    reader they share, so it has to accept both — it read NEITHER until
    2026-08-19, which is the defect the next test measures.
    """
    assert OverlayElement.from_dict(
        {"id": "e1", "type": "text", key: "Noto Sans"}).font == "Noto Sans"
    assert OverlayElement.from_dict({"id": "e2", "type": "text"}).font == ""


def test_a_user_element_keeps_its_font_across_a_restart(tmp_path: Path) -> None:
    """THE user-visible claim, measured through real Settings — not to_dict.

    Asserting ``to_dict()["name"]`` alone passes while the round-trip is
    broken, because it exercises the WRITE half of a pair whose READ half was
    missing: `asdict` wrote `font` to trcc.json and `from_dict` dropped it, so
    every user overlay element rendered in its own font for the session and
    reverted to the theme default on the next start.  That is why this test
    goes through a real save/load rather than a serializer call.

    MUTATION CHECK -- drop the ``font=`` line from ``OverlayElement.from_dict``
    and this fails while the to_dict assertions above keep passing.
    """
    from tests.conftest import FakePlatform

    paths = FakePlatform(tmp_path).paths()
    Settings(paths).add_user_overlay_element("0402:3922", OverlayElement(
        id="e1", type="text", text="hi", font="Comic Sans MS"))

    reloaded = Settings(paths).for_device("0402:3922").user_overlay_elements[0]
    assert reloaded.font == "Comic Sans MS", (
        f"after a restart the element's font is {reloaded.font!r} — it was "
        "persisted and then dropped on load, so the element reverts to the "
        "theme default and the user's choice survives only until they quit."
    )
    assert reloaded.to_dict()["name"] == "Comic Sans MS", (
        "the reloaded element must still hand the renderer its family"
    )


def test_the_commands_can_set_a_font(tmp_home: Path) -> None:
    """The UIs' only way in is a Command, and neither carried a font.

    Everything below the Command was already in place -- ``OverlayElement``
    has ``font``, ``to_dict`` publishes it under ``name``, ``from_dict`` reads
    it back, and ``OverlayService._element_family`` hands it to
    ``draw_text``.  But ``AddOverlayElement`` had no ``font`` field, so every
    element a CLI, API or daemon client created was born with ``font=""``,
    and ``UpdateOverlayElement`` had none either, so nothing could ever change
    it afterwards.  The whole chain worked and no caller could reach it.

    MUTATION CHECK -- delete ``font`` from either Command's field list, or
    stop passing it through in ``execute``, and this fails.
    """
    from tests.conftest import FakePlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import AddOverlayElement, UpdateOverlayElement

    # The Commands invalidate the display, so the App needs a renderer.
    app = App(platform=FakePlatform(tmp_home))
    app.set_renderer(QtRenderer())
    key = "0402:3922"

    added = app.dispatch(AddOverlayElement(
        key=key, type="text", text="hi", font="Comic Sans MS"))
    assert added.ok is True
    assert added.element is not None
    assert added.element.font == "Comic Sans MS", (
        "the Result must report the font back, or a client cannot read what "
        "it just set"
    )

    stored = app.settings.for_device(key).user_overlay_elements[0]
    assert stored.font == "Comic Sans MS"
    assert stored.to_dict()["name"] == "Comic Sans MS", (
        "the stored element must hand the renderer its family"
    )

    updated = app.dispatch(UpdateOverlayElement(
        key=key, element_id=added.element.id, font="Noto Sans"))
    assert updated.ok is True
    assert updated.element is not None
    assert updated.element.font == "Noto Sans"
    assert app.settings.for_device(key).user_overlay_elements[0].font \
        == "Noto Sans"


def test_updating_another_field_leaves_the_font_alone(tmp_home: Path) -> None:
    """``font=None`` means "don't touch", not "clear it".

    ``Settings.update_user_overlay_element`` skips ``None`` values, so moving
    an element must not silently reset its typeface.
    """
    from tests.conftest import FakePlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import AddOverlayElement, UpdateOverlayElement

    # The Commands invalidate the display, so the App needs a renderer.
    app = App(platform=FakePlatform(tmp_home))
    app.set_renderer(QtRenderer())
    key = "0402:3922"

    added = app.dispatch(AddOverlayElement(
        key=key, type="text", text="hi", font="Comic Sans MS"))
    assert added.element is not None

    app.dispatch(UpdateOverlayElement(key=key, element_id=added.element.id,
                                      x=42))
    element = app.settings.for_device(key).user_overlay_elements[0]
    assert element.x == 42
    assert element.font == "Comic Sans MS", (
        "moving an element must not clear the font the user chose"
    )
