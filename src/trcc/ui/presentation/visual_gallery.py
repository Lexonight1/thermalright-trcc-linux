"""The audio visuals gallery, as items -- the same in every window.

``ListVisuals`` answers with the visuals this computer can draw, each
carrying its own preset files.  A gallery shows one tile for a visual that
draws itself (TRCC Pipes) and one per preset of a visual that draws presets
(MilkDrop), in the App's order -- TRCC Pipes first.  Both
windows build their tiles from :func:`gallery_items`, so they cannot list
the visuals differently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePath

from ...core.results import VisualsResult

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VisualItem:
    """One gallery tile: the visual, its preset ("" for none), the name
    under the tile, and why it cannot play here ("" when it can)."""
    visual: str
    preset: str
    name: str
    problem: str = ""


def gallery_items(listed: VisualsResult) -> list[VisualItem]:
    """Every tile *listed* makes, in the App's order."""
    items = [item for info in listed.visuals
             for item in ([VisualItem(info.kind, preset, PurePath(preset).stem,
                                      info.problem)
                           for preset in info.presets]
                          if info.uses_presets
                          else [VisualItem(info.kind, "", info.title,
                                           info.problem)])]
    log.info("gallery_items: %d tile(s) from %d visual(s)", len(items),
             len(listed.visuals))
    return items
