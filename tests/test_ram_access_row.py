"""The RAM-lighting row in both windows: shows the App's answer, warns before
enabling, and waits for the password off the UI thread."""
from __future__ import annotations

import threading
from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox

from trcc.core.commands import RamLighting, SetRamLighting
from trcc.core.models import RamAccessState
from trcc.core.results import RamLightingResult


class _Bus:
    """A CommandBus answering the two RAM Commands; the switch can be held,
    as a password prompt holds it."""

    def __init__(self, state: RamAccessState) -> None:
        self.state = state
        self.sent: list[Any] = []
        self.release = threading.Event()
        self.release.set()

    def dispatch(self, cmd: Any) -> RamLightingResult:
        self.sent.append(cmd)
        if isinstance(cmd, SetRamLighting):
            self.release.wait(5)
            self.state = RamAccessState.ON if cmd.enabled else RamAccessState.OFF
        assert isinstance(cmd, (RamLighting, SetRamLighting))
        return RamLightingResult(ok=True, state=self.state,
                                 message=f"is {self.state.value}")


def _rows() -> list[Any]:
    from trcc.ui.gui.uc_ram_access import UCRamAccess
    from trcc.ui.qtgui.panels.led.ram_access import RamAccessControl
    return [UCRamAccess, RamAccessControl]


@pytest.mark.parametrize("row_cls", _rows(), ids=["gui", "qtgui"])
def test_enabling_warns_then_waits_for_the_password_without_freezing(
        row_cls: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _Bus(RamAccessState.OFF)
    row = row_cls(bus)
    qtbot.addWidget(row)
    assert row._status_label.text() == "is off"
    assert row._button.text() == "Enable RAM lighting..."
    assert not row._button.isHidden()

    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda _p, title, _t: asked.append(title) or QMessageBox.StandardButton.Yes))
    bus.release.clear()                       # the password prompt is open
    row._button.click()
    assert asked == ["Enable RAM lighting?"]
    assert row._status_label.text() == "Waiting for the password..."
    assert row._button.isHidden()             # nothing to press while waiting
    bus.release.set()                         # ...and it is typed
    qtbot.waitUntil(lambda: row._status_label.text() == "is on", timeout=3000)
    assert row._button.text() == "Turn off RAM lighting"
    assert [type(c).__name__ for c in bus.sent] == [
        "RamLighting", "SetRamLighting"]


@pytest.mark.parametrize("row_cls", _rows(), ids=["gui", "qtgui"])
def test_a_declined_warning_sends_nothing(
        row_cls: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _Bus(RamAccessState.OFF)
    row = row_cls(bus)
    qtbot.addWidget(row)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *_a: QMessageBox.StandardButton.No))
    row._button.click()
    assert [type(c).__name__ for c in bus.sent] == ["RamLighting"]
    assert row._status_label.text() == "is off"


@pytest.mark.parametrize("row_cls", _rows(), ids=["gui", "qtgui"])
def test_turning_off_needs_no_warning(
        row_cls: Any, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _Bus(RamAccessState.ON)
    row = row_cls(bus)
    qtbot.addWidget(row)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *_a: pytest.fail("warned before turning OFF")))
    row._button.click()
    qtbot.waitUntil(lambda: row._status_label.text() == "is off", timeout=3000)
    assert bus.sent[-1] == SetRamLighting(enabled=False)
