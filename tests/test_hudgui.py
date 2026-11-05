"""hudgui (``ui/hudgui``) -- the gui redesigned as a HUD (his design, 2026-11-05).

H0: the window, its chrome menu bar, the shared live preview as its body, and
the drop-down's toggle group as he described it -- a click drops its panel and
clears the last one lit; a second click on the same item pulls it up.
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


def test_the_bar_is_the_menu_and_the_body_previews_the_first_lcd(window: Any) -> None:
    from trcc.ui.qt_preview import PreviewSurface

    assert list(window.bar.buttons) == ["Devices", "Themes", "Sensors", "Modules",
                                        "Control Center", "About"]
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert isinstance(window.preview, PreviewSurface)
    assert window._selection.key == _KEY


def test_a_menu_item_drops_its_panel_and_a_second_click_pulls_it_up(
        window: Any, qtbot: Any) -> None:
    """MUTATION CHECK: drop the ``item == self.active`` branch in
    ``HudWindow._on_menu`` and the second click leaves the panel down."""
    window.bar.buttons["Themes"].click()
    assert (window.active, _lit(window), window.drop.title) == ("Themes", ["Themes"], "Themes")
    qtbot.waitUntil(window.drop.isVisible)
    window.bar.buttons["Sensors"].click()                  # clears the last one lit
    assert (window.active, _lit(window)) == ("Sensors", ["Sensors"])
    window.bar.buttons["Sensors"].click()                  # the same one again: up
    assert (window.active, _lit(window)) == (None, [])
    qtbot.waitUntil(lambda: not window.drop.isVisible(), timeout=2000)


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
