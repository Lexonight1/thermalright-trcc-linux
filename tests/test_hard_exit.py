"""How a Qt face's process ends: ``Platform.hard_exit``, through ``reap_after``.

Measured on the win11 VM, 2026-10-19: about one GUI exit in three crashed
(0xc0000005 in pyside6.abi3.dll+0x1c91e, ``SignalManager::retrieveMetaObject``)
after "cleanup complete".  ``os._exit`` there ends in ``ExitProcess``, which
runs Qt's DLL teardown into a half-stopped Python; Windows now ends the
process with ``TerminateProcess``, which runs none, as ``_exit`` runs none on
Linux.  And a face whose App vanished while its window was built raised past
the reap and took 6-80 s to leave on its own; it is reaped too.
"""
from __future__ import annotations

import ctypes
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import FakePlatform

from trcc.adapters.system import _base, windows
from trcc.ui import _base as ui_base


class _Exited(Exception):
    """``os._exit`` stood in for: what it was asked to exit with."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _no_os_exit(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    def fake(code: int) -> None:
        raise _Exited(code)
    monkeypatch.setattr(module.os, "_exit", fake)


# ── reap_after: the one place both Qt launchers end the process ─────────


def test_production_ends_the_process_with_the_faces_code(
        fake_platform: FakePlatform) -> None:
    """MUTATION CHECK: return without ``hard_exit`` and this fails."""
    with pytest.raises(SystemExit) as ended:
        ui_base.reap_after(fake_platform, lambda: 7, force_exit=True)
    assert ended.value.code == 7


def test_a_face_that_raises_is_reaped_too(
        fake_platform: FakePlatform, caplog: pytest.LogCaptureFixture) -> None:
    """The App vanished while the window was built: logged, then reaped.
    MUTATION CHECK: re-raise in production and this fails."""
    def start() -> int:
        raise RuntimeError("the TRCC daemon is not reachable")

    with caplog.at_level(logging.CRITICAL), pytest.raises(SystemExit) as ended:
        ui_base.reap_after(fake_platform, start, force_exit=True)
    assert ended.value.code == 1
    assert "the TRCC daemon is not reachable" in caplog.text


def test_without_force_exit_it_returns_and_raises_as_before(
        fake_platform: FakePlatform) -> None:
    """Tests and the dev mock keep the code and the exception."""
    assert ui_base.reap_after(fake_platform, lambda: 3, force_exit=False) == 3

    def start() -> int:
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        ui_base.reap_after(fake_platform, start, force_exit=False)


# ── the launchers route through it ───────────────────────────────────────


def test_the_gui_launcher_reaps_through_the_platform(
        monkeypatch: pytest.MonkeyPatch, fake_platform: FakePlatform) -> None:
    from trcc.ui import _uis
    from trcc.ui.gui import run
    monkeypatch.setattr(_uis.GuiUI, "start", lambda self, platform: 4)
    with pytest.raises(SystemExit) as ended:
        run(fake_platform)
    assert ended.value.code == 4


def test_the_qtgui_launcher_resolves_this_host_and_reaps(
        monkeypatch: pytest.MonkeyPatch, fake_platform: FakePlatform) -> None:
    """``trcc qtgui`` passes no platform: the launcher resolves the host's."""
    from trcc.adapters import system
    from trcc.ui import _uis
    from trcc.ui.qtgui import launch
    started: list[object] = []
    monkeypatch.setattr(system, "current_platform", lambda: fake_platform)
    monkeypatch.setattr(_uis.QtGuiUI, "start",
                        lambda self, platform: started.append(platform) or 5)
    with pytest.raises(SystemExit) as ended:
        launch()
    assert ended.value.code == 5
    assert started == [fake_platform]


# ── the per-OS ways to end a process ─────────────────────────────────────


def test_posix_ends_with_os_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from trcc.adapters.system.linux import LinuxOS
    _no_os_exit(monkeypatch, _base)
    with pytest.raises(_Exited) as ended:
        LinuxOS().hard_exit(2)
    assert ended.value.code == 2


class _Fn:
    """A kernel32 export: callable, with the prototype ctypes would carry."""

    def __init__(self, body: Any) -> None:
        self._body = body
        self.argtypes: list[Any] | None = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        return self._body(*args)


class _Kernel32:
    """``TerminateProcess`` on the current-process pseudo-handle, recorded.

    As Windows does, it refuses the pseudo-handle unless the prototypes are
    declared: undeclared, ctypes passes -1 as a 32-bit int and the call
    fails with WinError 6 -- what the first build of this fix did on the VM,
    so every exit fell back to os._exit and still crashed.
    """

    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.terminated: list[tuple[int, int]] = []
        self.GetCurrentProcess = _Fn(lambda: -1)
        self.TerminateProcess = _Fn(self._terminate)
        self.GetLastError = _Fn(lambda: 5)

    def _terminate(self, handle: int, code: int) -> int:
        declared = (self.GetCurrentProcess.restype is ctypes.c_void_p
                    and self.TerminateProcess.argtypes == [ctypes.c_void_p,
                                                           ctypes.c_uint])
        if not declared:
            return 0
        self.terminated.append((handle, code))
        return 1 if self.ok else 0


def test_windows_ends_with_terminate_process(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Not ``os._exit``: that runs the DLLs' teardown on Windows.
    MUTATION CHECK: call ``os._exit`` first and ``terminated`` stays empty."""
    kernel32 = _Kernel32(ok=True)
    monkeypatch.setattr(windows, "_kernel32", lambda: kernel32)
    _no_os_exit(monkeypatch, windows)
    with pytest.raises(_Exited):
        windows.WindowsPlatform.hard_exit(SimpleNamespace(), 3)  # type: ignore[arg-type]
    assert kernel32.terminated == [(-1, 3)]


def test_windows_falls_back_when_terminate_process_refuses(
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    kernel32 = _Kernel32(ok=False)
    monkeypatch.setattr(windows, "_kernel32", lambda: kernel32)
    _no_os_exit(monkeypatch, windows)
    with caplog.at_level(logging.WARNING), pytest.raises(_Exited) as ended:
        windows.WindowsPlatform.hard_exit(SimpleNamespace(), 3)  # type: ignore[arg-type]
    assert ended.value.code == 3
    assert "TerminateProcess failed (WinError 5)" in caplog.text
