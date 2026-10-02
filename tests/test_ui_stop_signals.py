"""A SIGTERM at ANY point of a UI's life ends in ``App.close``.

Each face used to install its handler only as its loop began: the Qt skins in
``run`` after the window was built, the daemon after its socket bound.  A
SIGTERM during compose or the coldplug -- measured 2026-10-01 on the mock
fleet, 1 s and 2.5 s after launch, both skins -- killed the process with the
default action (rc 143): no ``App.close``, so the panel stayed lit and the
transport held (#143).  ``UserInterface.start`` now owns the signals from
preflight to exit, and each face's ``stop`` ends whichever loop exists.

MUTATION CHECK -- MEASURED 2026-10-01; failures in THIS file:

  1. no flag check after ``bring_up``  →  **1**.
  2. handlers not restored after ``start``  →  **1**.
  3. ``_exec`` enters the loop despite the flag  →  **1**.
  4. ``DaemonUI.stop`` does not shut the server  →  **1**.
"""
from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any
from unittest import mock

import pytest

from trcc.ui._base import UserInterface


class _Face(UserInterface):
    """A face with no key (so unregistered) whose bring-up and loop are scripted."""

    def __init__(self, *, signal_in: str = "") -> None:
        self.signal_in = signal_in
        self.app = mock.MagicMock()
        self.ran = False
        self.stops = 0

    def compose(self, platform: Any) -> Any:
        return self.app

    def bring_up(self) -> bool:
        if self.signal_in == "bring_up":
            os.kill(os.getpid(), signal.SIGTERM)
        return True

    def run(self) -> int:
        self.ran = True
        if self.signal_in == "run":
            os.kill(os.getpid(), signal.SIGTERM)
        return 0

    def stop(self) -> None:
        self.stops += 1


def test_a_signal_during_bring_up_closes_without_running() -> None:
    face = _Face(signal_in="bring_up")
    assert face.start() == 0
    assert not face.ran
    face.app.close.assert_called_once()


def test_a_signal_while_running_reaches_the_face_s_stop() -> None:
    face = _Face(signal_in="run")
    assert face.start() == 0
    assert face.stops == 1
    face.app.close.assert_called_once()


def test_the_handlers_are_given_back() -> None:
    """A face started inside another program (the tests, a harness) must not
    leave its handler behind -- one bound to a face already closed."""
    before = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    _Face().start()
    assert (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)) \
        == before


def test_off_the_main_thread_start_installs_nothing_and_still_runs() -> None:
    """``signal.signal`` raises off the main thread; ``start`` must not."""
    face = _Face()
    done: list[int] = []
    worker = threading.Thread(target=lambda: done.append(face.start()))
    worker.start()
    worker.join(timeout=10)
    assert done == [0] and face.ran


def test_a_qt_face_stopped_while_its_window_was_built_never_enters_the_loop(
    qapp: Any,
) -> None:
    from PySide6.QtCore import QTimer

    from trcc.ui._uis import QtGuiUI

    face = QtGuiUI()
    face._stop_requested = True
    QTimer.singleShot(3000, qapp.quit)               # a hang is a failure, not a stall
    started = time.monotonic()
    assert face._exec() == 0
    assert time.monotonic() - started < 1.0


def test_a_qt_face_s_stop_ends_the_loop_even_if_queued_before_it(
    qapp: Any,
) -> None:
    from PySide6.QtCore import QTimer

    from trcc.ui._uis import QtGuiUI

    QtGuiUI().stop()
    QTimer.singleShot(3000, qapp.quit)
    started = time.monotonic()
    qapp.exec()
    assert time.monotonic() - started < 1.0


def test_a_sigterm_while_the_daemon_serves_ends_in_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """The real IPC server, a real signal: ``serve_forever`` returns, and the
    App is closed by the template, once."""
    from trcc import daemon, ipc

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(ipc, "daemon_running", lambda: False)
    app = mock.MagicMock()
    monkeypatch.setattr("trcc._boot._build_local_app",
                        lambda *, platform=None, renderer=None: app)
    servers: list[ipc.IPCServer] = []
    real = ipc.IPCServer

    def _capture(bound_app: Any) -> ipc.IPCServer:
        servers.append(real(bound_app))
        return servers[-1]

    monkeypatch.setattr(ipc, "IPCServer", _capture)
    rescued: list[bool] = []

    def _rescue() -> None:
        """A signal that ended nothing would hang the suite: fail instead."""
        rescued.append(True)
        servers[-1].shutdown()

    timers = (threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGTERM)),
              threading.Timer(5.0, _rescue))
    for timer in timers:
        timer.start()
    try:
        assert daemon.run_daemon() == 0
    finally:
        for timer in timers:
            timer.cancel()
    assert not rescued, "SIGTERM did not stop the daemon's server"
    app.close.assert_called_once()
    assert not ipc.socket_path().exists()
