"""RGB Lighting's events -- what every open window follows."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from trcc.core.events import Event
from trcc.core.models import RamAccessState
from trcc.modules.rgb.core.models import RgbFollowMode

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RgbLightsChanged(Event):
    """The RGB lights or their effects changed -- a scan, an effect saved --
    from any UI; every window re-reads ``RgbLights``."""


@dataclass(frozen=True, slots=True)
class RamLightingChanged(Event):
    """RAM lighting was switched on or off, from any UI -- so every window
    shows the new state, not only the one whose button was pressed."""
    state: RamAccessState


@dataclass(frozen=True, slots=True)
class RgbFollowChanged(Event):
    """What follows the cooler changed (#160), from any UI.

    Published by ``SetRgbFollow``; every window shows the choice from the
    ``RgbFollow`` Query on it, so a change from the CLI, the API or the
    other window shows up too.
    """
    mode: RgbFollowMode


@dataclass(frozen=True, slots=True)
class RgbFollowSent(Event):
    """The colours following just sent (#160): light *i* got column *i*
    (wrapping), top to bottom.

    As a screen shows them -- before the LEDs' gamma -- so a window's preview
    looks like the lights.  ``source`` is the device that led: the followed
    LCD's key, or the cooler's.  Published only after a send succeeded, at
    most as often as the follower sends.
    """
    #: Every follow tick -- ~18 a second (measured 2026-10-30).
    LOG_LEVEL: ClassVar[int] = logging.DEBUG
    source: str
    columns: tuple[tuple[tuple[int, int, int], ...], ...] = ()


#: What a page re-reads ``RgbLights`` on -- every change any UI made.
RGB_CHANGES = (RgbLightsChanged, RgbFollowChanged, RamLightingChanged)
