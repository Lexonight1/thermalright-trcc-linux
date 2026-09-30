"""The gui's screencast controls act on the SELECTED device's own cast.

The window kept one casting flag and one region for every device, filled from
any device's ``ScreencastStarted``.  Driven on 2026-09-30: another UI casts on
B; with A selected, the gui's mic toggle started a cast on A using B's region.
The window now asks the App (``LcdSnapshot.screencast_region``) whenever it
needs to know, and the panel shows the selected device's live cast.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeMic
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice, LcdSnapshot, StartScreencast

_A, _B = "0402:3922", "87ad:70db"
_SPECS = [{"vid": "0402", "pid": "3922", "fbl": 100},
          {"vid": "87ad", "pid": "70db", "pm": 72, "resolution": "480x480"}]
_B_CAST = (10, 20, 300, 200)


@pytest.fixture
def window(tmp_path: Path, qtbot: Any) -> Iterator[Any]:
    """Two LCDs, A selected, and a cast on B started by another UI."""
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(MockPlatform(_SPECS, tmp_path), renderer=QtRenderer())
    app.audio = FakeMic()                   # type: ignore[assignment]
    for key in (_A, _B):
        assert app.dispatch(ConnectDevice(key=key)).ok
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    win._activate_device(_A)
    assert app.dispatch(StartScreencast(key=_B, x=10, y=20, w=300, h=200)).ok
    qtbot.wait(50)                 # the bridge delivers the event queued
    yield win
    win.close()
    app.close()


def _cast(win: Any, key: str) -> Any:
    return win._app.dispatch(LcdSnapshot(key=key)).screencast_region


def _panel(win: Any) -> Any:
    return win.uc_theme_setting.screencast_panel


def test_the_mic_on_A_leaves_B_alone_and_starts_nothing(window: Any) -> None:
    window._on_screencast_audio_toggled(True)

    assert _cast(window, _A) is None, "the mic started a cast on A"
    assert _cast(window, _B) == (*_B_CAST, False), "the mic changed B's cast"


def test_selecting_B_shows_B_s_cast(window: Any, qtbot: Any) -> None:
    window._activate_device(_B)

    assert _panel(window).values() == _B_CAST


def test_the_mic_on_B_reissues_B_s_own_region(window: Any) -> None:
    """The live region, not whatever the fields were edited to since."""
    window._activate_device(_B)
    _panel(window).set_values(x=1, y=2, w=3, h=4)

    window._on_screencast_audio_toggled(True)

    assert _cast(window, _B) == (*_B_CAST, True)


def test_background_on_A_does_not_stop_B(window: Any) -> None:
    window._on_background_toggle(True)

    assert _cast(window, _B) == (*_B_CAST, False)


def test_start_sends_the_region_the_panel_shows(window: Any) -> None:
    _panel(window).set_values(x=5, y=6, w=64, h=64)
    _panel(window).set_audio(True)

    window._on_screencast_toggle(True)

    assert _cast(window, _A) == (5, 6, 64, 64, True)
