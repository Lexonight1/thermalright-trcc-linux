"""The Qt windows collect Python's garbage on the GUI thread, never another.

A collection on a background thread that frees a widget runs the widget's C++
destructor on that thread (shiboken, for every Python subclass of a widget):
the CI hang.  ``trcc.ui.qapp`` turns automatic gc off and collects from a
GUI-thread timer; ``tests/conftest.py`` holds the suite to the same rule.
"""
from __future__ import annotations

import gc
import weakref
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from trcc.ui import qapp


class _Node:
    """A garbage cycle the collector must find: ``node.me is node``."""

    def __init__(self) -> None:
        self.me = self


def _dead_cycle() -> weakref.ref[_Node]:
    """Leave one unreachable cycle behind and return a weak reference to it."""
    return weakref.ref(_Node())


def test_collect_on_gui_thread_turns_automatic_gc_off_and_times_it(
    _qapplication: QApplication,
) -> None:
    gc.enable()
    try:
        timer = qapp.collect_on_gui_thread(_qapplication)
        assert gc.isenabled() is False
    finally:
        gc.disable()
    try:
        assert timer.parent() is _qapplication
        assert timer.interval() == qapp.GC_INTERVAL_MS == 1000
        assert timer.isActive() is True
    finally:
        timer.stop()
        timer.deleteLater()


def test_build_qt_app_hands_gc_to_the_gui_thread_before_the_app_exists(
    monkeypatch: pytest.MonkeyPatch, _qapplication: QApplication,
) -> None:
    import trcc._boot as boot
    # configure_qt_environment pops / sets these; monkeypatch restores them.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QT_LOGGING_RULES", "")
    calls: list[str] = []
    monkeypatch.setattr(qapp, "collect_on_gui_thread",
                        lambda app: calls.append("collector"))
    monkeypatch.setattr(boot, "trcc", lambda **_kw: calls.append("app"))

    qapp.build_qt_app()

    assert calls == ["collector", "app"]


def test_nothing_is_collected_before_gen0_is_due() -> None:
    gc.collect()
    cycle = _dead_cycle()

    assert qapp.collect_due_garbage() == 0
    assert cycle() is not None
    gc.collect()


def test_a_due_collection_frees_the_cycle() -> None:
    gc.collect()
    cycle = _dead_cycle()
    alive = [[] for _ in range(gc.get_threshold()[0] + 1)]

    assert qapp.collect_due_garbage() >= 1
    assert cycle() is None
    del alive


@pytest.mark.parametrize(("incremental", "counts", "generations"), [
    (False, (701, 0, 0), [0]),
    (False, (701, 11, 0), [0, 1]),
    (False, (701, 11, 11), [0, 1, 2]),
    (False, (701, 0, 11), [0]),
    (True, (2001, 11, 11), [1]),
    (True, (2001, 0, 0), [1]),
])
def test_the_generations_collected_match_what_automatic_gc_runs(
    monkeypatch: pytest.MonkeyPatch, incremental: bool,
    counts: tuple[int, int, int], generations: list[int],
) -> None:
    thresholds = (2000, 10, 10) if incremental else (700, 10, 10)
    collected: list[int] = []

    def collect(generation: int = 2) -> int:
        collected.append(generation)
        return 0

    fake: Any = type("FakeGc", (), {
        "get_count": staticmethod(lambda: counts),
        "get_threshold": staticmethod(lambda: thresholds),
        "collect": staticmethod(collect),
    })
    monkeypatch.setattr(qapp, "gc", fake)
    monkeypatch.setattr(qapp, "_INCREMENTAL_GC", incremental)

    qapp.collect_due_garbage()

    assert collected == generations
