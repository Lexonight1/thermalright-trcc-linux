"""The gui -- the blueprint -- on the shared DeviceChoice.

Its last-used restore had never worked: the sidebar selected the first device
while the window was still being built, which recorded it as last used before
the restore read it, so every start opened on the first device (measured
2026-11-10 on the unmodified tree).
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
from trcc.core.commands import ConnectDevice, DisconnectDevice, GetPlatformInfo

A, B = "0402:3922", "87ad:70db"


@pytest.fixture
def two_panels(tmp_path: Path) -> Iterator[App]:
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100},
                            {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 5,
                             "sub": 1, "resolution": "320x240"}], tmp_path),
              renderer=QtRenderer())
    app.discover_and_connect()
    yield app
    app.close()


def _gui(app: App, qtbot: Any, last_used: str) -> Any:
    from trcc.ui.gui.trcc_app import TRCCApp

    config = Path(app.dispatch(GetPlatformInfo()).config_dir)
    (config / "ui_state.json").write_text(json.dumps({"last_device_key": last_used}),
                                          encoding="utf-8")
    win = TRCCApp(app=app)
    qtbot.addWidget(win)
    win.replay_initial_devices()
    return win


def _shown(win: Any) -> tuple[str, str]:
    return win._active_key, (win.uc_device.selected_device or {}).get("path", "")


def test_the_gui_opens_on_the_device_last_used(two_panels: App, qtbot: Any,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """And ONLY on it: the first device is never activated on the way.

    MUTATION CHECK (measured 2026-11-10): put ``self._select_device(devices[0])``
    back at the end of ``UCDevice.update_devices`` -- the first device is
    activated, then switched away from: ``activated`` reads ``[A, B]``."""
    from trcc.ui.gui.trcc_app import TRCCApp

    activated: list[str] = []
    real = TRCCApp._activate_device

    def spy(self: Any, key: str) -> None:
        activated.append(key)
        real(self, key)
    monkeypatch.setattr(TRCCApp, "_activate_device", spy)

    win = _gui(two_panels, qtbot, last_used=B)
    assert _shown(win) == (B, B)
    assert activated == [B]
    assert win._ui_state.state.last_device_key == B
    win.close()


def test_the_gui_brings_a_lost_device_back(two_panels: App, qtbot: Any) -> None:
    win = _gui(two_panels, qtbot, last_used=B)
    assert two_panels.dispatch(DisconnectDevice(key=B)).ok
    qtbot.waitUntil(lambda: _shown(win) == (A, A))
    assert two_panels.dispatch(ConnectDevice(key=B)).ok
    qtbot.waitUntil(lambda: _shown(win) == (B, B))
    win.close()


def test_a_pick_meanwhile_keeps_the_gui_where_the_user_put_it(two_panels: App,
                                                              qtbot: Any) -> None:
    win = _gui(two_panels, qtbot, last_used=B)
    assert two_panels.dispatch(DisconnectDevice(key=B)).ok
    qtbot.waitUntil(lambda: _shown(win) == (A, A))
    win.uc_device.device_buttons[0].click()               # the user picks A
    assert two_panels.dispatch(ConnectDevice(key=B)).ok
    qtbot.waitUntil(lambda: B in win._handlers)
    qtbot.wait(200)
    assert _shown(win) == (A, A)
    win.close()
