"""The gui LED panel shows what the App holds, on open and after any UI.

Measured before: the panel followed nothing; on a PA120 whose edits reach one
zone it showed zone 0 instead of the global values FormLED shows (FormLED.cs
:1883-1897); an LC2's 12/24 h and week start and an LC1's DDR ratio were never
loaded; the preview's mode moved only on a click.

Drives the real window offscreen against the mock platform.  A change is
dispatched on the App, exactly what another UI's Command does.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    ConnectDevice,
    SelectZone,
    SetClockFormat,
    SetLedBrightness,
    SetLedColor,
    SetLedMode,
    SetLedZoneSync,
    SetLedZoneSyncZones,
    SetMemoryRatio,
    SetWeekStart,
    ToggleLed,
)
from trcc.core.commands._base import Query
from trcc.core.led_models import LEDMode

_KEY = "0416:8001"
_PA120, _AX120, _LC2, _LC1 = 16, 1, 112, 128


def _rgb(panel: Any) -> tuple[int, ...]:
    return tuple(s.value() for s in panel._rgb_sliders)


# row: (style PM, what another UI sends, how the panel shows it, what it must show)
_ROWS: dict[str, tuple[int, Callable[[], Any], Callable[[Any], Any], Any]] = {
    "PA120 colour": (_PA120, lambda: SetLedColor(key=_KEY, color=(90, 80, 70)),
                     _rgb, (90, 80, 70)),
    "PA120 brightness": (_PA120, lambda: SetLedBrightness(key=_KEY, percent=75),
                         lambda p: p._brightness_slider.value(), 75),
    "PA120 off": (_PA120, lambda: ToggleLed(key=_KEY, on=False),
                  lambda p: p._color_wheel._onoff, 0),
    "PA120 effect": (_PA120, lambda: SetLedMode(key=_KEY, mode=LEDMode.RAINBOW),
                     lambda p: (p._current_mode, p._preview._led_mode),
                     (LEDMode.RAINBOW.value, LEDMode.RAINBOW.value)),
    "AX120 carousel": (_AX120, lambda: SetLedZoneSync(key=_KEY, enabled=True),
                       lambda p: p._carousel_btn.isChecked(), True),
    "AX120 page": (_AX120, lambda: SelectZone(key=_KEY, zone=2),
                   lambda p: p._zones.display_enabled[:3], [False, False, True]),
    "LC2 12-hour clock": (_LC2, lambda: SetClockFormat(key=_KEY, is_24h=False),
                          lambda p: (p._btn_24h.isChecked(), p._btn_12h.isChecked()),
                          (False, True)),
    "LC2 Sunday first": (_LC2, lambda: SetWeekStart(key=_KEY, sunday_first=True),
                         lambda p: (p._btn_sun.isChecked(), p._btn_mon.isChecked()),
                         (True, False)),
    "LC1 DDR ratio": (_LC1, lambda: SetMemoryRatio(key=_KEY, ratio=4),
                      lambda p: (p._memory_ratio, p._ddr_combo.currentIndex()), (4, 2)),
}


def _app(tmp_path: Path, pm: int) -> App:
    app = App(MockPlatform([{"type": "led", "vid": "0416", "pid": "8001", "pm": pm}],
                           tmp_path), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    return app


def _open(app: App, qtbot: Any) -> Any:
    from trcc.ui.gui.trcc_app import TRCCApp

    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    qtbot.waitUntil(lambda: _KEY in win._handlers)
    win._activate_device(_KEY)
    qtbot.waitUntil(lambda: win._handlers[_KEY].active)
    return win


@pytest.fixture
def opened(tmp_path: Path, qtbot: Any) -> Iterator[Callable[[int], tuple[Any, Any, App]]]:
    windows: list[Any] = []

    def open_style(pm: int) -> tuple[Any, Any, App]:
        app = _app(tmp_path / str(pm), pm)
        win = _open(app, qtbot)
        windows.append(win)
        return win, win.uc_led_control, app
    yield open_style
    for win in windows:
        win.close()


def _commands_sent(app: App, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every Command (not Query, not the App's own re-render) from now on."""
    sent: list[str] = []
    dispatch = app.dispatch

    def recording(cmd: Any) -> Any:
        if not isinstance(cmd, Query) and type(cmd).__name__ != "RenderLed":
            sent.append(type(cmd).__name__)
        return dispatch(cmd)

    monkeypatch.setattr(app, "dispatch", recording)
    return sent


@pytest.mark.parametrize("row", list(_ROWS))
def test_the_led_panel_shows_a_change_another_ui_made(
    opened: Any, qtbot: Any, row: str,
) -> None:
    pm, send, shown, expected = _ROWS[row]
    _win, panel, app = opened(pm)
    assert shown(panel) != expected, "the row proves nothing: already showing it"

    app.dispatch(send())

    qtbot.waitUntil(lambda: shown(panel) == expected, timeout=3000)


@pytest.mark.parametrize("row", list(_ROWS))
def test_opening_the_led_panel_shows_what_the_app_holds(
    tmp_path: Path, qtbot: Any, row: str,
) -> None:
    pm, send, shown, expected = _ROWS[row]
    app = _app(tmp_path, pm)
    assert app.dispatch(send()).ok

    win = _open(app, qtbot)

    assert shown(win.uc_led_control) == expected
    win.close()


@pytest.mark.parametrize("row", list(_ROWS))
def test_following_an_led_change_sends_nothing_back(
    opened: Any, qtbot: Any, row: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm, send, shown, expected = _ROWS[row]
    _win, panel, app = opened(pm)
    app.dispatch(send())
    sent = _commands_sent(app, monkeypatch)     # after the send: only the panel's

    qtbot.waitUntil(lambda: shown(panel) == expected, timeout=3000)
    qtbot.wait(200)

    assert sent == []


def test_a_pa120_edited_one_zone_at_a_time_shows_the_global_values(
    tmp_path: Path, qtbot: Any,
) -> None:
    """Zone 0 is not what the edit reached; FormLED shows the global value."""
    app = _app(tmp_path, _PA120)
    app.dispatch(SetLedZoneSyncZones(key=_KEY, zones=(False, True, False, False)))
    app.dispatch(SetLedZoneSync(key=_KEY, enabled=False))
    app.dispatch(SetLedColor(key=_KEY, color=(10, 20, 30)))
    app.dispatch(SetLedBrightness(key=_KEY, percent=40))

    win = _open(app, qtbot)

    panel = win.uc_led_control
    assert (_rgb(panel), panel._brightness_slider.value()) == ((10, 20, 30), 40)
    win.close()


# ── A control the user is holding is not pulled back ─────────────────────


def test_a_held_colour_slider_is_left_where_the_user_holds_it(
    opened: Any, qtbot: Any,
) -> None:
    _win, panel, app = opened(_PA120)
    red = panel._rgb_sliders[0]
    red.setSliderDown(True)
    held = red.value()

    app.dispatch(SetLedColor(key=_KEY, color=(90, 80, 70)))
    qtbot.waitUntil(lambda: panel._rgb_sliders[1].value() == 80, timeout=3000)

    assert red.value() == held
    red.setSliderDown(False)


def test_a_brightness_still_settling_is_left_alone(opened: Any, qtbot: Any) -> None:
    """The slider's value is sent 150 ms after it stops; until then it is the
    user's, and a loaded value would be the one sent."""
    _win, panel, app = opened(_PA120)
    panel._brightness_slider.setValue(30)            # arms the debounce
    assert panel._brightness_debounce.isActive()

    app.dispatch(SetLedColor(key=_KEY, color=(90, 80, 70)))    # a follow runs
    qtbot.waitUntil(lambda: _rgb(panel) == (90, 80, 70), timeout=3000)

    assert panel._brightness_slider.value() == 30


def test_a_colour_wheel_mid_drag_keeps_its_hue(opened: Any, qtbot: Any) -> None:
    _win, panel, app = opened(_PA120)
    wheel = panel._color_wheel
    wheel._dragging = True
    hue = wheel._hue

    app.dispatch(SetLedColor(key=_KEY, color=(0, 0, 255)))
    qtbot.waitUntil(lambda: _rgb(panel) == (0, 0, 255), timeout=3000)

    assert wheel._hue == hue
    wheel._dragging = False


def test_an_led_change_while_an_lcd_is_shown_waits_for_the_led_panel(
    tmp_path: Path, qtbot: Any,
) -> None:
    """The LED handler is inactive while an LCD owns the window; its panel
    reloads when it is shown again, not on every LED edit meanwhile."""
    from trcc.ui.gui.trcc_app import TRCCApp

    lcd = "0402:3922"
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100},
                            {"type": "led", "vid": "0416", "pid": "8001", "pm": _PA120}],
                           tmp_path), renderer=QtRenderer())
    for key in (lcd, _KEY):
        assert app.dispatch(ConnectDevice(key=key)).ok
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    qtbot.waitUntil(lambda: _KEY in win._handlers and lcd in win._handlers)
    win._activate_device(_KEY)
    qtbot.waitUntil(lambda: win._handlers[_KEY].active)
    panel = win.uc_led_control
    shown = _rgb(panel)
    win._activate_device(lcd)
    qtbot.waitUntil(lambda: not win._handlers[_KEY].active)

    app.dispatch(SetLedColor(key=_KEY, color=(90, 80, 70)))
    qtbot.wait(300)
    assert _rgb(panel) == shown

    win._activate_device(_KEY)
    qtbot.waitUntil(lambda: _rgb(panel) == (90, 80, 70), timeout=3000)
    win.close()
