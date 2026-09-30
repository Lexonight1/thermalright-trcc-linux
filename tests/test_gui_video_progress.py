"""The gui's video progress bar, through the real window.

It fed a 0..1 fraction into a 0..100 slider, so the thumb sat at 0; it read
the 0..100 slider back as a 0..1 fraction, so every drag seeked to the last
frame; and its label showed frame counts.  It now works as qtgui's does and
as the C#'s player (``UCBoFangQiKongZhi``) does: the slider counts frames, a
drag moves only the label, the seek goes on release, and the label is the
``HH:MM:SS.mmm`` clock.

Every hop is the shipping one: ``VideoAdvanced`` on the App's bus -> the
bridge -> ``LCDHandler`` -> ``UCPreview``, and back through the preview's
delegate -> ``LCDHandler.seek`` -> ``SeekVideo``.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice, VideoStatus
from trcc.core.events import VideoAdvanced

_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_KEY = "0402:3922"
_FRAMES, _FPS = 300, 30


@pytest.fixture
def window(tmp_path: Path, qtbot: Any) -> Iterator[Any]:
    from trcc.services.media import Playback
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    app.media._playbacks[_KEY] = Playback(   # pyright: ignore[reportPrivateUsage]
        frames=[b"f"] * _FRAMES, fps=_FPS)
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    qtbot.waitUntil(lambda: win._handlers[_KEY]._pm.ui_active)
    yield win
    win.close()
    app.close()


def _advance(win: Any, qtbot: Any, cursor: int) -> None:
    """Publish a frame on the App's bus and wait for the window to draw it."""
    win._app.events.publish(VideoAdvanced(
        key=_KEY, cursor=cursor, frame_count=_FRAMES, fps=_FPS))
    # The bridge posts its queued signal inside publish(), so any event
    # processing delivers it -- this wait is not a race.
    qtbot.wait(50)


def test_a_frame_moves_the_thumb_and_reads_as_time(window: Any, qtbot: Any) -> None:
    preview = window.uc_preview
    _advance(window, qtbot, 150)

    slider = preview.progress_slider
    assert (slider.minimum(), slider.maximum(), slider.value()) == (0, 299, 150)
    assert preview.time_label.text() == "00:00:05.000/00:00:10.000"


def test_release_seeks_to_the_frame_under_the_thumb(window: Any, qtbot: Any) -> None:
    _advance(window, qtbot, 150)
    slider = window.uc_preview.progress_slider

    slider.setValue(42)
    slider.sliderReleased.emit()

    assert window._app.dispatch(VideoStatus(key=_KEY)).cursor == 42


def test_a_drag_moves_only_the_label(window: Any, qtbot: Any) -> None:
    _advance(window, qtbot, 150)
    preview = window.uc_preview

    preview.progress_slider.sliderMoved.emit(60)

    assert preview.time_label.text() == "00:00:02.000/00:00:10.000"
    assert window._app.dispatch(VideoStatus(key=_KEY)).cursor == 0, (
        "a drag seeked before the release")


def test_frames_leave_a_held_thumb_alone(window: Any, qtbot: Any) -> None:
    _advance(window, qtbot, 150)
    slider = window.uc_preview.progress_slider
    slider.setSliderDown(True)
    slider.setValue(42)

    _advance(window, qtbot, 151)

    assert slider.value() == 42, "a frame tore the thumb out of the user's hand"
