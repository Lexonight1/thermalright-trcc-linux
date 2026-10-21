"""What the Audio Visualizer's Commands answer."""
from __future__ import annotations

from dataclasses import dataclass, field

from trcc.core.results import Result

from .models import VisualInfo


@dataclass(frozen=True, slots=True)
class VisualResult(Result):
    """Result of ``PlayVisual`` / ``StopVisual``: the audio visual on a
    panel.  ``preset`` is relative to the visuals' folder; ``playing`` is
    True while one draws the background."""
    key: str = ""
    preset: str = ""
    playing: bool = False


@dataclass(frozen=True, slots=True)
class VisualsResult(Result):
    """``ListVisuals``: every audio visual this machine can draw -- each with
    its own preset files (``VisualInfo.presets``) -- and the user's centre
    picture ("" for none)."""
    visuals: tuple[VisualInfo, ...] = ()
    center: str = ""
    #: Asked for one panel (``ListVisuals(key=...)``): the visual it plays
    #: ("" for none), that visual's preset and its slider values.
    playing: str = ""
    preset: str = ""
    sliders: dict[str, float] = field(default_factory=dict)
    #: Whether that panel's visual hears any sound right now.
    hearing: bool = False
    #: The centre picture switched on, and its fire (0 to 1).
    center_shown: bool = True
    center_fire: float = 0.0


@dataclass(frozen=True, slots=True)
class VisualTileResult(Result):
    """``VisualTile``: a visual's gallery tile, PNG-encoded -- what it draws
    for noise, so the tile is the same whatever is playing."""
    visual: str = ""
    preset: str = ""
    image: bytes = field(default=b"", repr=False)
    media_type: str = ""
