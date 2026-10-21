"""The audio visuals gallery's tiles, fetched for either window.

Each tile is the App's picture of what a visual draws for noise
(``VisualTile``).  Drawing one takes the App a moment, so they are asked for
one at a time, off the GUI thread (``dispatch_in_background``), and each
arrives at the window's *show* as it comes -- the gallery fills in tile by
tile and never freezes the window.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from trcc.core.commands import Query
from trcc.core.ports import CommandBus
from trcc.ui.qt_background import dispatch_in_background

from ..core.results import VisualTileResult
from .presentation.visual_gallery import VisualItem

log = logging.getLogger(__name__)


class VisualTiles:
    """Asks *bus* for each item's tile, one after another -- the window
    builds the ``VisualTile`` Query (*ask*), at its own size -- and hands
    each picture (PNG bytes) to *show*."""

    def __init__(self, bus: CommandBus,
                 ask: Callable[[VisualItem], Query[VisualTileResult]],
                 show: Callable[[VisualItem, bytes], None]) -> None:
        log.debug("VisualTiles.__init__")
        self._bus, self._ask, self._show = bus, ask, show
        self._waiting: list[VisualItem] = []

    @property
    def waiting(self) -> int:
        """How many tiles have not arrived yet."""
        return len(self._waiting)

    def fetch(self, items: list[VisualItem]) -> None:
        """Ask for *items*' tiles -- in place of any still waiting."""
        log.info("VisualTiles.fetch: %d tile(s)", len(items))
        asking = bool(self._waiting)
        self._waiting = list(items)
        if not asking:
            self._next()

    def _next(self) -> None:
        if not self._waiting:
            log.debug("VisualTiles._next: every tile asked for")
            return
        item = self._waiting[0]
        log.debug("VisualTiles._next: %s", item.name)
        dispatch_in_background(self._bus, self._ask(item), self._on_tile)

    def _on_tile(self, tile: VisualTileResult) -> None:
        item = next((i for i in self._waiting
                     if (i.visual, i.preset) == (tile.visual, tile.preset)),
                    None)
        log.debug("VisualTiles._on_tile: %s %r ok=%s", tile.visual,
                  tile.preset, tile.ok)
        if item is not None:
            self._waiting.remove(item)
            if tile.ok:
                self._show(item, tile.image)
            else:
                log.warning("VisualTiles: %s %r -- %s", tile.visual,
                            tile.preset, tile.message)
        self._next()
