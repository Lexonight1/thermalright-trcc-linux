"""Everything drawn on a panel is a ``Layer`` (``services/layers.py``).

Pixels are gated whole by ``dev/tools/frame_snapshot.py`` (156 frames, every
builder, three panels, four angles: byte-identical across the Layer
increments).  These pin what a snapshot cannot see: the rules for a layer
with nothing to draw, a layer whose draw fails, and the overlay cache key.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, cast

import pytest

from trcc.core.models import OverlayElement
from trcc.core.ports import Renderer
from trcc.services.layers import Mask, Split


class _Refuses:
    """The two renderer calls a layer makes; composite fails, as Qt's can
    on a bad surface."""

    def composite(self, *_: Any, **__: Any) -> Any:
        raise RuntimeError("composite refused")

    def surface_size(self, _: Any) -> tuple[int, int]:
        return (1, 1)


def test_a_mask_with_nothing_to_draw_returns_the_canvas() -> None:
    """Hidden, or none resolved: the C# skips the draw, it draws no blank."""
    canvas, refuses = object(), cast(Renderer, _Refuses())
    assert Mask(None).draw(canvas, refuses) is canvas
    assert Mask(Path("01.png"), surface=None).draw(canvas, refuses) is canvas


def test_a_split_overlay_that_fails_leaves_the_frame_as_it_was() -> None:
    """A failed island must not take the frame with it -- #149 froze the
    screen on every split-mode frame.  MUTATION CHECK: drop the try in
    ``Split.draw``."""
    canvas, refuses = object(), cast(Renderer, _Refuses())
    assert Split("island.png", object()).draw(canvas, refuses) is canvas
    assert Split(None).draw(canvas, refuses) is canvas


@pytest.mark.parametrize(
    "field_name",
    [f.name for f in dataclasses.fields(OverlayElement) if f.name != "id"])
def test_every_element_field_moves_the_overlay_key(tmp_path: Path,
                                                    field_name: str) -> None:
    """The overlay surface is cached on a key; a field left out of it is a
    change the panel shows only if something else also invalidates.  It
    left out ``font`` and ``show_unit``.  MUTATION CHECK: build the key from
    a hand-picked field list again."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.models import Theme

    from .mock_platform import MockPlatform

    key = "0402:3922"
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    app.discover_and_connect()
    element = OverlayElement(id="e1", type="metric", metric="cpu:temp")
    app.settings.for_device(key).user_overlay_elements = [element]
    theme = Theme(path=tmp_path, name="t", resolution=(320, 320))
    before = app.display._overlay_key(app.devices[key].info, theme, (320, 320), {}, {})
    value = getattr(element, field_name)
    changed = {bool: lambda v: not v, int: lambda v: v + 1}.get(
        type(value), lambda v: f"{v}x")(value)
    setattr(element, field_name, changed)
    after = app.display._overlay_key(app.devices[key].info, theme, (320, 320), {}, {})
    assert after != before, f"changing {field_name} left the overlay key as it was"
