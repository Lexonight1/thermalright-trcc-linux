"""WindowsPlatform.check_permissions — the per-OS connect-failure hint.

A process that is not administrator reaches the panels through TRCC's
service, so the hint is about the service, not elevation: it said "Not
running as administrator -- will fail" to every such process, which every
installed TRCC is once its exes stop asking for admin.  Testable on Linux by
faking ``_is_elevated`` and the service connection.
"""
from __future__ import annotations

from typing import Any

import pytest

from trcc.adapters.system import _service_client
from trcc.adapters.system import windows as win


class _Client:
    closed = False

    def close(self) -> None:
        self.closed = True


def _service(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[float]:
    """Stand the service in with *answer* (a client, or an error to raise)."""
    timeouts: list[float] = []

    def connect(timeout: float = 5.0) -> Any:
        timeouts.append(timeout)
        if isinstance(answer, BaseException):
            raise answer
        return answer
    monkeypatch.setattr(_service_client, "connect_service", connect)
    return timeouts


def test_silent_when_elevated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win, "_is_elevated", lambda: True)
    asked = _service(monkeypatch, ConnectionRefusedError("unused"))

    assert win.WindowsPlatform().check_permissions() == []
    assert asked == [], "an administrator has direct access; no need to ask"


def test_silent_when_the_service_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTATION CHECK: warn on not-elevated alone and this fails."""
    monkeypatch.setattr(win, "_is_elevated", lambda: False)
    client = _Client()
    _service(monkeypatch, client)

    assert win.WindowsPlatform().check_permissions() == []
    assert client.closed, "the probe left its connection open"


@pytest.mark.parametrize("error", [
    ConnectionRefusedError(2, "no App serves the pipe"),   # not installed/started
    TimeoutError("every pipe instance is busy"),
    PermissionError(5, "the pipe's owner is not SYSTEM"),
])
def test_names_the_service_when_it_does_not_answer(
    monkeypatch: pytest.MonkeyPatch, error: OSError,
) -> None:
    monkeypatch.setattr(win, "_is_elevated", lambda: False)
    timeouts = _service(monkeypatch, error)

    assert win.WindowsPlatform().check_permissions() == [_service_client.NOT_RUNNING]
    assert timeouts == [1.0], "a hint must not hang a report for 5 s"


def test_is_elevated_defaults_true_off_windows() -> None:
    """No shell32 (Linux/CI) → assume fine, emit no false warning."""
    assert win._is_elevated() is True
