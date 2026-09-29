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
    WindowsTaskAutostart,
    _render_task_xml,
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

    autostart = WindowsAutostart(command="X", registry=fake)  # type: ignore[arg-type]
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
    autostart = WindowsAutostart(command="X", registry=reg)

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
# WindowsTaskAutostart — the sign-in task the installer build needs
# =========================================================================
#
# Measured on the win11 VM: the installer's exes are --uac-admin, and a Run
# entry for one was present, the user signed in, and nothing started — Windows
# blocks elevation in the sign-in path.  The C# oracle uses a task at the
# highest run level (Form1.cs:271).


_PROGRAM = Path(r"C:\Program Files\TRCC\trcc-gui.exe")


class _FakeSchtasks:
    """In-memory ``schtasks.exe``: keeps each task's XML as Windows returns it.

    ``/query /xml`` hands back the whole document INCLUDING its
    ``encoding="UTF-16"`` declaration, as the real one does — a parser that
    cannot take that is a parser that fails on Windows.
    """

    def __init__(self, *, fail_create: bool = False) -> None:
        self.tasks: dict[str, str] = {}
        self.fail_create = fail_create

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        def done(code: int, out: str = "", err: str = ""):
            return subprocess.CompletedProcess(args, code, out, err)
        match args:
            case ["/create", "/tn", name, "/xml", path, "/f"]:
                if self.fail_create:
                    return done(1, err="ERROR: Access is denied.")
                self.tasks[name] = Path(path).read_text(encoding="utf-16")
                return done(0, "SUCCESS")
            case ["/query", "/tn", name, "/xml"]:
                if name not in self.tasks:
                    return done(1, err="ERROR: The system cannot find the file specified.")
                return done(0, self.tasks[name])
            case ["/delete", "/tn", name, "/f"]:
                return done(0 if self.tasks.pop(name, None) is not None else 1)
        raise AssertionError(f"unexpected schtasks call: {args}")


def _task_autostart(**kw: Any) -> tuple[WindowsTaskAutostart, _FakeSchtasks, _FakeWinreg]:
    schtasks = kw.pop("schtasks", None) or _FakeSchtasks()
    reg = _FakeWinreg()
    legacy = WindowsAutostart(command='"C:\\py\\pythonw.exe" -m trcc gui --resume',
                              registry=reg)
    return (WindowsTaskAutostart(program=kw.pop("program", _PROGRAM),
                                 run=schtasks, legacy=legacy),
            schtasks, reg)


def _run_key(reg: _FakeWinreg) -> dict[str, str]:
    """The fake Run key's values — the LIVE dict, so a write lands.

    ``.get(..., {})`` returned a throwaway dict until something had opened the
    key, which made "the old entry is gone" pass without it ever existing.
    """
    return reg.store.setdefault(
        ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"), {})


def test_the_task_is_what_the_oracle_ends_up_with() -> None:
    """Highest run level (no UAC prompt at sign-in), no 72 h kill, no battery
    rule, one instance — the settings the C# rewrites into its task XML."""
    import xml.etree.ElementTree as ET
    xml = _render_task_xml(r"C:\A & B\trcc-gui.exe", "gui --resume", r"PC\me")
    task = ET.fromstring(xml.split("?>", 1)[1])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

    def text(path: str) -> str | None:
        return task.findtext(path, None, ns)

    assert text("t:Triggers/t:LogonTrigger/t:UserId") == r"PC\me"
    assert text("t:Principals/t:Principal/t:RunLevel") == "HighestAvailable"
    assert text("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text("t:Settings/t:ExecutionTimeLimit") == "PT0S"
    assert text("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert text("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text("t:Actions/t:Exec/t:Command") == r"C:\A & B\trcc-gui.exe"
    assert text("t:Actions/t:Exec/t:Arguments") == "gui --resume"


def test_enable_creates_the_task_and_removes_the_run_entry_it_replaces() -> None:
    autostart, schtasks, reg = _task_autostart()
    _run_key(reg)["TRCCNext"] = '"C:\\Program Files\\TRCC\\trcc.exe" gui --resume'

    autostart.enable()

    assert "TRCC Linux" in schtasks.tasks
    assert autostart.is_enabled()
    assert autostart.installed_target() == "gui"
    assert "TRCCNext" not in _run_key(reg), "the dead Run entry was left behind"


def test_is_enabled_false_before_enable() -> None:
    autostart, _, _ = _task_autostart()
    assert not autostart.is_enabled()
    assert autostart.installed_target() is None


def test_a_task_for_a_moved_install_reads_as_disabled() -> None:
    """The gui re-enables what reads disabled, so the entry heals itself."""
    autostart, schtasks, _ = _task_autostart()
    autostart.enable()
    moved, _, _ = _task_autostart(schtasks=schtasks,
                                  program=Path(r"D:\TRCC\trcc-gui.exe"))
    assert not moved.is_enabled()


def test_disable_removes_the_task_and_the_run_entry() -> None:
    autostart, schtasks, reg = _task_autostart()
    autostart.enable()
    _run_key(reg)["TRCCNext"] = "stale"

    autostart.disable()

    assert schtasks.tasks == {}
    assert "TRCCNext" not in _run_key(reg)
    assert not autostart.is_enabled()


def test_a_failed_create_keeps_the_run_entry() -> None:
    """Never strand a user: the old entry goes only once the task exists."""
    autostart, _, reg = _task_autostart(schtasks=_FakeSchtasks(fail_create=True))
    _run_key(reg)["TRCCNext"] = "old"

    autostart.enable()

    assert _run_key(reg)["TRCCNext"] == "old"
    assert not autostart.is_enabled()


def test_refresh_migrates_a_run_entry_to_a_task_for_the_same_target() -> None:
    """An upgrade from the Run-key era: the old value IS the user's choice."""
    autostart, schtasks, reg = _task_autostart()
    _run_key(reg)["TRCCNext"] = '"C:\\Program Files\\TRCC\\trcc.exe" qtgui --resume'

    autostart.refresh()

    assert autostart.installed_target() == "qtgui"
    assert autostart.is_enabled()
    assert "TRCCNext" not in _run_key(reg)


def test_refresh_does_not_enable_a_task_nobody_asked_for() -> None:
    autostart, schtasks, _ = _task_autostart()
    autostart.refresh()
    assert schtasks.tasks == {}


def test_refresh_keeps_the_installed_target() -> None:
    autostart, _, _ = _task_autostart()
    autostart.enable("daemon")
    autostart.refresh()
    assert autostart.installed_target() == "daemon"


@pytest.mark.parametrize(("frozen", "kind"), [
    (True, WindowsTaskAutostart), (False, WindowsAutostart)])
def test_the_installer_build_gets_the_task_and_pip_the_run_key(
    monkeypatch: pytest.MonkeyPatch, frozen: bool, kind: type,
) -> None:
    from trcc.adapters.system.windows import WindowsPlatform
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    assert type(WindowsPlatform()._build_autostart()) is kind


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
