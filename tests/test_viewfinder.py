"""The screencast viewfinder: a frame round each casting device's region.

The C#'s ``FormScreenshot``: shown while screencast mode is on unless the hide
flag is set; dragged to move the region; double-clicked to hide.  Its ring
sits OUTSIDE our region (Qt's grab would capture one drawn inside), so the
window is the region plus 5 px all round, masked to the ring alone.

Driven through a real App and bus over a mock 854x480 panel; every state the
frame shows is read from the App, as another UI would leave it.

MUTATION CHECK -- eight ways, MEASURED 2026-10-01; failures in THIS file:

  1. the ring drawn on the region, not round it  →  **3**.
  2. no mask (the middle is part of the window)  →  **1**.
  3. a region sent on every drag step  →  **1**.
  4. the hide flag ignored  →  **2**.
  5. the UI's Qt platform ignored (frames on a Wayland client)  →  **1**.
  6. no look at the devices already casting  →  **1**.
  7. a picked region not fitted to the canvas  →  **1** (+1 in test_gui_panels).
  8. the fleet not holding its bus (a temporary bridge is collected)  →  **1**.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    LcdSnapshot,
    SetScreencastRegion,
    StartScreencast,
    StopScreencast,
)
from trcc.ui import viewfinder
from trcc.ui.bus_bridge import BusBridge
from trcc.ui.viewfinder import RING, ViewfinderFleet

_KEY = "87ad:70db"
_SPECS = [{"vid": "87ad", "pid": "70db", "pm": 11}]       # 854x480


class _Harness:
    def __init__(self, qtbot: Any, tmp_path: Path) -> None:
        self.app = App(MockPlatform(_SPECS, tmp_path, host_sensors=False),
                       renderer=QtRenderer())
        assert self.app.dispatch(ConnectDevice(key=_KEY)).ok
        self.sent: list[SetScreencastRegion] = []
        real = self.app.dispatch

        def counting(command: Any) -> Any:
            if isinstance(command, SetScreencastRegion):
                self.sent.append(command)
            return real(command)

        self.app.dispatch = counting          # type: ignore[method-assign]
        self.bus = BusBridge(self.app.events)
        self.qtbot = qtbot
        self.fleet: ViewfinderFleet | None = None

    def build(self) -> ViewfinderFleet:
        self.fleet = ViewfinderFleet(self.app, self.bus)
        return self.fleet

    def frame(self) -> Any:
        assert self.fleet is not None
        return self.fleet.frame_for(_KEY)

    def visible(self) -> bool:
        frame = self.frame()
        return frame is not None and frame.isVisible()

    def stored(self) -> tuple[int, int, int, int] | None:
        return self.app.dispatch(LcdSnapshot(key=_KEY)).screencast_rect

    def cast(self, x: int = 100, y: int = 50, w: int = 427, h: int = 240) -> None:
        assert self.app.dispatch(StartScreencast(key=_KEY, x=x, y=y, w=w, h=h)).ok
        self.qtbot.waitUntil(self.visible, timeout=2000)


@pytest.fixture
def harness(qtbot: Any, tmp_path: Path) -> Iterator[_Harness]:
    h = _Harness(qtbot, tmp_path)
    h.app.dispatch(SetScreencastRegion(key=_KEY, x=100, y=50, w=427, h=240,
                                       hide_border=False))
    h.sent.clear()
    yield h
    if h.fleet is not None:
        h.fleet.close()
    h.app.dispatch(StopScreencast(key=_KEY))
    h.app.close()


def _mouse(frame: Any, kind: str, local: QPoint,
           cursor: QPoint | None = None) -> None:
    """Deliver one mouse event.  *cursor* is the GLOBAL position -- where the
    pointer is on screen, which does not move with the window under it."""
    at = QPointF(local)
    glob = QPointF(cursor if cursor is not None else frame.mapToGlobal(local))
    held = (Qt.MouseButton.NoButton if kind == "MouseButtonRelease"
            else Qt.MouseButton.LeftButton)
    event = QMouseEvent(getattr(QMouseEvent.Type, kind), at, glob,
                        Qt.MouseButton.LeftButton, held,
                        Qt.KeyboardModifier.NoModifier)
    {"MouseButtonPress": frame.mousePressEvent,
     "MouseMove": frame.mouseMoveEvent,
     "MouseButtonRelease": frame.mouseReleaseEvent,
     "MouseButtonDblClick": frame.mouseDoubleClickEvent}[kind](event)


def test_no_frame_until_the_device_casts(harness: _Harness) -> None:
    harness.build()
    assert harness.frame() is None


def test_a_cast_rings_its_region_from_outside(harness: _Harness) -> None:
    """The window is the region plus RING all round -- never on top of it."""
    harness.build()
    harness.cast()
    frame = harness.frame()
    assert (frame.x(), frame.y(), frame.width(), frame.height()) == (
        100 - RING, 50 - RING, 427 + 2 * RING, 240 + 2 * RING)


def test_the_middle_is_not_part_of_the_window(harness: _Harness) -> None:
    """Masked to the ring: clicks in the region reach the desktop below, and
    nothing of the frame is painted where the cast grabs."""
    harness.build()
    harness.cast()
    mask = harness.frame().mask()
    assert not mask.contains(QPoint(RING + 200, RING + 100))      # the region
    assert not mask.contains(QPoint(RING, RING))                   # its corner
    assert mask.contains(QPoint(1, 1))                             # the ring


def test_stopping_the_cast_takes_the_frame_down(harness: _Harness) -> None:
    harness.build()
    harness.cast()
    harness.app.dispatch(StopScreencast(key=_KEY))
    harness.qtbot.waitUntil(lambda: not harness.visible(), timeout=2000)


def test_the_hide_flag_hides_it_and_clearing_it_shows_it(harness: _Harness) -> None:
    harness.build()
    harness.cast()
    harness.app.dispatch(SetScreencastRegion(key=_KEY, x=100, y=50, w=427,
                                             h=240, hide_border=True))
    harness.qtbot.waitUntil(lambda: not harness.visible(), timeout=2000)
    harness.app.dispatch(SetScreencastRegion(key=_KEY, x=100, y=50, w=427,
                                             h=240, hide_border=False))
    harness.qtbot.waitUntil(harness.visible, timeout=2000)


def test_another_ui_s_edit_moves_the_frame(harness: _Harness) -> None:
    harness.build()
    harness.cast()
    harness.app.dispatch(SetScreencastRegion(key=_KEY, x=300, y=200, w=320,
                                             h=180))
    harness.qtbot.waitUntil(
        lambda: (harness.frame().x(), harness.frame().y()) == (295, 195),
        timeout=2000)


def test_a_drag_sends_one_region_at_the_new_place(harness: _Harness) -> None:
    """Moving the frame by (+40, +30) moves the region by the same, size kept,
    in ONE Command -- the release, never each step of the drag."""
    harness.build()
    harness.cast()
    frame = harness.frame()
    grip = QPoint(2, 2)                                    # on the ring
    start = frame.mapToGlobal(grip)
    _mouse(frame, "MouseButtonPress", grip, start)
    for dx, dy in ((13, 10), (26, 20), (40, 30)):
        _mouse(frame, "MouseMove", grip, start + QPoint(dx, dy))
    assert harness.sent == [], "a drag step sent a region"
    _mouse(frame, "MouseButtonRelease", grip, start + QPoint(40, 30))
    assert len(harness.sent) == 1
    assert harness.stored() == (140, 80, 427, 240)


def test_a_click_without_a_drag_sends_nothing(harness: _Harness) -> None:
    harness.build()
    harness.cast()
    frame = harness.frame()
    _mouse(frame, "MouseButtonPress", QPoint(2, 2))
    _mouse(frame, "MouseButtonRelease", QPoint(2, 2))
    assert harness.sent == []


def test_a_double_click_hides_it_through_the_app(harness: _Harness) -> None:
    """The C# sets ``myYcbk``; ours is the App's flag, so every UI agrees."""
    harness.build()
    harness.cast()
    _mouse(harness.frame(), "MouseButtonDblClick", QPoint(2, 2))
    assert harness.app.dispatch(LcdSnapshot(key=_KEY)).screencast_hide_border
    harness.qtbot.waitUntil(lambda: not harness.visible(), timeout=2000)


def test_a_fleet_built_mid_cast_shows_the_frame_at_once(harness: _Harness) -> None:
    """A UI opened while another one's cast runs: the frame is there."""
    assert harness.app.dispatch(StartScreencast(key=_KEY, x=100, y=50, w=427,
                                                h=240)).ok
    harness.build()
    assert harness.visible()


def test_no_frames_where_a_window_cannot_place_itself(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Wayland client: no frame is ever built, and the fleet says so."""
    monkeypatch.setattr(viewfinder.QGuiApplication, "platformName",
                        staticmethod(lambda: "wayland"))
    fleet = harness.build()
    assert fleet.enabled is False
    harness.app.dispatch(StartScreencast(key=_KEY, x=100, y=50, w=427, h=240))
    harness.qtbot.wait(100)
    assert harness.frame() is None


def test_a_picked_region_is_fitted_to_the_canvas(harness: _Harness) -> None:
    """Both skins' pickers end here: locked to the 854x480 canvas."""
    viewfinder.store_picked_region(harness.app, _KEY, 10, 20, 300, 999)
    assert harness.stored() == (10, 20, 300, 169)


def test_the_fleet_keeps_its_bus_alive(harness: _Harness) -> None:
    """A fleet handed a bridge nobody else holds must still follow the App.

    Found by driving it on a real X server: a temporary ``BusBridge`` was
    collected, its signals with it, and no frame ever appeared.  Both windows
    happen to keep theirs, which is why no test of a window could see it.
    """
    import gc

    harness.fleet = ViewfinderFleet(harness.app, BusBridge(harness.app.events))
    gc.collect()
    harness.cast()
