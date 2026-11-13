"""The audio visuals gallery's tiles, fetched for either window.

Each tile is the App's picture of what a visual draws for noise
(``VisualTile``).  Drawing one takes the App a moment, so they are asked for
one at a time, off the GUI thread (``dispatch_in_background``), and each
arrives at the window's *show* as it comes -- the gallery fills in tile by
tile and never freezes the window.

The chain ends three ways besides running out of items: the window is gone
(its tiles would draw into nothing), or the App refuses tiles altogether --
the module off, the App gone -- when asking for the next one would get the
same answer, forever.  It did: the refusal names no visual, so it matched no
waiting item and the same first tile was asked for again and again while the
page lived, which is how one test's tiles logged into another's (2026-11-12).
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from trcc.core.commands import Query
from trcc.core.ports import CommandBus
from trcc.ui.qt_background import dispatch_in_background, still_there

from ..core.results import VisualTileResult
from .presentation.visual_gallery import VisualItem

log = logging.getLogger(__name__)


def _no_tile(message: str) -> VisualTileResult:
    """The answer when the App could not be asked -- a ``VisualTileResult``,
    read like any other refusal (a plain ``Result`` has no ``visual``)."""
    log.debug("_no_tile: %s", message)
    return VisualTileResult(ok=False, message=message)


class VisualTiles:
    """Asks *bus* for each item's tile, one after another -- the window
    builds the ``VisualTile`` Query (*ask*), at its own size -- and hands
    each picture (PNG bytes) to *show*.  When the App refuses every tile,
    its reason goes to *refused*, once."""

    def __init__(self, bus: CommandBus,
                 ask: Callable[[VisualItem], Query[VisualTileResult]],
                 show: Callable[[VisualItem, bytes], None],
                 refused: Callable[[str], None]) -> None:
        log.debug("VisualTiles.__init__")
        self._bus, self._ask, self._show, self._refused = bus, ask, show, refused
        self._waiting: list[VisualItem] = []

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
        dispatch_in_background(self._bus, self._ask(item), self._on_tile,
                               failed=_no_tile)

    def _on_tile(self, tile: VisualTileResult) -> None:
        if not still_there(self._show):
            # Before showing: a tile drawn into a deleted page raises.
            log.info("VisualTiles._on_tile: the window is gone -- %d tile(s) "
                     "not asked for", len(self._waiting))
            self._waiting.clear()
            return
        item = next((i for i in self._waiting
                     if (i.visual, i.preset) == (tile.visual, tile.preset)),
                    None)
        log.debug("VisualTiles._on_tile: %s %r ok=%s", tile.visual,
                  tile.preset, tile.ok)
        if item is None and not tile.ok and not tile.visual:
            # Refused before any visual was drawn: the next tile would get it too.
            log.warning("VisualTiles: no tiles -- %s (%d not asked for)",
                        tile.message, len(self._waiting))
            self._waiting.clear()
            self._refused(tile.message)
            return
        if item is not None:
            self._waiting.remove(item)
            if tile.ok:
                self._show(item, tile.image)
            else:
                log.warning("VisualTiles: %s %r -- %s", tile.visual,
                            tile.preset, tile.message)
        self._next()
