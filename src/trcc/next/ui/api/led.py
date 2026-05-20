"""/devices/{key}/led router — set LED colors + animation modes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...core.commands import (
    EnableLedTestMode,
    RenderLed,
    SetLedBrightness,
    SetLedColor,
    SetLedColors,
    SetLedLoadSource,
    SetLedMode,
    SetLedTempSource,
)
from ...core.led_models import LEDMode
from ._shared import http_error_if_failed, to_led_response
from .schemas import (
    LedBrightnessRequest,
    LedColorRequest,
    LedColorsRequest,
    LedColorsResponse,
    LedModeRequest,
    LedRenderRequest,
    LedSourceRequest,
    LedTestModeRequest,
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
