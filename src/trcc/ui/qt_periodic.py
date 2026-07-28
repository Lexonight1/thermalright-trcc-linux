"""PeriodicUpdater — one restartable QTimer, owned by a widget.

Both Qt skins give their panels a ``start_periodic_updates`` /
``stop_periodic_updates`` pair, and both had written the same restart dance:
create the timer on first use, and on a repeat call stop it, drop the previous
connection, then reconnect at the new cadence.  Getting that wrong leaks a
connection and fires the callback twice per tick.

It is shared by *composition*, not inheritance: ``ui/gui`` and ``ui/qtgui``
have deliberately different ``BasePanel`` designs (an MVC delegate panel vs an
App+bus injected one), so a common base would force a merge neither wants.
A panel owns an updater; its two public methods stay exactly where they were.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


class PeriodicUpdater:
    """A single restartable timer belonging to *owner*.

    Parented to the owning widget, so Qt tears the timer down with the panel
    and a closed panel can't keep ticking.
    """

    __slots__ = ("_owner", "_timer")

    def __init__(self, owner: QWidget) -> None:
        self._owner = owner
        self._timer: QTimer | None = None

    def start(self, interval_ms: int, callback: Callable[[], None]) -> None:
        """Call *callback* every *interval_ms* ms on the Qt main thread.

        Safe to re-call with a new cadence: the previous connection is dropped
        first, so the callback fires once per tick, not once per start().
        """
        log.info(
            "%s.start_periodic_updates: interval_ms=%d callback=%s",
            type(self._owner).__name__, interval_ms,
            getattr(callback, "__qualname__", repr(callback)),
        )
        if self._timer is None:
            self._timer = QTimer(self._owner)
        else:
            self._timer.stop()
            try:
                self._timer.timeout.disconnect()
            except RuntimeError:
                # Nothing was connected — a fresh timer, nothing to undo.
                pass
        self._timer.timeout.connect(callback)
        self._timer.start(interval_ms)

    def stop(self) -> None:
        """Stop ticking.  A no-op if never started."""
        log.info("%s.stop_periodic_updates: active=%s",
                 type(self._owner).__name__, self.is_active)
        if self._timer is not None:
            self._timer.stop()

    @property
    def is_active(self) -> bool:
        """True while the timer is running."""
        return self._timer is not None and self._timer.isActive()
