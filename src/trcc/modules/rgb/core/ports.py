"""RGB Lighting's own ports: the other systems whose lights follow.

Concrete in its ``adapters/``: ``OpenRgbMirror`` (OpenRGB's SDK server),
``CorsairDramMirror`` (Corsair RGB memory over the SMBus) and
``AuraMainboard`` (an ASUS motherboard's own RGB).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

from trcc.modules.rgb.core.models import RamEffectSettings, RgbMirrorDevice

log = logging.getLogger(__name__)


class RgbMirror(ABC):
    """Another RGB system whose devices follow the cooler's colours.

    TRCC leads: the colours a cooler shows are sent to the other system's
    devices, so the whole PC matches.  Concrete: ``OpenRgbMirror``
    (``adapters/openrgb.py``), a client of OpenRGB's SDK server,
    ``CorsairDramMirror`` (``adapters/corsair_dram.py``), Corsair RGB
    memory over the SMBus, and ``AuraMainboard``
    (``adapters/asus_aura.py``), an ASUS motherboard's own RGB.
    Every method may raise ``OSError`` when the other system is unreachable;
    the caller decides when to try again.
    """

    @abstractmethod
    def devices(self) -> tuple[RgbMirrorDevice, ...]:
        """The other system's devices, connecting first if needed."""
        ...

    @abstractmethod
    def show(self, device: RgbMirrorDevice,
             colors: Sequence[tuple[int, int, int]]) -> None:
        """Show *colors* on *device*, stretched to its LED count."""
        ...

    def release(self) -> None:
        """Hand every light TRCC drove back to what its own system ran
        before -- without saving anything.  Called when following stops.
        Nothing to hand back by default: OpenRGB keeps its own state, and
        the RAM shows its last colours until an effect is chosen."""
        log.debug("%s.release: nothing to hand back", type(self).__name__)

    @abstractmethod
    def close(self) -> None:
        """Drop the connection; the next call connects again."""
        ...


class RamLights(RgbMirror):
    """RGB memory TRCC drives itself: it follows, and it keeps effects.

    Concrete: ``CorsairDramMirror`` (``adapters/corsair_dram.py``).  The
    follow half is ``RgbMirror``'s; this adds what only memory TRCC drives
    directly can do.
    """

    @abstractmethod
    def found(self) -> tuple[RgbMirrorDevice, ...] | None:
        """The sticks of the last scan, None before any.  Never touches the
        bus -- what a page shows without asking the hardware anything."""

    @abstractmethod
    def apply_effect(self, device: RgbMirrorDevice,
                     settings: RamEffectSettings) -> None:
        """Save *settings* on *device* as its own effect.

        ``ValueError`` for settings the effect cannot take, before anything
        is written; ``OSError`` when the stick does not take it.
        """
