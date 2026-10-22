"""What RGB Lighting's Commands answer."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from trcc.core.models import RamAccessState
from trcc.core.results import Result
from trcc.modules.rgb.core.models import FollowColors, FollowMapping, RgbFollowMode, RgbLight

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RgbLightsResult(Result):
    """The lights the RGB page lists, from the last scan -- no bus traffic.

    ``scanned`` is whether the RAM has been looked for since the App started;
    ``openrgb_error`` why OpenRGB's devices could not be listed, if they
    could not.
    """
    lights: tuple[RgbLight, ...] = ()
    scanned: bool = False
    openrgb_error: str = ""
    ram_access: RamAccessState = RamAccessState.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class RamLightingResult(Result):
    """Whether TRCC may reach RGB memory, and how to change it from here.

    ``command`` is empty when this App can switch it itself (a password
    prompt); otherwise it is the terminal command that will.
    """
    state: RamAccessState = RamAccessState.UNSUPPORTED
    command: str = ""


@dataclass(frozen=True, slots=True)
class RgbFollowResult(Result):
    """What follows the cooler (#160): the setting and what it is doing.

    ``mode`` is what follows -- OpenRGB at ``host``:``port``, or Corsair RAM
    directly.  ``devices`` are those that follow; ``lead`` the cooler they
    follow; ``error`` why the follower could not be reached, if it could not.
    """
    mode: RgbFollowMode = RgbFollowMode.OFF
    host: str = ""
    port: int = 0
    #: The device that leads -- empty for the first LED cooler -- how an
    #: LCD's picture maps onto the followers, and which lights follow (refs;
    #: empty for every one).
    source: str = ""
    mapping: FollowMapping = FollowMapping.HALVES
    targets: tuple[str, ...] = ()
    #: How a region of that picture becomes a light's colour.
    colors: FollowColors = FollowColors.VIVID
    connected: bool = False
    devices: tuple[str, ...] = ()
    lead: str = ""
    error: str = ""
