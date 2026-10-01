"""qtgui's screencast region has typed X / Y / W / H fields, as gui does.

qtgui could only DRAG a region (``RegionSelectOverlay``) and showed the result
as a read-only label, so a region could not be typed or nudged a pixel at a
time -- the C#'s ``UCTouPingXianShi`` has four boxes and eight +/- buttons, and
gui ports them.  The fields here are spin boxes: their arrows are the nudges.

Driven through a REAL ``ScreencastPanel`` over the real App and bus on a mock
854x480 bulk panel, whose canvas the App reports as (854, 480) and whose region
starts at the C#'s off-aspect default, (0, 0, 320, 240).

MUTATION CHECK -- four ways, MEASURED 2026-10-01; failures in THIS file:

  1. keyboard tracking back on (a send per keystroke)  →  **1**.
  2. W/H edits stop locking the other edge  →  **2**.
  3. the refresh from the App is not signal-blocked (it echoes)  →  **3**.
  4. an X/Y nudge locks too (re-shapes the C#'s off-aspect default)  →  **1**.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.mock_platform import MockPlatform
from trcc.app import App
from trcc.core.commands import ConnectDevice, LcdSnapshot, SetScreencastRegion
from trcc.ui.bus_bridge import BusBridge
from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

_KEY = "87ad:70db"
_SPECS = [{"vid": "87ad", "pid": "70db", "pm": 11}]       # 854x480


class _Harness:
    """A real panel over a real App, counting the region Commands it sends."""

    def __init__(self, qtbot: Any, tmp_path: Path) -> None:
        self.app = App(MockPlatform(_SPECS, tmp_path, host_sensors=False))
        assert self.app.dispatch(ConnectDevice(key=_KEY)).ok
        self.sent: list[SetScreencastRegion] = []
        real = self.app.dispatch

        def counting(command: Any) -> Any:
            if isinstance(command, SetScreencastRegion):
                self.sent.append(command)
            return real(command)

        self.app.dispatch = counting          # type: ignore[method-assign]
        self.bus = BusBridge(self.app.events)
        self.panel = ScreencastPanel(self.app, self.bus)
        qtbot.addWidget(self.panel)
        self.qtbot = qtbot

    def stored(self) -> tuple[int, int, int, int] | None:
        """The region the App holds -- what every UI shows."""
        return self.app.dispatch(LcdSnapshot(key=_KEY)).screencast_rect

    def shown(self) -> tuple[int, ...]:
        return tuple(self.panel._fields[a].value() for a in "xywh")

    def type_into(self, axis: str, text: str, *, enter: bool = True) -> None:
        """Type into a field as a user does: select all, keys, then Enter."""
        editor = self.panel._fields[axis].lineEdit()
        editor.selectAll()
        self.qtbot.keyClicks(editor, text)
        if enter:
            self.qtbot.keyClick(editor, Qt.Key.Key_Return)


@pytest.fixture
def harness(qtbot: Any, tmp_path: Path) -> Iterator[_Harness]:
    h = _Harness(qtbot, tmp_path)
    yield h
    h.app.close()


def test_the_fields_show_the_App_s_region(harness: _Harness) -> None:
    assert harness.panel._picker.current_key() == _KEY
    assert harness.shown() == harness.stored() == (0, 0, 320, 240)


def test_a_typed_width_locks_the_height_and_is_stored(harness: _Harness) -> None:
    """300 across an 854x480 canvas is round(300 * 480 / 854) = 169 down."""
    harness.type_into("w", "300")
    assert harness.stored() == (0, 0, 300, 169)
    assert harness.shown() == (0, 0, 300, 169)


def test_a_typed_height_locks_the_width(harness: _Harness) -> None:
    """The other edge leading: 120 down is round(120 * 854 / 480) = 214 across."""
    harness.type_into("h", "120")
    assert harness.stored() == (0, 0, 214, 120)


def test_typing_sends_once_on_enter_not_per_keystroke(harness: _Harness) -> None:
    """"320" half-typed is not a region anyone asked for."""
    harness.type_into("x", "25", enter=False)
    assert harness.sent == []
    harness.qtbot.keyClick(harness.panel._fields["x"].lineEdit(), Qt.Key.Key_Return)
    assert [c.x for c in harness.sent] == [25]


def test_an_arrow_is_a_one_pixel_nudge_that_moves_and_locks_nothing(
    harness: _Harness,
) -> None:
    """X and Y move the region; the off-aspect default keeps its height."""
    harness.panel._fields["x"].stepUp()
    harness.panel._fields["y"].stepUp()
    assert harness.stored() == (1, 1, 320, 240)


def test_the_fields_clamp_where_the_C_sharp_does(harness: _Harness) -> None:
    field = harness.panel._fields["x"]
    assert (field.minimum(), field.maximum()) == (0, 9999)


def test_an_unchanged_field_losing_focus_sends_nothing(harness: _Harness) -> None:
    harness.panel._fields["w"].editingFinished.emit()
    assert harness.sent == []


def test_another_UI_s_edit_shows_here_and_is_not_echoed(harness: _Harness) -> None:
    """The fields are refilled quietly: showing a region is not editing it."""
    harness.app.dispatch(SetScreencastRegion(key=_KEY, x=5, y=6, w=427, h=240))
    harness.qtbot.waitUntil(lambda: harness.shown() == (5, 6, 427, 240),
                            timeout=2000)
    assert len(harness.sent) == 1, "the panel echoed the region it was shown"
