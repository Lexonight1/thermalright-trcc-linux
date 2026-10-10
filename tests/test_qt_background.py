"""A Command off the GUI thread, without the thread holding a widget."""
from __future__ import annotations

import gc
import threading
import weakref
from typing import Any

from PySide6.QtWidgets import QWidget

from trcc.core.commands import ListDevices
from trcc.core.results import Result
from trcc.ui.qt_background import dispatch_in_background


class _Bus:
    """Answers on the worker; can hold the answer back until released."""

    def __init__(self, hold: bool = False) -> None:
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.on_thread: list[str] = []

    def dispatch(self, cmd: Any) -> Result:
        self.on_thread.append(threading.current_thread().name)
        self.release.wait(5)
        return Result(ok=True, message=type(cmd).__name__)


class _Page(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.heard: list[tuple[Result, str]] = []

    def on_done(self, result: Result) -> None:
        self.heard.append((result, threading.current_thread().name))


def test_the_answer_comes_back_on_the_gui_thread(qtbot: Any) -> None:
    bus, page = _Bus(), _Page()
    qtbot.addWidget(page)
    dispatch_in_background(bus, ListDevices(), page.on_done)
    qtbot.waitUntil(lambda: bool(page.heard), timeout=2000)
    (result, thread), = page.heard
    assert result.message == "ListDevices"
    assert thread == threading.main_thread().name
    assert bus.on_thread == ["trcc-ListDevices"]        # dispatched off it


def test_a_page_dropped_mid_wait_is_freed_and_never_answered(qtbot: Any) -> None:
    """The worker holds no reference to the page: dropping it frees it at
    once, ON the GUI thread -- what the old ``Thread(target=self._work)``
    could not do."""
    bus = _Bus(hold=True)
    page = _Page()
    gone = weakref.ref(page)
    dispatch_in_background(bus, ListDevices(), page.on_done)
    qtbot.waitUntil(lambda: bool(bus.on_thread), timeout=2000)
    del page
    gc.collect()
    assert gone() is None                               # freed while waiting
    bus.release.set()                                   # the answer lands...
    qtbot.wait(100)                                     # ...and goes nowhere


def test_an_app_gone_meanwhile_is_an_answer_too(qtbot: Any) -> None:
    class _Gone:
        def dispatch(self, cmd: Any) -> Result:
            raise ConnectionError("socket closed")

    page = _Page()
    qtbot.addWidget(page)
    dispatch_in_background(_Gone(), ListDevices(), page.on_done)
    qtbot.waitUntil(lambda: bool(page.heard), timeout=2000)
    assert page.heard[0][0] == Result(
        ok=False, message="Could not reach TRCC: socket closed")
