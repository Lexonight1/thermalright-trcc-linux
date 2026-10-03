"""The gui's brightness slider -- the C#'s ``UCScrollB`` (UCScrollB.cs).

A 0-100 track from x=72 to x=172 (one pixel per percent) and a number box.
It sends once per gesture: on release, or on finishing the number -- never per
mouse move, since ``SetBrightness`` persists.  Showing the App's value sends
nothing.

MUTATION CHECK -- MEASURED 2026-10-02: emit on every move → the drag test
fails; emit from ``show_value`` → the silent-show test fails.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from trcc.ui.gui.uc_brightness import UCBrightness


def _slider(qtbot: Any) -> tuple[UCBrightness, list[int]]:
    slider = UCBrightness()
    qtbot.addWidget(slider)
    sent: list[int] = []
    slider.changed.connect(sent.append)
    return slider, sent


def _mouse(slider: UCBrightness, kind: str, x: int) -> None:
    held = (Qt.MouseButton.NoButton if kind == "MouseButtonRelease"
            else Qt.MouseButton.LeftButton)
    event = QMouseEvent(getattr(QMouseEvent.Type, kind), QPointF(QPoint(x, 12)),
                        QPointF(slider.mapToGlobal(QPoint(x, 12))),
                        Qt.MouseButton.LeftButton, held,
                        Qt.KeyboardModifier.NoModifier)
    {"MouseButtonPress": slider.mousePressEvent,
     "MouseMove": slider.mouseMoveEvent,
     "MouseButtonRelease": slider.mouseReleaseEvent}[kind](event)


def test_a_drag_moves_the_value_and_sends_once_on_release(qtbot: Any) -> None:
    slider, sent = _slider(qtbot)
    _mouse(slider, "MouseButtonPress", 72 + 10)
    for x in (72 + 20, 72 + 35, 72 + 60):
        _mouse(slider, "MouseMove", x)
    assert slider.value == 60 and slider._box.text() == "60"
    assert sent == [], "a drag step sent a value"

    _mouse(slider, "MouseButtonRelease", 72 + 60)

    assert sent == [60]


def test_the_track_clamps_to_0_and_100(qtbot: Any) -> None:
    slider, sent = _slider(qtbot)
    _mouse(slider, "MouseButtonPress", 5)
    _mouse(slider, "MouseButtonRelease", 5)
    _mouse(slider, "MouseButtonPress", 179)
    _mouse(slider, "MouseButtonRelease", 179)
    assert sent == [0, 100]


def test_a_typed_value_is_sent_when_finished(qtbot: Any) -> None:
    slider, sent = _slider(qtbot)
    slider._box.setText("45")
    slider._box.editingFinished.emit()
    assert sent == [45] and slider.value == 45


def test_showing_the_app_s_value_sends_nothing(qtbot: Any) -> None:
    slider, sent = _slider(qtbot)
    slider.show_value(25)
    assert (slider.value, slider._box.text(), sent) == (25, "25", [])
