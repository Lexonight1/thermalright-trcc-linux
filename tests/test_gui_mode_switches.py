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
    from trcc.ui.gui.lcd_handler import LCDHandler
    from trcc.ui.gui.trcc_app import TRCCApp

    def fake_load(self: Any, device_key: str, path: Path, size: Any, **_k: Any) -> Any:
        playback = Playback(frames=[_encoded_frame(0xFF202020, 320, 320)] * 3)
        self._playbacks[device_key] = playback
        return playback
    monkeypatch.setattr(MediaService, "load_video", fake_load)

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    monkeypatch.setattr(app, "audio", FakeMic())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    show_a_theme(app, _KEY)
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()

    def lcd_shown() -> bool:
        handler = win._handlers[_KEY]
        assert isinstance(handler, LCDHandler)
        return handler._pm.ui_active
    qtbot.waitUntil(lcd_shown)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    monkeypatch.setattr(win, "_clip", clip, raising=False)
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


# ── an element's own switch: °C | °F, 12H | 24H (2026-11-05) ─────────


def _click_side(qtbot: Any, switch: Any, right: bool) -> None:
    """Click one side of a two-way switch, the way a user does."""
    from PySide6.QtCore import QPoint, Qt

    x = switch.width() * 3 // 4 if right else switch.width() // 4
    qtbot.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(x, switch.height() // 2))


def _unit(win: Any) -> str:
    from trcc.core.commands import ControlCenterSnapshot

    return win._app.dispatch(ControlCenterSnapshot()).temp_unit


def test_a_temperature_element_switches_the_temperature_unit(window: Any, qtbot: Any) -> None:
    """Legacy's overlay editor switched °C/°F on a temperature element; the
    cutover left the toggle inert (bd880902, 2026-06-23, "deferred pending a
    decision") and then the button became show/hide only -- the switch was
    lost.  It is the App's ONE unit, as the About page's radios set it, and
    a change from any UI shows on it.  MUTATION CHECK: drop the
    ``uc_theme_setting.temp_unit_changed`` connect in TRCCApp."""
    from trcc.core.commands import SetTempUnit
    from trcc.core.models import OverlayElementConfig, OverlayMode

    ts = window.uc_theme_setting
    table = ts.data_table
    ts._show_element(OverlayElementConfig(id="t", mode=OverlayMode.HARDWARE,
                                          metric="cpu:temp"))
    assert (table.temp_switch.isHidden(), table.unit_switch.isHidden()) == (False, True)

    _click_side(qtbot, table.temp_switch, right=True)
    qtbot.waitUntil(lambda: _unit(window) == "F", timeout=3000)

    window._app.dispatch(SetTempUnit(unit="C"))                  # another UI
    qtbot.waitUntil(lambda: table.temp_switch._chosen == 0, timeout=3000)


def test_any_other_reading_switches_its_own_unit_on_or_off(
        window: Any, qtbot: Any) -> None:
    """A load has no °F: its switch is — | % -- the C#'s show/hide-unit
    (myModeSub 0 bare, 1 with the unit), labelled with the element's OWN
    unit.  MUTATION CHECK: label the right side with a fixed "°C"."""
    from trcc.core.models import OverlayElementConfig, OverlayMode

    ts = window.uc_theme_setting
    ts._show_element(OverlayElementConfig(id="u", mode=OverlayMode.HARDWARE,
                                          metric="cpu:usage", mode_sub=1))
    table = ts.data_table
    assert (table.temp_switch.isHidden(), table.unit_switch.isHidden()) == (True, False)
    assert (table.unit_switch._labels, table.unit_switch._chosen) == (("—", "%"), 1)
    picked: list[tuple[int, int]] = []
    table.format_changed.connect(lambda mode, sub: picked.append((mode, sub)))
    _click_side(qtbot, table.unit_switch, right=False)
    assert picked == [(OverlayMode.HARDWARE, 0)]


def test_a_clock_element_picks_12h_or_24h(window: Any, qtbot: Any) -> None:
    """12H | 24H, the chosen one highlighted; picking sends this clock's own
    format (myModeSub 1 = 12H, 2 = 24H)."""
    from trcc.core.models import OverlayElementConfig, OverlayMode

    ts = window.uc_theme_setting
    table = ts.data_table
    ts._show_element(OverlayElementConfig(id="c", mode=OverlayMode.TIME, mode_sub=1))
    assert (table.time_switch.isHidden(), table.time_switch._chosen) == (False, 0)
    picked: list[tuple[int, int]] = []
    table.format_changed.connect(lambda mode, sub: picked.append((mode, sub)))
    _click_side(qtbot, table.time_switch, right=True)
    assert picked == [(OverlayMode.TIME, 2)]


def test_a_date_element_shows_its_four_formats_and_picks_one(
        window: Any, qtbot: Any) -> None:
    """All four at once, the chosen one highlighted -- the C# cycled one
    image per click.  Picking D/M sends mode_sub 4.  MUTATION CHECK: send
    the index instead of index + 1."""
    from PySide6.QtCore import QPoint, Qt

    from trcc.core.models import OverlayElementConfig, OverlayMode

    ts = window.uc_theme_setting
    table = ts.data_table
    ts._show_element(OverlayElementConfig(id="d", mode=OverlayMode.DATE, mode_sub=2))
    switch = table.date_switch
    assert (switch.isHidden(), switch._labels, switch._chosen) == (
        False, ("Y/M/D", "D/M/Y", "M/D", "D/M"), 1)
    picked: list[tuple[int, int]] = []
    table.format_changed.connect(lambda mode, sub: picked.append((mode, sub)))
    qtbot.mouseClick(switch, Qt.MouseButton.LeftButton,
                     pos=QPoint(switch.width() * 7 // 8, switch.height() // 2))
    assert picked == [(OverlayMode.DATE, 4)]


def test_the_background_panels_box_sets_and_clears_a_module_box(
        window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Box opens on what the App holds, lists the modules that draw
    pictures, and sends ONE Command: SetInset, or ClearInset for Off.
    MUTATION CHECK: drop ``"Box"`` from ``UCThemeSetting``'s action map."""
    import trcc.ui.gui.trcc_app as trcc_app_module

    opened: list[tuple[Any, ...]] = []
    answers = iter([("visualizer", "top-left", "small"), ("", "top-left", "small")])

    class _Dialog:
        def __init__(self, modules: Any, module: str, corner: str, size: str,
                     parent: Any = None) -> None:
            opened.append((tuple(modules), module, corner, size))

        def exec(self) -> Any:
            from PySide6.QtWidgets import QDialog
            return QDialog.DialogCode.Accepted

        def choice(self) -> tuple[str, str, str]:
            return next(answers)

    monkeypatch.setattr(trcc_app_module, "InsetDialog", _Dialog)
    box = window.uc_theme_setting.background_panel._action_buttons[2]
    assert box.text() == "Box" and box.isEnabled()
    box.click()
    snap = _snap(window)
    assert (snap.inset_module, snap.inset_corner, snap.inset_size) == (
        "visualizer", "top-left", "small")
    assert opened[0][1:] == ("", "bottom-right", "medium")      # no box yet
    assert "visualizer" in dict(opened[0][0])
    box.click()
    assert opened[1][1:] == ("visualizer", "top-left", "small")  # what the App holds
    assert _snap(window).inset_module == ""
