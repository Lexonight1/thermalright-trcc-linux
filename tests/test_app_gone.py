"""The App going away: a quit closes every UI, a crash brings it back.

Decided 2026-10-07.  Before this, every long-lived UI (gui, qtgui, ``trcc
api``, ``trcc shell``) stayed open as a dead remote after ``trcc kill``: no
events, and every click raised ``DaemonUnavailableError``.  It became
everyone's problem when the shared App became the default -- v9.10.4 ran each
UI in-process.

The whole distinction rides on ONE line: the App writes ``AppStopping`` as the
last line of every stream when it stops on purpose.  A crash writes nothing.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

import pytest

from trcc import ipc
from trcc.app import App
from trcc.core.events import AppStopping, EventBus
from trcc.core.ports import CommandBus
from trcc.ipc import IPCServer
from trcc.proxy import AppProxy
from trcc.ui import _base
from trcc.ui._base import RELAUNCHED_ENV, UserInterface
from trcc.ui.cli import shell


@pytest.fixture()
def daemon(fake_platform, tmp_path, monkeypatch):
    """A real IPCServer on a throwaway socket, plus a real AppProxy."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    srv = IPCServer(App(fake_platform))
    srv.start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    proxy = AppProxy()
    yield srv, proxy
    proxy.close()
    srv.shutdown()


def _attached(srv: IPCServer, n: int = 1) -> None:
    """Wait until *n* streams are attached -- a notice cannot reach one before."""
    deadline = time.monotonic() + 5
    while len(srv._subscribers) < n and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(srv._subscribers) >= n, "precondition: the stream attached"


def _watch(proxy: AppProxy) -> tuple[threading.Event, threading.Event]:
    stopped, lost = threading.Event(), threading.Event()
    proxy.on_app_gone(stopped.set, lost.set)
    return stopped, lost


# ── The App's half: the last line says why ────────────────────────────


def test_a_stopping_app_tells_every_stream_why(daemon) -> None:
    """Both a lifecycle-only stream and a full one end on ``AppStopping``.

    MUTATION CHECK: drop the notice from ``IPCServer.shutdown`` and both
    streams end on EOF.
    """
    srv, _proxy = daemon
    streams = [ipc.open_event_stream(["AppStopping"]), ipc.open_event_stream()]
    _attached(srv, 2)

    srv.shutdown()

    ends = []
    for sock in streams:                   # read (and close) both, THEN judge
        with sock, sock.makefile("rb") as reader:
            ends.append([line for line in reader if line.strip()])
    for lines in ends:
        assert len(lines) == 1
        assert isinstance(ipc.decode_event(json.loads(lines[0])), AppStopping)


# ── The proxy's half: stopped, lost, or neither ───────────────────────


def test_a_quit_is_heard_as_stopped(daemon) -> None:
    srv, proxy = daemon
    stopped, lost = _watch(proxy)
    _attached(srv)

    srv.shutdown()

    assert stopped.wait(5)
    assert not lost.is_set()


def test_a_crash_is_heard_as_lost(daemon) -> None:
    """A crash closes the stream with no last line.

    MUTATION CHECK: report every end of stream as ``stopped`` and this
    reads stopped.
    """
    srv, proxy = daemon
    stopped, lost = _watch(proxy)
    _attached(srv)

    for sub in list(srv._subscribers):     # the process died: sockets just close
        sub.close()

    assert lost.wait(5)
    assert not stopped.is_set()


def test_closing_this_client_is_neither(daemon) -> None:
    """A window closing its own proxy must not restart itself."""
    srv, proxy = daemon
    stopped, lost = _watch(proxy)
    _attached(srv)

    proxy.close()
    time.sleep(0.3)

    assert not stopped.is_set()
    assert not lost.is_set()


def test_watching_does_not_subscribe_to_frames(daemon) -> None:
    """The watch must not cost the App a JPEG per frame for this client."""
    srv, proxy = daemon
    _watch(proxy)
    _attached(srv)

    assert [sub.types for sub in srv._subscribers] == [{"AppStopping"}]


def test_an_app_in_this_process_has_nothing_to_watch(fake_platform) -> None:
    app = App(fake_platform)
    stopped, lost = threading.Event(), threading.Event()
    before = threading.active_count()

    app.on_app_gone(stopped.set, lost.set)

    assert threading.active_count() == before
    assert not stopped.is_set() and not lost.is_set()


# ── The UI's half: close on a quit, restart on a crash ────────────────


class _Bus(CommandBus):
    """A shared App stand-in that records the watch it was given."""

    def __init__(self) -> None:
        self.watch: tuple[Any, Any] | None = None
        self.closed = False

    def dispatch(self, cmd: Any) -> Any:
        raise AssertionError("not dispatched in these tests")

    @property
    def events(self) -> EventBus:
        return EventBus()

    @property
    def remote(self) -> bool:
        return True

    def on_app_gone(self, stopped: Any, lost: Any) -> None:
        self.watch = (stopped, lost)

    def close(self) -> None:
        self.closed = True


class _Face(UserInterface):
    """A face whose loop ends the way *ending* says, mid-run."""

    def __init__(self, ending: str = "") -> None:
        self.bus = _Bus()
        self.ending = ending
        self.stops = 0

    def compose(self, platform: Any) -> Any:
        return self.bus

    def bring_up(self) -> bool:
        return True

    def run(self) -> int:
        assert self.bus.watch is not None, "the watch is set before the loop"
        stopped, lost = self.bus.watch
        {"stopped": stopped, "lost": lost}.get(self.ending, lambda: None)()
        return 0

    def stop(self) -> None:
        self.stops += 1


@pytest.fixture()
def execs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every ``os.execv`` the face asks for, without replacing the test."""
    calls: list[list[str]] = []
    monkeypatch.setattr(_base.os, "execv",
                        lambda path, argv: calls.append(list(argv)))
    # set, THEN delete: monkeypatch restores only what it saw, and the face
    # writes this variable into os.environ itself.
    monkeypatch.setenv(RELAUNCHED_ENV, "")
    monkeypatch.delenv(RELAUNCHED_ENV)
    return calls


def test_a_quit_closes_the_face_and_does_not_restart_it(execs) -> None:
    face = _Face("stopped")

    assert face.start() == 0

    assert face.stops == 1
    assert face.bus.closed
    assert execs == []


def test_a_crash_restarts_the_face_with_its_own_command_line(execs) -> None:
    """After cleanup, the same interpreter runs the same command again.

    MUTATION CHECK: drop ``_relaunch_self`` from ``start`` and nothing
    restarts.
    """
    face = _Face("lost")

    face.start()

    assert face.bus.closed, "cleaned up before the restart"
    assert execs == [[sys.executable, *sys.orig_argv[1:]]]
    assert os.environ[RELAUNCHED_ENV]


def test_a_second_crash_within_a_minute_does_not_restart(execs) -> None:
    """An App that dies on start must not relaunch the window forever.

    MUTATION CHECK: drop the guard and this restarts.
    """
    os.environ[RELAUNCHED_ENV] = f"{time.time() - 5:.0f}"

    _Face("lost").start()

    assert execs == []


def test_a_crash_long_after_the_last_restart_restarts_again(execs) -> None:
    os.environ[RELAUNCHED_ENV] = f"{time.time() - 600:.0f}"

    _Face("lost").start()

    assert len(execs) == 1


def test_a_restarted_face_says_so(execs, monkeypatch) -> None:
    said: list[str] = []
    monkeypatch.setattr(_Face, "announce", lambda self, text: said.append(text))
    os.environ[RELAUNCHED_ENV] = f"{time.time() - 600:.0f}"

    _Face().start()

    assert said == [_base.RELAUNCH_NOTICE]


def test_an_app_the_restarted_face_starts_is_not_told_it_restarted(
        execs) -> None:
    """The marker is the face's alone: an App it spawns inherits its
    environment, and logged "stopped unexpectedly and was restarted" about
    ITSELF (driven 2026-10-07).

    MUTATION CHECK: read the marker with ``get`` instead of ``pop`` and it
    stays in the environment.
    """
    os.environ[RELAUNCHED_ENV] = f"{time.time() - 600:.0f}"

    _Face().start()

    assert RELAUNCHED_ENV not in os.environ


def test_a_face_that_was_not_restarted_says_nothing(execs, monkeypatch) -> None:
    said: list[str] = []
    monkeypatch.setattr(_Face, "announce", lambda self, text: said.append(text))

    _Face().start()

    assert said == []


# ── The shell ──────────────────────────────────────────────────────────


class _Prompt:
    """A prompt that answers *lines*, then Ctrl-D."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = list(lines)
        self.asked = 0

    def __call__(self, *args: Any, **kwargs: Any) -> _Prompt:
        return self

    def prompt(self, _text: str) -> str:
        self.asked += 1
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


class _StubWatch:
    instances: list[_StubWatch] = []

    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.lost = threading.Event()
        self.renewed = 0
        _StubWatch.instances.append(self)

    def watch(self) -> None:
        pass

    def renew(self) -> None:
        self.renewed += 1
        self.lost.clear()


@pytest.fixture()
def repl(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _StubWatch.instances.clear()
    monkeypatch.setattr(shell, "_AppWatch", _StubWatch)
    ran: list[list[str]] = []

    def run_line(_app: Any, argv: list[str]) -> int:
        ran.append(argv)
        if argv == ["kill"]:
            _StubWatch.instances[0].stopped.set()
        if argv == ["crash"]:
            _StubWatch.instances[0].lost.set()
        return 0

    monkeypatch.setattr(shell, "_run_typer_line", run_line)
    return ran


def test_the_shell_leaves_when_the_app_is_stopped(repl, monkeypatch, capsys) -> None:
    prompt = _Prompt(["kill", "display list"])
    monkeypatch.setattr(shell, "PromptSession", prompt)

    assert shell.run_shell(shell.typer.Typer()) == 0

    assert repl == [["kill"]], "nothing ran after the App stopped"
    assert "TRCC was stopped" in capsys.readouterr().out


def test_the_shell_carries_on_after_a_crash(repl, monkeypatch, capsys) -> None:
    prompt = _Prompt(["crash", "display list"])
    monkeypatch.setattr(shell, "PromptSession", prompt)

    shell.run_shell(shell.typer.Typer())

    assert repl == [["crash"], ["display", "list"]]
    assert _StubWatch.instances[0].renewed == 1
    assert "the next command starts a new one" in capsys.readouterr().err
