"""GUI startup prompt — the no-Qt decision branches.

The dialog-show path is a View seam exercised by running the app; here we
cover the two branches that must NEVER construct a dialog (already declined,
and no offer), so the prompt can't nag or block startup spuriously.
"""
from __future__ import annotations

import pytest

from trcc.core.commands import InstallGpuReader
from trcc.core.results import GpuReaderInstallResult, GpuReaderStatusResult
from trcc.ui.gui.gpu_reader_prompt import maybe_offer_gpu_reader_install


class _FakeSettings:
    def __init__(self, declined: bool) -> None:
        self.app = type("AppSettings", (), {"gpu_reader_install_declined": declined})()
        self.declined_recorded: bool | None = None

    def set_gpu_reader_install_declined(self, declined: bool) -> None:
        self.declined_recorded = declined


class _FakeApp:
    def __init__(
        self, declined: bool, status: GpuReaderStatusResult,
        install_result: GpuReaderInstallResult | None = None,
    ) -> None:
        self.settings = _FakeSettings(declined)
        self._status = status
        self._install_result = install_result or GpuReaderInstallResult(
            ok=True, message="installed",
        )
        self.dispatched: list[object] = []

    def dispatch(self, command: object) -> object:
        self.dispatched.append(command)
        if isinstance(command, InstallGpuReader):
            return self._install_result
        return self._status


class _FakeButton:
    pass


class _FakeMessageBox:
    """Minimal QMessageBox stand-in — records the buttons, returns a
    pre-selected one from ``clickedButton`` so the accept / decline branches
    run without a real window."""

    click_install = True

    class Icon:
        Question = Information = Warning = 0

    class ButtonRole:
        AcceptRole = RejectRole = 0

    def __init__(self, *_a: object, **_k: object) -> None:
        self._install_btn: _FakeButton | None = None

    def setWindowTitle(self, _t: str) -> None: ...
    def setIcon(self, _i: object) -> None: ...
    def setText(self, _t: str) -> None: ...
    def setInformativeText(self, _t: str) -> None: ...

    def addButton(self, label: str, _role: object) -> _FakeButton:
        button = _FakeButton()
        if label == "Install":
            self._install_btn = button
        return button

    def exec(self) -> None: ...

    def clickedButton(self) -> _FakeButton:
        return self._install_btn if _FakeMessageBox.click_install else _FakeButton()


def test_skips_without_querying_when_previously_declined() -> None:
    app = _FakeApp(
        declined=True,
        status=GpuReaderStatusResult(ok=True, offer_install=True),
    )
    maybe_offer_gpu_reader_install(app, parent=None)
    # Declined short-circuits before even dispatching the status query.
    assert app.dispatched == []
    assert app.settings.declined_recorded is None


def test_skips_after_query_when_no_offer() -> None:
    status = GpuReaderStatusResult(
        ok=True, nvidia_present=False, reader_installed=False, offer_install=False,
    )
    app = _FakeApp(declined=False, status=status)
    maybe_offer_gpu_reader_install(app, parent=None)
    # Queried exactly once, no dialog, no decline recorded.
    assert len(app.dispatched) == 1
    assert app.settings.declined_recorded is None


def test_accept_dispatches_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox", _FakeMessageBox)
    monkeypatch.setattr(_FakeMessageBox, "click_install", True)
    status = GpuReaderStatusResult(ok=True, nvidia_present=True, offer_install=True)
    app = _FakeApp(declined=False, status=status)
    maybe_offer_gpu_reader_install(app, parent=None)
    assert any(isinstance(c, InstallGpuReader) for c in app.dispatched)
    assert app.settings.declined_recorded is None


def test_decline_via_dialog_remembers_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox", _FakeMessageBox)
    monkeypatch.setattr(_FakeMessageBox, "click_install", False)
    status = GpuReaderStatusResult(ok=True, nvidia_present=True, offer_install=True)
    app = _FakeApp(declined=False, status=status)
    maybe_offer_gpu_reader_install(app, parent=None)
    assert not any(isinstance(c, InstallGpuReader) for c in app.dispatched)
    assert app.settings.declined_recorded is True
