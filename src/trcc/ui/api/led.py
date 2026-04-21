"""/devices/{key}/led router — set LED colors on RGB LED controllers."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...core.commands import SetLedColors
from ._shared import http_error_if_failed, to_led_response
from .schemas import LedColorsRequest, LedColorsResponse

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
