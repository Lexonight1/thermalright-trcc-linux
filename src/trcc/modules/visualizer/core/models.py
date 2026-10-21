"""The Audio Visualizer's data: what a visual is, and what it starts from."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from trcc.core.models import STREAM_TICK_S

log = logging.getLogger(__name__)

#: How often an audio visual draws: the media player's pace, the C# player's
#: 62.5 ms step -- 16 fps.  A panel shows no more, so drawing faster only
#: burned CPU (it was 30, "projectM's default": half a core for Pipes).  The
#: reader is paced by the sound itself -- 1/VISUAL_FPS of audio per frame.
VISUAL_FPS = round(1 / STREAM_TICK_S)


@dataclass(frozen=True, slots=True)
class VisualAdjustment:
    """One slider a visual offers; the Audio Visuals page draws it from
    this, so a new visual needs no UI code."""
    name: str
    label: str
    minimum: float
    maximum: float
    default: float


@dataclass(frozen=True, slots=True)
class VisualInfo:
    """What an audio visual is, for the gallery: its registry key, its
    title and category, whether it draws preset files or a centre picture,
    and its sliders."""
    kind: str
    title: str
    category: str
    uses_presets: bool = False
    uses_center: bool = False
    adjustments: tuple[VisualAdjustment, ...] = ()
    #: Why it cannot play on this computer -- "" when it can.  Filled by
    #: ``ListVisuals``; a class's own ``INFO`` leaves it empty.
    problem: str = ""
    #: The preset files THIS visual draws -- one gallery tile each.  Filled
    #: by ``ListVisuals`` like ``problem``.  Each visual carries its own:
    #: they were one flat list, which gave every visual that draws presets
    #: all of them.
    presets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisualSpec:
    """Everything an audio visual needs to start: which one, its preset
    file (if it draws presets), the panel's size, the user's centre picture
    (``None`` when off) and how hot it burns, and the slider values by
    name."""
    kind: str
    width: int
    height: int
    preset: Path | None = None
    center: Path | None = None
    center_fire: float = 0.0
    settings: Mapping[str, float] = field(default_factory=dict)
