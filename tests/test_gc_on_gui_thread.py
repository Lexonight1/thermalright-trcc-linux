"""The Qt windows collect Python's garbage on the GUI thread, never another.

A collection on a background thread that frees a widget runs the widget's C++
destructor on that thread (shiboken, for every Python subclass of a widget):
the CI hang.  ``trcc.ui.qapp`` turns automatic gc off and collects from a
GUI-thread timer; ``tests/conftest.py`` holds the suite to the same rule.
"""
from __future__ import annotations

import gc
import threading
import time
import weakref
from collections.abc import Callable
from pathlib import Path
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


def _off_gui_thread_now() -> dict[int, str]:
    """Live QObjects whose thread affinity is not the GUI thread: id -> type."""
    from PySide6.QtCore import QObject
    qapp_instance = QApplication.instance()
    assert qapp_instance is not None
    gui = qapp_instance.thread()
    found: dict[int, str] = {}
    for obj in gc.get_objects():
        if not isinstance(obj, QObject):
            continue
        try:
            owner = obj.thread()
        except RuntimeError:        # C++ side already deleted
            continue
        if owner != gui:
            found[id(obj)] = f"{type(obj).__module__}.{type(obj).__qualname__}"
    return found


def _living_off_gui_thread(pause: Callable[[], None]) -> dict[str, int]:
    """QObjects that LIVE on another thread, by type: seen at two scans.

    The collector cannot help an object a background thread owns.  A thread
    may use one of its own for a moment -- the renderer's ``QBuffer`` lives one
    ``encode_png`` call, freed by refcount on that thread, which is sound -- so
    only an object present at both scans counts.  ``dev/tools/gc_threads.py``
    applies the same rule beside the running window.
    """
    before = _off_gui_thread_now()
    pause()
    after = _off_gui_thread_now()
    living: dict[str, int] = {}
    for oid, key in after.items():
        if before.get(oid) == key:
            living[key] = living.get(key, 0) + 1
    return living


def test_the_scan_names_a_qobject_made_on_a_thread(
    _qapplication: QApplication,
) -> None:
    from PySide6.QtCore import QTimer
    made: list[QTimer] = []
    thread = threading.Thread(target=lambda: made.append(QTimer()))
    thread.start()
    thread.join()

    assert _living_off_gui_thread(lambda: time.sleep(0.05)).get(
        "PySide6.QtCore.QTimer", 0) >= 1
    made.clear()
    gc.collect()


def test_the_window_and_its_app_own_no_qobject_off_the_gui_thread(
    tmp_path: Path, qtbot: Any,
) -> None:
    """Two panels connected, a theme playing, the window open on each in turn,
    the App's threads (render, sender, metrics, LED) running -- and every
    QObject still lives on the GUI thread.  Measured 0 in a 40 s drive of the
    whole mock fleet before this gate was written."""
    from tests.conftest import renderable_theme
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import ConnectDevice, LoadTheme
    from trcc.ui.gui.trcc_app import TRCCApp

    lcd, led = "0402:3922", "0416:8001"
    app = App(MockPlatform([{"vid": "0402", "pid": "3922", "fbl": 100},
                            {"type": "led", "vid": "0416", "pid": "8001", "pm": 16}],
                           tmp_path), renderer=QtRenderer())
    try:
        for key in (lcd, led):
            assert app.dispatch(ConnectDevice(key=key)).ok
        theme = renderable_theme(app.platform.paths().theme_dir(320, 320), "Theme1")
        assert app.dispatch(LoadTheme(key=lcd, path=theme)).ok
        app.start_session()             # the loops the window's launcher starts
        assert app.led_animation_loop.is_running and app.metrics_loop.is_running
        win = TRCCApp(app=app)
        qtbot.addWidget(win)
        win.replay_initial_devices()
        qtbot.waitUntil(lambda: lcd in win._handlers and led in win._handlers)
        for key in (led, lcd):
            win._activate_device(key)
            qtbot.wait(750)

        assert _living_off_gui_thread(lambda: qtbot.wait(300)) == {}
        win.close()
    finally:
        app.close()
