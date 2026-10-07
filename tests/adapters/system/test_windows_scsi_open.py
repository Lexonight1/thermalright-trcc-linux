"""``WindowsScsiTransport.open`` -- which failures are the user's setup.

Driven on any OS: ``CreateFileW`` and ``GetLastError`` are scripted, the
decision under test is ours.
"""
from __future__ import annotations

import ctypes
from typing import Any

import pytest

import trcc.adapters.system.windows as windows
from trcc.core.errors import PermissionError_


class _Kernel32:
    """``CreateFileW`` fails; ``GetLastError`` says why."""

    def CreateFileW(self, *_args: Any) -> int:
        return -1


def _failing_with(monkeypatch: pytest.MonkeyPatch, err: int) -> None:
    monkeypatch.setattr(windows, "_kernel32", lambda: _Kernel32())
    monkeypatch.setattr(ctypes, "GetLastError", lambda: err, raising=False)


def test_access_denied_is_a_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only elevation opens it; retrying every few minutes would not.

    MUTATION CHECK: return False on ERROR_ACCESS_DENIED again and this fails.
    """
    _failing_with(monkeypatch, 5)

    with pytest.raises(PermissionError_, match="Run as administrator"):
        windows.WindowsScsiTransport(r"\\.\PhysicalDrive3").open()


@pytest.mark.parametrize("err", [2, 32])
def test_a_drive_that_is_gone_or_busy_is_worth_retrying(
    monkeypatch: pytest.MonkeyPatch, err: int,
) -> None:
    """Not found (replug) and sharing violation (another process) can pass."""
    _failing_with(monkeypatch, err)

    assert windows.WindowsScsiTransport(r"\\.\PhysicalDrive3").open() is False
