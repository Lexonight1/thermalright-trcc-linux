"""Which channel reaches the shared App, decided per OS -- run on Linux.

The named pipe itself is exercised for real by ``test_ipc_server.py`` and
``test_ipc_events.py`` in the Windows CI job; this file pins the choices
around it: which transport, whether a UI shares the App, what is spawned.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import types
from pathlib import Path

import pytest

from trcc import _boot, daemon, ipc

#: The tests below that FAKE Windows by deleting AF_UNIX need a host that has
#: it; on real Windows, the last test checks the real answer instead.
posix_only = pytest.mark.skipif(not hasattr(socket, "AF_UNIX"),
                                reason="simulates Windows on a POSIX host")


@posix_only
def test_a_host_with_af_unix_uses_the_unix_socket() -> None:
    from trcc.adapters.infra.unix_socket import UnixSocketChannel
    chan = ipc.channel()
    assert isinstance(chan, UnixSocketChannel)
    assert chan.address == str(ipc.socket_path())


@pytest.mark.skipif(sys.platform != "win32", reason="the real Windows answer")
def test_real_windows_uses_the_named_pipe_of_this_user(monkeypatch) -> None:
    """What a real UI on Windows gets -- conftest's private pipe undone."""
    from trcc.adapters.infra.named_pipe import NamedPipeChannel
    monkeypatch.undo()
    chan = ipc.channel()
    assert isinstance(chan, NamedPipeChannel)
    assert chan.address.startswith("\\\\.\\pipe\\trcc-S-1-5-")


def test_the_os_names_its_channel() -> None:
    """The choice is the Platform's, as SOUND_INPUT is -- not a sys.platform
    test inside ipc.py.  MUTATION CHECK: drop WindowsPlatform.IPC_CHANNEL."""
    from trcc.adapters.infra.named_pipe import NamedPipeChannel
    from trcc.adapters.infra.unix_socket import UnixSocketChannel
    from trcc.adapters.system._base import PLATFORMS
    assert PLATFORMS["linux"].IPC_CHANNEL is UnixSocketChannel
    assert PLATFORMS["darwin"].IPC_CHANNEL is UnixSocketChannel
    assert PLATFORMS["win32"].IPC_CHANNEL is NamedPipeChannel


@posix_only
def test_no_af_unix_means_no_channel(monkeypatch) -> None:
    monkeypatch.delattr(socket, "AF_UNIX")
    assert ipc.channel() is None


def _windows(monkeypatch: pytest.MonkeyPatch, channel: type | None) -> None:
    """Windows as ``ipc`` sees it: the win32 platform, whose channel is
    *channel* -- or, with None, the real pipe, which needs pywin32."""
    from trcc.adapters.system._base import PLATFORMS
    monkeypatch.setattr(sys, "platform", "win32")
    if channel is not None:
        monkeypatch.setattr(PLATFORMS["win32"], "IPC_CHANNEL", channel)


class _FakePipe:
    """A channel that can be built here."""

    def __init__(self, path: Path) -> None:
        self.path = path


@posix_only
def test_windows_uses_the_channel_its_platform_names(monkeypatch) -> None:
    _windows(monkeypatch, _FakePipe)
    chan = ipc.channel()
    assert isinstance(chan, _FakePipe) and chan.path == ipc.socket_path()


@posix_only
def test_windows_without_pywin32_runs_in_process_and_says_why(
        monkeypatch, caplog) -> None:
    """The real pipe module imports anywhere (the ports page is generated
    from every module); its channel refuses without pywin32."""
    _windows(monkeypatch, None)
    with caplog.at_level("WARNING"):
        assert ipc.channel() is None
    assert "NamedPipeChannel cannot work here" in caplog.text


@posix_only
def test_a_windows_ui_shares_the_app_once_a_pipe_exists(monkeypatch) -> None:
    """It used to build its own App, because CPython has no AF_UNIX there."""
    _windows(monkeypatch, _FakePipe)
    monkeypatch.delenv(_boot._ENV_FLAG, raising=False)
    assert _boot._local_reason() is None


def test_a_host_without_geteuid_is_never_elevated_root(monkeypatch) -> None:
    """Windows has no ``os.geteuid``: with a pipe, ``_elevated`` is reached
    there for the first time, and every CLI command died on it (VM,
    2026-10-16).  MUTATION CHECK: call ``os.geteuid()`` unguarded."""
    monkeypatch.delattr(os, "geteuid", raising=False)
    monkeypatch.setenv("SUDO_UID", "1000")
    assert _boot._elevated() is None


@posix_only
def test_a_windows_ui_without_a_pipe_still_runs_in_process(monkeypatch) -> None:
    _windows(monkeypatch, None)
    monkeypatch.delenv(_boot._ENV_FLAG, raising=False)
    assert _boot._local_reason() == "no IPC transport on this platform"


def test_frozen_windows_starts_the_windowed_exe(monkeypatch, tmp_path) -> None:
    """``trcc.exe daemon`` would hold a console window open for the App's
    whole life; ``trcc-gui.exe`` is its windowed twin in the same folder."""
    console = tmp_path / "trcc.exe"
    windowed = tmp_path / "trcc-gui.exe"
    console.touch()
    windowed.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(console))
    monkeypatch.setattr(sys, "platform", "win32")
    assert daemon._daemon_spawn_cmd() == [str(windowed), "daemon"]


def test_a_detached_app_has_no_window_and_its_own_process_group(
        monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200,
                        raising=False)
    assert daemon._detached_flags() == 0x08000200


def test_the_app_lock_uses_msvcrt_where_there_is_no_fcntl(
        monkeypatch, tmp_path: Path) -> None:
    """Windows had no lock at all, so two UIs launched together could each
    start an App (#314's shape)."""
    calls: list[tuple[int, int, int]] = []
    fake = types.ModuleType("msvcrt")
    fake.LK_NBLCK = 2                                      # type: ignore[attr-defined]
    fake.locking = lambda fd, mode, n: calls.append((fd, mode, n))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    lock = ipc.AppLock(tmp_path / "trcc.lock")
    assert lock.acquire()
    lock.release()
    assert len(calls) == 1 and calls[0][1:] == (2, 1)


def test_one_failed_accept_does_not_stop_the_app() -> None:
    """A probe that connects and leaves at once failed the pipe's accept on
    Windows, and the server read any accept error as "closed": the whole App
    shut down after the second CLI command (VM, 2026-10-16).  Only a stop on
    purpose ends the loop.  MUTATION CHECK: ``break`` on every OSError."""
    server = ipc.IPCServer(app=object())  # type: ignore[arg-type]
    calls: list[str] = []

    class _Listener:
        def accept(self) -> tuple[object, None]:
            calls.append("accept")
            if len(calls) == 2:
                server._stop = True          # the real stop, on the 2nd call
            raise OSError(232, "the client already left")

    server._sock = _Listener()
    server.serve_forever()
    assert calls == ["accept", "accept"]
