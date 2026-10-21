"""The Audio Visualizer's API routes -- the same paths they always had.

Added for every installed module, on or off; while it is off each answers
that it is off and where to turn it on.  Stopping a panel's visual is the
App's own ``ClearModuleSource``, so ``"visual": ""`` works with it off too.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from trcc.core.commands import ClearModuleSource
from trcc.core.results import Result
from trcc.ui.api._shared import http_error_if_failed, owned_path

from ...core import (
    ListVisuals,
    PlayVisual,
    SetVisualCenter,
    SetVisualPicture,
    SetVisualSettings,
    VisualResult,
    VisualsResult,
    VisualTile,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/devices/{key}/display", tags=["display"])
meta_router = APIRouter(prefix="/display", tags=["display"])


class VisualRequest(BaseModel):
    """Body for ``POST /devices/{key}/display/visual``: the audio visual
    (``GET /display/visuals``), its preset and slider values by name;
    '' stops it."""
    visual: str = Field("", max_length=64, pattern=r"^[a-z0-9-]*$")
    preset: str = Field("", max_length=512)
    sliders: dict[str, float] = Field(default_factory=dict, max_length=16)


class VisualSettingsRequest(BaseModel):
    """Body for ``POST /devices/{key}/display/visual/settings``: slider
    values by name for the playing visual."""
    sliders: dict[str, float] = Field(default_factory=dict, max_length=16)


class VisualPictureRequest(BaseModel):
    """Body for ``POST /display/visual-picture``: the centre picture on or
    off, and its fire (0 to 1) -- each only when given."""
    shown: bool | None = None
    fire: float | None = Field(None, ge=0.0, le=1.0)


class VisualCenterRequest(BaseModel):
    """Body for ``POST /display/visual-center``: a picture in TRCC's own
    folders; '' for none."""
    path: str = Field("", max_length=1024)


@router.post("/visual")
def visual(key: str, body: VisualRequest, request: Request) -> VisualResult:
    """Draw *key*'s background from what is playing with an audio visual --
    or stop it ('' as the visual).  The preset is checked by the visual,
    which refuses one outside the visuals' folder."""
    log.info("api POST /devices/{key}/display/visual: key=%s visual=%s",
             key, body.visual)
    if body.visual:
        result = request.app.state.trcc.dispatch(PlayVisual(
            key=key, visual=body.visual, preset=body.preset,
            settings=dict(body.sliders)))
    else:
        stopped: Result = request.app.state.trcc.dispatch(
            ClearModuleSource(key=key, module="visualizer"))
        result = VisualResult(ok=stopped.ok, key=key, message=stopped.message)
    http_error_if_failed(result)
    return result


@router.post("/visual/settings")
def visual_settings(key: str, body: VisualSettingsRequest,
                    request: Request) -> VisualResult:
    """Move *key*'s playing audio visual's sliders -- live."""
    log.info("api POST /devices/{key}/display/visual/settings: key=%s %s",
             key, sorted(body.sliders))
    result = request.app.state.trcc.dispatch(
        SetVisualSettings(key=key, settings=dict(body.sliders)))
    http_error_if_failed(result)
    return result


@meta_router.get("/visuals")
def list_visuals(request: Request, key: str = "") -> VisualsResult:
    """The audio visuals this computer can draw, their sliders and presets,
    and the centre picture -- with ``?key=``, also what that panel plays and
    its slider values."""
    log.info("api GET /display/visuals: key=%r", key)
    return request.app.state.trcc.dispatch(ListVisuals(key=key))


@meta_router.get("/visual-tile")
def visual_tile(request: Request, visual: str, preset: str = "",
                size: int = 120) -> Response:
    """A visual's gallery tile as PNG -- what it draws for noise, the same
    whatever is playing.  The preset is checked by the visual, which refuses
    one outside the visuals' folder."""
    log.info("api GET /display/visual-tile: %s size=%d", visual, size)
    result = request.app.state.trcc.dispatch(
        VisualTile(visual=visual, preset=preset, size=size))
    if not result.ok:
        raise HTTPException(404, result.message)
    return Response(content=result.image, media_type=result.media_type)


@meta_router.post("/visual-picture")
def visual_picture(body: VisualPictureRequest, request: Request) -> VisualsResult:
    """Switch the centre picture on or off (kept while off) and set its
    fire."""
    log.info("api POST /display/visual-picture: shown=%s fire=%s",
             body.shown, body.fire)
    result = request.app.state.trcc.dispatch(
        SetVisualPicture(shown=body.shown, fire=body.fire))
    http_error_if_failed(result)
    return result


@meta_router.post("/visual-center")
def visual_center(body: VisualCenterRequest, request: Request) -> VisualsResult:
    """The picture in the middle of the visuals that show one -- a file in
    TRCC's own folders; '' for none."""
    log.info("api POST /display/visual-center: %s", bool(body.path))
    path = str(owned_path(request, body.path)) if body.path else ""
    result = request.app.state.trcc.dispatch(SetVisualCenter(path=path))
    http_error_if_failed(result)
    return result


ROUTERS = (router, meta_router)
