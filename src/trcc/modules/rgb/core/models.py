"""RGB Lighting's own domain: what follows what, the lights, their effects.

Pure data -- the types its Commands, services and pages share.  What the OS
reports about the hardware (``HidInterface``, ``BoardIdentity``, the
memory-access state) and the catalog of controllers stay in TRCC's core: the
Platform answers in them and the udev rule is built from them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from trcc.core.logs import per_frame
from trcc.core.models import HidInterface, LinkedMetric, RgbControllerId, SensorLink

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)


class RgbFollowMode(str, Enum):
    """What follows the LED cooler's colours (#160) -- one at a time.

    ``OPENRGB``: the devices of an OpenRGB SDK server.  ``RAM``: Corsair RGB
    memory, driven directly over the SMBus with no OpenRGB running.
    ``BOARD``: the motherboard's own RGB -- its LEDs and ARGB headers --
    driven directly.  One choice, so TRCC and OpenRGB never drive the same
    lights at once.
    """
    OFF = "off"
    OPENRGB = "openrgb"
    RAM = "ram"
    BOARD = "board"


class FollowMapping(str, Enum):
    """How an LCD's picture lights the devices that follow it.

    ``HALVES``: the panel split into a column per device, left to right; each
    device shows its column top to bottom.  ``SINGLE``: the whole panel as one
    column, top to bottom, the same on every device.
    """
    HALVES = "halves"
    SINGLE = "single"


class FollowColors(str, Enum):
    """How a region of an LCD's picture becomes one light's colour.

    ``VIVID``: its brightest pixels -- a small yellow star in a red sky still
    shows.  ``SMOOTH``: all of it, averaged -- steadier, but small bright
    details drown.  Picked between on the maintainer's sticks, 2026-10-10.
    """
    VIVID = "vivid"
    SMOOTH = "smooth"


@dataclass(frozen=True, slots=True)
class RgbMirrorDevice:
    """One device of another RGB system that follows the cooler (#160).

    ``index`` is the device's place in that system's list -- what every
    later request names it by.  ``led_count`` is how many colours it takes.
    ``ref`` names it across scans, which ``index`` cannot: a rescan renumbers.
    Corsair RAM's is its bus and address, ``i2c-3/0x19``; empty where the
    other system has no stable name.
    """
    index: int
    name: str
    led_count: int
    ref: str = ""


#: The most LEDs an ASUS Aura ARGB header drives -- its config table's 0x78
#: per header (OpenRGB #3693), a limit, not a count.
MAX_ARGB_LEDS = 120


#: An ASUS Aura ARGB header's light ref, by header number from 1 -- made by
#: the driver, named by ``SetAuraLeds``.
AURA_HEADER_REF = "aura/argb{}"


#: The follow source that is TRCC's own effect clock: the motherboard's
#: lights run the built-in effect each was given (``SetAuraEffect``), drawn
#: by TRCC, instead of following a cooler or an LCD.
EFFECT_SOURCE = "effect"


def aura_header(ref: str) -> int | None:
    """The ARGB header number *ref* names, or None for any other light."""
    prefix = AURA_HEADER_REF.format("")
    number = ref.removeprefix(prefix)
    frame_log.debug("aura_header: %s", ref)
    return int(number) if ref.startswith(prefix) and number.isdigit() else None


@dataclass(frozen=True, slots=True)
class FoundBoard:
    """A motherboard RGB controller Find spotted: its catalog row, the HID
    interface it matched, and the name its lights take -- OpenRGB's naming,
    the brand and the board's DMI name ("ASUS PRIME Z790-V AX")."""
    controller: RgbControllerId
    interface: HidInterface
    label: str


class RamEffect(str, Enum):
    """An effect Corsair RGB memory runs by itself, saved on the stick."""
    STATIC = "static"
    COLOR_SHIFT = "color-shift"
    COLOR_PULSE = "color-pulse"
    RAINBOW = "rainbow"
    RAINBOW_WAVE = "rainbow-wave"
    COLOR_WAVE = "color-wave"
    VISOR = "visor"
    RAIN = "rain"
    MARQUEE = "marquee"
    SEQUENTIAL = "sequential"


class EffectSpeed(str, Enum):
    """The three speeds the stick knows."""
    SLOW = "slow"
    MEDIUM = "medium"
    FAST = "fast"


class EffectDirection(str, Enum):
    """Which way an effect moves along the stick."""
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


@dataclass(frozen=True, slots=True)
class EffectTraits:
    """What an effect takes -- the controls a UI shows for it.

    ``colors`` is how many it needs (0-2); ``random`` whether the stick can
    pick them itself; ``directions`` the ways it can move, the first being
    its default.
    """
    speed: bool
    colors: int
    random: bool
    brightness: bool
    directions: tuple[EffectDirection, ...] = ()


_UD = (EffectDirection.DOWN, EffectDirection.UP)


_UDLR = (*_UD, EffectDirection.LEFT, EffectDirection.RIGHT)


#: Each effect's controls, as OpenRGB's ``RGBController_CorsairDRAM.cpp``
#: declares them (credited in ``adapters/rgb/corsair_dram.py``).
EFFECT_TRAITS: dict[RamEffect, EffectTraits] = {
    RamEffect.STATIC: EffectTraits(speed=False, colors=1, random=False,
                                   brightness=False),
    RamEffect.COLOR_SHIFT: EffectTraits(True, 2, True, True),
    RamEffect.COLOR_PULSE: EffectTraits(True, 2, True, True),
    RamEffect.RAINBOW: EffectTraits(True, 0, False, False),
    RamEffect.RAINBOW_WAVE: EffectTraits(True, 0, False, False, _UDLR),
    RamEffect.COLOR_WAVE: EffectTraits(True, 2, True, True, _UDLR),
    RamEffect.VISOR: EffectTraits(
        True, 2, True, True,
        (EffectDirection.VERTICAL, EffectDirection.HORIZONTAL)),
    RamEffect.RAIN: EffectTraits(True, 2, True, True, _UD),
    RamEffect.MARQUEE: EffectTraits(True, 1, False, True),
    RamEffect.SEQUENTIAL: EffectTraits(True, 1, True, True, _UD),
}


@dataclass(frozen=True, slots=True)
class RamEffectSettings:
    """One effect, as the user set it.  ``direction`` None is the effect's
    default; ``colors`` holds as many as its traits ask for."""
    effect: RamEffect
    speed: EffectSpeed = EffectSpeed.MEDIUM
    direction: EffectDirection | None = None
    colors: tuple[tuple[int, int, int], ...] = ()
    random_colors: bool = False
    brightness: int = 255


@dataclass(frozen=True, slots=True)
class LinkedEffect:
    """A light whose colour follows a reading -- *metric* of *source* -- through
    a green-yellow-orange-red scale (``LIGHT_GRADIENTS``), red when hot or busy.
    The LED cooler's Temperature-linked and Load-linked modes, for a light
    TRCC draws (a motherboard header)."""
    metric: LinkedMetric
    source: SensorLink = "cpu"


#: What a light TRCC drives can run: a built-in effect, or a linked colour.
LightEffect = RamEffectSettings | LinkedEffect


class LightKind(str, Enum):
    """What a light is and what drives it.

    ``RAM``: RGB memory, driven by TRCC.  ``OPENRGB``: one of OpenRGB's
    devices.  ``BOARD``: a motherboard's lights TRCC drives (an ASUS Aura
    header).  ``FOUND``: a motherboard controller OpenRGB knows that TRCC
    does not drive -- listed, never written to.
    """
    RAM = "ram"
    OPENRGB = "openrgb"
    BOARD = "board"
    FOUND = "found"


@dataclass(frozen=True, slots=True)
class RgbLight:
    """One light the RGB page lists: a RAM stick, or one of OpenRGB's devices.

    ``ref`` names it across scans (a stick's ``i2c-3/0x19``, an OpenRGB
    device's name).  ``effect`` is the built-in effect TRCC last gave it --
    saved on a RAM stick, run by TRCC on a motherboard light -- what TRCC
    set, not read back.
    ``remembered``: listed from the last Find's record in the settings, not
    looked for since the App started.
    """
    ref: str
    name: str
    led_count: int
    kind: LightKind
    effect: LightEffect | None = None
    remembered: bool = False
