"""The gui's mode switches show what the App holds, and act through it.

The C# sets five switches from every theme it loads (``FormCZTV.cs:6766``):
background, screencast, overlay, video player, mask -- and all 3411 theme DCs
on disk say background ON, screencast OFF, overlay ON, mask ON.  The gui's
background / screencast / video / mask switches started unchecked and were
only ever set False, so they showed the opposite for every theme; background
OFF dispatched nothing; and the video panel played its file as a background
with the overlay switched off.  Now ``LcdSnapshot.display_source`` decides,
once, and every control is the Command every UI dispatches.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeMic, show_a_theme
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    LcdSnapshot,
    SetMaskVisible,
    SetMediaPlayer,
    StartScreencast,
    StopScreencast,
)

_KEY = "0402:3922"
_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}


@pytest.fixture
def window(tmp_path: Path, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from tests.test_video_playback import _encoded_frame
    from trcc.services.media import MediaService, Playback
    from trcc.ui.gui.trcc_app import TRCCApp

    def fake_load(self: Any, device_key: str, path: Path, size: Any, **_k: Any) -> Any:
        playback = Playback(frames=[_encoded_frame(0xFF202020, 320, 320)] * 3)
        self._playbacks[device_key] = playback
        return playback
    monkeypatch.setattr(MediaService, "load_video", fake_load)

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    app.audio = FakeMic()                   # type: ignore[assignment]
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    show_a_theme(app, _KEY)
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    qtbot.waitUntil(lambda: win._handlers[_KEY]._pm.ui_active)
    win._clip = tmp_path / "clip.mp4"                      # type: ignore[attr-defined]
    win._clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")   # type: ignore[attr-defined]
    yield win
    win.close()
    app.close()


def _switches(win: Any) -> dict[str, bool]:
    ts = win.uc_theme_setting
    return {"background": ts.background_panel.toggle_btn.isChecked(),
            "screencast": ts.screencast_panel.toggle_btn.isChecked(),
            "video": ts.video_panel.toggle_btn.isChecked(),
            "mask": ts.mask_panel.toggle_btn.isChecked()}


def _snap(win: Any) -> Any:
    return win._app.dispatch(LcdSnapshot(key=_KEY))


def _elsewhere(win: Any, qtbot: Any, cmd: Any) -> None:
    """Another UI dispatches *cmd*; wait for the queued events to land."""
    assert win._app.dispatch(cmd).ok
    qtbot.wait(50)


_THEME = {"background": True, "screencast": False, "video": False, "mask": True}


def test_a_theme_shows_what_every_theme_file_says(window: Any) -> None:
    assert _switches(window) == _THEME


def test_the_switches_follow_a_cast_from_another_ui(window: Any, qtbot: Any) -> None:
    _elsewhere(window, qtbot, StartScreencast(key=_KEY, x=0, y=0, w=64, h=64))
    assert _switches(window) == {**_THEME, "background": False, "screencast": True}

    _elsewhere(window, qtbot, StopScreencast(key=_KEY))
    assert _switches(window) == _THEME


def test_the_switches_follow_a_media_player_from_another_ui(
    window: Any, qtbot: Any,
) -> None:
    _elsewhere(window, qtbot, SetMediaPlayer(key=_KEY, uri=str(window._clip)))
    assert _switches(window) == {**_THEME, "background": False, "video": True}

    _elsewhere(window, qtbot, SetMediaPlayer(key=_KEY, uri=""))
    assert _switches(window) == _THEME


def test_the_mask_eye_and_switch_are_one_fact(window: Any, qtbot: Any) -> None:
    _elsewhere(window, qtbot, SetMaskVisible(key=_KEY, visible=False))

    assert _switches(window)["mask"] is False
    assert window.uc_theme_setting.mask_panel._mask_visible is False


def test_background_off_draws_none_and_on_draws_it_again(
    window: Any, qtbot: Any,
) -> None:
    """As the C#'s isDrawBkImage: off was a no-op here."""
    window._on_background_toggle(False)
    qtbot.wait(50)
    assert _snap(window).background_mode == "transparent"
    assert _switches(window)["background"] is False

    window._on_background_toggle(True)
    qtbot.wait(50)
    assert _snap(window).background_mode == "theme"
    assert _switches(window) == _THEME


def test_background_on_closes_the_media_player(window: Any, qtbot: Any) -> None:
    _elsewhere(window, qtbot, SetMediaPlayer(key=_KEY, uri=str(window._clip)))

    window._on_background_toggle(True)
    qtbot.wait(50)

    assert _snap(window).display_source == "background"
    assert _switches(window) == _THEME


def test_the_video_panel_is_the_media_player_and_leaves_the_overlay(
    window: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        lambda *a, **k: (str(window._clip), ""))
    before = _snap(window).overlay_enabled

    window._on_media_player_load_clicked()
    qtbot.wait(50)

    snap = _snap(window)
    assert snap.display_source == "media"
    assert snap.overlay_enabled == before, "the gui switched the overlay off"
    assert _switches(window)["video"] is True


def test_the_video_switch_off_ends_the_media_player(window: Any, qtbot: Any) -> None:
    _elsewhere(window, qtbot, SetMediaPlayer(key=_KEY, uri=str(window._clip)))

    window._on_video_display_toggle(False)
    qtbot.wait(50)

    assert _snap(window).display_source == "background"
    assert _switches(window) == _THEME
