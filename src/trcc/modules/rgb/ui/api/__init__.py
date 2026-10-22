"""RGB Lighting's API routes -- the same paths they always had, under
``/system``.

Added for every installed module, on or off; while it is off each answers
that it is off and where to turn it on.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from trcc.modules.rgb.core.commands import (
    BlinkAuraHeader,
    RamLighting,
    RgbFollow,
    RgbLights,
    ScanRgbLights,
    SetAuraEffect,
    SetAuraLeds,
    SetLinkedColor,
    SetRamEffect,
    SetRamLighting,
    SetRgbFollow,
)
from trcc.modules.rgb.core.results import RamLightingResult, RgbFollowResult, RgbLightsResult
from trcc.modules.rgb.ui.api.schemas import (
    AuraBlinkRequest,
    AuraEffectRequest,
    AuraLedsRequest,
    LinkedColorRequest,
    RamEffectRequest,
    RamLightingRequest,
    RgbFollowRequest,
    RgbScanRequest,
)
from trcc.ui.api._shared import http_error_if_failed

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/rgb-follow")
def rgb_follow_status(request: Request) -> RgbFollowResult:
    """What follows the cooler's colours, and what it is doing (#160)."""
    log.info("api GET /system/rgb-follow")
    result = request.app.state.trcc.dispatch(RgbFollow())
    http_error_if_failed(result)
    return result


@router.post("/rgb-follow")
def rgb_follow_set(body: RgbFollowRequest,
                   request: Request) -> RgbFollowResult:
    """Pick what follows the cooler's colours: off, openrgb, ram or board (#160)."""
    log.info("api POST /system/rgb-follow: mode=%s host=%r port=%s "
             "mapping=%s colors=%s", body.mode.value, body.host, body.port,
             body.mapping, body.colors)
    result = request.app.state.trcc.dispatch(SetRgbFollow(
        mode=body.mode, host=body.host, port=body.port, source=body.source,
        mapping=body.mapping, colors=body.colors,
        targets=None if body.targets is None else tuple(body.targets)))
    http_error_if_failed(result)
    return result


@router.get("/rgb")
def rgb_lights(request: Request) -> RgbLightsResult:
    """The RGB lights from the last scan -- no bus traffic."""
    log.info("api GET /system/rgb")
    result = request.app.state.trcc.dispatch(RgbLights())
    http_error_if_failed(result)
    return result


@router.post("/rgb/scan")
def rgb_scan(request: Request,
             body: RgbScanRequest | None = None) -> RgbLightsResult:
    """Look for the lights: RGB memory, and OpenRGB's devices."""
    scan = body or RgbScanRequest()
    log.info("api POST /system/rgb/scan: openrgb %s:%d", scan.host or "(saved)",
             scan.port)
    result = request.app.state.trcc.dispatch(
        ScanRgbLights(host=scan.host, port=scan.port))
    http_error_if_failed(result)
    return result


@router.post("/aura-leds")
def aura_leds_set(body: AuraLedsRequest, request: Request) -> RgbLightsResult:
    """Say how many LEDs an ASUS Aura ARGB header drives."""
    log.info("api POST /system/aura-leds: header %d -> %d", body.header,
             body.leds)
    result = request.app.state.trcc.dispatch(SetAuraLeds(header=body.header,
                                                         leds=body.leds))
    http_error_if_failed(result)
    return result


@router.post("/aura-blink")
def aura_blink(body: AuraBlinkRequest, request: Request) -> RgbLightsResult:
    """Blink an ASUS Aura ARGB header for two seconds, to see which it is."""
    log.info("api POST /system/aura-blink: header %d", body.header)
    result = request.app.state.trcc.dispatch(BlinkAuraHeader(header=body.header))
    http_error_if_failed(result)
    return result


@router.post("/aura-effect")
def aura_effect_set(body: AuraEffectRequest, request: Request) -> RgbLightsResult:
    """Run a built-in effect on the motherboard's lights, drawn by TRCC."""
    log.info("api POST /system/aura-effect: %s lights=%s", body.effect.value,
             body.lights)
    result = request.app.state.trcc.dispatch(SetAuraEffect(
        effect=body.effect, refs=tuple(body.lights), speed=body.speed,
        direction=body.direction, colors=tuple(body.colors),
        random_colors=body.random_colors, brightness=body.brightness))
    http_error_if_failed(result)
    return result


@router.post("/linked-color")
def linked_color_set(body: LinkedColorRequest, request: Request) -> RgbLightsResult:
    """Colour RAM sticks or the motherboard's lights by the CPU's or GPU's
    temperature or load."""
    log.info("api POST /system/linked-color: %s %s lights=%s", body.metric,
             body.source, body.lights)
    result = request.app.state.trcc.dispatch(SetLinkedColor(
        metric=body.metric, refs=tuple(body.lights), source=body.source))
    http_error_if_failed(result)
    return result


@router.post("/ram-effect")
def ram_effect_set(body: RamEffectRequest, request: Request) -> RgbLightsResult:
    """Save one of the memory's own effects on the sticks."""
    log.info("api POST /system/ram-effect: %s sticks=%s", body.effect.value,
             body.sticks)
    result = request.app.state.trcc.dispatch(SetRamEffect(
        effect=body.effect, refs=tuple(body.sticks), speed=body.speed,
        direction=body.direction, colors=tuple(body.colors),
        random_colors=body.random_colors, brightness=body.brightness))
    http_error_if_failed(result)
    return result


@router.get("/ram-lighting")
def ram_lighting_status(request: Request) -> RamLightingResult:
    """Whether TRCC may reach RGB memory -- read from files, no bus traffic."""
    log.info("api GET /system/ram-lighting")
    result = request.app.state.trcc.dispatch(RamLighting())
    http_error_if_failed(result)
    return result


@router.post("/ram-lighting")
def ram_lighting_set(body: RamLightingRequest,
                     request: Request) -> RamLightingResult:
    """Switch RAM lighting on or off: a password prompt at the desktop,
    answered when it is typed or closed."""
    log.info("api POST /system/ram-lighting: enabled=%s", body.enabled)
    result = request.app.state.trcc.dispatch(
        SetRamLighting(enabled=body.enabled))
    http_error_if_failed(result)
    return result


#: What the API adds (``ui/api/``'s fixed name).
ROUTERS = (router,)
