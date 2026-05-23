"""WindowsAutostart — HKCU Run-key writer via DI-injected ``winreg``.

The real ``winreg`` only exists on Windows; the protocol logic here is
driven through a fake ``winreg`` module so the full enable / disable /
state-check cycle runs on the Linux dev box.
"""
from __future__ import annotations

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


def test_resolve_command_falls_back_to_python_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When trcc isn't on PATH, we use ``python -m trcc gui``."""
    from trcc.adapters.system import _autostart

    monkeypatch.setattr(_autostart.shutil, "which", lambda name: None)
    cmd = _autostart._resolve_command()
    assert "-m trcc gui" in cmd


def test_resolve_command_prefers_installed_console_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trcc.adapters.system import _autostart

    fake_path = "/opt/trcc/bin/trcc"
    monkeypatch.setattr(_autostart.shutil, "which",
                        lambda name: fake_path if name == "trcc" else None)
    cmd = _autostart._resolve_command()
    assert cmd == f'"{fake_path}" gui'


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
