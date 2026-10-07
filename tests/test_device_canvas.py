"""``DeviceCanvas`` — the size to author an asset for, or a reason there is none.

A registry row of 0x0 (0416:5302 covers several panels; an LED controller
has no screen) answered ok=True, and the qtgui theme and mask browsers then
listed and cropped for a 0x0 panel.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import ConnectDevice, DeviceCanvas

from .mock_platform import MockPlatform


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100}],
                            tmp_path))


@pytest.mark.parametrize(("key", "says"), [
    ("0416:5302", "only after it answers a handshake"),
    ("0416:8001", "LED"),
    ("dead:beef", "no canvas known"),
])
def test_no_size_is_a_refusal_that_says_why(app: App, key: str, says: str) -> None:
    """MUTATION CHECK: refuse only an unknown key and 5302 answers ok 0x0."""
    r = app.dispatch(DeviceCanvas(key=key))

    assert r.ok is False
    assert (r.width, r.height) == (0, 0)
    assert says in r.message


def test_a_known_panel_answers_its_size(app: App) -> None:
    assert app.dispatch(DeviceCanvas(key="0402:3922")).ok   # from the registry
    assert app.dispatch(ConnectDevice(key="0402:3922")).ok

    r = app.dispatch(DeviceCanvas(key="0402:3922"))

    assert (r.ok, r.width, r.height, r.source) == (True, 320, 320, "handshake")
