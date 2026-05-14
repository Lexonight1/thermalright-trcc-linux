"""LED RGB control endpoints — color, mode, zones, segments.

All endpoints route through Trcc.led (universal command layer).
Same surface CLI + GUI use — response shape is `asdict(LEDResult)` /
`asdict(OpResult)` so clients see the same fields across every UI.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from trcc._boot import trcc
from trcc.ui.api.models import (
    ClockFormatRequest,
    HexColorRequest,
    LEDBrightnessRequest,
    LEDSensorRequest,
    ModeRequest,
    TempUnitRequest,
    ToggleRequest,
    ZoneSyncRequest,
    parse_hex_or_400,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/led", tags=["led"])


def _result(result) -> dict:
    """Return asdict(result) or raise HTTPException on failure."""
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error or 'failed')
    return asdict(result)


# ── Global operations ──────────────────────────────────────────────────

@router.post("/color")
def set_color(body: HexColorRequest, led: int = 0) -> dict:
    """Set LED static color."""
    r, g, b = parse_hex_or_400(body.hex)
    return _result(trcc().led.set_color(led, r, g, b))


@router.post("/mode")
def set_mode(body: ModeRequest, led: int = 0) -> dict:
    """Set LED effect mode (static, breathing, colorful, rainbow, temp_linked, load_linked).

    Animated modes drive the device from the per-process metrics loop
    started by `Trcc.discover()`; no explicit start needed here.
    """
    result = trcc().led.set_mode(led, body.mode)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return asdict(result)


@router.post("/brightness")
def set_brightness(body: LEDBrightnessRequest, led: int = 0) -> dict:
    """Set LED brightness (0-100)."""
    return _result(trcc().led.set_brightness(led, body.level))


@router.post("/off")
def turn_off(led: int = 0) -> dict:
    """Turn LEDs off."""
    return _result(trcc().led.toggle(led, False))


@router.post("/sensor")
def set_sensor(body: LEDSensorRequest, led: int = 0) -> dict:
    """Set CPU/GPU sensor source for temp/load linked modes."""
    return _result(trcc().led.set_sensor_source(led, body.source))


# ── Zone operations ────────────────────────────────────────────────────

@router.post("/zones/{zone}/color")
def set_zone_color(zone: int, body: HexColorRequest, led: int = 0) -> dict:
    """Set color for a specific LED zone."""
    r, g, b = parse_hex_or_400(body.hex)
    return _result(trcc().led.set_color(led, r, g, b, zone=zone))


@router.post("/zones/{zone}/mode")
def set_zone_mode(zone: int, body: ModeRequest, led: int = 0) -> dict:
    """Set effect mode for a specific LED zone."""
    return _result(trcc().led.set_mode(led, body.mode, zone=zone))


@router.post("/zones/{zone}/brightness")
def set_zone_brightness(zone: int, body: LEDBrightnessRequest, led: int = 0) -> dict:
    """Set brightness for a specific LED zone (0-100)."""
    return _result(trcc().led.set_brightness(led, body.level, zone=zone))


@router.post("/zones/{zone}/toggle")
def toggle_zone(zone: int, body: ToggleRequest, led: int = 0) -> dict:
    """Toggle a specific LED zone on/off."""
    return _result(trcc().led.toggle(led, body.on, zone=zone))


@router.post("/sync")
def set_sync(body: ZoneSyncRequest, led: int = 0) -> dict:
    """Enable/disable zone sync (circulate/select-all)."""
    return _result(
        trcc().led.set_zone_sync(led, body.enabled, interval_s=body.interval),
    )


# ── Segment operations ─────────────────────────────────────────────────

@router.post("/segments/{index}/toggle")
def toggle_segment(index: int, body: ToggleRequest, led: int = 0) -> dict:
    """Toggle a specific LED segment on/off."""
    return _result(trcc().led.toggle_segment(led, index, body.on))


@router.post("/clock")
def set_clock(body: ClockFormatRequest, led: int = 0) -> dict:
    """Set LED segment display clock format (12h/24h)."""
    return _result(trcc().led.set_clock_format(led, body.is_24h))


@router.post("/temp-unit")
def set_temp_unit(body: TempUnitRequest) -> dict:
    """Set app-wide temperature unit (affects LED segments + LCD overlay)."""
    unit_str = 'F' if body.unit else 'C'
    return _result(trcc().control_center.set_temp_unit(unit_str))


# ── Snapshot ──────────────────────────────────────────────────────────

@router.get("/snapshot")
def led_snapshot(led: int = 0) -> dict:
    """Return the LED device's full state snapshot."""
    return asdict(trcc().led.snapshot(led))


@router.get("/styles")
def list_led_styles() -> list[dict]:
    """List all supported LED device styles and their capabilities."""
    return [asdict(s) for s in trcc().led.list_styles()]


# ── Status (backward-compat) ──────────────────────────────────────────

@router.get("/status")
def led_status(led: int = 0) -> dict:
    """Get current LED state — connected, style id."""
    snap = trcc().led.snapshot(led)
    return {
        "connected": snap.connected,
        "style_id": snap.style_id,
    }
