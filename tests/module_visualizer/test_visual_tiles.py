"""The gallery's tile chain: one tile at a time, and it ends.

A chain that never ends asks the App for tiles in the background for as long
as its page lives -- and one test's tiles logged into another test's capture
(CI, 2026-11-12).  Real Qt signals carry each answer, so these use ``qtbot``.
"""
from __future__ import annotations

import threading
from typing import Any

import shiboken6
from PySide6.QtWidgets import QWidget

from trcc.core.events import EventBus
from trcc.core.ports import CommandBus
from trcc.modules.visualizer.core.commands import VisualTile
from trcc.modules.visualizer.core.results import VisualTileResult
from trcc.modules.visualizer.ui.presentation.visual_gallery import VisualItem
from trcc.modules.visualizer.ui.visual_tiles import VisualTiles

ITEMS = [VisualItem("bars", "", "Bars"), VisualItem("pipes", "", "Pipes"),
         VisualItem("spikes", "", "Spikes")]
OFF = "Audio Visualizer is off -- turn it on in Modules"


class _Bus(CommandBus):
    """Draws every tile, refuses every tile as the App does for an off module
    (``Command.refusal``: no visual named), or is gone.  Can hold an answer."""

    def __init__(self, answer: str = "draw", hold: bool = False) -> None:
        self.answer = answer
        self.asked: list[str] = []
        self.release = threading.Event()
        if not hold:
            self.release.set()

    def dispatch(self, cmd: Any) -> VisualTileResult:
        self.asked.append(cmd.visual)
        self.release.wait(5)
        if self.answer == "gone":
            raise ConnectionError("the App went away")
        if self.answer == "refuse":
            return VisualTileResult(ok=False, message=OFF)
        return VisualTileResult(ok=True, visual=cmd.visual, preset=cmd.preset,
                                image=cmd.visual.encode())

    @property
    def events(self) -> EventBus:
        return EventBus()

    @property
    def remote(self) -> bool:
        return False


class _Page(QWidget):
    """What the gallery page gives the chain: where tiles and refusals go."""

    def __init__(self) -> None:
        super().__init__()
        self.shown: list[str] = []
        self.said: list[str] = []

    def show_tile(self, item: VisualItem, png: bytes) -> None:
        self.shown.append(png.decode())

    def say(self, message: str) -> None:
        self.said.append(message)


def _ask(item: VisualItem) -> VisualTile:
    return VisualTile(visual=item.visual, preset=item.preset)


def _tiles(bus: _Bus, page: _Page) -> VisualTiles:
    return VisualTiles(bus, _ask, page.show_tile, page.say)


def test_every_tile_arrives_one_at_a_time_in_order(qtbot: Any) -> None:
    bus, page = _Bus(), _Page()
    qtbot.addWidget(page)
    tiles = _tiles(bus, page)    # the page holds it: answers come by weak ref
    tiles.fetch(ITEMS)
    qtbot.waitUntil(lambda: len(page.shown) == 3, timeout=3000)
    assert (page.shown, bus.asked, page.said) == (
        ["bars", "pipes", "spikes"], ["bars", "pipes", "spikes"], [])


def test_a_refusal_for_every_tile_asks_once_and_says_why(qtbot: Any) -> None:
    """The module off: the refusal names no visual, so it matched no waiting
    tile and the first one was asked for again, forever.

    MUTATION CHECK: drop the refusal branch in VisualTiles._on_tile -> the
    first tile is asked for over and over."""
    bus, page = _Bus("refuse"), _Page()
    qtbot.addWidget(page)
    tiles = _tiles(bus, page)    # the page holds it: answers come by weak ref
    tiles.fetch(ITEMS)
    qtbot.waitUntil(lambda: bool(page.said), timeout=3000)
    qtbot.wait(300)                         # long enough for a loop to show
    assert (bus.asked, page.said, page.shown) == (["bars"], [OFF], [])


def test_an_app_gone_is_a_refusal_too(qtbot: Any) -> None:
    """MUTATION CHECK: drop ``failed=_no_tile`` -> a plain Result reaches
    ``_on_tile``, which has no ``visual``: AttributeError on the GUI thread."""
    bus, page = _Bus("gone"), _Page()
    qtbot.addWidget(page)
    tiles = _tiles(bus, page)    # the page holds it: answers come by weak ref
    tiles.fetch(ITEMS)
    qtbot.waitUntil(lambda: bool(page.said), timeout=3000)
    qtbot.wait(300)
    assert bus.asked == ["bars"]
    assert page.said == ["Could not reach TRCC: the App went away"]


def test_a_page_deleted_mid_chain_is_not_drawn_into_and_stops_it(qtbot: Any) -> None:
    """Qt deletes the page while Python still holds it -- a window closed with
    its gallery: the tile in flight must not draw into it, nor ask for more.

    MUTATION CHECK: drop the ``still_there`` check in VisualTiles._on_tile ->
    RuntimeError drawing into the deleted page, and the chain asks on."""
    bus, page = _Bus(hold=True), _Page()
    tiles = _tiles(bus, page)
    tiles.fetch(ITEMS)
    qtbot.waitUntil(lambda: bus.asked == ["bars"], timeout=3000)
    shiboken6.delete(page)
    bus.release.set()
    qtbot.wait(300)
    assert bus.asked == ["bars"]
    assert tiles._waiting == []
