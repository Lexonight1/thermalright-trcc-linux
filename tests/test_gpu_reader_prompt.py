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
    def __init__(self, suppressed: bool) -> None:
        self.app = type("AppSettings", (), {"gpu_reader_offer_suppressed": suppressed})()
        self.suppressed_recorded: bool | None = None

    def set_gpu_reader_offer_suppressed(self, suppressed: bool) -> None:
        self.suppressed_recorded = suppressed


class _FakeApp:
    def __init__(
        self, suppressed: bool, status: GpuReaderStatusResult,
        install_result: GpuReaderInstallResult | None = None,
    ) -> None:
        self.settings = _FakeSettings(suppressed)
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


class _FakeCheckBox:
    """QCheckBox stand-in — starts unchecked; a test flips ``checked`` to
    simulate the user ticking "Don't ask again"."""

    checked = False

    def __init__(self, *_a: object, **_k: object) -> None: ...

    def isChecked(self) -> bool:
        return _FakeCheckBox.checked


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
    def setCheckBox(self, _cb: object) -> None: ...

    def addButton(self, label: str, _role: object) -> _FakeButton:
        button = _FakeButton()
        if label == "Install":
            self._install_btn = button
        return button

    def exec(self) -> None: ...

    def clickedButton(self) -> _FakeButton:
        return self._install_btn if _FakeMessageBox.click_install else _FakeButton()


def _install_qt_stubs(monkeypatch: pytest.MonkeyPatch, *, checked: bool) -> None:
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox", _FakeMessageBox)
    monkeypatch.setattr("PySide6.QtWidgets.QCheckBox", _FakeCheckBox)
    monkeypatch.setattr(_FakeCheckBox, "checked", checked)


def test_skips_without_querying_when_opted_out() -> None:
    app = _FakeApp(
        suppressed=True,
        status=GpuReaderStatusResult(ok=True, offer_install=True),
    )
    maybe_offer_gpu_reader_install(app, parent=None)
    # Opt-out short-circuits before even dispatching the status query.
    assert app.dispatched == []
    assert app.settings.suppressed_recorded is None


def test_skips_after_query_when_no_offer() -> None:
    status = GpuReaderStatusResult(
        ok=True, nvidia_present=False, reader_installed=False, offer_install=False,
    )
    app = _FakeApp(suppressed=False, status=status)
    maybe_offer_gpu_reader_install(app, parent=None)
    # Queried exactly once, no dialog, nothing recorded.
    assert len(app.dispatched) == 1
    assert app.settings.suppressed_recorded is None


def test_accept_dispatches_install(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_qt_stubs(monkeypatch, checked=False)
    monkeypatch.setattr(_FakeMessageBox, "click_install", True)
    status = GpuReaderStatusResult(ok=True, nvidia_present=True, offer_install=True)
    app = _FakeApp(suppressed=False, status=status)
    maybe_offer_gpu_reader_install(app, parent=None)
    assert any(isinstance(c, InstallGpuReader) for c in app.dispatched)
    assert app.settings.suppressed_recorded is None


def test_not_now_without_optout_does_not_suppress(monkeypatch: pytest.MonkeyPatch) -> None:
    # A plain "Not now"/dismissal must re-offer next launch — nothing recorded.
    _install_qt_stubs(monkeypatch, checked=False)
    monkeypatch.setattr(_FakeMessageBox, "click_install", False)
    status = GpuReaderStatusResult(ok=True, nvidia_present=True, offer_install=True)
    app = _FakeApp(suppressed=False, status=status)
    maybe_offer_gpu_reader_install(app, parent=None)
    assert not any(isinstance(c, InstallGpuReader) for c in app.dispatched)
    assert app.settings.suppressed_recorded is None


def test_dont_ask_again_suppresses(monkeypatch: pytest.MonkeyPatch) -> None:
    # Dismissing WITH "Don't ask again" ticked persists the opt-out.
    _install_qt_stubs(monkeypatch, checked=True)
    monkeypatch.setattr(_FakeMessageBox, "click_install", False)
    status = GpuReaderStatusResult(ok=True, nvidia_present=True, offer_install=True)
    app = _FakeApp(suppressed=False, status=status)
    maybe_offer_gpu_reader_install(app, parent=None)
    assert not any(isinstance(c, InstallGpuReader) for c in app.dispatched)
    assert app.settings.suppressed_recorded is True
