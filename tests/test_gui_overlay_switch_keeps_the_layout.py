"""The gui's overlay switch turns the overlay off; it never deletes it.

"Off" used to be sent as an empty element list, the legacy renderer's way of
drawing nothing.  To ``SetOverlayConfig`` an empty list means the user deleted
every element, so switching off wiped the layout, and so did any edit made
while it was off.  Another UI switching it back on found nothing to draw.

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
    ConnectDevice,
    DeleteOverlayElement,
    EnableOverlay,
    LoadTheme,
    ResolveOverlay,
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
    """What the App holds for the panel, by text.  Not by id: a gui edit
    re-mints every id positionally, which is a separate defect."""
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
    """The gui's grid does not follow a layout edit made elsewhere, so a
    switch that re-sent the grid brought back what another UI deleted."""
    assert window._app.dispatch(
        DeleteOverlayElement(key=_KEY, element_id="gpu")).ok

    _switch(window, qtbot, on=False)
    _switch(window, qtbot, on=True)

    assert _drawn(window) == ["CPU", "FAN"]
