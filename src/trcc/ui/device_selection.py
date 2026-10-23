"""DeviceSelection — the ONE device a window is working on, for both windows.

A current value with a change signal: a part built late reads :attr:`key`
when it is built and follows :attr:`changed` after, so it cannot miss the
moment the device was chosen.  A value pushed at chosen moments can be missed,
and in the gui it was (2026-10-22): a module tab added while the Modules page
showed never learned the panel, and the cache that skipped the push on the way
back left it dropping every click.

**Why this exists.**  qtgui gave every panel its own
:class:`~trcc.ui.qtgui.device_picker.DevicePickerWidget` — ten of them — and
nothing kept them in sync.  ``DevicePickerWidget.set_key`` was written for
exactly that sync ("programmatic set_key calls don't emit, so panels can sync
state without thrashing") and had **zero production callers**.  Driven on a
two-device fleet: pick the second device in Preview and the overlay editor
still edits the first, silently, so every element added lands on the wrong
screen.  One device hides it completely, because each picker independently
defaults to index 0 and they agree by accident.

**Why it is view state and not a Setting.**  Which device a *window* is
currently editing is not app state — it is not persisted, it is not shared
with the CLI, and a second window would legitimately want its own.  ``ui/gui``
holds one for its LCDs (``TRCCApp``); there is deliberately no
``selected_device`` anywhere in ``Settings`` or the Command surface.

**Why one per KIND.**  An LCD workspace and an LED workspace are different
contexts — ``ui/gui`` makes them different top-level views — so selecting an
LCD must not blank the LED panel.  qtgui's MainWindow owns one of these per
kind and hands each panel the one matching its filter.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class DeviceSelection(QObject):
    """A single device key, shared by every panel that edits that kind."""

    #: Emitted only on a real transition, so a re-select is not a broadcast.
    changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        log.debug("DeviceSelection.__init__: parent=%s", parent)
        super().__init__(parent)
        self._key = ""

    @property
    def key(self) -> str:
        """The device every panel sharing this selection is editing."""
        log.debug("DeviceSelection.key -> %r", self._key)
        return self._key

    def set_key(self, key: str) -> None:
        """Adopt *key*, announcing it once if it actually changed."""
        new = (key or "").strip()
        if new == self._key:
            log.debug("DeviceSelection.set_key: %r unchanged — no broadcast",
                      new)
            return
        log.info("DeviceSelection.set_key: %r -> %r", self._key, new)
        self._key = new
        self.changed.emit(new)
