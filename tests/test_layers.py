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


def test_a_picture_with_nothing_resolved_leaves_the_canvas_black() -> None:
    """A theme with no background: the canvas it makes is black."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.models import FitMode
    from trcc.services.layers import Picture

    r = QtRenderer()
    canvas = Picture(("theme",), None, False, FitMode.WIDTH).canvas((4, 3), r)
    assert r.surface_size(canvas) == (4, 3)
    assert canvas.pixelColor(3, 2).getRgb() == (0, 0, 0, 255)


def test_a_background_drawn_over_a_canvas_covers_it() -> None:
    """The bottom of the stack makes the canvas; drawn over another, it
    takes that one's size and covers it."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.services.layers import SolidColor

    r = QtRenderer()
    drawn = SolidColor((10, 20, 30)).draw(r.create_surface(5, 2, color=(255, 255, 255, 255)), r)
    assert r.surface_size(drawn) == (5, 2)
    assert drawn.pixelColor(4, 1).getRgb() == (10, 20, 30, 255)


@pytest.mark.parametrize(("mode", "layer"), [
    ("color", "SolidColor"), ("theme", "Picture"), ("transparent", None),
])
def test_background_mode_is_read_in_one_place(tmp_path: Path, mode: str,
                                              layer: str | None) -> None:
    """``background_mode`` becomes the background layer in
    ``_background_layer`` and nowhere else: colour, the picture, or none.
    MUTATION CHECK: map 'transparent' to a black SolidColor -- same pixels,
    so the frame snapshot cannot see it; this does."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.models import Theme

    from .mock_platform import MockPlatform

    key = "0402:3922"
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    app.discover_and_connect()
    app.settings.set_background_mode(key, mode)
    s = app.settings.for_device(key)
    theme = Theme(path=tmp_path, name="t", resolution=(320, 320))
    found = app.display._background_layer(app.devices[key].info, theme, s, (320, 320))
    assert (type(found).__name__ if found is not None else None) == layer


def test_a_live_frame_is_never_the_same_picture_twice() -> None:
    """A cast or stream frame is new every tick: two layers of the very same
    frame object still have different keys, so no cache keyed on a layer can
    hand an old frame back.  MUTATION CHECK: key on ``self.frame`` -- two
    layers of one frame then share a key."""
    from trcc.core.models import RawFrame
    from trcc.services.layers import LivePicture

    frame = RawFrame(data=b"\x00" * 3, width=1, height=1)
    one, two = LivePicture(frame), LivePicture(frame)
    assert one.key() != two.key()
    assert one.key() == one.key()


def test_a_live_frame_is_stretched_to_the_canvas() -> None:
    """The C# stretches the grab into its ``GIFSize`` canvas; a frame the
    panel's size is drawn as it is."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.models import RawFrame
    from trcc.services.layers import LivePicture

    r = QtRenderer()
    small = RawFrame(data=bytes([255, 0, 0]) * (4 * 2), width=4, height=2)
    drawn = LivePicture(small).canvas((8, 6), r)
    assert r.surface_size(drawn) == (8, 6)
    assert drawn.pixelColor(7, 5).getRgb()[:3] == (255, 0, 0)


def test_building_frames_never_draws_into_the_cached_background(tmp_path: Path) -> None:
    """The bg+mask surface is cached and reused frame after frame; a layer
    may paint in place (the spectrum does), so drawing over the cached one
    would burn the overlay into every later frame.  ``composite`` copies its
    base, which is what keeps it clean.  MUTATION CHECK: make
    ``Overlay.draw`` paint a pixel onto the canvas it is given."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App

    from .conftest import renderable_theme
    from .mock_platform import MockPlatform

    key = "0402:3922"
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    app.discover_and_connect()
    device = app.devices[key]
    theme = app.themes.load(renderable_theme(tmp_path / "themes", "T"))
    app.display.build_frame(info=device.info, theme=theme,
                            sensors={"cpu:temp": 40.0}, profile=device.profile)
    cached = app.display.background_surface(key)
    assert cached is not None
    for temp in (41.0, 42.0, 43.0):                  # each rebuilds the overlay
        app.display.build_frame(info=device.info, theme=theme,
                                sensors={"cpu:temp": temp}, profile=device.profile)
    assert app.display.background_surface(key) is cached, "the cache was rebuilt"
    # Against one built fresh, outside the cache -- the first frame drew over
    # the cached surface too, so a "before" taken after it proves nothing.
    clean = app.display._build_bg_mask(device.info, theme, (320, 320))
    assert app.renderer.encode_png(cached) == app.renderer.encode_png(clean)


# ── A module's picture in a box over the theme (item 23, #328) ─────────────


class _Alternates:
    """A module's source: red, then blue, then red ... one frame each ask --
    or, *gone*, a source that has failed for good."""
    name = "alternates"

    def __init__(self, gone: bool = False) -> None:
        self.asked, self.closed, self._gone = 0, 0, gone

    def latest(self) -> Any:
        from trcc.core.models import RawFrame

        self.asked += 1
        if self._gone:
            raise OSError("module stopped")
        rgb = (255, 0, 0) if self.asked % 2 else (0, 0, 255)
        return RawFrame(bytes(rgb) * (8 * 6), 8, 6)

    def close(self) -> None:
        self.closed += 1


def _boxed(tmp_path: Path, source: _Alternates) -> tuple[Any, Any, Any]:
    """A 320x320 panel with *source* in a medium box, bottom right."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.models import InsetChoice

    from .conftest import renderable_theme
    from .mock_platform import MockPlatform

    key = "0402:3922"
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    app.discover_and_connect()
    app.settings.for_device(key).inset = InsetChoice("viz")
    app.media.open_inset(key, cast(Any, source))
    theme = app.themes.load(renderable_theme(tmp_path / "themes", "T"))
    return app, app.devices[key], theme


def test_a_boxed_picture_reaches_the_panel_every_frame(tmp_path: Path) -> None:
    """Each frame carries the box's newest picture, in its corner, with the
    theme around it.  The whole frame is cached on a key; a box left out of
    it sends the first picture forever.  MUTATION CHECK: drop the inset
    keys from ``frame_key`` in ``build_frame``."""
    from trcc.core.geometry import inset_rect

    source = _Alternates()
    app, device, theme = _boxed(tmp_path, source)
    sent, inside, outside = [], [], []
    for _ in range(2):
        sent.append(app.display.build_frame(info=device.info, theme=theme,
                                            sensors={}, profile=device.profile))
        img = app.display.rendered_surface(device.info.key)
        box = inset_rect((320, 320), app.settings.for_device(device.info.key).inset)
        inside.append(img.pixelColor(box.x + box.width // 2,
                                     box.y + box.height // 2).getRgb()[:3])
        outside.append(img.pixelColor(box.x - 5, box.y - 5).getRgb()[:3])
    assert sent[0] != sent[1], "the second frame was the first one again"
    assert inside == [(255, 0, 0), (0, 0, 255)]
    assert outside[0] == outside[1] and outside[0] not in inside


def test_a_boxed_picture_that_is_gone_is_closed_once(tmp_path: Path) -> None:
    """A source failed for good is closed -- one WARNING, not one a frame --
    and the next frames are the theme alone.  MUTATION CHECK: drop the
    ``close_inset`` in ``DisplayService._inset``."""
    source = _Alternates(gone=True)
    app, device, theme = _boxed(tmp_path, source)
    for _ in range(3):
        app.display.build_frame(info=device.info, theme=theme, sensors={},
                                profile=device.profile)
    assert (source.asked, source.closed) == (1, 1)
    assert app.media.inset(device.info.key) is None


def test_a_box_choice_survives_being_saved() -> None:
    """Through trcc.json's own writer and reader."""
    import json

    from trcc.core.models import DeviceSettings, InsetChoice, InsetCorner, InsetSize
    from trcc.services.settings import _device_json, _device_settings_from_dict

    choice = InsetChoice("viz", InsetCorner.TOP_LEFT, InsetSize.LARGE)
    saved = json.loads(json.dumps(_device_json(DeviceSettings(inset=choice))))
    assert _device_settings_from_dict(saved).inset == choice
    assert _device_settings_from_dict(json.loads(json.dumps(
        _device_json(DeviceSettings())))).inset is None


@pytest.mark.parametrize("saved", [
    {"module": "viz", "corner": "middle", "size": "small"},   # a newer TRCC's corner
    {"module": "viz"},                                        # fields missing
    "junk",
])
def test_an_unreadable_box_is_no_box(saved: Any) -> None:
    from trcc.services.settings import _device_settings_from_dict

    assert _device_settings_from_dict({"inset": saved}).inset is None


def test_a_module_draws_in_its_box_through_its_own_host_calls(tmp_path: Path) -> None:
    """A module knows nothing of boxes: where a panel has it in one, the
    calls it already makes -- size, show, draws, source, end -- are the
    box's, and the theme under it is left alone.  Switched off, the box
    closes and stays chosen; ended by the module, it is no longer chosen.
    MUTATION CHECK: drop the box branch from ``App.show_source``."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.models import InsetChoice, ModuleManifest, ThemeBackground
    from trcc.services.modules import ModuleScope

    from .mock_platform import MockPlatform

    key = "87ad:70db"
    app = App(MockPlatform([{"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 11,
                             "sub": 5}], tmp_path), renderer=QtRenderer())
    app.discover_and_connect()
    host = ModuleScope(app, ModuleManifest(key="viz", title="Viz", summary="s",
                                           capabilities=("panels", "background")))
    s = app.settings.for_device(key)
    assert host.panels.size(key) == (854, 480)            # no box: the panel
    app.settings.set_inset(key, InsetChoice("viz"))
    assert host.panels.size(key) == (285, 160)            # a third, its shape
    first = _Alternates()
    host.background.show(key, cast(Any, first), 30)
    assert app.media.inset(key) is first and app.media.live(key) is None
    assert s.background == ThemeBackground()
    assert host.background.draws(key) and host.background.source(key) is first
    assert not app.draws("other", key)
    host.release()
    assert (first.closed, app.media.inset(key), s.inset) == (1, None, InsetChoice("viz"))
    second = _Alternates()
    host.background.show(key, cast(Any, second), 30)
    host.background.end(key)
    assert (second.closed, s.inset, host.background.draws(key)) == (1, None, False)


def test_the_box_sends_only_when_nothing_else_drives_the_panel(tmp_path: Path) -> None:
    """A box on a still theme sends at its module's rate; under anything
    that already sends this panel's frames it sends nothing -- two producers
    on one panel is the "blinking metrics mask" (2026-09-14).  MUTATION
    CHECK: drop the owner test from ``InsetDriver._run``."""
    from trcc.core.events import FrameSent
    from trcc.core.models import PanelSource
    from trcc.services.source_driver import BOX_OWNER, InsetDriver, cadence_owner

    app, device, theme = _boxed(tmp_path, _Alternates())
    key = device.info.key
    app.active_themes[key] = theme
    sent: list[FrameSent] = []
    app.events.subscribe(FrameSent, sent.append)
    driver = InsetDriver(app, key, 1 / 30)
    assert cadence_owner(app, key) == BOX_OWNER
    driver._run(0.0)
    assert len(sent) == 1
    app.media.open_live(key, PanelSource.MODULE, cast(Any, _Alternates()))
    assert cadence_owner(app, key) == "module"
    driver._run(0.0)
    assert len(sent) == 1, "the box sent over a live picture"


def test_letting_the_panel_go_stops_its_box(tmp_path: Path) -> None:
    """A disconnect stops the box's driver and closes its source -- and
    keeps the choice, as every source's.  MUTATION CHECK: drop the box key
    from ``live_task_keys``."""
    from trcc.core.models import InsetChoice, ModuleManifest
    from trcc.services.modules import ModuleScope
    from trcc.services.source_driver import inset_task_key, live_task_keys

    source = _Alternates()
    app, device, _ = _boxed(tmp_path, _Alternates())
    key = device.info.key
    host = ModuleScope(app, ModuleManifest(key="viz", title="Viz", summary="s",
                                           capabilities=("panels", "background")))
    host.background.show(key, cast(Any, source), 30)
    assert inset_task_key(key) in live_task_keys(key)
    app.stop_sources(key)
    assert (source.closed, app.media.inset(key)) == (1, None)
    assert app.settings.for_device(key).inset == InsetChoice("viz")
