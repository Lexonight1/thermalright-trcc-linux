"""``RaiseEvent`` -- "show your window" from a second launch, on Windows.

On Linux and macOS a second ``trcc gui`` connects to the running one's Unix
socket and asks it to raise its window (``ipc.SingleInstance``).  CPython on
Windows has no ``AF_UNIX``, so there the single-instance lock was a file lock
alone, and a second launch -- the Start menu, the desktop icon -- logged
"raised" and did nothing: a window hidden in the tray stayed hidden (win11 VM,
2026-10-20).

A named auto-reset event is Windows' own cross-process doorbell: the running
GUI creates ``Local\\TRCC-<name>-raise`` and waits on it on a thread; a second
launch opens it and sets it.  ``Local\\`` keeps it to this sign-in session, as
the socket is kept to this user.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, cast

try:
    import pywintypes  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32api  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
    import win32event  # pyright: ignore[reportMissingImports, reportMissingModuleSource]
except ImportError:
    # Off Windows the module still imports (doc/REFERENCE_PORTS.md is built
    # from every module); RaiseEvent is only ever made where these exist.
    pywintypes = win32api = win32event = cast(Any, None)

log = logging.getLogger(__name__)

_EVENT_MODIFY_STATE = 0x0002


def _event_name(name: str) -> str:
    """The event's name for the GUI flavour *name* (``gui``, ``qtgui``)."""
    event = f"Local\\TRCC-{name}-raise"
    log.debug("_event_name: %s -> %s", name, event)
    return event


class RaiseEvent:
    """The running GUI's doorbell: :meth:`listen` there, :meth:`ring` here."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._event: Any = None
        self._stop: Any = None
        log.debug("RaiseEvent: %s", _event_name(name))

    def listen(self, on_raise: Callable[[], Callable[[], None] | None]) -> None:
        """Create the event and wait on it on a thread.

        *on_raise* returns the callback to run, or None while the window does
        not exist yet -- read at ring time, as the socket path reads its
        ``on_raise`` attribute.
        """
        self._event = win32event.CreateEvent(None, False, False, _event_name(self._name))
        self._stop = win32event.CreateEvent(None, True, False, None)
        threading.Thread(target=self._wait, args=(on_raise,), daemon=True,
                         name=f"trcc-raise-{self._name}").start()
        log.info("RaiseEvent: listening on %s", _event_name(self._name))

    def _wait(self, on_raise: Callable[[], Callable[[], None] | None]) -> None:
        log.debug("RaiseEvent._wait: %s", self._name)
        while True:
            got = win32event.WaitForMultipleObjects(
                [self._event, self._stop], False, win32event.INFINITE)
            if got != win32event.WAIT_OBJECT_0:
                log.debug("RaiseEvent: %s stopped", self._name)
                return
            callback = on_raise()
            if callback is None:
                log.info("RaiseEvent: raise before the window exists -- dropped")
                continue
            log.info("RaiseEvent: %s rung -- raising the window", self._name)
            try:
                callback()
            except Exception:
                log.exception("RaiseEvent: on_raise raised")

    def ring(self) -> bool:
        """Ask the running GUI to show its window; False when none listens."""
        try:
            handle = win32event.OpenEvent(_EVENT_MODIFY_STATE, False,
                                          _event_name(self._name))
        except pywintypes.error as e:
            log.warning("RaiseEvent: no running %s to raise (%s)", self._name, e)
            return False
        try:
            win32event.SetEvent(handle)
        finally:
            win32api.CloseHandle(handle)
        log.info("RaiseEvent: rang %s", _event_name(self._name))
        return True

    def close(self) -> None:
        """Stop waiting and release the event."""
        log.debug("RaiseEvent.close: %s", self._name)
        if self._stop is not None:
            win32event.SetEvent(self._stop)
        for handle in (self._event, self._stop):
            if handle is not None:
                win32api.CloseHandle(handle)
        self._event = self._stop = None
