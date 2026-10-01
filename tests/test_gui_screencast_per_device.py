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
from trcc.core.commands import (
    ConnectDevice,
    LcdSnapshot,
    SetOrientation,
    SetScreencastRegion,
    StartScreencast,
)

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


# ── The region is the App's: shown, edited and followed ───────────────


def _rect(win: Any, key: str) -> Any:
    snap = win._app.dispatch(LcdSnapshot(key=key))
    return snap.screencast_rect, snap.screencast_hide_border


def test_selecting_A_shows_A_s_own_region_not_B_s(window: Any) -> None:
    window._activate_device(_B)
    window._activate_device(_A)

    assert _panel(window).values() == _rect(window, _A)[0] != _B_CAST


def test_a_finished_edit_stores_A_s_region_and_leaves_B(window: Any) -> None:
    panel = _panel(window)
    panel.entry_x.setText("7")
    panel.entry_x.editingFinished.emit()

    assert _rect(window, _A)[0][0] == 7
    assert _rect(window, _B)[0] == _B_CAST


def test_another_ui_s_edit_on_A_shows(window: Any, qtbot: Any) -> None:
    window._app.dispatch(SetScreencastRegion(key=_A, x=9, y=8, w=64, h=64,
                                             hide_border=False))
    qtbot.wait(50)

    assert _panel(window).values() == (9, 8, 64, 64)
    assert _panel(window)._hide_border is False


def test_another_ui_s_edit_on_B_leaves_A_s_fields(window: Any, qtbot: Any) -> None:
    before = _panel(window).values()
    window._app.dispatch(SetScreencastRegion(key=_B, x=1, y=1, w=50, h=50))
    qtbot.wait(50)

    assert _panel(window).values() == before


def test_the_border_button_sets_the_app_s_flag(window: Any) -> None:
    assert _rect(window, _A)[1] is True
    _panel(window).border_btn.click()

    assert _rect(window, _A) == (_panel(window).values(), False)


def test_a_refused_edit_puts_the_app_s_region_back(window: Any) -> None:
    """0..9999 is the C#'s field range; past it the App refuses, and the
    panel shows what the App kept rather than what was typed."""
    kept = _rect(window, _A)[0]
    _panel(window).entry_w.setText("10000")      # setText skips the validator
    _panel(window).entry_w.editingFinished.emit()

    assert _rect(window, _A)[0] == kept
    assert _panel(window).values() == kept


# A non-square, portrait-MOUNTED panel: the lock follows the App's canvas,
# which turns with the orientation.  It used to lock to the native size.
_WIDE = "87ad:70db"


@pytest.fixture
def wide(tmp_path: Path, qtbot: Any) -> Iterator[Any]:
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(MockPlatform([{"vid": "87ad", "pid": "70db", "resolution": "854x480",
                             "pm": 11, "sub": 5}], tmp_path), renderer=QtRenderer())
    app.audio = FakeMic()                   # type: ignore[assignment]
    assert app.dispatch(ConnectDevice(key=_WIDE)).ok
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    win._activate_device(_WIDE)
    yield win
    win.close()
    app.close()


@pytest.mark.parametrize("degrees", [0, 90])
def test_the_lock_follows_the_app_s_canvas(wide: Any, qtbot: Any, degrees: int) -> None:
    wide._app.dispatch(SetOrientation(key=_WIDE, degrees=degrees))
    qtbot.wait(50)
    canvas = wide._app.dispatch(LcdSnapshot(key=_WIDE)).screencast_canvas

    _panel(wide).entry_w.setText("200")

    assert _panel(wide).entry_h.text() == str(round(200 * canvas[1] / canvas[0]))


def test_on_wayland_the_border_button_picks_a_region_instead(
    window: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No frame can place itself, so the button opens the drag picker, and
    the picked rectangle is stored -- the hide flag it would have toggled is
    left alone."""
    from PySide6.QtCore import QObject, Signal

    import trcc.ui.gui.trcc_app as gui_app

    class _Picker(QObject):
        region_selected = Signal(int, int, int, int)

    opened: list[_Picker] = []

    def fake_open(parent: Any) -> _Picker:
        opened.append(_Picker())
        return opened[-1]

    monkeypatch.setattr(gui_app, "open_picker", fake_open)
    window._viewfinders.enabled = False
    before = window._app.dispatch(LcdSnapshot(key=_A)).screencast_hide_border

    window._on_screencast_border_toggled(not before)

    assert len(opened) == 1
    assert window._app.dispatch(LcdSnapshot(key=_A)).screencast_hide_border == before
    opened[0].region_selected.emit(10, 20, 200, 999)
    assert window._app.dispatch(LcdSnapshot(key=_A)).screencast_rect == (
        10, 20, 200, 200)                      # A is the 320x320 panel
