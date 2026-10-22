"""The request bodies of RGB Lighting's API routes."""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from trcc.core.models import (
    LinkedMetric,
    SensorLink,
)
from trcc.modules.rgb.core.models import (
    MAX_ARGB_LEDS,
    EffectDirection,
    EffectSpeed,
    FollowColors,
    FollowMapping,
    RamEffect,
    RgbFollowMode,
)

log = logging.getLogger(__name__)


class RgbFollowRequest(BaseModel):
    """Pick what follows the cooler (#160): off, openrgb, ram or board.

    ``host`` is OpenRGB's, a hostname or IP address only -- no scheme, path
    or port -- so the API cannot point TRCC at an arbitrary URL.  Empty keeps
    the saved one; ``port`` 0 likewise.
    """
    mode: RgbFollowMode
    host: str = Field("", max_length=253, pattern=r"^[A-Za-z0-9.:\-]*$")
    port: int = Field(0, ge=0, le=65535)
    #: The device that leads -- an LCD's key, or "" for the first LED
    #: cooler; None keeps the saved one.
    source: str | None = Field(None, max_length=64,
                               pattern=r"^([0-9a-fA-F]{4}:[0-9a-fA-F]{4}(@[\w.:-]+)?)?$")
    mapping: FollowMapping | None = None
    #: How a region of that picture becomes a light's colour; None keeps
    #: the saved one.
    colors: FollowColors | None = None
    #: The lights that follow (refs); [] for every one; None keeps the saved.
    targets: list[str] | None = Field(None, max_length=64)


class RgbScanRequest(BaseModel):
    """Where to look for OpenRGB -- a hostname or IP address only, as in
    ``RgbFollowRequest``.  Empty keeps the saved address, ``port`` 0 likewise;
    a new one is saved."""
    host: str = Field(default="", max_length=253,
                      pattern=r"^[A-Za-z0-9.:\-]*$")
    port: int = Field(default=0, ge=0, le=65535)


class AuraLedsRequest(BaseModel):
    """How many LEDs ASUS Aura ARGB ``header`` (from 1) drives."""
    header: int = Field(..., ge=1, le=16)
    leds: int = Field(..., ge=0, le=MAX_ARGB_LEDS)


class AuraBlinkRequest(BaseModel):
    """ASUS Aura ARGB ``header`` (from 1) to blink."""
    header: int = Field(..., ge=1, le=16)


class _EffectRequest(BaseModel):
    """One of the built-in effects and its settings."""
    effect: RamEffect
    speed: EffectSpeed = EffectSpeed.MEDIUM
    direction: EffectDirection | None = None
    colors: list[tuple[int, int, int]] = Field(default_factory=list,
                                               max_length=2)
    random_colors: bool = False
    brightness: int = Field(255, ge=0, le=255)


class RamEffectRequest(_EffectRequest):
    """One of the memory's own effects, saved on the sticks named by
    ``sticks`` (refs; [] for every stick)."""
    sticks: list[str] = Field(default_factory=list, max_length=16)


class LinkedColorRequest(BaseModel):
    """The lights named by ``lights`` -- RAM sticks or motherboard lights --
    coloured by ``metric`` (temp or load) of ``source`` (cpu or gpu)."""
    metric: LinkedMetric
    source: SensorLink = "cpu"
    lights: list[str] = Field(..., min_length=1, max_length=16)


class AuraEffectRequest(_EffectRequest):
    """A built-in effect TRCC runs on the motherboard lights named by
    ``lights`` (refs such as ``aura/argb1``; [] for every one)."""
    lights: list[str] = Field(default_factory=list, max_length=16)


class RamLightingRequest(BaseModel):
    """Switch the opt-in RAM-lighting grant on or off.  The App asks for the
    password at the desktop; the API cannot grant the bus by itself."""
    enabled: bool
