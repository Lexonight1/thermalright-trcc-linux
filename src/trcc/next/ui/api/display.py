"""/devices/{key}/display router — orientation, brightness, theme."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...core.commands import (
    EnableOverlay,
    LoadTheme,
    RenderAndSend,
    SendColor,
    SetBrightness,
    SetFitMode,
    SetOrientation,
    SetSplitMode,
)
from ._shared import (
    http_error_if_failed,
    to_brightness_response,
    to_fit_mode_response,
    to_orientation_response,
    to_overlay_response,
    to_render_response,
    to_send_response,
    to_split_mode_response,
    to_theme_response,
)
from .schemas import (
    BrightnessRequest,
    BrightnessResponse,
    ColorRequest,
    FitModeRequest,
    FitModeResponse,
    OrientationRequest,
    OrientationResponse,
    OverlayRequest,
    OverlayResponse,
    RenderResponse,
    SendResponse,
    SplitModeRequest,
    SplitModeResponse,
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
    # Whitelist by basename (CodeQL py/path-injection sanitizer barrier).
    # Themes are flat dirs directly under ``user_content_dir`` (see
    # ThemeService.list — only top-level subdirs with config.json /
    # config1.dc count). We enumerate the trusted root once and look
    # up by basename, so the Path passed to LoadTheme comes entirely
    # from ``iterdir()`` — no user-controlled component flows into a
    # filesystem call.
    platform = request.app.state.trcc.platform
    allowed_root = platform.user_content_dir().resolve(strict=True)
    requested_name = Path(body.path).name
    if not requested_name:
        raise HTTPException(400, "Theme path required")
    themes = {p.name: p for p in allowed_root.iterdir() if p.is_dir()}
    candidate = themes.get(requested_name)
    if candidate is None:
        raise HTTPException(400, "Unknown theme")

    result = request.app.state.trcc.dispatch(
        LoadTheme(key=key, path=candidate),
    )
    http_error_if_failed(result)
    return to_theme_response(result)


@router.post("/fit-mode", response_model=FitModeResponse)
def set_fit_mode(key: str, body: FitModeRequest,
                 request: Request) -> FitModeResponse:
    result = request.app.state.trcc.dispatch(
        SetFitMode(key=key, mode=body.mode),
    )
    http_error_if_failed(result)
    return to_fit_mode_response(result)


@router.post("/overlay", response_model=OverlayResponse)
def set_overlay(key: str, body: OverlayRequest,
                request: Request) -> OverlayResponse:
    result = request.app.state.trcc.dispatch(
        EnableOverlay(key=key, enabled=body.enabled),
    )
    http_error_if_failed(result)
    return to_overlay_response(result)


@router.post("/split-mode", response_model=SplitModeResponse)
def set_split_mode(key: str, body: SplitModeRequest,
                   request: Request) -> SplitModeResponse:
    result = request.app.state.trcc.dispatch(
        SetSplitMode(key=key, mode=body.mode),
    )
    http_error_if_failed(result)
    return to_split_mode_response(result)


@router.post("/color", response_model=SendResponse)
def send_color(key: str, body: ColorRequest, request: Request) -> SendResponse:
    """Push a solid-color frame to a connected LCD device."""
    result = request.app.state.trcc.dispatch(
        SendColor(key=key, r=body.r, g=body.g, b=body.b),
    )
    http_error_if_failed(result)
    return to_send_response(result)


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
