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


# ── Pause / resume from any UI (2026-10-02) ──────────────────────────────────


def _icon_is(button: Any, image: Any) -> bool:
    """Whether *button* shows *image* (an icon path or pixmap) right now."""
    from PySide6.QtGui import QIcon
    return (button.icon().pixmap(24).toImage()
            == QIcon(image).pixmap(24).toImage())


def test_the_gui_follows_a_pause_from_another_ui(window: Any, qtbot: Any) -> None:
    """``PauseVideo`` published nothing, so the gui showed whatever ITS OWN
    last toggle returned: paused from the CLI or qtgui, its button still said
    playing and its metric refreshes did not redraw the held frame.

    MUTATION CHECK -- MEASURED 2026-10-02: drop the PauseVideo publish, or the
    gui's ``video_pause_changed`` hookup → fails.
    """
    from trcc.core.commands import PauseVideo

    handler = window._handlers[_KEY]
    btn = window.uc_preview.play_btn
    play_img, pause_img = btn._img_refs[0], btn._img_refs[1]

    window._app.dispatch(PauseVideo(key=_KEY, paused=False))
    qtbot.waitUntil(lambda: handler._video_playing and _icon_is(btn, pause_img),
                    timeout=3000)

    window._app.dispatch(PauseVideo(key=_KEY, paused=True))
    qtbot.waitUntil(lambda: not handler._video_playing
                    and _icon_is(btn, play_img), timeout=3000)


def test_qtgui_s_button_says_what_a_click_will_do_wherever_the_pause_came_from(
    tmp_path: Path, qtbot: Any,
) -> None:
    """The button read "Pause/Resume" whatever the state -- qtgui showed no
    pause state at all.  It now names the next action, on open and when any
    UI toggles.

    MUTATION CHECK -- MEASURED 2026-10-02: drop qtgui's
    ``video_pause_changed`` hookup → fails.
    """
    from trcc.core.commands import PauseVideo
    from trcc.services.media import Playback
    from trcc.ui.bus_bridge import BusBridge
    from trcc.ui.qtgui.panels.display_panel import DisplayPanel

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        app.media._playbacks[_KEY] = Playback(   # pyright: ignore[reportPrivateUsage]
            frames=[b"f"] * _FRAMES, fps=_FPS)
        panel = DisplayPanel(app, BusBridge(app.events))
        qtbot.addWidget(panel)
        qtbot.waitUntil(lambda: panel._picker.current_key() == _KEY)
        button = panel._pause_video_btn

        app.dispatch(PauseVideo(key=_KEY, paused=True))
        qtbot.waitUntil(lambda: button.text() == "Resume", timeout=3000)
        app.dispatch(PauseVideo(key=_KEY, paused=False))
        qtbot.waitUntil(lambda: button.text() == "Pause", timeout=3000)
    finally:
        app.close()


def test_a_key_step_on_the_bar_seeks(window: Any, qtbot: Any) -> None:
    """A groove click or an arrow/page key moves the value without a press on
    the handle, so neither ``sliderMoved`` nor ``sliderReleased`` fires -- it
    seeked nothing, and the next frame snapped the thumb back."""
    from PySide6.QtCore import Qt

    _advance(window, qtbot, 150)
    slider = window.uc_preview.progress_slider
    slider.setFocus()

    qtbot.keyClick(slider, Qt.Key.Key_Right)

    assert window._app.dispatch(VideoStatus(key=_KEY)).cursor == 151


def test_a_held_thumb_seeks_only_on_release(window: Any, qtbot: Any) -> None:
    """Value changes during a drag must not seek, or a drag would rebuild the
    frame once per step; the release seeks once."""
    _advance(window, qtbot, 150)
    slider = window.uc_preview.progress_slider
    slider.setSliderDown(True)
    slider.setValue(60)
    assert window._app.dispatch(VideoStatus(key=_KEY)).cursor == 0

    slider.setSliderDown(False)          # Qt emits sliderReleased here

    assert window._app.dispatch(VideoStatus(key=_KEY)).cursor == 60


def test_a_frame_from_playback_seeks_nothing(window: Any, qtbot: Any) -> None:
    """The thumb follows playback with signals blocked, so following it is
    never mistaken for the user asking to seek."""
    _advance(window, qtbot, 150)
    _advance(window, qtbot, 200)

    assert window._app.dispatch(VideoStatus(key=_KEY)).cursor == 0


def test_qtgui_s_bar_seeks_on_a_key_step_too(tmp_path: Path, qtbot: Any) -> None:
    """Same defect in qtgui's display panel: only a handle drag seeked."""
    from PySide6.QtCore import Qt

    from trcc.services.media import Playback
    from trcc.ui.bus_bridge import BusBridge
    from trcc.ui.qtgui.panels.display_panel import DisplayPanel

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    try:
        assert app.dispatch(ConnectDevice(key=_KEY)).ok
        app.media._playbacks[_KEY] = Playback(   # pyright: ignore[reportPrivateUsage]
            frames=[b"f"] * _FRAMES, fps=_FPS)
        panel = DisplayPanel(app, BusBridge(app.events))
        qtbot.addWidget(panel)
        qtbot.waitUntil(lambda: panel._picker.current_key() == _KEY)
        panel._show_position(150, _FRAMES, _FPS)
        assert app.dispatch(VideoStatus(key=_KEY)).cursor == 0   # following, not seeking
        panel._seek.setFocus()

        qtbot.keyClick(panel._seek, Qt.Key.Key_Right)

        assert app.dispatch(VideoStatus(key=_KEY)).cursor == 151
    finally:
        app.close()
