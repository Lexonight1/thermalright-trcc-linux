"""Dispatch a Command off the GUI thread -- without the thread holding a widget.

Some Commands wait: a password prompt, a scan, a network check.  A window
must not freeze meanwhile, so it hands the Command to a worker thread.  What
the worker must never hold is the widget.  The way every window used to do
it -- ``Thread(target=self._work)`` -- kept the widget alive on the worker;
when the window was dropped mid-wait, the last reference died THERE, and
PySide tore Qt objects down off the GUI thread: a SIGSEGV in one test
(2026-10-08) and a GIL vs Qt-mutex deadlock that hung the suite, locally and
three times on CI (2026-10-09/10).

So the worker holds three things: the App connection, the Command, and one
relay that lives as long as the process.  The answer crosses to the GUI
thread through the relay's queued signal and goes to *on_done* through a
WEAK method reference -- a widget closed meanwhile just never hears it.
``tests/test_architecture_boundaries.py`` keeps ``Thread(target=self...)``
out of the UI.
"""
from __future__ import annotations

import itertools
import logging
import threading
import weakref
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..core.commands import Command
from ..core.ports import CommandBus
from ..core.results import Result

log = logging.getLogger(__name__)

#: Builds the answer a window shows when the App could not be reached.
Failed = Callable[[str], Result]


class _Relay(QObject):
    """The one QObject a worker touches: made on the GUI thread, never freed."""

    answered = Signal(int, object)          # token, Result

    def __init__(self) -> None:
        super().__init__()
        log.debug("_Relay.__init__")
        self._waiting: dict[int, weakref.WeakMethod[Callable[[Any], None]]] = {}
        self._tokens = itertools.count(1)
        self.answered.connect(self._deliver)

    def wait_for(self, on_done: Callable[[Any], None]) -> int:
        """A token for one answer, to go to *on_done* if it is still there."""
        token = next(self._tokens)
        self._waiting[token] = weakref.WeakMethod(on_done)
        log.debug("_Relay.wait_for: %d -> %s", token,
                  getattr(on_done, "__qualname__", on_done))
        return token

    def _deliver(self, token: int, result: Result) -> None:
        ref = self._waiting.pop(token, None)
        on_done = ref() if ref is not None else None
        if on_done is None:
            log.info("_Relay: answer %d arrived after its window closed -- "
                     "dropped", token)
            return
        log.debug("_Relay._deliver: %d ok=%s", token, result.ok)
        on_done(result)


_relay: _Relay | None = None


def _the_relay() -> _Relay:
    """Made on first use -- by a window, so on the GUI thread."""
    global _relay
    if _relay is None:
        log.info("qt_background: the relay is made")
        _relay = _Relay()
    return _relay


def could_not_reach(message: str) -> Result:
    """The plain failure answer."""
    log.debug("could_not_reach: %s", message)
    return Result(ok=False, message=message)


def dispatch_in_background(bus: CommandBus, command: Command[Any],
                           on_done: Callable[[Any], None], *,
                           failed: Failed = could_not_reach) -> None:
    """Dispatch *command* on a worker thread; its Result goes to *on_done*
    -- a bound method -- on the GUI thread, if its widget is still there.

    The App gone meanwhile is an answer too: *failed* builds it.
    """
    relay = _the_relay()
    token = relay.wait_for(on_done)
    log.info("dispatch_in_background: %s (answer %d)", type(command).__name__,
             token)
    threading.Thread(target=_dispatch, args=(bus, command, relay, token, failed),
                     daemon=True, name=f"trcc-{type(command).__name__}").start()


def _dispatch(bus: CommandBus, command: Command[Any], relay: _Relay,
              token: int, failed: Failed) -> None:
    """The worker: no widget anywhere in reach."""
    log.debug("_dispatch: %s", type(command).__name__)
    try:
        result = bus.dispatch(command)
    except Exception as e:                # the App went away meanwhile
        log.warning("dispatch_in_background: %s failed -- %s: %s",
                    type(command).__name__, type(e).__name__, e)
        result = failed(f"Could not reach TRCC: {e}")
    relay.answered.emit(token, result)
