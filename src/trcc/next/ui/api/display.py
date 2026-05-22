"""/devices/{key}/display router — orientation, brightness, theme."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...core.commands import (
    AddOverlayElement,
    ApplyMask,
    ConfigureSlideshow,
    DeleteOverlayElement,
    EnableOverlay,
    FlashOverlayElement,
    KeepAliveLoop,
    LcdSnapshot,
    ListMasks,
    LoadTheme,
    LoopVideo,
    PauseVideo,
    PlayVideo,
    RenderAndSend,
    RestoreLastTheme,
    SeekVideo,
    SendColor,
    SetBackgroundMode,
    SetBrightness,
    SetFitMode,
    SetMaskPosition,
    SetMaskVisible,
    SetOrientation,
    SetOverlayBackground,
    SetOverlayConfig,
    SetSlideshow,
    SetSplitMode,
    StopVideo,
    UpdateOverlayElement,
    UploadBootAnimation,
    UploadCustomMask,
)
from ._shared import (
    http_error_if_failed,
    to_background_mode_response,
    to_boot_animation_response,
    to_brightness_response,
    to_fit_mode_response,
    to_keepalive_response,
    to_lcd_snapshot_response,
    to_loop_video_response,
    to_mask_apply_response,
    to_mask_position_response,
    to_mask_upload_response,
    to_mask_visibility_response,
    to_masks_list_response,
    to_orientation_response,
    to_overlay_background_response,
    to_overlay_config_response,
    to_overlay_element_delete_response,
    to_overlay_element_response,
    to_overlay_response,
    to_pause_video_response,
    to_render_response,
    to_seek_video_response,
    to_send_response,
    to_slideshow_response,
    to_split_mode_response,
    to_theme_response,
    to_video_response,
)
from .schemas import (
    BackgroundModeRequest,
    BackgroundModeResponse,
    BootAnimationRequest,
    BootAnimationResponse,
    BrightnessRequest,
    BrightnessResponse,
    ColorRequest,
    FitModeRequest,
    FitModeResponse,
    KeepaliveRequest,
    KeepaliveResponse,
    LcdSnapshotResponse,
    LoopVideoRequest,
    LoopVideoResponse,
    MaskApplyRequest,
    MaskApplyResponse,
    MaskPositionRequest,
    MaskPositionResponse,
    MasksListResponse,
    MaskUploadRequest,
    MaskUploadResponse,
    MaskVisibilityRequest,
    MaskVisibilityResponse,
    OrientationRequest,
    OrientationResponse,
    OverlayBackgroundRequest,
    OverlayBackgroundResponse,
    OverlayConfigRequest,
    OverlayConfigResponse,
    OverlayElementAddRequest,
    OverlayElementDeleteResponse,
    OverlayElementResponse,
    OverlayElementUpdateRequest,
    OverlayFlashRequest,
    OverlayRequest,
    OverlayResponse,
    PauseVideoRequest,
    PauseVideoResponse,
    PlayVideoRequest,
    RenderResponse,
    SeekVideoRequest,
    SeekVideoResponse,
    SendResponse,
    SlideshowConfigureRequest,
    SlideshowResponse,
    SlideshowToggleRequest,
    SplitModeRequest,
    SplitModeResponse,
    ThemeRequest,
    ThemeResponse,
    VideoResponse,
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


@router.post("/mask", response_model=MaskApplyResponse)
def apply_mask(key: str, body: MaskApplyRequest,
                request: Request) -> MaskApplyResponse:
    """Apply a user-supplied mask.

    Path is whitelisted by basename within the user_content_dir/masks
    directory — mirrors the legacy theme-load CodeQL sanitizer so the
    Path passed to the Command comes entirely from a trusted iterdir().
    """
    from pathlib import Path

    from fastapi import HTTPException
    platform = request.app.state.trcc.platform
    masks_root = (platform.paths().user_content_dir() / "masks").resolve()
    if not masks_root.is_dir():
        raise HTTPException(400, "masks directory missing")
    requested_name = Path(body.path).name
    if not requested_name:
        raise HTTPException(400, "mask path required")
    candidates = {p.name: p for p in masks_root.iterdir() if p.is_file()}
    chosen = candidates.get(requested_name)
    if chosen is None:
        raise HTTPException(400, f"unknown mask: {requested_name!r}")
    result = request.app.state.trcc.dispatch(
        ApplyMask(key=key, path=chosen),
    )
    http_error_if_failed(result)
    return to_mask_apply_response(result)


@router.post("/mask-position", response_model=MaskPositionResponse)
def set_mask_position(key: str, body: MaskPositionRequest,
                      request: Request) -> MaskPositionResponse:
    result = request.app.state.trcc.dispatch(
        SetMaskPosition(key=key, x=body.x, y=body.y),
    )
    http_error_if_failed(result)
    return to_mask_position_response(result)


@router.post("/mask-visible", response_model=MaskVisibilityResponse)
def set_mask_visible(key: str, body: MaskVisibilityRequest,
                     request: Request) -> MaskVisibilityResponse:
    result = request.app.state.trcc.dispatch(
        SetMaskVisible(key=key, visible=body.visible),
    )
    http_error_if_failed(result)
    return to_mask_visibility_response(result)


@router.post("/split-mode", response_model=SplitModeResponse)
def set_split_mode(key: str, body: SplitModeRequest,
                   request: Request) -> SplitModeResponse:
    result = request.app.state.trcc.dispatch(
        SetSplitMode(key=key, mode=body.mode),
    )
    http_error_if_failed(result)
    return to_split_mode_response(result)


@router.post("/play-video", response_model=VideoResponse)
def play_video(key: str, body: PlayVideoRequest,
                request: Request) -> VideoResponse:
    """Start a video playback override on the device."""
    from pathlib import Path as _Path
    result = request.app.state.trcc.dispatch(
        PlayVideo(key=key, path=_Path(body.path), fps=body.fps),
    )
    http_error_if_failed(result)
    return to_video_response(result)


@router.post("/stop-video", response_model=VideoResponse)
def stop_video(key: str, request: Request) -> VideoResponse:
    """Clear the video playback override on the device."""
    result = request.app.state.trcc.dispatch(StopVideo(key=key))
    http_error_if_failed(result)
    return to_video_response(result)


_BOOT_ANIM_IMAGE_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".webp",
})


@router.post("/boot-animation", response_model=BootAnimationResponse)
def upload_boot_animation(key: str, body: BootAnimationRequest,
                          request: Request) -> BootAnimationResponse:
    """Upload a multi-frame compressed boot animation to a SCSI LCD's flash.

    *frames_dir* must point to an existing directory; we enumerate it
    via iterdir() and dispatch only image files we found.  No user-
    supplied path component flows into a filesystem call beyond the
    initial directory resolution.
    """
    frames_path = Path(body.frames_dir).resolve()
    if not frames_path.is_dir():
        raise HTTPException(400, f"frames_dir is not a directory: {body.frames_dir!r}")

    frame_paths = sorted(
        p for p in frames_path.iterdir()
        if p.is_file() and p.suffix.lower() in _BOOT_ANIM_IMAGE_EXTS
    )
    if not frame_paths:
        raise HTTPException(400, "No supported image frames found in frames_dir")
    if len(frame_paths) > 248:
        raise HTTPException(
            400, f"Too many frames: {len(frame_paths)} (max 248)",
        )

    delays = [body.delay_ds] * len(frame_paths)
    result = request.app.state.trcc.dispatch(UploadBootAnimation(
        key=key, frame_paths=frame_paths, delays_ds=delays,
    ))
    http_error_if_failed(result)
    return to_boot_animation_response(result)


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



@router.post("/restore-theme", response_model=ThemeResponse)
def restore_theme(key: str, request: Request) -> ThemeResponse:
    """Reload the device's persisted theme."""
    result = request.app.state.trcc.dispatch(RestoreLastTheme(key=key))
    http_error_if_failed(result)
    return to_theme_response(result)


@router.get("/snapshot", response_model=LcdSnapshotResponse)
def snapshot(key: str, request: Request) -> LcdSnapshotResponse:
    """Return the persisted LCD state for one device."""
    result = request.app.state.trcc.dispatch(LcdSnapshot(key=key))
    http_error_if_failed(result)
    return to_lcd_snapshot_response(result)


@router.post("/slideshow", response_model=SlideshowResponse)
def slideshow_toggle(key: str, body: SlideshowToggleRequest,
                     request: Request) -> SlideshowResponse:
    """Turn the device's slideshow on / off."""
    result = request.app.state.trcc.dispatch(
        SetSlideshow(key=key, enabled=body.enabled),
    )
    http_error_if_failed(result)
    return to_slideshow_response(result)


@router.put("/slideshow", response_model=SlideshowResponse)
def slideshow_configure(key: str, body: SlideshowConfigureRequest,
                        request: Request) -> SlideshowResponse:
    """Set the theme list + interval for a device's slideshow."""
    result = request.app.state.trcc.dispatch(ConfigureSlideshow(
        key=key,
        themes=tuple(body.themes) if body.themes is not None else None,
        interval_s=body.interval_s,
    ))
    http_error_if_failed(result)
    return to_slideshow_response(result)


@router.post("/keepalive", response_model=KeepaliveResponse)
def keepalive(key: str, body: KeepaliveRequest,
              request: Request) -> KeepaliveResponse:
    """Run a keepalive burst (resend the last frame N times)."""
    result = request.app.state.trcc.dispatch(KeepAliveLoop(
        key=key, count=body.count, interval_s=body.interval_s,
    ))
    http_error_if_failed(result)
    return to_keepalive_response(result)


@router.post("/background-mode", response_model=BackgroundModeResponse)
def background_mode(key: str, body: BackgroundModeRequest,
                    request: Request) -> BackgroundModeResponse:
    """Pick what fills the LCD behind overlays (theme/color/transparent)."""
    result = request.app.state.trcc.dispatch(
        SetBackgroundMode(key=key, mode=body.mode),
    )
    http_error_if_failed(result)
    return to_background_mode_response(result)


@router.post("/overlay-background", response_model=OverlayBackgroundResponse)
def overlay_background(key: str, body: OverlayBackgroundRequest,
                       request: Request) -> OverlayBackgroundResponse:
    """Set the solid background color used when background-mode=color."""
    result = request.app.state.trcc.dispatch(
        SetOverlayBackground(key=key, color=body.color),
    )
    http_error_if_failed(result)
    return to_overlay_background_response(result)


# ── Overlay element CRUD ─────────────────────────────────────────────


@router.post("/overlay-elements", response_model=OverlayElementResponse)
def overlay_add(key: str, body: OverlayElementAddRequest,
                request: Request) -> OverlayElementResponse:
    """Add a user-edited overlay element."""
    result = request.app.state.trcc.dispatch(AddOverlayElement(
        key=key, type=body.type, x=body.x, y=body.y,
        color=body.color, size=body.size,
        bold=body.bold, italic=body.italic,
        text=body.text, metric=body.metric, format=body.format,
        source=body.source, element_id=body.element_id,
    ))
    http_error_if_failed(result)
    return to_overlay_element_response(result)


@router.patch(
    "/overlay-elements/{element_id}",
    response_model=OverlayElementResponse,
)
def overlay_update(key: str, element_id: str,
                   body: OverlayElementUpdateRequest,
                   request: Request) -> OverlayElementResponse:
    """Mutate fields on an existing user-edited overlay element."""
    result = request.app.state.trcc.dispatch(UpdateOverlayElement(
        key=key, element_id=element_id,
        x=body.x, y=body.y, color=body.color, size=body.size,
        bold=body.bold, italic=body.italic,
        text=body.text, metric=body.metric, format=body.format,
        source=body.source,
    ))
    http_error_if_failed(result)
    return to_overlay_element_response(result)


@router.delete(
    "/overlay-elements/{element_id}",
    response_model=OverlayElementDeleteResponse,
)
def overlay_delete(key: str, element_id: str,
                   request: Request) -> OverlayElementDeleteResponse:
    """Remove an overlay element by id."""
    result = request.app.state.trcc.dispatch(
        DeleteOverlayElement(key=key, element_id=element_id),
    )
    http_error_if_failed(result)
    return to_overlay_element_delete_response(result)


@router.post(
    "/overlay-elements/{element_id}/flash",
    response_model=OverlayElementResponse,
)
def overlay_flash(key: str, element_id: str,
                  body: OverlayFlashRequest,
                  request: Request) -> OverlayElementResponse:
    """Briefly highlight an overlay element in the GUI."""
    result = request.app.state.trcc.dispatch(FlashOverlayElement(
        key=key, element_id=element_id, duration_ms=body.duration_ms,
    ))
    http_error_if_failed(result)
    return to_overlay_element_response(result)


@router.put(
    "/overlay-elements",
    response_model=OverlayConfigResponse,
)
def overlay_set_config(key: str, body: OverlayConfigRequest,
                       request: Request) -> OverlayConfigResponse:
    """Bulk replace the user-overlay element list."""
    elements = tuple(e.model_dump() for e in body.elements)
    result = request.app.state.trcc.dispatch(
        SetOverlayConfig(key=key, elements=elements),
    )
    http_error_if_failed(result)
    return to_overlay_config_response(result)


@router.post("/pause-video", response_model=PauseVideoResponse)
def pause_video(key: str, body: PauseVideoRequest,
                request: Request) -> PauseVideoResponse:
    """Pause / resume video playback."""
    result = request.app.state.trcc.dispatch(
        PauseVideo(key=key, paused=body.paused),
    )
    http_error_if_failed(result)
    return to_pause_video_response(result)


@router.post("/seek-video", response_model=SeekVideoResponse)
def seek_video(key: str, body: SeekVideoRequest,
               request: Request) -> SeekVideoResponse:
    """Jump to a specific frame."""
    result = request.app.state.trcc.dispatch(
        SeekVideo(key=key, frame=body.frame),
    )
    http_error_if_failed(result)
    return to_seek_video_response(result)


@router.post("/loop-video", response_model=LoopVideoResponse)
def loop_video(key: str, body: LoopVideoRequest,
               request: Request) -> LoopVideoResponse:
    """Toggle whether playback wraps or sticks at the last frame."""
    result = request.app.state.trcc.dispatch(
        LoopVideo(key=key, loop=body.loop),
    )
    http_error_if_failed(result)
    return to_loop_video_response(result)


@router.post("/upload-mask", response_model=MaskUploadResponse)
def upload_mask(key: str, body: MaskUploadRequest,
                request: Request) -> MaskUploadResponse:
    """Upload a mask file (server-side path) + apply it."""
    from pathlib import Path as _Path
    result = request.app.state.trcc.dispatch(
        UploadCustomMask(key=key, source=_Path(body.source)),
    )
    http_error_if_failed(result)
    return to_mask_upload_response(result)


# ── Meta routes (no device key in path) ──────────────────────────────


meta_router = APIRouter(prefix="/display", tags=["display"])


@meta_router.get("/masks", response_model=MasksListResponse)
def list_masks(
    request: Request,
    key: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> MasksListResponse:
    """List masks for a device resolution.

    Pass either ``?key=vid:pid`` (resolved through the connected
    device's handshake profile) or ``?width=W&height=H`` for an
    explicit override.
    """
    resolution: tuple[int, int] | None = None
    if key is not None:
        device = request.app.state.trcc.devices.get(key)
        if device is None or device.profile is None:
            return MasksListResponse(
                ok=False, directory="", masks=[],
                message=(f"Device {key} not connected — connect first "
                         "so we know the target resolution"),
            )
        resolution = device.profile.resolution
    elif width is not None and height is not None:
        resolution = (width, height)
    result = request.app.state.trcc.dispatch(ListMasks(resolution=resolution))
    http_error_if_failed(result)
    return to_masks_list_response(result)
