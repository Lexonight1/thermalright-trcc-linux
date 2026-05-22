"""/devices/{key}/led router — set LED colors + animation modes."""
from __future__ import annotations

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
    SetLedZoneColor,
    SetLedZoneSync,
    SetLedZoneSyncInterval,
    SetMemoryRatio,
    SetWeekStart,
    ToggleLed,
    ToggleSegment,
)
from ...core.led_models import LEDMode
from ._shared import (
    http_error_if_failed,
    to_clock_format_response,
    to_disk_index_response,
    to_led_modes_list_response,
    to_led_response,
    to_led_snapshot_response,
    to_led_styles_list_response,
    to_memory_ratio_response,
    to_week_start_response,
)
from .schemas import (
    ClockFormatRequest,
    ClockFormatResponse,
    DiskIndexRequest,
    DiskIndexResponse,
    LedBrightnessRequest,
    LedColorRequest,
    LedColorsRequest,
    LedColorsResponse,
    LedModeRequest,
    LedModesListResponse,
    LedRenderRequest,
    LedSelectZoneRequest,
    LedSnapshotResponse,
    LedSourceRequest,
    LedStylesListResponse,
    LedTestModeRequest,
    LedToggleRequest,
    LedToggleSegmentRequest,
    LedZoneColorRequest,
    LedZoneSyncRequest,
    MemoryRatioRequest,
    MemoryRatioResponse,
    WeekStartRequest,
    WeekStartResponse,
)

router = APIRouter(prefix="/devices/{key}/led", tags=["led"])


@router.post("/colors", response_model=LedColorsResponse)
def set_colors(key: str, body: LedColorsRequest,
               request: Request) -> LedColorsResponse:
    result = request.app.state.trcc.dispatch(
        SetLedColors(
            key=key,
            colors=body.colors,
            global_on=body.global_on,
            brightness=body.brightness,
        ),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/render", response_model=LedColorsResponse)
def render(key: str, body: LedRenderRequest,
           request: Request) -> LedColorsResponse:
    """One tick — engine reads Settings, advances counters, sends a frame."""
    result = request.app.state.trcc.dispatch(
        RenderLed(key=key, color=body.color, phase=body.phase),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/mode", response_model=LedColorsResponse)
def set_mode(key: str, body: LedModeRequest,
             request: Request) -> LedColorsResponse:
    try:
        mode = LEDMode[body.mode.upper()]
    except KeyError as e:
        raise HTTPException(400, f"Unknown LED mode: {body.mode!r}") from e
    result = request.app.state.trcc.dispatch(SetLedMode(key=key, mode=mode))
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/color", response_model=LedColorsResponse)
def set_color(key: str, body: LedColorRequest,
              request: Request) -> LedColorsResponse:
    result = request.app.state.trcc.dispatch(
        SetLedColor(key=key, color=body.color),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/brightness", response_model=LedColorsResponse)
def set_brightness(key: str, body: LedBrightnessRequest,
                   request: Request) -> LedColorsResponse:
    result = request.app.state.trcc.dispatch(
        SetLedBrightness(key=key, percent=body.percent),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/test-mode", response_model=LedColorsResponse)
def test_mode(key: str, body: LedTestModeRequest,
              request: Request) -> LedColorsResponse:
    result = request.app.state.trcc.dispatch(
        EnableLedTestMode(key=key, enabled=body.enabled),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/temp-source", response_model=LedColorsResponse)
def temp_source(key: str, body: LedSourceRequest,
                request: Request) -> LedColorsResponse:
    result = request.app.state.trcc.dispatch(
        SetLedTempSource(key=key, source=body.source),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/load-source", response_model=LedColorsResponse)
def load_source(key: str, body: LedSourceRequest,
                request: Request) -> LedColorsResponse:
    result = request.app.state.trcc.dispatch(
        SetLedLoadSource(key=key, source=body.source),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/toggle", response_model=LedColorsResponse)
def toggle(key: str, body: LedToggleRequest,
           request: Request) -> LedColorsResponse:
    """Turn the LED device (or one zone) on/off."""
    result = request.app.state.trcc.dispatch(
        ToggleLed(key=key, on=body.on, zone=body.zone),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/zone-color", response_model=LedColorsResponse)
def zone_color(key: str, body: LedZoneColorRequest,
               request: Request) -> LedColorsResponse:
    """Set one zone's persistent color."""
    result = request.app.state.trcc.dispatch(
        SetLedZoneColor(key=key, zone=body.zone, color=body.color),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/zone-sync", response_model=LedColorsResponse)
def zone_sync(key: str, body: LedZoneSyncRequest,
              request: Request) -> LedColorsResponse:
    """Enable/disable the zone-sync carousel (optionally set interval)."""
    trcc = request.app.state.trcc
    result = trcc.dispatch(SetLedZoneSync(key=key, enabled=body.enabled))
    http_error_if_failed(result)
    if body.interval_ticks is not None:
        ir = trcc.dispatch(
            SetLedZoneSyncInterval(key=key, ticks=body.interval_ticks),
        )
        http_error_if_failed(ir)
    return to_led_response(result)


@router.post("/select-zone", response_model=LedColorsResponse)
def select_zone(key: str, body: LedSelectZoneRequest,
                request: Request) -> LedColorsResponse:
    """Pick the currently-active zone."""
    result = request.app.state.trcc.dispatch(
        SelectZone(key=key, zone=body.zone),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.post("/toggle-segment", response_model=LedColorsResponse)
def toggle_segment(key: str, body: LedToggleSegmentRequest,
                   request: Request) -> LedColorsResponse:
    """Flip one segment on/off."""
    result = request.app.state.trcc.dispatch(
        ToggleSegment(key=key, index=body.index, on=body.on),
    )
    http_error_if_failed(result)
    return to_led_response(result)


@router.get("/snapshot", response_model=LedSnapshotResponse)
def snapshot(key: str, request: Request) -> LedSnapshotResponse:
    """Return the persisted LED state for one device."""
    result = request.app.state.trcc.dispatch(LedSnapshot(key=key))
    http_error_if_failed(result)
    return to_led_snapshot_response(result)


@router.post("/clock-format", response_model=ClockFormatResponse)
def clock_format(key: str, body: ClockFormatRequest,
                 request: Request) -> ClockFormatResponse:
    """Set the 12h/24h clock display."""
    result = request.app.state.trcc.dispatch(
        SetClockFormat(key=key, is_24h=body.is_24h),
    )
    http_error_if_failed(result)
    return to_clock_format_response(result)


@router.post("/week-start", response_model=WeekStartResponse)
def week_start(key: str, body: WeekStartRequest,
               request: Request) -> WeekStartResponse:
    """Pick the week-start day (Sunday-first vs Monday-first)."""
    result = request.app.state.trcc.dispatch(
        SetWeekStart(key=key, sunday_first=body.sunday_first),
    )
    http_error_if_failed(result)
    return to_week_start_response(result)


@router.post("/memory-ratio", response_model=MemoryRatioResponse)
def memory_ratio(key: str, body: MemoryRatioRequest,
                 request: Request) -> MemoryRatioResponse:
    """Memory display mode: ratio (%) or absolute (GB)."""
    result = request.app.state.trcc.dispatch(
        SetMemoryRatio(key=key, ratio_mode=body.ratio_mode),
    )
    http_error_if_failed(result)
    return to_memory_ratio_response(result)


@router.post("/disk-index", response_model=DiskIndexResponse)
def disk_index(key: str, body: DiskIndexRequest,
               request: Request) -> DiskIndexResponse:
    """Pick which disk's read/write stats to surface."""
    result = request.app.state.trcc.dispatch(
        SetDiskIndex(key=key, index=body.index),
    )
    http_error_if_failed(result)
    return to_disk_index_response(result)


# ── Top-level meta routes (no device key in path) ────────────────────


meta_router = APIRouter(prefix="/led", tags=["led"])


@meta_router.get("/styles", response_model=LedStylesListResponse)
def list_styles(request: Request) -> LedStylesListResponse:
    """Enumerate every LED style in the PM byte registry."""
    result = request.app.state.trcc.dispatch(ListLedStyles())
    http_error_if_failed(result)
    return to_led_styles_list_response(result)


@meta_router.get("/modes", response_model=LedModesListResponse)
def list_modes(request: Request) -> LedModesListResponse:
    """Enumerate animation modes."""
    result = request.app.state.trcc.dispatch(ListLedModes())
    http_error_if_failed(result)
    return to_led_modes_list_response(result)
