"""hudgui (``ui/hudgui``) -- the gui redesigned as a HUD (his design, 2026-11-05).

His goal: "so gamers can have a thin bar above their gaming".  At rest the
window is only the chrome bar, always on top; a click drops a panel down,
clearing the last one lit; a second click on the same item pulls it up and the
window is the bar again.  Devices drops down the found devices as the gui's
symbols; a picked one opens its side panel: its own tab bar and the shared
live preview.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.conftest import show_a_theme
from tests.mock_platform import MockPlatform
from trcc.adapters.render.qt import QtRenderer
from trcc.app import App
from trcc.core.commands import ConnectDevice

_KEY = "0402:3922"


@pytest.fixture
def window(tmp_path: Path, qtbot: Any) -> Iterator[Any]:
    from trcc.ui.hudgui.window import HudWindow

    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}], tmp_path),
              renderer=QtRenderer())
    assert app.dispatch(ConnectDevice(key=_KEY)).ok
    show_a_theme(app, _KEY)
    win = HudWindow(app)
    qtbot.addWidget(win)
    win.show()
    yield win
    app.close()


def _lit(win: Any) -> list[str]:
    return [item for item, button in win.bar.buttons.items() if button.isChecked()]


def test_at_rest_it_is_only_the_bar_on_top_of_everything(window: Any) -> None:
    from trcc.ui.hudgui.chrome import CHROME_HEIGHT

    assert list(window.bar.buttons) == ["Devices", "Sensors", "Modules",
                                        "Control Center", "About"]
    flags = window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert window.height() == CHROME_HEIGHT and not window.drop.isVisible()


def test_devices_lists_the_symbols_and_a_pick_opens_its_side_panel(
        window: Any, qtbot: Any) -> None:
    """MUTATION CHECK: drop the ``set_key`` in ``DevicesView._on_picked`` and
    the panel's preview shows no device."""
    from trcc.ui.qt_preview import PreviewSurface

    window.bar.buttons["Devices"].click()
    devices = window.panel("Devices")
    button = devices.menu.buttons[_KEY]
    assert not button.icon().isNull() and button.text() == ""   # the symbol, as gui
    assert not devices.panel.isVisible()                        # until one is picked
    button.click()
    qtbot.waitUntil(devices.panel.isVisible)
    assert window._selection.key == _KEY and button.isChecked()
    assert list(devices.panel.tabs.tabs) == ["Local Themes", "Cloud Masks",
                                             "Cloud Backgrounds", "Settings"]
    assert devices.panel.tabs.current == "Local Themes"
    assert isinstance(devices.panel.preview, PreviewSurface)


def test_a_menu_item_drops_its_panel_and_a_second_click_pulls_it_up(
        window: Any, qtbot: Any) -> None:
    """MUTATION CHECK: drop the ``item == self.active`` branch in
    ``HudWindow._on_menu`` and the second click leaves the panel down."""
    from trcc.ui.hudgui.chrome import CHROME_HEIGHT

    window.bar.buttons["Modules"].click()
    assert (window.active, _lit(window), window.drop.title) == ("Modules", ["Modules"], "Modules")
    qtbot.waitUntil(window.drop.isVisible)
    assert window.height() > CHROME_HEIGHT
    window.bar.buttons["Sensors"].click()                  # clears the last one lit
    assert (window.active, _lit(window)) == ("Sensors", ["Sensors"])
    window.bar.buttons["Sensors"].click()                  # the same one again: up
    assert (window.active, _lit(window)) == (None, [])
    qtbot.waitUntil(lambda: not window.drop.isVisible(), timeout=2000)
    assert window.height() == CHROME_HEIGHT              # back to just the bar


def test_escape_pulls_the_open_panel_up(window: Any, qtbot: Any) -> None:
    window.bar.buttons["About"].click()
    qtbot.keyClick(window, Qt.Key.Key_Escape)
    assert (window.active, _lit(window)) == (None, [])


def test_the_bar_reads_out_the_cpu_and_gpu_temperature(window: Any) -> None:
    """In the user's unit, from the broadcast every UI hears."""
    from trcc.core.events import SensorsUpdated
    from trcc.core.models import HardwareMetrics

    window._on_sensors(SensorsUpdated(readings={}, temp_unit="F", reading_count=0,
                                      metrics=HardwareMetrics(cpu_temp=122.0,
                                                              gpu_temp=140.0)))
    assert window.bar.readout.text() == "CPU 122°F   GPU 140°F"
