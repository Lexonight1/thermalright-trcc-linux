"""Periodic updates that run only while a widget is on screen -- for every Qt
window that keeps a widget alive while it is hidden (qtgui's stacked panels,
hudgui's preview under a dropped-down panel).

Moved here from ``ui/qtgui/base.py`` (2026-11-05) when hudgui needed the same
preview: one implementation, two skins.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import QWidget

from .qt_periodic import PeriodicUpdater

log = logging.getLogger(__name__)


# Typed as a QWidget for the checker, a plain mixin at runtime: both users
# (PreviewSurface, BasePanel) are QWidgets, but a mixin that really
# subclassed QWidget gave them two Qt base paths and PySide segfaulted
# (measured 2026-10-20, test_gui_panels).
if TYPE_CHECKING:
    _Shown = QWidget
else:
    _Shown = object


class TicksWhileShown(_Shown):
    """Periodic updates that run only while the widget is on screen.

    qtgui stacks its panels (``app.py:102``), so all but one are hidden at
    any moment, and the window itself hides to the tray on close.  A timer
    that kept going there dispatched a full ``BuildPreview`` every second for
    nobody: measured 2026-09-30 on the preview, 3 renders in 3 s after the
    window closed, the same as while shown.

    This lives on the WIDGET, not on ``PeriodicUpdater``, which the gui skin
    shares without show/hide hooks.  No window-owned updater drives the
    device (the core's loops do, #249), so pausing one never freezes a panel.
    A widget that is not on screen when it starts waits for its first show.
    """

    _updates: PeriodicUpdater

    if TYPE_CHECKING:
        # Declared for the type checker only: defined for real it would come
        # FIRST in the MRO and shadow the QWidget's own isVisible.
        def isVisible(self) -> bool: ...

    def _start_updates(self, interval_ms: int,
                       callback: Callable[[], None]) -> None:
        self._updates.start(interval_ms, callback)
        if not self.isVisible():
            log.debug("_start_updates: %s not on screen yet — waiting",
                      type(self).__name__)
            self._updates.suspend()

    def showEvent(self, event: QShowEvent) -> None:
        log.debug("showEvent: %s back on screen", type(self).__name__)
        super().showEvent(event)
        self._updates.resume()

    def hideEvent(self, event: QHideEvent) -> None:
        log.debug("hideEvent: %s left the screen", type(self).__name__)
        super().hideEvent(event)
        self._updates.suspend()
