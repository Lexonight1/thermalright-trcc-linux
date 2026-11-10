"""ThemeSettings -- what the device Settings panel sends, for every GUI.

Plain pytest, no Qt: a fake bus records each Command and answers from a table,
so every test says exactly which Commands a control sends, in what order.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from trcc.core.commands import (
    AddOverlayElement,
    ApplyMask,
    ClearInset,
    DeleteOverlayElement,
    EnableOverlay,
    LcdSnapshot,
    RecentColors,
    RememberColor,
    ResolveOverlay,
    SetBackground,
    SetInset,
    SetMaskPosition,
    SetMaskVisible,
    SetMediaPlayer,
    SetScreencastRegion,
    SetTempUnit,
    ShowBackground,
    StartScreencast,
    StopScreencast,
    UpdateOverlayElement,
    UploadCustomMask,
)
from trcc.core.ports import CommandBus
from trcc.ui.presentation.theme_settings import ElementDrag, ThemeSettings, nudge

KEY = "0402:3922"


class _Bus:
    """Records every dispatched Command; answers by Command type."""

    def __init__(self, **answers: Any) -> None:
        self.sent: list[Any] = []
        self._answers = answers

    def dispatch(self, command: Any) -> Any:
        self.sent.append(command)
        return self._answers.get(type(command).__name__, SimpleNamespace(ok=True))


def _settings(**answers: Any) -> tuple[ThemeSettings, _Bus]:
    bus = _Bus(**answers)
    return ThemeSettings(cast(CommandBus, bus)), bus


@pytest.mark.parametrize(("action", "sent"), [
    (lambda s: s.set_overlay(KEY, False), EnableOverlay(key=KEY, enabled=False)),
    (lambda s: s.set_temp_unit("F"), SetTempUnit(unit="F")),
    (lambda s: s.set_mask_visible(KEY, False), SetMaskVisible(key=KEY, visible=False)),
    (lambda s: s.set_mask_position(KEY, 3, 4), SetMaskPosition(key=KEY, x=3, y=4)),
    (lambda s: s.show_background(KEY, True), ShowBackground(key=KEY, shown=True)),
    (lambda s: s.set_background(KEY, Path("/a.png")),
     SetBackground(key=KEY, path=Path("/a.png"))),
    (lambda s: s.set_inset(KEY, "weather", "top_left", "small"),
     SetInset(key=KEY, module="weather", corner="top_left", size="small")),
    (lambda s: s.set_inset(KEY, "", "top_left", "small"), ClearInset(key=KEY)),
    (lambda s: s.play_media(KEY, "rtsp://cam"), SetMediaPlayer(key=KEY, uri="rtsp://cam")),
    (lambda s: s.stop_media(KEY), SetMediaPlayer(key=KEY, uri="")),
    (lambda s: s.start_cast(KEY, (1, 2, 30, 40), True),
     StartScreencast(key=KEY, x=1, y=2, w=30, h=40, audio=True)),
    (lambda s: s.stop_cast(KEY), StopScreencast(key=KEY)),
    (lambda s: s.set_region(KEY, (1, 2, 30, 40)),
     SetScreencastRegion(key=KEY, x=1, y=2, w=30, h=40)),
], ids=["overlay", "temp-unit", "mask-visible", "mask-position", "background-switch",
        "background", "box", "box-off", "media", "media-off", "cast", "cast-off", "region"])
def test_a_control_sends_its_one_command(action: Any, sent: Any) -> None:
    settings, bus = _settings()
    action(settings)
    assert bus.sent == [sent]


# ── An edit and the overlay switch ─────────────────────────────────────

_ADD = partial(AddOverlayElement, type="text", text="Hi", element_id="a")


def test_an_edit_to_a_dark_overlay_switches_it_on() -> None:
    settings, bus = _settings(ResolveOverlay=SimpleNamespace(ok=True, elements=("a",)))
    assert settings.edit_element(KEY, _ADD, overlay_on=False).overlay_on is True
    assert bus.sent == [_ADD(key=KEY), ResolveOverlay(key=KEY),
                        EnableOverlay(key=KEY, enabled=True)]


def test_deleting_the_last_element_does_not_switch_it_on() -> None:
    settings, bus = _settings(ResolveOverlay=SimpleNamespace(ok=True, elements=()))
    delete = partial(DeleteOverlayElement, element_id="a")
    assert settings.edit_element(KEY, delete, overlay_on=False).overlay_on is False
    assert bus.sent == [delete(key=KEY), ResolveOverlay(key=KEY)]


def test_an_edit_to_a_lit_overlay_is_the_edit_alone() -> None:
    settings, bus = _settings()
    edit = partial(UpdateOverlayElement, element_id="a", x=5)
    assert settings.edit_element(KEY, edit, overlay_on=True).overlay_on is True
    assert bus.sent == [edit(key=KEY)]


def test_a_refused_edit_switches_nothing() -> None:
    refused = SimpleNamespace(ok=False)
    settings, bus = _settings(AddOverlayElement=refused)
    assert settings.edit_element(KEY, _ADD, overlay_on=False) == (refused, False)
    assert bus.sent == [_ADD(key=KEY)]


@pytest.mark.parametrize(("elements", "on"), [(("a",), True), ((), False)])
def test_a_loaded_layout_sets_the_switch(elements: tuple, on: bool) -> None:
    settings, bus = _settings(ResolveOverlay=SimpleNamespace(ok=True, elements=elements))
    assert settings.follow_loaded_layout(KEY) is on
    assert bus.sent == [ResolveOverlay(key=KEY), EnableOverlay(key=KEY, enabled=on)]


@pytest.mark.parametrize(("custom", "applied"), [
    (True, UploadCustomMask(key=KEY, source=Path("/m"))),
    (False, ApplyMask(key=KEY, path=Path("/m"))),
])
def test_a_mask_applied_sets_the_switch_from_its_layout(custom: bool, applied: Any) -> None:
    settings, bus = _settings(ResolveOverlay=SimpleNamespace(ok=True, elements=("a",)))
    assert settings.apply_mask(KEY, Path("/m"), custom=custom).ok
    assert bus.sent == [applied, ResolveOverlay(key=KEY), EnableOverlay(key=KEY, enabled=True)]


def test_a_refused_mask_leaves_the_switch() -> None:
    settings, bus = _settings(ApplyMask=SimpleNamespace(ok=False))
    settings.apply_mask(KEY, Path("/m"), custom=False)
    assert bus.sent == [ApplyMask(key=KEY, path=Path("/m"))]


# ── Colours ────────────────────────────────────────────────────────────

def test_a_remembered_colour_answers_the_recent_row() -> None:
    row = ((1, 2, 3), (4, 5, 6))
    settings, bus = _settings(RememberColor=SimpleNamespace(ok=True, colors=row))
    assert settings.remember_colour(KEY, (1, 2, 3)) == row
    assert bus.sent == [RememberColor(key=KEY, color=(1, 2, 3))]


def test_a_refused_colour_answers_none() -> None:
    settings, bus = _settings(RecentColors=SimpleNamespace(ok=False, colors=()))
    assert settings.recent_colours(KEY) is None
    assert bus.sent == [RecentColors(key=KEY)]


# ── The cast's sound and border ────────────────────────────────────────

def test_sound_during_a_cast_reissues_that_devices_own_cast() -> None:
    live = SimpleNamespace(ok=True, screencast_region=(5, 6, 70, 80, False))
    settings, bus = _settings(LcdSnapshot=live)
    settings.set_cast_audio(KEY, True)
    assert bus.sent == [LcdSnapshot(key=KEY),
                        StartScreencast(key=KEY, x=5, y=6, w=70, h=80, audio=True)]


def test_sound_with_no_cast_sends_nothing() -> None:
    settings, bus = _settings(LcdSnapshot=SimpleNamespace(ok=True, screencast_region=None))
    assert settings.set_cast_audio(KEY, True) is None
    assert bus.sent == [LcdSnapshot(key=KEY)]


def test_the_border_goes_with_the_apps_region() -> None:
    held = SimpleNamespace(ok=True, screencast_rect=(1, 2, 3, 4))
    settings, bus = _settings(LcdSnapshot=held)
    settings.set_hide_border(KEY, True)
    assert bus.sent[-1] == SetScreencastRegion(key=KEY, x=1, y=2, w=3, h=4,
                                               hide_border=True)


def test_no_region_no_border() -> None:
    settings, bus = _settings(LcdSnapshot=SimpleNamespace(ok=True, screencast_rect=None))
    assert settings.set_hide_border(KEY, True) is None
    assert bus.sent == [LcdSnapshot(key=KEY)]


# ── Moving an element ──────────────────────────────────────────────────

def test_a_dragged_element_follows_the_pointer_and_stays_on_the_canvas() -> None:
    drag = ElementDrag(origin=(100, 100), start=(50, 60), canvas=(320, 240))
    assert drag.to(110, 95) == (60, 55)
    assert drag.to(-500, 900) == (0, 240)


def test_a_nudge_stays_on_the_canvas() -> None:
    assert nudge((10, 10), -1, 1, (320, 240)) == (9, 11)
    assert nudge((0, 240), -1, 1, (320, 240)) == (0, 240)
