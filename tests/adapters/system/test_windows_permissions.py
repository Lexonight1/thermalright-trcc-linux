"""WindowsPlatform.check_permissions — the per-OS connect-failure hint.

The cutover left this port returning ``[]``; now it answers the Windows
reason (raw drive access needs admin) so the shared connect-failure path
can surface it.  Testable on Linux by faking ``_is_elevated``.
"""
from __future__ import annotations

import pytest

from trcc.adapters.system import windows as win


def test_warns_when_not_elevated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win, "_is_elevated", lambda: False)
    warnings = win.WindowsPlatform().check_permissions()
    assert len(warnings) == 1
    assert "administrator" in warnings[0].lower()


def test_silent_when_elevated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win, "_is_elevated", lambda: True)
    assert win.WindowsPlatform().check_permissions() == []


def test_is_elevated_defaults_true_off_windows() -> None:
    """No shell32 (Linux/CI) → assume fine, emit no false warning."""
    assert win._is_elevated() is True
