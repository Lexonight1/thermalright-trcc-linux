"""``RaiseEvent`` and SingleInstance's Windows branch, driven on any OS.

CPython on Windows has no ``AF_UNIX``, so a second ``trcc gui`` there could
not reach the running one: it logged "raised" and exited, and a window hidden
in the tray stayed hidden (win11 VM, 2026-10-20).  pywin32's event calls are
stood in for by threading events -- the Windows semantics are the OS's; what
is pinned here is our side: who creates, who rings, what a ring runs.
"""
from __future__ import annotations

import socket
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from trcc import ipc
from trcc.adapters.infra import raise_event


class _FakeWin32:
    """CreateEvent / OpenEvent / SetEvent / WaitForMultipleObjects by name."""

    INFINITE = -1
    WAIT_OBJECT_0 = 0

    def __init__(self) -> None:
        self.named: dict[str, threading.Event] = {}
        self.manual: set[int] = set()          # ids of manual-reset events
        self.rang: list[str] = []

    def CreateEvent(self, _sa: Any, manual: bool, _initial: bool, name: str | None) -> Any:
        event = threading.Event()
        if manual:
            self.manual.add(id(event))
        if name is not None:
            self.named[name] = event
        return event

    def OpenEvent(self, _access: int, _inherit: bool, name: str) -> Any:
        if name not in self.named:
            raise _Error(2, "OpenEvent", "The system cannot find the file specified.")
        return self.named[name]

    def SetEvent(self, event: threading.Event) -> None:
        if event in self.named.values():
            self.rang.append(next(n for n, e in self.named.items() if e is event))
        event.set()

    def WaitForMultipleObjects(self, events: list[threading.Event], _all: bool,
                               _ms: int) -> int:
        while True:
            for i, event in enumerate(events):
                if event.is_set():
                    if id(event) not in self.manual:
                        event.clear()                        # auto-reset
                    return i
            threading.Event().wait(0.01)

    def CloseHandle(self, _handle: Any) -> None:
        pass


class _Error(Exception):
    def __init__(self, code: int, func: str, text: str) -> None:
        super().__init__(code, func, text)


@pytest.fixture
def win(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> _FakeWin32:
    """Windows' shape: no AF_UNIX, a file lock that works, pywin32 faked."""
    fake = _FakeWin32()
    monkeypatch.setattr(raise_event, "win32event", fake)
    monkeypatch.setattr(raise_event, "win32api", fake)
    monkeypatch.setattr(raise_event, "pywintypes", SimpleNamespace(error=_Error))
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)
    held: set[str] = set()

    def acquire(name: str) -> object | None:
        if name in held:
            return None
        held.add(name)
        return object()

    monkeypatch.setattr(ipc, "_msvcrt_acquire", acquire)
    return fake


def test_a_second_launch_raises_the_running_window_on_windows(win: _FakeWin32) -> None:
    """MUTATION CHECK: drop the ring -- the window is never raised."""
    first = ipc.SingleInstance("gui")
    assert first is not None
    raised = threading.Event()
    first.on_raise = raised.set
    try:
        assert ipc.SingleInstance("gui") is None
        assert raised.wait(3.0), "the second launch did not reach the running GUI"
        assert win.rang == ["Local\\TRCC-gui-raise"]
    finally:
        first.close()


def test_a_hidden_launch_rings_nothing_on_windows(win: _FakeWin32) -> None:
    """``gui --resume`` (sign-in) must not pop the window the first one keeps hidden.

    MUTATION CHECK: ring whatever *raise_peer* says -- this fails."""
    first = ipc.SingleInstance("gui")
    assert first is not None
    try:
        assert ipc.SingleInstance("gui", raise_peer=False) is None
        assert win.rang == []
    finally:
        first.close()


def test_a_ring_before_the_window_exists_is_dropped(
        win: _FakeWin32, caplog: pytest.LogCaptureFixture) -> None:
    """The window is still being built: the ring is dropped, said once, and
    the doorbell still answers the next one."""
    import logging
    import time

    first = ipc.SingleInstance("gui")
    assert first is not None
    try:
        with caplog.at_level(logging.INFO, logger=raise_event.__name__):
            assert ipc.SingleInstance("gui") is None
            deadline = time.monotonic() + 3.0
            while "dropped" not in caplog.text and time.monotonic() < deadline:
                time.sleep(0.01)
        assert "raise before the window exists -- dropped" in caplog.text
        raised = threading.Event()
        first.on_raise = raised.set
        assert ipc.SingleInstance("gui") is None
        assert raised.wait(3.0)
    finally:
        first.close()
