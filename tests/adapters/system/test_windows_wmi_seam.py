"""wmi_handle — the sanctioned COM-safe WMI construction seam.

The one-shot Windows probes (device scan, memory/disk/SMART, GPU
enumeration) run on whatever thread a Command lands on, with no COM
apartment from the poll-thread context.  ``wmi_handle`` gives them an
idempotent ``CoInitialize`` + ``wmi.WMI()`` so they don't hit the #131
``x_wmi_uninitialised_thread`` crash.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from trcc.adapters.system._windows_wmi import wmi_handle


class _FakePythoncom:
    com_error = type("com_error", (Exception,), {})

    def __init__(self, *, raise_on_init: bool = False) -> None:
        self.init_calls = 0
        self._raise = raise_on_init

    def CoInitialize(self) -> None:
        self.init_calls += 1
        if self._raise:
            raise self.com_error("already initialized")


def _install(monkeypatch: pytest.MonkeyPatch, pythoncom: object) -> list:
    seen: list = []
    fake_wmi = SimpleNamespace(WMI=lambda **kw: seen.append(kw) or SimpleNamespace(**kw))
    monkeypatch.setitem(sys.modules, "wmi", fake_wmi)
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    return seen


def test_wmi_handle_coinitializes_then_constructs(monkeypatch: pytest.MonkeyPatch) -> None:
    pc = _FakePythoncom()
    seen = _install(monkeypatch, pc)

    handle = wmi_handle(namespace="root\\WMI")

    assert pc.init_calls == 1
    assert seen == [{"namespace": "root\\WMI"}]
    assert handle.namespace == "root\\WMI"


def test_wmi_handle_swallows_double_init_com_error(monkeypatch: pytest.MonkeyPatch) -> None:
    pc = _FakePythoncom(raise_on_init=True)
    _install(monkeypatch, pc)

    # com_error from a second CoInitialize must not propagate.
    handle = wmi_handle()
    assert handle is not None


def test_wmi_handle_works_without_pythoncom(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_wmi = SimpleNamespace(WMI=lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setitem(sys.modules, "wmi", fake_wmi)
    monkeypatch.setitem(sys.modules, "pythoncom", None)  # import → ImportError

    assert wmi_handle() is not None


def test_wmi_handle_raises_without_wmi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "wmi", None)  # import → ImportError
    with pytest.raises(ImportError):
        wmi_handle()


def test_windows_platform_has_no_direct_wmi_construction() -> None:
    """Every WMI handle in windows.py goes through the wmi_handle seam."""
    src = Path("src/trcc/adapters/system/windows.py").read_text()
    assert "wmi.WMI(" not in src, "construct WMI via wmi_handle(), not wmi.WMI() directly"
