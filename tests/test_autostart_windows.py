"""WindowsAutostart — HKCU Run-key writer via DI-injected ``winreg``.

The real ``winreg`` only exists on Windows; the protocol logic here is
driven through a fake ``winreg`` module so the full enable / disable /
state-check cycle runs on the Linux dev box.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trcc.adapters.system._autostart import (
    NoopAutostart,
    WindowsAutostart,
)

# =========================================================================
# Fake winreg — captures every call into an in-memory dict
# =========================================================================


class _FakeKey:
    """One open registry key.  Acts as a context manager + dict store."""

    def __init__(self, store: dict[str, str]) -> None:
        self._store = store

    def __enter__(self) -> _FakeKey:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _FakeWinreg:
    """In-memory stand-in for the ``winreg`` module."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        # Maps (hive, subkey) → {value_name: data}
        self.store: dict[tuple[str, str], dict[str, str]] = {}

    def OpenKeyEx(self, hive: str, subkey: str, reserved: int, access: int) -> _FakeKey:
        del reserved, access
        store = self.store.setdefault((hive, subkey), {})
        return _FakeKey(store)

    def QueryValueEx(self, key: _FakeKey, name: str) -> tuple[str, int]:
        if name not in key._store:
            raise FileNotFoundError(name)
        return key._store[name], self.REG_SZ

    def SetValueEx(self, key: _FakeKey, name: str, reserved: int,
                   regtype: int, data: str) -> None:
        del reserved, regtype
        key._store[name] = data

    def DeleteValue(self, key: _FakeKey, name: str) -> None:
        if name not in key._store:
            raise FileNotFoundError(name)
        del key._store[name]


# =========================================================================
# is_enabled / enable / disable round-trip
# =========================================================================


def test_is_enabled_false_before_any_writes() -> None:
    reg = _FakeWinreg()
    autostart = WindowsAutostart(
        command='"C:\\trcc.exe" gui',
        registry=reg,
    )
    assert autostart.is_enabled() is False


def test_enable_writes_the_command_to_run_key() -> None:
    reg = _FakeWinreg()
    autostart = WindowsAutostart(
        command='"C:\\trcc.exe" gui',
        registry=reg,
    )
    autostart.enable()

    run_key = reg.store[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run")]
    assert run_key == {"TRCCNext": '"C:\\trcc.exe" gui'}


def test_is_enabled_true_after_enable() -> None:
    reg = _FakeWinreg()
    autostart = WindowsAutostart(command="X", registry=reg)
    autostart.enable()
    assert autostart.is_enabled() is True


def test_disable_removes_the_value() -> None:
    reg = _FakeWinreg()
    autostart = WindowsAutostart(command="X", registry=reg)
    autostart.enable()
    assert autostart.is_enabled() is True
    autostart.disable()
    assert autostart.is_enabled() is False


def test_disable_when_value_missing_is_silent() -> None:
    """Disabling a never-enabled autostart shouldn't raise."""
    reg = _FakeWinreg()
    autostart = WindowsAutostart(command="X", registry=reg)
    autostart.disable()                  # no FileNotFoundError leaks out
    assert autostart.is_enabled() is False


def test_is_enabled_false_when_value_differs_from_current_command() -> None:
    """Stale install with a different command path → report disabled.

    Defensive: the next enable() rewrites the right value, so we never
    silently honor a wrong launch line.
    """
    reg = _FakeWinreg()
    # User had v9.x installed with the old command line
    reg.store[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run")] = {
        "TRCCNext": '"C:\\old\\trcc.exe" gui',
    }
    autostart = WindowsAutostart(command='"C:\\new\\trcc.exe" gui',
                                 registry=reg)
    assert autostart.is_enabled() is False


def test_value_name_is_configurable() -> None:
    """Tests + future plugins might want a non-default value name."""
    reg = _FakeWinreg()
    autostart = WindowsAutostart(command="X", registry=reg, value_name="Custom")
    autostart.enable()
    run_key = reg.store[("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run")]
    assert run_key == {"Custom": "X"}


# =========================================================================
# Graceful degradation when winreg is unavailable
# =========================================================================


def test_methods_are_noop_when_registry_is_none() -> None:
    """Linux dev box — winreg unavailable → silent degradation, never crash."""
    autostart = WindowsAutostart(command="X", registry=None)
    assert autostart.is_enabled() is False
    autostart.enable()                 # silent
    autostart.disable()                # silent
    autostart.refresh()


# =========================================================================
# NoopAutostart fallback used by macOS / BSD
# =========================================================================


def test_noop_autostart_always_disabled() -> None:
    autostart = NoopAutostart()
    assert autostart.is_enabled() is False
    autostart.enable()                 # silent
    autostart.disable()                # silent
    autostart.refresh()


# =========================================================================
# _resolve_command — sanity on the fallback path
# =========================================================================


def test_the_run_entry_uses_the_windowless_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pythonw.exe -m trcc``: python.exe and the pip trcc.exe launcher are
    console programs, and a Run entry naming either opens a terminal at every
    sign-in.  Never the PATH lookup ``launch_argv`` makes for Linux."""
    from trcc.adapters.system import _autostart

    (tmp_path / "python.exe").touch()
    (tmp_path / "pythonw.exe").touch()
    monkeypatch.setattr(_autostart.sys, "executable", str(tmp_path / "python.exe"))
    monkeypatch.setattr(_autostart.shutil, "which", lambda name: "/opt/trcc.exe")

    assert _autostart._resolve_command() == (
        f'"{tmp_path / "pythonw.exe"}" -m trcc gui --resume')
    assert _autostart._resolve_command("daemon") == (
        f'"{tmp_path / "pythonw.exe"}" -m trcc daemon')


def test_a_missing_pythonw_falls_back_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from trcc.adapters.system import _autostart

    (tmp_path / "python.exe").touch()
    monkeypatch.setattr(_autostart.sys, "executable", str(tmp_path / "python.exe"))

    assert _autostart._resolve_command() == (
        f'"{tmp_path / "python.exe"}" -m trcc gui --resume')
    assert any("opens a console window at sign-in" in r.getMessage()
               for r in caplog.records)


# =========================================================================
# Helper — SimpleNamespace fake satisfies the same protocol
# =========================================================================


def test_works_with_simplenamespace_fake_for_quick_smoke() -> None:
    """The protocol surface is small enough to fake with a SimpleNamespace."""
    storage: dict[str, str] = {}

    class _Key:
        def __enter__(self) -> _Key:
            return self
        def __exit__(self, *a: Any) -> None:
            pass

    def open_key(*args: Any) -> _Key:
        del args
        return _Key()

    fake = SimpleNamespace(
        HKEY_CURRENT_USER="HKCU",
        KEY_READ=1,
        KEY_SET_VALUE=2,
        REG_SZ=1,
        OpenKeyEx=open_key,
        QueryValueEx=lambda key, name: (storage[name], 1) if name in storage else _raise(FileNotFoundError),
        SetValueEx=lambda key, name, _r, _t, data: storage.__setitem__(name, data),
        DeleteValue=lambda key, name: storage.pop(name, None),
    )

    autostart = WindowsAutostart(command="X", registry=fake)
    autostart.enable()
    assert storage == {"TRCCNext": "X"}


def _raise(exc_class: type[BaseException]) -> Any:
    raise exc_class


# =========================================================================
# refresh — rewrite a stale Run-key value (the #201 upgrade path)
# =========================================================================
#
# This was a no-op: "the Run key needs no compilation step", true only while
# the command could never change.  #201 added ``--resume``; without a refresh
# that rewrites, an already-enabled user keeps the old bare command forever
# and the fix reaches new installs only.  Linux picks changes up because XDG
# refresh() re-renders; these are that, for the registry and the plist.


def test_refresh_rewrites_a_stale_command() -> None:
    reg = _FakeWinreg()
    old = WindowsAutostart(command='"C:\\old\\trcc.exe" gui', registry=reg)
    old.enable()

    new = WindowsAutostart(command='"C:\\new\\trcc.exe" gui --resume',
                           registry=reg)
    assert not new.is_enabled(), (
        "precondition: a stale value must not read as enabled"
    )

    new.refresh()

    assert new.is_enabled(), "refresh left the stale command in place"


def test_refresh_does_not_enable_autostart_nobody_asked_for() -> None:
    """The invariant every refresh shares: never create an entry."""
    reg = _FakeWinreg()
    autostart = WindowsAutostart(command="X", registry=reg,
                                 schtasks=_FakeSchtasks())

    autostart.refresh()

    assert not autostart.is_enabled()
    assert all(not values for values in reg.store.values()), (
        f"refresh created a Run-key entry: {reg.store}"
    )


# =========================================================================
# Which UI starts with the computer
# =========================================================================


@pytest.mark.parametrize("target", ["gui", "qtgui", "api", "daemon"])
def test_a_non_default_target_still_reads_as_enabled(target: str) -> None:
    """``is_enabled`` compares against the INSTALLED target's command.

    It used to compare against a fixed ``self._cmd``, so an entry enabled for
    ``daemon`` reported DISABLED because this manager's default is ``gui`` —
    and the UI would have offered to enable autostart that was already on.
    This is the constraint that rules out storing the target in Settings: the
    manager cannot see Settings, so the entry has to carry the answer.
    """
    reg = _FakeWinreg()
    autostart = WindowsAutostart(registry=reg)

    autostart.enable(target)

    assert autostart.installed_target() == target
    assert autostart.is_enabled(), f"{target} entry read as disabled"


def test_refresh_preserves_a_non_default_target() -> None:
    reg = _FakeWinreg()
    autostart = WindowsAutostart(registry=reg)
    autostart.enable("daemon")

    autostart.refresh()

    assert autostart.installed_target() == "daemon"


# =========================================================================
# The sign-in task the elevated builds used — carried over, never written
# =========================================================================
#
# Builds before TRCC's service were --uac-admin, which Windows will not start
# from the Run key, so they autostarted from a task at the highest run level
# (the C# oracle's way, Form1.cs:271).  No build is elevated now: the Run key
# starts every one, and an upgraded install's task is the user's choice to
# carry over.  The App only READS it -- measured on the win11 VM 2026-10-19, a
# normal process gets "Access is denied" deleting a task made elevated, so the
# installer deletes it.


_PROGRAM = Path(r"C:\Program Files\TRCC\trcc-gui.exe")


def _task_xml(arguments: str) -> str:
    """A task as ``schtasks /query /xml`` hands it back, declaration and all."""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <Principals><Principal id="Author">'
        '<RunLevel>HighestAvailable</RunLevel></Principal></Principals>\n'
        '  <Actions Context="Author"><Exec>'
        f'<Command>{_PROGRAM}</Command><Arguments>{arguments}</Arguments>'
        '</Exec></Actions>\n'
        '</Task>\n'
    )


class _FakeSchtasks:
    """In-memory ``schtasks.exe`` that answers queries, as a normal user's does.

    Keeps each task's XML as Windows returns it -- INCLUDING its
    ``encoding="UTF-16"`` declaration: a parser that cannot take that is a
    parser that fails on Windows.  Records every call, so a test can prove
    the App never tried to write one.
    """

    def __init__(self, tasks: dict[str, str] | None = None) -> None:
        self.tasks = dict(tasks or {})
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        match args:
            case ["/query", "/tn", name, "/xml"]:
                if name not in self.tasks:
                    return subprocess.CompletedProcess(
                        args, 1, "",
                        "ERROR: The system cannot find the file specified.")
                return subprocess.CompletedProcess(args, 0, self.tasks[name], "")
        raise AssertionError(f"the App wrote to Task Scheduler: {args}")


def _upgraded(arguments: str | None, **kw: Any) -> tuple[WindowsAutostart,
                                                         _FakeSchtasks,
                                                         _FakeWinreg]:
    """An install upgraded from an elevated build whose task ran *arguments*."""
    schtasks = _FakeSchtasks({} if arguments is None
                             else {"TRCC Linux": _task_xml(arguments)})
    reg = _FakeWinreg()
    return (WindowsAutostart(registry=reg, schtasks=schtasks, **kw),
            schtasks, reg)


def _run_key(reg: _FakeWinreg) -> dict[str, str]:
    """The fake Run key's values — the LIVE dict, so a write lands.

    ``.get(..., {})`` returned a throwaway dict until something had opened the
    key, which made "the old entry is gone" pass without it ever existing.
    """
    return reg.store.setdefault(
        ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"), {})


@pytest.mark.parametrize("target", ["gui", "qtgui", "api", "daemon"])
def test_refresh_carries_the_old_task_over_for_the_same_target(
    target: str,
) -> None:
    """The upgrade path: the task IS the user's choice, so it becomes a Run
    value for the same target.

    MUTATION CHECK: drop the task branch from refresh and nothing is written.
    """
    from trcc.core.models import AUTOSTART_TARGETS
    autostart, _, reg = _upgraded(" ".join([target, *AUTOSTART_TARGETS[target]]))

    autostart.refresh()

    assert autostart.installed_target() == target
    assert autostart.is_enabled()
    assert list(_run_key(reg)) == ["TRCCNext"]


def test_the_app_only_reads_the_task() -> None:
    """Deleting it would fail for the App anyway -- the installer does it."""
    autostart, schtasks, _ = _upgraded("gui --resume")

    autostart.refresh()

    assert schtasks.calls == [["/query", "/tn", "TRCC Linux", "/xml"]]
    assert "TRCC Linux" in schtasks.tasks


def test_a_run_value_already_there_wins_over_the_task() -> None:
    """The Run value is the newer record: the task is never consulted."""
    autostart, schtasks, reg = _upgraded("qtgui --resume")
    autostart.enable("daemon")

    autostart.refresh()

    assert autostart.installed_target() == "daemon"
    assert schtasks.calls == []


@pytest.mark.parametrize("task", [None, "--log-file x gui", "not xml at all"])
def test_no_task_or_one_we_cannot_read_enables_nothing(task: str | None) -> None:
    """A wrong target is worse than none: never guess the user's choice."""
    schtasks = _FakeSchtasks({} if task is None else {
        "TRCC Linux": task if task == "not xml at all" else _task_xml(task)})
    reg = _FakeWinreg()
    autostart = WindowsAutostart(registry=reg, schtasks=schtasks)

    autostart.refresh()

    assert not autostart.is_enabled()
    assert _run_key(reg) == {}


def test_a_missing_schtasks_reads_as_no_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows without schtasks.exe on its path must not fail a refresh."""
    from trcc.adapters.system import _autostart

    def gone(*args: Any, **kw: Any) -> Any:
        raise FileNotFoundError(2, "No such file", str(_autostart._SCHTASKS))
    monkeypatch.setattr(_autostart.subprocess, "run", gone)

    result = _autostart._run_schtasks(["/query", "/tn", "TRCC Linux", "/xml"])

    assert result.returncode == 1


@pytest.mark.parametrize("frozen", [True, False])
def test_every_build_autostarts_from_the_run_key(
    monkeypatch: pytest.MonkeyPatch, frozen: bool,
) -> None:
    from trcc.adapters.system.windows import WindowsPlatform
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    assert type(WindowsPlatform()._build_autostart()) is WindowsAutostart


@pytest.mark.parametrize(("target", "tail"), [
    ("gui", "gui --resume"), ("daemon", "daemon")])
def test_the_installer_build_runs_its_windowed_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, tail: str,
) -> None:
    """``trcc-gui.exe`` beside whichever exe is running, never ``trcc.exe``:
    that is a console program and would open a terminal at every sign-in."""
    from trcc.adapters.system import _autostart
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(_autostart.sys, "executable", str(tmp_path / "trcc.exe"))

    assert _autostart._resolve_command(target) == (
        f'"{tmp_path / "trcc-gui.exe"}" {tail}')


# =========================================================================
# trcc-gui.exe — the windowed twin of trcc.exe takes its arguments
# =========================================================================


@pytest.mark.parametrize(("exe", "args", "dispatched"), [
    ("trcc-gui.exe", [], ["gui"]),
    ("trcc-gui.exe", ["--resume"], ["gui", "--resume"]),
    ("trcc-gui.exe", ["qtgui", "--resume"], ["qtgui", "--resume"]),
    ("trcc.exe", ["--version"], ["--version"]),
])
def test_the_frozen_entry_passes_its_arguments_on(
    tmp_path: Path, exe: str, args: list[str], dispatched: list[str],
) -> None:
    """The sign-in task runs ``trcc-gui.exe --resume``.  ``__main__`` used to
    call ``gui()`` directly, so ``--resume`` never arrived and the window
    popped at every sign-in.  Run in a SUBPROCESS: ``__main__`` sets up
    process-wide logging at import, which must not leak into this one."""
    import json
    import os
    code = (
        "import json, runpy, sys\n"
        "import trcc._entry as e\n"
        "e.main = lambda: print(json.dumps(sys.argv[1:])) or 0\n"
        f"sys.executable = {str(tmp_path / exe)!r}\n"
        f"sys.argv = ['x', *{args!r}]\n"
        "runpy.run_module('trcc', run_name='__main__')\n"
    )
    src = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        timeout=60, env={**os.environ, "PYTHONPATH": src,
                         "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == dispatched


_RUN = ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run")


@pytest.mark.parametrize("action", ["enable", "disable", "refresh"])
def test_the_legacy_run_value_trcc_wrote_is_removed(action: str) -> None:
    """Legacy wrote 'TRCC Linux' = "<exe>" gui --resume; nothing removed it.

    MUTATION CHECK: skip the retirement and the value stays.
    """
    reg = _FakeWinreg()
    reg.store[_RUN] = {"TRCC Linux": '"C:\\Users\\u\\AppData\\trcc.exe" gui --resume'}
    autostart = WindowsAutostart(command='"C:\\trcc.exe" gui', registry=reg,
                                 schtasks=_FakeSchtasks())

    getattr(autostart, action)()

    assert "TRCC Linux" not in reg.store[_RUN]


def test_a_run_value_of_that_name_for_another_program_stays() -> None:
    reg = _FakeWinreg()
    reg.store[_RUN] = {"TRCC Linux": '"C:\\Other\\app.exe"'}
    autostart = WindowsAutostart(command='"C:\\trcc.exe" gui', registry=reg)

    autostart.disable()

    assert reg.store[_RUN] == {"TRCC Linux": '"C:\\Other\\app.exe"'}


def test_refresh_retires_it_even_with_nothing_to_carry_over() -> None:
    """Refresh returns early with no entry and no task -- the one case where
    only the legacy value is left.

    MUTATION CHECK: move the retirement below that return and it stays.
    """
    autostart, _, reg = _upgraded(None)
    _run_key(reg)["TRCC Linux"] = '"C:\\Program Files\\TRCC\\trcc-gui.exe" gui --resume'

    autostart.refresh()

    assert "TRCC Linux" not in _run_key(reg)
