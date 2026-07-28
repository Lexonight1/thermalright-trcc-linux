"""/devices/{key}/led router — set LED colors + animation modes."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ...core.commands import (
    EnableLedTestMode,
    LedSnapshot,
    ListLedModes,
    ListLedStyles,
    RenderLed,
    SelectZone,
    SetClockFormat,
    SetDiskIndex,
    SetLedBrightness,
    SetLedColor,
    SetLedColors,
    SetLedLoadSource,
    SetLedMode,
    SetLedTempSource,
    SetLedZoneBrightness,
    SetLedZoneColor,
    SetLedZoneMode,
    SetLedZoneSync,
    SetLedZoneSyncInterval,
    SetMemoryRatio,
    SetWeekStart,
    ToggleLed,
    ToggleSegment,
)
from ...core.led_models import LEDMode
from ...core.results import (
    ClockFormatResult,
    DiskIndexResult,
    LedColorsResult,
    LedModesListResult,
    LedSnapshotResult,
    LedStylesListResult,
    MemoryRatioResult,
    WeekStartResult,
)
from ._shared import (
    http_error_if_failed,
)
from .schemas import (
    ClockFormatRequest,
    DiskIndexRequest,
    LedBrightnessRequest,
    LedColorRequest,
    LedColorsRequest,
    LedModeRequest,
    LedRenderRequest,
    LedSelectZoneRequest,
    LedSourceRequest,
    LedTestModeRequest,
    LedToggleRequest,
    LedToggleSegmentRequest,
    LedZoneBrightnessRequest,
    LedZoneColorRequest,
    LedZoneModeRequest,
    LedZoneSyncRequest,
    MemoryRatioRequest,
    WeekStartRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/devices/{key}/led", tags=["led"])


@router.post("/colors")
def set_colors(key: str, body: LedColorsRequest,
               request: Request) -> LedColorsResult:
    log.info(
        "api POST /devices/{key}/led/colors: key=%s brightness=%s global_on=%s",
        key, body.brightness, body.global_on,
    )
    result = request.app.state.trcc.dispatch(
        SetLedColors(
            key=key,
            colors=body.colors,
            global_on=body.global_on,
            brightness=body.brightness,
        ),
    )
    http_error_if_failed(result)
    return result


@router.post("/render")
def render(key: str, body: LedRenderRequest,
           request: Request) -> LedColorsResult:
    """One tick — engine reads Settings, advances counters, sends a frame."""
    log.info(
        "api POST /devices/{key}/led/render: key=%s phase=%s",
        key, body.phase,
    )
    result = request.app.state.trcc.dispatch(
        RenderLed(key=key, color=body.color, phase=body.phase),
    )
    http_error_if_failed(result)
    return result


@router.post("/mode")
def set_mode(key: str, body: LedModeRequest,
             request: Request) -> LedColorsResult:
    log.info(
        "api POST /devices/{key}/led/mode: key=%s mode=%s", key, body.mode,
    )
    try:
        mode = LEDMode[body.mode.upper()]
    except KeyError as e:
        raise HTTPException(400, f"Unknown LED mode: {body.mode!r}") from e
    result = request.app.state.trcc.dispatch(SetLedMode(key=key, mode=mode))
    http_error_if_failed(result)
    return result


@router.post("/color")
def set_color(key: str, body: LedColorRequest,
              request: Request) -> LedColorsResult:
    log.info(
        "api POST /devices/{key}/led/color: key=%s color=%s", key, body.color,
    )
    result = request.app.state.trcc.dispatch(
        SetLedColor(key=key, color=body.color),
    )
    http_error_if_failed(result)
    return result


@router.post("/brightness")
def set_brightness(key: str, body: LedBrightnessRequest,
                   request: Request) -> LedColorsResult:
    log.info(
        "api POST /devices/{key}/led/brightness: key=%s percent=%s",
        key, body.percent,
    )
    result = request.app.state.trcc.dispatch(
        SetLedBrightness(key=key, percent=body.percent),
    )
    http_error_if_failed(result)
    return result


@router.post("/test-mode")
def test_mode(key: str, body: LedTestModeRequest,
              request: Request) -> LedColorsResult:
    log.info(
        "api POST /devices/{key}/led/test-mode: key=%s enabled=%s",
        key, body.enabled,
    )
    result = request.app.state.trcc.dispatch(
        EnableLedTestMode(key=key, enabled=body.enabled),
    )
    http_error_if_failed(result)
    return result


@router.post("/temp-source")
def temp_source(key: str, body: LedSourceRequest,
                request: Request) -> LedColorsResult:
    log.info(
        "api POST /devices/{key}/led/temp-source: key=%s source=%s",
        key, body.source,
    )
    result = request.app.state.trcc.dispatch(
        SetLedTempSource(key=key, source=body.source),
    )
    http_error_if_failed(result)
    return result


@router.post("/load-source")
def load_source(key: str, body: LedSourceRequest,
                request: Request) -> LedColorsResult:
    log.info(
        "api POST /devices/{key}/led/load-source: key=%s source=%s",
        key, body.source,
    )
    result = request.app.state.trcc.dispatch(
        SetLedLoadSource(key=key, source=body.source),
    )
    http_error_if_failed(result)
    return result


@router.post("/toggle")
def toggle(key: str, body: LedToggleRequest,
           request: Request) -> LedColorsResult:
    """Turn the LED device (or one zone) on/off."""
    log.info(
        "api POST /devices/{key}/led/toggle: key=%s on=%s zone=%s",
        key, body.on, body.zone,
    )
    result = request.app.state.trcc.dispatch(
        ToggleLed(key=key, on=body.on, zone=body.zone),
    )
    http_error_if_failed(result)
    return result


@router.post("/zone-color")
def zone_color(key: str, body: LedZoneColorRequest,
               request: Request) -> LedColorsResult:
    """Set one zone's persistent color."""
    log.info(
        "api POST /devices/{key}/led/zone-color: key=%s zone=%s color=%s",
        key, body.zone, body.color,
    )
    result = request.app.state.trcc.dispatch(
        SetLedZoneColor(key=key, zone=body.zone, color=body.color),
    )
    http_error_if_failed(result)
    return result


@router.post("/zone-mode")
def zone_mode(key: str, body: LedZoneModeRequest,
              request: Request) -> LedColorsResult:
    """Set one zone's persistent LED mode."""
    log.info(
        "api POST /devices/{key}/led/zone-mode: key=%s zone=%s mode=%s",
        key, body.zone, body.mode,
    )
    try:
        mode = LEDMode[body.mode.upper()]
    except KeyError as e:
        raise HTTPException(400, f"Unknown LED mode: {body.mode!r}") from e
    result = request.app.state.trcc.dispatch(
        SetLedZoneMode(key=key, zone=body.zone, mode=mode),
    )
    http_error_if_failed(result)
    return result


@router.post("/zone-brightness")
def zone_brightness(key: str, body: LedZoneBrightnessRequest,
                    request: Request) -> LedColorsResult:
    """Set one zone's persistent brightness (0-100)."""
    log.info(
        "api POST /devices/{key}/led/zone-brightness: key=%s zone=%s "
        "percent=%s",
        key, body.zone, body.percent,
    )
    result = request.app.state.trcc.dispatch(
        SetLedZoneBrightness(
            key=key, zone=body.zone, percent=body.percent,
        ),
    )
    http_error_if_failed(result)
    return result


@router.post("/zone-sync")
def zone_sync(key: str, body: LedZoneSyncRequest,
              request: Request) -> LedColorsResult:
    """Enable/disable the zone-sync carousel (optionally set interval)."""
    log.info(
        "api POST /devices/{key}/led/zone-sync: key=%s enabled=%s "
        "interval_ticks=%s",
        key, body.enabled, body.interval_ticks,
    )
    trcc = request.app.state.trcc
    result = trcc.dispatch(SetLedZoneSync(key=key, enabled=body.enabled))
    http_error_if_failed(result)
    if body.interval_ticks is not None:
        ir = trcc.dispatch(
            SetLedZoneSyncInterval(key=key, ticks=body.interval_ticks),
        )
        http_error_if_failed(ir)
    return result


@router.post("/select-zone")
def select_zone(key: str, body: LedSelectZoneRequest,
                request: Request) -> LedColorsResult:
    """Pick the currently-active zone."""
    log.info(
        "api POST /devices/{key}/led/select-zone: key=%s zone=%s",
        key, body.zone,
    )
    result = request.app.state.trcc.dispatch(
        SelectZone(key=key, zone=body.zone),
    )
    http_error_if_failed(result)
    return result


@router.post("/toggle-segment")
def toggle_segment(key: str, body: LedToggleSegmentRequest,
                   request: Request) -> LedColorsResult:
    """Flip one segment on/off."""
    log.info(
        "api POST /devices/{key}/led/toggle-segment: key=%s index=%s on=%s",
        key, body.index, body.on,
    )
    result = request.app.state.trcc.dispatch(
        ToggleSegment(key=key, index=body.index, on=body.on),
    )
    http_error_if_failed(result)
    return result


@router.get("/snapshot")
def snapshot(key: str, request: Request) -> LedSnapshotResult:
    """Return the persisted LED state for one device."""
    log.info("api GET /devices/{key}/led/snapshot: key=%s", key)
    result = request.app.state.trcc.dispatch(LedSnapshot(key=key))
    http_error_if_failed(result)
    return result


@router.post("/clock-format")
def clock_format(key: str, body: ClockFormatRequest,
                 request: Request) -> ClockFormatResult:
    """Set the 12h/24h clock display."""
    log.info(
        "api POST /devices/{key}/led/clock-format: key=%s is_24h=%s",
        key, body.is_24h,
    )
    result = request.app.state.trcc.dispatch(
        SetClockFormat(key=key, is_24h=body.is_24h),
    )
    http_error_if_failed(result)
    return result


@router.post("/week-start")
def week_start(key: str, body: WeekStartRequest,
               request: Request) -> WeekStartResult:
    """Pick the week-start day (Sunday-first vs Monday-first)."""
    log.info(
        "api POST /devices/{key}/led/week-start: key=%s sunday_first=%s",
        key, body.sunday_first,
    )
    result = request.app.state.trcc.dispatch(
        SetWeekStart(key=key, sunday_first=body.sunday_first),
    )
    http_error_if_failed(result)
    return result


@router.post("/memory-ratio")
def memory_ratio(key: str, body: MemoryRatioRequest,
                 request: Request) -> MemoryRatioResult:
    """Set the DDR memory multiplier (1, 2, or 4)."""
    log.info(
        "api POST /devices/{key}/led/memory-ratio: key=%s ratio=%s",
        key, body.ratio,
    )
    result = request.app.state.trcc.dispatch(
        SetMemoryRatio(key=key, ratio=body.ratio),
    )
    http_error_if_failed(result)
    return result


@router.post("/disk-index")
def disk_index(key: str, body: DiskIndexRequest,
               request: Request) -> DiskIndexResult:
    """Pick which disk's read/write stats to surface."""
    log.info(
        "api POST /devices/{key}/led/disk-index: key=%s index=%s",
        key, body.index,
    )
    result = request.app.state.trcc.dispatch(
        SetDiskIndex(key=key, index=body.index),
    )
    http_error_if_failed(result)
    return result


# ── Top-level meta routes (no device key in path) ────────────────────


meta_router = APIRouter(prefix="/led", tags=["led"])


@meta_router.get("/styles")
def list_styles(request: Request) -> LedStylesListResult:
    """Enumerate every LED style in the PM byte registry."""
    log.info("api GET /led/styles")
    result = request.app.state.trcc.dispatch(ListLedStyles())
    http_error_if_failed(result)
    return result


@meta_router.get("/modes")
def list_modes(request: Request) -> LedModesListResult:
    """Enumerate animation modes."""
    log.info("api GET /led/modes")
    result = request.app.state.trcc.dispatch(ListLedModes())
    http_error_if_failed(result)
    return result
