"""The gui's overlay switch turns the overlay off; it never deletes it.

"Off" used to be sent as an empty element list, the legacy renderer's way of
drawing nothing.  To ``SetOverlayConfig`` an empty list means the user deleted
every element, so switching off wiped the layout, and so did any edit made
while it was off.  Another UI switching it back on found nothing to draw.

The second half: the gui's editor shows and edits the layout the App holds —
the grid follows every UI, each edit names ONE element by id, and nothing it
cannot represent is rewritten.  It used to fill from the theme's files and
re-send the whole grid, so it reverted other UIs' edits and re-minted every id.

Drives the real window offscreen against the mock platform.  The theme is a
real folder loaded the way a user loads one, so the gui's grid and the App
start from the same layout, as they do on a real panel.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import (
    AddOverlayElement,
    ConnectDevice,
    DeleteOverlayElement,
    EnableOverlay,
    LoadTheme,
    ResolveOverlay,
    UpdateOverlayElement,
)

_SPEC = {"vid": "0402", "pid": "3922", "fbl": 100}
_KEY = "0402:3922"
_ELEMENTS = [
    {"id": "cpu", "type": "text", "text": "CPU", "x": 10, "y": 20, "size": 16},
    {"id": "gpu", "type": "text", "text": "GPU", "x": 10, "y": 60, "size": 16},
    {"id": "fan", "type": "text", "text": "FAN", "x": 10, "y": 100, "size": 16},
]


def _write_theme(directory: Path) -> Path:
    from PySide6.QtGui import QImage

    theme = directory / "Theme1"
    theme.mkdir(parents=True)
    (theme / "trcc.json").write_text(json.dumps({
        "name": "Theme1", "width": 320, "height": 320,
        "overlay_enabled": True, "elements": _ELEMENTS,
    }))
    image = QImage(320, 320, QImage.Format.Format_RGB888)
    image.fill(0xFF0000)
    image.save(str(theme / "00.png"))
    return theme


@pytest.fixture
def window(tmp_path: Path, qtbot: Any) -> Iterator[Any]:
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(MockPlatform([_SPEC], tmp_path), renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    assert app.dispatch(LoadTheme(key=_KEY, path=_write_theme(tmp_path))).ok
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    qtbot.waitUntil(lambda: len(_grid(win).get_all_configs()) == len(_ELEMENTS))
    assert _drawn(win) == ["CPU", "GPU", "FAN"]
    yield win
    win.close()


def _grid(window: Any) -> Any:
    return window.uc_theme_setting.overlay_grid


def _drawn(window: Any) -> list[str]:
    """What the App holds for the panel, by text."""
    return [e.text for e in window._app.dispatch(ResolveOverlay(key=_KEY)).elements]


def _switch(window: Any, qtbot: Any, on: bool) -> None:
    """Flip the gui's overlay switch the way a user does."""
    button = _grid(window)._toggle_btn
    if button.isChecked() != on:
        button.click()
    qtbot.wait(100)


def test_switching_off_keeps_the_layout_for_whoever_switches_it_on(
    window: Any, qtbot: Any,
) -> None:
    _switch(window, qtbot, on=False)
    assert window._app.dispatch(ResolveOverlay(key=_KEY)).enabled is False

    window._app.dispatch(EnableOverlay(key=_KEY, enabled=True))   # another UI

    assert _drawn(window) == ["CPU", "GPU", "FAN"]


def test_an_edit_while_off_keeps_every_other_element(
    window: Any, qtbot: Any,
) -> None:
    _switch(window, qtbot, on=False)

    _grid(window).delete_element(0)
    qtbot.wait(100)

    assert _drawn(window) == ["GPU", "FAN"]


def test_the_switch_does_not_resend_the_layout_over_another_uis_edit(
    window: Any, qtbot: Any,
) -> None:
    """A switch that re-sent the grid brought back what another UI deleted."""
    assert window._app.dispatch(
        DeleteOverlayElement(key=_KEY, element_id="gpu")).ok

    _switch(window, qtbot, on=False)
    _switch(window, qtbot, on=True)

    assert _drawn(window) == ["CPU", "FAN"]


# ── The editor shows and edits what the App holds ────────────────────────


def _held(window: Any) -> dict[str, Any]:
    """The App's elements for the panel, by id, in order."""
    return {e.id: e for e in
            window._app.dispatch(ResolveOverlay(key=_KEY)).elements}


def _cell_ids(window: Any) -> list[str]:
    return [c.id for c in _grid(window).get_all_configs()]


def test_the_grid_follows_another_uis_move_and_delete(
    window: Any, qtbot: Any,
) -> None:
    app = window._app
    assert app.dispatch(UpdateOverlayElement(
        key=_KEY, element_id="cpu", x=50)).ok
    assert app.dispatch(DeleteOverlayElement(key=_KEY, element_id="gpu")).ok

    qtbot.waitUntil(lambda: _cell_ids(window) == ["cpu", "fan"])
    assert _grid(window).get_all_configs()[0].x == 50


def test_a_gui_edit_changes_only_its_element_and_keeps_every_id(
    window: Any, qtbot: Any,
) -> None:
    """Two elements the grid cannot represent, added by another UI: a time
    pattern outside the table and bold AND italic.  Recolouring a different
    element in the gui must leave both exactly as they were."""
    app = window._app
    assert app.dispatch(AddOverlayElement(
        key=_KEY, element_id="clk", type="clock", source="time",
        format="%H:%M:%S", x=5, y=200)).ok
    assert app.dispatch(AddOverlayElement(
        key=_KEY, element_id="bi", type="text", text="BI", bold=True,
        italic=True, x=5, y=240)).ok
    qtbot.waitUntil(lambda: len(_cell_ids(window)) == 5)

    _grid(window).select_element(0)
    window.uc_theme_setting._on_color_changed(0x11, 0x22, 0x33)
    qtbot.wait(100)

    held = _held(window)
    assert list(held) == ["cpu", "gpu", "fan", "clk", "bi"], (
        "a gui edit re-minted the ids another UI holds")
    assert held["cpu"].color == "#112233"
    assert held["clk"].format == "%H:%M:%S"
    assert (held["bi"].bold, held["bi"].italic) == (True, True)
    assert app.dispatch(UpdateOverlayElement(
        key=_KEY, element_id="gpu", y=61)).ok, "the CLI's id stopped resolving"

    # Moving the element that HOLDS the pattern the cell cannot represent:
    # the move must not write the cell's stand-in pattern over it.
    _grid(window).select_element(3)
    window.uc_theme_setting._on_position_changed(8, 210)
    qtbot.wait(100)
    clk = _held(window)["clk"]
    assert (clk.x, clk.y, clk.format) == (8, 210, "%H:%M:%S")


def test_a_drag_keeps_moving_the_same_element_through_the_follow(
    window: Any, qtbot: Any,
) -> None:
    """Every move reloads the grid (the App's echo of the gui's own edit);
    the selection must survive it or the second move has nothing to move."""
    _grid(window).select_element(1)                      # GPU at (10, 60)
    window._on_drag_start(10, 60)
    window._on_drag_move(30, 70)
    qtbot.wait(100)
    assert _grid(window).get_selected_config().id == "gpu"
    window._on_drag_move(40, 80)
    qtbot.wait(100)

    held = _held(window)
    assert (held["gpu"].x, held["gpu"].y) == (40, 80)
    assert (held["cpu"].x, held["cpu"].y) == (10, 20), "the drag moved another"


def test_the_side_panel_shows_another_uis_move_of_the_selected_element(
    window: Any, qtbot: Any,
) -> None:
    _grid(window).select_element(0)
    assert window._app.dispatch(UpdateOverlayElement(
        key=_KEY, element_id="cpu", x=77, y=33)).ok

    spin = window.uc_theme_setting.color_panel
    qtbot.waitUntil(lambda: (spin.x_spin.value(), spin.y_spin.value()) == (77, 33))


def test_a_click_flashes_the_element_by_its_own_id(
    window: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = window._app
    real = app.dispatch
    flashed: list[tuple[str, bool]] = []

    def _record(command: Any) -> Any:
        result = real(command)
        if type(command).__name__ == "FlashOverlayElement":
            flashed.append((command.element_id, result.ok))
        return result

    monkeypatch.setattr(app, "dispatch", _record)
    _grid(window).select_element(2)

    assert flashed == [("fan", True)]


def test_reopening_shows_the_guis_own_edits(
    window: Any, qtbot: Any,
) -> None:
    """The gui used to fill from the theme FILE, so its own edits vanished
    the next time the window opened.  ``TRCCApp`` is a singleton and a real
    close quits Qt, so this blanks the grid the way a fresh window starts and
    runs the panel's own open path (``apply_device_config`` → ``_refresh``)."""
    _grid(window).select_element(0)
    window.uc_theme_setting._on_position_changed(90, 95)
    qtbot.wait(100)

    _grid(window).load_configs([])
    window._handlers[_KEY].apply_device_config(_KEY, 320, 320)
    qtbot.waitUntil(lambda: len(_cell_ids(window)) == len(_ELEMENTS))

    cpu = _grid(window).get_all_configs()[0]
    assert (cpu.id, cpu.x, cpu.y) == ("cpu", 90, 95)


def test_the_format_button_changes_only_the_selected_clock(
    window: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The C# keeps the format per element (myModeSub).  The button used to
    also send a key-less SetTimeFormat that rewrote every device."""
    from trcc.core.models import OverlayMode

    app = window._app
    for eid, y in (("t1", 150), ("t2", 190)):
        assert app.dispatch(AddOverlayElement(
            key=_KEY, element_id=eid, type="clock", source="time",
            format="%H:%M", x=5, y=y)).ok
    qtbot.waitUntil(lambda: len(_cell_ids(window)) == 5)
    sent: list[str] = []
    real = app.dispatch

    def _record(command: Any) -> Any:
        sent.append(type(command).__name__)
        return real(command)

    monkeypatch.setattr(app, "dispatch", _record)
    _grid(window).select_element(3)                           # t1
    window.uc_theme_setting._on_format_changed(OverlayMode.TIME, 1)
    qtbot.wait(100)

    held = _held(window)
    assert (held["t1"].format, held["t2"].format) == ("%I:%M %p", "%H:%M")
    assert "SetTimeFormat" not in sent, sent


def test_a_gui_edit_renders_once_and_re_resolves_nothing(
    window: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The App's render observer renders on the edit's own event; the gui
    rendered a second time by hand.  And its follow path re-ran the rotation
    restore on every event -- a ResolveThemeDirectories + PreviewSize per
    drag move.  Measured 2026-09-30: 2 renders + both Queries -> 1 render."""
    app = window._app
    _grid(window).select_element(0)
    qtbot.wait(100)
    sent: list[str] = []
    real = app.dispatch

    def _record(command: Any) -> Any:
        sent.append(type(command).__name__)
        return real(command)

    monkeypatch.setattr(app, "dispatch", _record)
    window.uc_theme_setting._on_color_changed(0x11, 0x22, 0x33)
    qtbot.wait(200)

    assert sent.count("RenderAndSend") == 1, sent
    assert not {"ResolveThemeDirectories", "PreviewSize"} & set(sent), sent


def test_the_gui_still_follows_an_orientation_change(
    window: Any, qtbot: Any,
) -> None:
    """The rotation redo now runs only on a change -- a change must still run it."""
    from trcc.core.commands import SetOrientation

    assert window._app.dispatch(SetOrientation(key=_KEY, degrees=90)).ok
    combo = window._handlers[_KEY]._w["rotation_combo"]
    qtbot.waitUntil(lambda: combo.currentIndex() == 1, timeout=2000)   # 90°
