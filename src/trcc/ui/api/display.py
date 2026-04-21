"""/devices/{key}/display router — orientation, brightness, theme."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...core.commands import (
    LoadTheme,
    RenderAndSend,
    SetBrightness,
    SetOrientation,
)
from ._shared import (
    http_error_if_failed,
    to_brightness_response,
    to_orientation_response,
    to_render_response,
    to_theme_response,
)
from .schemas import (
    BrightnessRequest,
    BrightnessResponse,
    OrientationRequest,
    OrientationResponse,
    RenderResponse,
    ThemeRequest,
    ThemeResponse,
)

router = APIRouter(prefix="/devices/{key}/display", tags=["display"])


@router.post("/orientation", response_model=OrientationResponse)
def set_orientation(key: str, body: OrientationRequest,
                    request: Request) -> OrientationResponse:
    result = request.app.state.trcc.dispatch(
        SetOrientation(key=key, degrees=body.degrees),
    )
    http_error_if_failed(result)
    return to_orientation_response(result)


@router.post("/brightness", response_model=BrightnessResponse)
def set_brightness(key: str, body: BrightnessRequest,
                   request: Request) -> BrightnessResponse:
    result = request.app.state.trcc.dispatch(
        SetBrightness(key=key, percent=body.percent),
    )
    http_error_if_failed(result)
    return to_brightness_response(result)


@router.post("/theme", response_model=ThemeResponse)
def load_theme(key: str, body: ThemeRequest,
               request: Request) -> ThemeResponse:
    path = Path(body.path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise HTTPException(400, f"Theme path not a directory: {path}")
    result = request.app.state.trcc.dispatch(
        LoadTheme(key=key, path=path),
    )
    http_error_if_failed(result)
    return to_theme_response(result)


@router.post("/tick", response_model=RenderResponse)
def tick(key: str, request: Request) -> RenderResponse:
    """Render the active theme with live sensors + send one frame.

    Stateless — the caller (scheduled job, cron, client-side timer)
    polls this at AppSettings.refresh_interval_s or whatever cadence
    they like.  Uses the scene cache so ticks are cheap.
    """
    result = request.app.state.trcc.dispatch(RenderAndSend(key=key))
    http_error_if_failed(result)
    return to_render_response(result)
