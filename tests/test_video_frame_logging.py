"""A playing video logs only through the per-frame family, in both windows.

``VideoAdvanced`` arrives every frame -- 24-30 a second.  On 2026-10-21 the
qtgui wrote three DEBUG lines per frame to the file every ``trcc report`` is
read from (display_panel ``_on_video_advanced`` / ``_show_position``, the
device picker's ``current_key``), and the log rotated every 90 seconds.
Per-frame lines go through ``core.logs.per_frame``; the file never gets them
unless ``-vvv`` asks.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.events import VideoAdvanced
from trcc.core.logs import PER_FRAME_ROOT

_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_KEY = "0402:3922"
_FRAMES, _FPS = 300, 30


@pytest.fixture
def app(tmp_path: Path) -> Iterator[App]:
    from trcc.services.media import Playback

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    app.media._playbacks[_KEY] = Playback(frames=[b"f"] * _FRAMES, fps=_FPS)
    yield app
    app.close()


def _window(face: str, app: App, qtbot: Any) -> Any:
    if face == "gui":
        from trcc.ui.gui.lcd_handler import LCDHandler
        from trcc.ui.gui.trcc_app import TRCCApp

        window = TRCCApp(app=app)
        qtbot.addWidget(window)
        window.replay_initial_devices()
        handler = window._handlers[_KEY]
        assert isinstance(handler, LCDHandler)
        qtbot.waitUntil(lambda: handler._pm.ui_active)
        return window
    from trcc.ui.qtgui.app import MainWindow

    window = MainWindow(app)
    qtbot.addWidget(window)
    return window


@pytest.mark.parametrize("face", ["gui", "qtgui"])
def test_video_frames_log_only_through_the_per_frame_family(
        face: str, app: App, qtbot: Any, caplog: pytest.LogCaptureFixture) -> None:
    """MUTATION CHECK: log display_panel ``_show_position`` with ``log.debug``."""
    window = _window(face, app, qtbot)
    app.events.publish(VideoAdvanced(key=_KEY, cursor=0, frame_count=_FRAMES, fps=_FPS))
    qtbot.wait(100)                 # the first frame may say something once
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        for cursor in range(1, 31):
            app.events.publish(VideoAdvanced(key=_KEY, cursor=cursor,
                                             frame_count=_FRAMES, fps=_FPS))
            qtbot.wait(5)
        qtbot.wait(100)
        records = list(caplog.records)
    window.close()
    outside = sorted({f"{r.levelname} {r.name}:{r.funcName}" for r in records
                      if r.name.startswith("trcc.")
                      and not r.name.startswith(f"{PER_FRAME_ROOT}.")})
    assert outside == []
