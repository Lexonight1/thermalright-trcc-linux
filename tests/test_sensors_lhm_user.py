"""The user's own LibreHardwareMonitor: read when it publishes WMI, never
launched.  TRCC stopped shipping LHM when 0.9.5 removed its WMI provider
(#319); an LHM 0.9.4 or older the user runs is still read."""
from __future__ import annotations

import threading

from trcc.adapters.sensors import _lhm
from trcc.adapters.sensors._lhm import _UserLhm


def test_a_running_lhm_is_found_by_its_namespace() -> None:
    assert _UserLhm(probe=lambda: object()).available() is True


def test_no_namespace_is_asked_once_and_remembered() -> None:
    """Probing a missing namespace costs a WMI round-trip; a machine without
    LHM -- most of them -- must not pay it every tick.

    MUTATION CHECK: probe on every call."""
    asked: list[int] = []

    def probe() -> None:
        asked.append(1)

    user_lhm = _UserLhm(probe=probe)
    assert [user_lhm.available() for _ in range(5)] == [False] * 5
    assert asked == [1]


def test_threads_asking_together_probe_once() -> None:
    asked: list[int] = []
    gate = threading.Barrier(4)

    def probe() -> object:
        asked.append(1)
        return object()

    user_lhm = _UserLhm(probe=probe)

    def ask() -> None:
        gate.wait()
        user_lhm.available()

    threads = [threading.Thread(target=ask) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert asked == [1]


def test_without_lhm_the_sources_get_no_handle(monkeypatch) -> None:
    monkeypatch.setattr(_lhm, "_USER_LHM", _UserLhm(probe=lambda: None))
    monkeypatch.setattr(_lhm._handle_local, "lhm_ns", None, raising=False)
    assert _lhm._default_handle_factory() is None


def test_nothing_launches_lhm_any_more() -> None:
    """The bundled copy and everything that started it are gone."""
    for gone in ("LhmSubprocess", "_SHARED_LHM", "_spawn_lhm", "_lhm_exe_path",
                 "_lhm_process_running"):
        assert not hasattr(_lhm, gone), gone
