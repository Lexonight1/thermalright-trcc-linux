"""Whether a newer TRCC exists -- asked by the App, never by a window.

Once when the session starts, then every hour, on a thread of its own; the
last answer is kept, so a window opening later reads it without the network.
Every answer goes to *on_checked* -- the App publishes it as an event, and
every open window shows the same thing.

It used to run in the classic window: each About panel started a thread whose
target was its own bound method, so the thread kept the widget alive.  When a
window was dropped while a check was in flight, the last reference died on
that thread, and PySide tore Qt objects down off the GUI thread -- a SIGSEGV
in one test (2026-10-08) and a GIL vs Qt-mutex deadlock that hung the suite,
locally and three times on CI (2026-10-09/10).  Here the thread holds no
widget at all.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from ..core.results import UpdateCheckResult

log = logging.getLogger(__name__)

#: Seconds between checks -- the classic window's hourly timer, kept.
INTERVAL_S = 60 * 60


class UpdateWatch:
    """Asks *check* now and every *interval_s*; tells *on_checked* each answer."""

    def __init__(self, check: Callable[[], UpdateCheckResult],
                 on_checked: Callable[[UpdateCheckResult], None], *,
                 interval_s: float = INTERVAL_S) -> None:
        log.debug("UpdateWatch.__init__: every %.0f s", interval_s)
        self._check, self._on_checked = check, on_checked
        self._interval_s = interval_s
        self._latest: UpdateCheckResult | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def latest(self) -> UpdateCheckResult | None:
        """The last answer, None before the first -- no network."""
        log.debug("UpdateWatch.latest: %s", self._latest)
        return self._latest

    def start(self) -> None:
        """Check now, then every interval -- once, however often it is called."""
        if self._thread is not None:
            log.debug("UpdateWatch.start: already watching")
            return
        log.info("UpdateWatch.start: checking now, then every %.0f s",
                 self._interval_s)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="trcc-update-watch")
        self._thread.start()

    def stop(self) -> None:
        """Stop watching; an answer in flight is dropped."""
        log.info("UpdateWatch.stop: watching=%s", self._thread is not None)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        log.debug("UpdateWatch._run: started")
        while not self._stop.is_set():
            try:
                result = self._check()
            except Exception:
                log.exception("UpdateWatch: the check raised")
            else:
                if not self._stop.is_set():
                    self._latest = result
                    self._on_checked(result)
            if self._stop.wait(self._interval_s):
                break
        log.debug("UpdateWatch._run: stopped")
