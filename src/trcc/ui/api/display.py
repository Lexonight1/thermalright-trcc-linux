"""/devices/{key}/display router — orientation, brightness, theme."""
from __future__ import annotations

import hmac
import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response

from ...core.commands import (
    AddOverlayElement,
    ApplyMask,
    ConfigureSlideshow,
    DeleteOverlayElement,
    DeviceState,
    EnableOverlay,
    FlashOverlayElement,
    GetPaths,
    KeepAliveLoop,
    LcdSnapshot,
    ListMasks,
    LoadImage,
    LoadTheme,
    LoopVideo,
    PauseVideo,
    PlayVideo,
    RestoreDeviceState,
    SeekVideo,
    SendColor,
    SetBackgroundMode,
    SetBrightness,
    SetFitMode,
    SetMaskPosition,
    SetMaskVisible,
    SetMediaPlayer,
    SetOrientation,
    SetOverlayBackground,
    SetOverlayConfig,
    SetSlideshow,
    SetSplitMode,
    SleepDevice,
    StartScreencast,
    StopScreencast,
    StopVideo,
    TickDisplay,
    UpdateOverlayElement,
    UploadBootAnimation,
    UploadCustomMask,
    VideoStatus,
)
from ...core.results import (
    BackgroundModeResult,
    BootAnimationResult,
    BrightnessResult,
    FitModeResult,
    KeepaliveResult,
    LcdSnapshotResult,
    LoopVideoResult,
    MaskApplyResult,
    MaskPositionResult,
    MasksListResult,
    MaskUploadResult,
    MaskVisibilityResult,
    MediaPlayerResult,
    OrientationResult,
    OverlayBackgroundResult,
    OverlayConfigResult,
    OverlayElementDeleteResult,
    OverlayElementResult,
    OverlayResult,
    PauseVideoResult,
    RenderResult,
    ScreencastResult,
    SeekVideoResult,
    SendResult,
    SlideshowResult,
    SplitModeResult,
    VideoResult,
)
from ._shared import (
    http_error_if_failed,
    staging_dir,
    to_theme_response,
)
from .schemas import (
    BackgroundModeRequest,
    BootAnimationRequest,
    BrightnessRequest,
    ColorRequest,
    CreateThemeResponse,
    FitModeRequest,
    KeepaliveRequest,
    LoopVideoRequest,
    MaskApplyRequest,
    MaskPositionRequest,
    MaskUploadRequest,
    MaskVisibilityRequest,
    MediaPlayerRequest,
    OrientationRequest,
    OverlayBackgroundRequest,
    OverlayConfigRequest,
    OverlayElementAddRequest,
    OverlayElementUpdateRequest,
    OverlayFlashRequest,
    OverlayRequest,
    PauseVideoRequest,
    PlayVideoRequest,
    ScreencastStartRequest,
    SeekVideoRequest,
    SlideshowConfigureRequest,
    SlideshowToggleRequest,
    SplitModeRequest,
    ThemeRequest,
    ThemeResponse,
    VideoStatusResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/devices/{key}/display", tags=["display"])


@router.post("/orientation")
def set_orientation(key: str, body: OrientationRequest,
                    request: Request) -> OrientationResult:
    log.info(
        "api POST /devices/{key}/display/orientation: key=%s degrees=%s",
        key, body.degrees,
    )
    result = request.app.state.trcc.dispatch(
        SetOrientation(key=key, degrees=body.degrees),
    )
    http_error_if_failed(result)
    return result


@router.post("/brightness")
def set_brightness(key: str, body: BrightnessRequest,
                   request: Request) -> BrightnessResult:
    log.info(
        "api POST /devices/{key}/display/brightness: key=%s percent=%s",
        key, body.percent,
    )
    result = request.app.state.trcc.dispatch(
        SetBrightness(key=key, percent=body.percent),
    )
    http_error_if_failed(result)
    return result


@router.post("/theme", response_model=ThemeResponse)
def load_theme(key: str, body: ThemeRequest,
               request: Request) -> ThemeResponse:
    log.info(
        "api POST /devices/{key}/display/theme: key=%s path=%s",
        key, body.path,
    )
    # Whitelist by basename (CodeQL py/path-injection sanitizer barrier).
    # Themes live at ``<root>/theme{w}{h}/<name>/`` under BOTH the program data
    # dir and the user content dir (mirrors ThemeService's two-root enumeration)
    # — NOT flat under ``user_content_dir``, which is why a path straight from
    # ``/theme/list`` was wrongly rejected "Unknown theme" (#239).  We match the
    # request's resolution-dir + theme-dir basenames against the trusted roots
    # by iterating, so the Path handed to LoadTheme comes entirely from
    # ``iterdir()`` — no user-controlled component flows into a filesystem call.
    paths = request.app.state.trcc.platform.paths()
    requested = Path(body.path)
    res_name, theme_name = requested.parent.name, requested.name
    if not theme_name:
        raise HTTPException(400, "Theme path required")
    candidate: Path | None = None
    for root in (paths.user_data_dir(), paths.data_dir()):
        if not root.is_dir():
            continue
        res_dir = next((d for d in root.iterdir()
                        if d.is_dir() and d.name == res_name), None)
        if res_dir is None:
            continue
        candidate = next((d for d in res_dir.iterdir()
                          if d.is_dir() and d.name == theme_name), None)
        if candidate is not None:
            break
    if candidate is None:
        raise HTTPException(400, "Unknown theme")

    result = request.app.state.trcc.dispatch(
        LoadTheme(key=key, path=candidate),
    )
    http_error_if_failed(result)
    return to_theme_response(result)


@router.post("/fit-mode")
def set_fit_mode(key: str, body: FitModeRequest,
                 request: Request) -> FitModeResult:
    log.info(
        "api POST /devices/{key}/display/fit-mode: key=%s mode=%s",
        key, body.mode,
    )
    result = request.app.state.trcc.dispatch(
        SetFitMode(key=key, mode=body.mode),
    )
    http_error_if_failed(result)
    return result


@router.post("/overlay")
def set_overlay(key: str, body: OverlayRequest,
                request: Request) -> OverlayResult:
    log.info(
        "api POST /devices/{key}/display/overlay: key=%s enabled=%s",
        key, body.enabled,
    )
    result = request.app.state.trcc.dispatch(
        EnableOverlay(key=key, enabled=body.enabled),
    )
    http_error_if_failed(result)
    return result


@router.post("/mask")
def apply_mask(key: str, body: MaskApplyRequest,
                request: Request) -> MaskApplyResult:
    """Apply a user-supplied mask.

    Path is whitelisted by basename within the user_content_dir/masks
    directory — mirrors the legacy theme-load CodeQL sanitizer so the
    Path passed to the Command comes entirely from a trusted iterdir().
    """
    log.info(
        "api POST /devices/{key}/display/mask: key=%s path=%s",
        key, body.path,
    )
    user_content = request.app.state.trcc.dispatch(GetPaths()).user_content_dir
    if not user_content:
        log.warning("apply_mask: GetPaths returned no user_content_dir — "
                    "refusing (an empty path resolves to the process cwd)")
        raise HTTPException(500, "user content directory unavailable")
    masks_root = (Path(user_content) / "masks").resolve()
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
    return result


@router.post("/mask-position")
def set_mask_position(key: str, body: MaskPositionRequest,
                      request: Request) -> MaskPositionResult:
    log.info(
        "api POST /devices/{key}/display/mask-position: key=%s x=%s y=%s",
        key, body.x, body.y,
    )
    result = request.app.state.trcc.dispatch(
        SetMaskPosition(key=key, x=body.x, y=body.y),
    )
    http_error_if_failed(result)
    return result


@router.post("/mask-visible")
def set_mask_visible(key: str, body: MaskVisibilityRequest,
                     request: Request) -> MaskVisibilityResult:
    log.info(
        "api POST /devices/{key}/display/mask-visible: key=%s visible=%s",
        key, body.visible,
    )
    result = request.app.state.trcc.dispatch(
        SetMaskVisible(key=key, visible=body.visible),
    )
    http_error_if_failed(result)
    return result


@router.post("/split-mode")
def set_split_mode(key: str, body: SplitModeRequest,
                   request: Request) -> SplitModeResult:
    log.info(
        "api POST /devices/{key}/display/split-mode: key=%s mode=%s",
        key, body.mode,
    )
    result = request.app.state.trcc.dispatch(
        SetSplitMode(key=key, mode=body.mode),
    )
    http_error_if_failed(result)
    return result


@router.post("/play-video")
def play_video(key: str, body: PlayVideoRequest,
                request: Request) -> VideoResult:
    """Start a video playback override on the device."""
    log.info(
        "api POST /devices/{key}/display/play-video: key=%s path=%s fps=%s",
        key, body.path, body.fps,
    )
    # PlayVideo persists the background path itself (#249).
    result = request.app.state.trcc.dispatch(
        PlayVideo(key=key, path=Path(body.path), fps=body.fps),
    )
    http_error_if_failed(result)
    return result


@router.post("/stop-video")
def stop_video(key: str, request: Request) -> VideoResult:
    """Clear the video playback override on the device."""
    log.info("api POST /devices/{key}/display/stop-video: key=%s", key)
    result = request.app.state.trcc.dispatch(StopVideo(key=key))
    http_error_if_failed(result)
    return result


@router.get("/video-status", response_model=VideoStatusResponse)
def video_status(key: str, request: Request) -> VideoStatusResponse:
    """Current playback state for the device's video background override.

    Read-only — returns ``playing=False`` (and zeros elsewhere) when
    no playback is loaded.  Use ``play-video`` to start, ``stop-video``
    to clear, ``pause-video`` / ``seek-video`` / ``loop-video`` to
    control an active playback.
    """
    log.info("api GET /devices/{key}/display/video-status: key=%s", key)
    result = request.app.state.trcc.dispatch(VideoStatus(key=key))
    # The Result keeps None for "no playback" (distinct from frame 0 / 0 fps);
    # this response has always sent 0 there, so map at the wire and leave the
    # published shape untouched.
    return VideoStatusResponse(
        ok=result.ok, key=result.key, message=result.message,
        playing=result.playing, paused=result.paused,
        cursor=result.cursor or 0,
        frame_count=result.frame_count or 0,
        fps=result.fps or 0,
        loop=result.loop,
    )


@router.post("/send-image", response_model=ThemeResponse)
async def send_image(
    key: str,
    request: Request,
    image: UploadFile = File(...),
) -> ThemeResponse:
    """One-shot image-to-LCD via multipart upload.

    Remote clients (web dashboard, mobile app) that have an image in
    memory rather than on the server's filesystem can use this instead
    of ``POST /devices/{key}/display/theme`` (which requires the file
    to already exist server-side).  Stages the upload to
    ``user_content_dir/uploads/`` then dispatches ``LoadImage``.

    Supported formats: PNG / JPG / JPEG / BMP / WEBP — matches
    :class:`LoadImage`.
    """
    log.info(
        "api POST /devices/{key}/display/send-image: key=%s filename=%s",
        key, image.filename,
    )
    uploads_dir = staging_dir(request)
    suffix = Path(image.filename or "image.png").suffix.lower() or ".png"
    if suffix not in _CREATE_THEME_IMG_EXTS:
        raise HTTPException(
            400,
            f"unsupported image extension {suffix!r}; expected one of "
            f"{sorted(_CREATE_THEME_IMG_EXTS)}",
        )
    staged = uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    with staged.open("wb") as f:
        shutil.copyfileobj(image.file, f)
    result = request.app.state.trcc.dispatch(
        LoadImage(key=key, path=staged),
    )
    http_error_if_failed(result)
    return to_theme_response(result)


@router.websocket("/preview/stream")
async def preview_stream(ws: WebSocket, key: str) -> None:
    """Stream JPEG-encoded preview frames over a WebSocket at ~5 fps.

    Auth: clients pass the API token as a ``?token=...`` query
    parameter (WebSocket clients can't easily set custom headers in
    browsers; query param is the conventional workaround).  When the
    server has no token configured (loopback dev mode), the param is
    accepted but not enforced — same posture as the HTTP middleware.

    JPEG (not PNG) for streaming: a ~5 fps PNG stream of a 320×320
    panel would burn through bandwidth on every frame.  Lossy quality
    is acceptable for a live dashboard preview; the one-shot
    ``GET /preview`` endpoint stays lossless for screenshot use.

    Disconnects from either side end the loop cleanly; the next
    ``connect`` from the same client re-arms the stream.
    """
    import asyncio

    from ...core.errors import DeviceNotFoundError
    from .main import _api_token

    # Token check FIRST — accept the WS handshake only when auth
    # passes.  Skipping the accept on auth failure is the conventional
    # way to send a 1008 close without exposing why.
    if _api_token is not None:
        token = ws.query_params.get("token", "")
        if not hmac.compare_digest(token, _api_token):
            await ws.close(code=1008)
            return

    trcc = ws.app.state.trcc
    try:
        device = trcc.get(key)
    except DeviceNotFoundError:
        await ws.close(code=1008)
        return

    await ws.accept()
    frame_interval_s = 0.200  # 5 fps — see docstring
    try:
        while True:
            theme = trcc.active_themes.get(key)
            if theme is not None:
                sensors_full = trcc.platform.sensors().read_all()
                surface = trcc.display.build_preview_surface(
                    info=device.info,
                    theme=theme,
                    sensors=sensors_full,
                    profile=device.profile,
                )
                jpeg_bytes = trcc.display.encode_jpeg(surface)
                await ws.send_bytes(jpeg_bytes)
            await asyncio.sleep(frame_interval_s)
    except WebSocketDisconnect:
        log.debug("preview_stream %s: client disconnected", key)


@router.get("/preview")
def preview(key: str, request: Request) -> Response:
    """Return the device's current rendered frame as a PNG image.

    Goes through the same pipeline as ``RenderAndSend`` but stops at
    the surface-encode step — useful for dashboards / mobile clients
    that want a screenshot of what the LCD is showing.  Returns 404
    when the device has no active theme.

    Lossless PNG keeps overlay text + CJK glyphs legible; JPEG would
    chew them up at typical 320×320 panel resolutions.
    """
    log.info("api GET /devices/{key}/display/preview: key=%s", key)
    from ...core.errors import DeviceNotFoundError
    trcc = request.app.state.trcc
    try:
        device = trcc.get(key)
    except DeviceNotFoundError as e:
        raise HTTPException(404, f"Device {key} not attached") from e
    theme = trcc.active_themes.get(key)
    if theme is None:
        raise HTTPException(404, "No active theme — load one first")
    sensors_full = trcc.platform.sensors().read_all()
    surface = trcc.display.build_preview_surface(
        info=device.info,
        theme=theme,
        sensors=sensors_full,
        profile=device.profile,
    )
    png_bytes = trcc.display.encode_png(surface)
    return Response(content=png_bytes, media_type="image/png")


@router.post("/screencast/start")
def screencast_start(key: str, body: ScreencastStartRequest,
                     request: Request) -> ScreencastResult:
    """Begin a screen-capture session for *key*.

    Dispatches :class:`StartScreencast` which validates region geometry,
    stops any active video playback, and publishes
    :class:`ScreencastStarted` for the GUI's ``ScreencastHandler`` to
    pick up.  Headless API callers can fire this even without a GUI
    attached — the bus event still fires, just no consumer picks
    it up.
    """
    log.info(
        "api POST /devices/{key}/display/screencast/start: key=%s "
        "x=%s y=%s w=%s h=%s audio=%s",
        key, body.x, body.y, body.w, body.h, body.audio,
    )
    result = request.app.state.trcc.dispatch(
        StartScreencast(
            key=key, x=body.x, y=body.y, w=body.w, h=body.h,
            audio=body.audio,
        ),
    )
    http_error_if_failed(result)
    return result


@router.post("/screencast/stop")
def screencast_stop(key: str, request: Request) -> ScreencastResult:
    """End the screen-capture session for *key*.

    Idempotent — returns ``ok=True`` even when no session was running.
    """
    log.info("api POST /devices/{key}/display/screencast/stop: key=%s", key)
    result = request.app.state.trcc.dispatch(StopScreencast(key=key))
    http_error_if_failed(result)
    return result


@router.post("/media-player")
def media_player(key: str, body: MediaPlayerRequest,
                 request: Request) -> MediaPlayerResult:
    """Set the media-player source for *key* — a local file or a web URL/stream.

    Dispatches :class:`SetMediaPlayer`: a local file plays through the video
    pipeline; a web URL is referenced (persisted so a theme save captures it).
    An empty ``uri`` clears the source.
    """
    log.info("api POST /devices/{key}/display/media-player: key=%s uri=%s",
             key, body.uri)
    result = request.app.state.trcc.dispatch(
        SetMediaPlayer(key=key, uri=body.uri),
    )
    http_error_if_failed(result)
    return result


_BOOT_ANIM_IMAGE_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".webp",
})


@router.post("/boot-animation")
def upload_boot_animation(key: str, body: BootAnimationRequest,
                          request: Request) -> BootAnimationResult:
    """Upload a multi-frame compressed boot animation to a SCSI LCD's flash.

    *frames_dir* is a subdirectory **name** under the user-content
    directory (not an arbitrary path — this is a network endpoint).  We
    enumerate the trusted root by basename so the directory passed to a
    filesystem call comes entirely from ``iterdir()``; no user-controlled
    path component reaches the FS (CodeQL py/path-injection barrier, same
    shape as ``load_theme``).
    """
    log.info(
        "api POST /devices/{key}/display/boot-animation: key=%s "
        "frames_dir=%s delay_ds=%s",
        key, body.frames_dir, body.delay_ds,
    )
    user_content = request.app.state.trcc.dispatch(GetPaths()).user_content_dir
    if not user_content:
        log.warning("frames dir: GetPaths returned no user_content_dir — "
                    "refusing (an empty path resolves to the process cwd)")
        raise HTTPException(500, "user content directory unavailable")
    allowed_root = Path(user_content).resolve(strict=True)
    requested_name = Path(body.frames_dir).name
    if not requested_name:
        raise HTTPException(400, "frames_dir required")
    subdirs = {p.name: p for p in allowed_root.iterdir() if p.is_dir()}
    frames_path = subdirs.get(requested_name)
    if frames_path is None:
        raise HTTPException(
            400,
            f"frames_dir not found under the user content directory: "
            f"{requested_name!r}",
        )

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
    return result


# Extensions accepted by the create-theme background upload — union of
# what ``LoadImage`` accepts (static) + what ``PlayVideo`` accepts
# (animated).  Kept here (not imported from ``core.commands``) so the
# API edge can reject obviously-wrong uploads before staging them.
_CREATE_THEME_IMG_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".webp",
})
_CREATE_THEME_VID_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".zt", ".gif",
})


@router.post("/create-theme", response_model=CreateThemeResponse)
async def create_theme(
    key: str,
    request: Request,
    background: UploadFile = File(...),
    mask: UploadFile | None = File(None),
    overlay: UploadFile | None = File(None),
    loop: bool = Form(True),
) -> CreateThemeResponse:
    """Create + apply a custom theme from uploaded multipart files.

    Mirrors legacy ``POST /display/create-theme``: accept a background
    image OR video plus an optional mask PNG and an optional overlay
    JSON config, then route through the existing per-device Commands
    so the wire path stays identical to a load-from-disk flow.

    Inputs (multipart/form-data):
      * ``background`` — required.  Image (png/jpg/jpeg/bmp/webp) or
        video (mp4/mov/webm/mkv/avi/zt/gif).  Animated → ``PlayVideo``;
        static → ``LoadImage``.
      * ``mask``       — optional PNG.  Applied via ``ApplyMask`` after
        the background lands.
      * ``overlay``    — optional JSON file with ``{"elements": [...]}``
        shape.  Dispatched via ``SetOverlayConfig`` + ``EnableOverlay``.
      * ``loop``       — passed to ``PlayVideo`` via ``LoopVideo`` when
        the background is animated.

    Legacy's ``metric`` form-field shorthand (``metric=cpu_temp:10,20``)
    is NOT supported here yet — it requires the ``build_overlay_config``
    helper that lives on the cutover close-plan as a separate item
    (G45).  Upload an ``overlay`` JSON file instead until that lands.
    """
    log.info(
        "api POST /devices/{key}/display/create-theme: key=%s "
        "background=%s mask=%s overlay=%s loop=%s",
        key,
        background.filename,
        mask.filename if mask is not None else None,
        overlay.filename if overlay is not None else None,
        loop,
    )
    uploads_dir = staging_dir(request)

    bg_name = Path(background.filename or "background").name
    bg_suffix = Path(bg_name).suffix.lower() or ".jpg"
    if (bg_suffix not in _CREATE_THEME_IMG_EXTS
            and bg_suffix not in _CREATE_THEME_VID_EXTS):
        raise HTTPException(
            400,
            f"unsupported background extension {bg_suffix!r} "
            f"(expected image {sorted(_CREATE_THEME_IMG_EXTS)} or "
            f"video {sorted(_CREATE_THEME_VID_EXTS)})",
        )
    bg_path = uploads_dir / f"{uuid.uuid4().hex}{bg_suffix}"
    with bg_path.open("wb") as f:
        shutil.copyfileobj(background.file, f)

    mask_path: Path | None = None
    if mask is not None and mask.file is not None:
        mask_suffix = Path(mask.filename or "mask.png").suffix.lower() or ".png"
        if mask_suffix not in _CREATE_THEME_IMG_EXTS:
            raise HTTPException(
                400,
                f"unsupported mask extension {mask_suffix!r}",
            )
        mask_target: Path = uploads_dir / f"{uuid.uuid4().hex}{mask_suffix}"
        with mask_target.open("wb") as f:
            shutil.copyfileobj(mask.file, f)
        mask_path = mask_target

    overlay_elements: tuple[dict, ...] | None = None
    if overlay is not None and overlay.file is not None:
        try:
            overlay_config = json.loads(overlay.file.read())
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(
                400, f"invalid overlay JSON: {e}",
            ) from e
        if not isinstance(overlay_config, dict):
            raise HTTPException(400, "overlay JSON must be an object")
        raw_elements = overlay_config.get("elements", [])
        if not isinstance(raw_elements, list):
            raise HTTPException(
                400,
                "overlay JSON 'elements' must be a list of element dicts",
            )
        overlay_elements = tuple(
            e for e in raw_elements if isinstance(e, dict)
        )

    animated = bg_suffix in _CREATE_THEME_VID_EXTS
    if animated:
        # PlayVideo persists the background path itself (#249).
        play_result = request.app.state.trcc.dispatch(
            PlayVideo(key=key, path=bg_path),
        )
        http_error_if_failed(play_result)
        if not loop:
            request.app.state.trcc.dispatch(
                LoopVideo(key=key, loop=False),
            )
    else:
        load_result = request.app.state.trcc.dispatch(
            LoadImage(key=key, path=bg_path),
        )
        http_error_if_failed(load_result)

    if mask_path is not None:
        mask_result = request.app.state.trcc.dispatch(
            ApplyMask(key=key, path=mask_path),
        )
        http_error_if_failed(mask_result)

    if overlay_elements is not None:
        config_result = request.app.state.trcc.dispatch(
            SetOverlayConfig(key=key, elements=overlay_elements),
        )
        http_error_if_failed(config_result)
        request.app.state.trcc.dispatch(
            EnableOverlay(key=key, enabled=True),
        )

    # Resolve the device's resolution for the response — same lookup
    # ``_resolve_resolution`` does in core/commands.py, kept inline so
    # the route doesn't reach into private helpers.
    state = request.app.state.trcc.dispatch(DeviceState(key=key))
    # Handshake resolution wins; the registry's native size is the fallback
    # for an attached-but-not-yet-handshaken device.  `resolution is None`
    # is what distinguishes those — a 0x0 panel would be a real answer.
    if not state.ok:
        w, h = 0, 0
    elif state.resolution is not None:
        w, h = state.resolution
    else:
        w, h = state.native_resolution

    return CreateThemeResponse(
        ok=True,
        key=key,
        animated=animated,
        resolution=f"{w}x{h}",
        message=(f"theme created from {bg_name} "
                 f"({'video' if animated else 'image'}, {w}x{h})"),
    )


@router.post("/color")
def send_color(key: str, body: ColorRequest, request: Request) -> SendResult:
    """Push a solid-color frame to a connected LCD device."""
    log.info(
        "api POST /devices/{key}/display/color: key=%s r=%s g=%s b=%s",
        key, body.r, body.g, body.b,
    )
    result = request.app.state.trcc.dispatch(
        SendColor(key=key, r=body.r, g=body.g, b=body.b),
    )
    http_error_if_failed(result)
    return result


@router.post("/sleep")
def sleep(key: str, request: Request) -> SendResult:
    """Blank the panel so it goes dark — the shutdown / turn-off action.

    Sends a solid-black frame (LCD) or an all-off payload (LED), the same
    Command the GUI + daemon fire at PC shutdown.
    """
    log.info("api POST /devices/{key}/display/sleep: key=%s", key)
    result = request.app.state.trcc.dispatch(SleepDevice(key=key))
    http_error_if_failed(result)
    return result


@router.post("/reset")
def reset(key: str, request: Request) -> SendResult:
    """Reset the display — stop any active video, then send a solid red frame.

    Mirrors legacy's reset: blanks the panel to a known state regardless
    of what was playing.  ``StopVideo`` is best-effort (idempotent when
    nothing is playing); the red frame is the reported result.
    """
    log.info("api POST /devices/{key}/display/reset: key=%s", key)
    trcc = request.app.state.trcc
    trcc.dispatch(StopVideo(key=key))   # best-effort — ok if nothing playing
    result = trcc.dispatch(SendColor(key=key, r=255, g=0, b=0))
    http_error_if_failed(result)
    return result


@router.post("/tick")
def tick(key: str, request: Request) -> RenderResult:
    """Render the active theme with live sensors + send one frame.

    Stateless — the caller (scheduled job, cron, client-side timer)
    polls this at AppSettings.refresh_interval_s or whatever cadence
    they like.  Uses the scene cache so ticks are cheap.

    Self-primes and advances video the way the CLI ``display play`` loop does,
    so a headless poller Just Works (#239): (1) ``RestoreDeviceState`` is
    idempotent — a no-op once a theme is active — so the first tick can't fail
    "No active theme"; (2) ``TickDisplay`` advances a play-video override's
    cursor before rendering, so successive ticks animate it.

    The restore stays HERE rather than inside ``TickDisplay``: a stateless
    poller may arrive with nothing loaded, but the GUI's animation timer would
    otherwise pay a restore 15-30 times a second.  Self-priming is this
    route's concern; advancing is the Command's.
    """
    log.info("api POST /devices/{key}/display/tick: key=%s", key)
    app = request.app.state.trcc
    app.dispatch(RestoreDeviceState(key=key))
    result = app.dispatch(TickDisplay(key=key))
    http_error_if_failed(result)
    return result



@router.post("/restore-theme", response_model=ThemeResponse)
def restore_theme(key: str, request: Request) -> ThemeResponse:
    """Restore the device's display state (persisted theme + background).

    Dispatches the unified ``RestoreDeviceState`` — persisted theme, or the
    first available theme when none is saved, then replays the persisted
    background video — the same path the GUI/CLI use at their display entry.
    """
    log.info("api POST /devices/{key}/display/restore-theme: key=%s", key)
    result = request.app.state.trcc.dispatch(RestoreDeviceState(key=key))
    http_error_if_failed(result)
    return to_theme_response(result)


@router.get("/snapshot")
def snapshot(key: str, request: Request) -> LcdSnapshotResult:
    """Return the persisted LCD state for one device."""
    log.info("api GET /devices/{key}/display/snapshot: key=%s", key)
    result = request.app.state.trcc.dispatch(LcdSnapshot(key=key))
    http_error_if_failed(result)
    return result


@router.post("/slideshow")
def slideshow_toggle(key: str, body: SlideshowToggleRequest,
                     request: Request) -> SlideshowResult:
    """Turn the device's slideshow on / off."""
    log.info(
        "api POST /devices/{key}/display/slideshow: key=%s enabled=%s",
        key, body.enabled,
    )
    result = request.app.state.trcc.dispatch(
        SetSlideshow(key=key, enabled=body.enabled),
    )
    http_error_if_failed(result)
    return result


@router.put("/slideshow")
def slideshow_configure(key: str, body: SlideshowConfigureRequest,
                        request: Request) -> SlideshowResult:
    """Set the theme list + interval for a device's slideshow."""
    log.info(
        "api PUT /devices/{key}/display/slideshow: key=%s themes=%s "
        "interval_s=%s",
        key, body.themes, body.interval_s,
    )
    result = request.app.state.trcc.dispatch(ConfigureSlideshow(
        key=key,
        themes=tuple(body.themes) if body.themes is not None else None,
        interval_s=body.interval_s,
    ))
    http_error_if_failed(result)
    return result


@router.post("/keepalive")
def keepalive(key: str, body: KeepaliveRequest,
              request: Request) -> KeepaliveResult:
    """Run a keepalive burst (resend the last frame N times)."""
    log.info(
        "api POST /devices/{key}/display/keepalive: key=%s count=%s "
        "interval_s=%s metric_interval_s=%s",
        key, body.count, body.interval_s, body.metric_interval_s,
    )
    result = request.app.state.trcc.dispatch(KeepAliveLoop(
        key=key,
        count=body.count,
        interval_s=body.interval_s,
        metric_interval_s=body.metric_interval_s,
    ))
    http_error_if_failed(result)
    return result


@router.post("/background-mode")
def background_mode(key: str, body: BackgroundModeRequest,
                    request: Request) -> BackgroundModeResult:
    """Pick what fills the LCD behind overlays (theme/color/transparent)."""
    log.info(
        "api POST /devices/{key}/display/background-mode: key=%s mode=%s",
        key, body.mode,
    )
    result = request.app.state.trcc.dispatch(
        SetBackgroundMode(key=key, mode=body.mode),
    )
    http_error_if_failed(result)
    return result


@router.post("/overlay-background")
def overlay_background(key: str, body: OverlayBackgroundRequest,
                       request: Request) -> OverlayBackgroundResult:
    """Set the solid background color used when background-mode=color."""
    log.info(
        "api POST /devices/{key}/display/overlay-background: key=%s color=%s",
        key, body.color,
    )
    result = request.app.state.trcc.dispatch(
        SetOverlayBackground(key=key, color=body.color),
    )
    http_error_if_failed(result)
    return result


# ── Overlay element CRUD ─────────────────────────────────────────────


@router.post("/overlay-elements")
def overlay_add(key: str, body: OverlayElementAddRequest,
                request: Request) -> OverlayElementResult:
    """Add a user-edited overlay element."""
    log.info(
        "api POST /devices/{key}/display/overlay-elements: key=%s type=%s "
        "metric=%s element_id=%s",
        key, body.type, body.metric, body.element_id,
    )
    result = request.app.state.trcc.dispatch(AddOverlayElement(
        key=key, type=body.type, x=body.x, y=body.y,
        color=body.color, size=body.size,
        bold=body.bold, italic=body.italic,
        text=body.text, metric=body.metric, format=body.format,
        source=body.source, element_id=body.element_id,
    ))
    http_error_if_failed(result)
    return result


@router.patch("/overlay-elements/{element_id}")
def overlay_update(key: str, element_id: str,
                   body: OverlayElementUpdateRequest,
                   request: Request) -> OverlayElementResult:
    """Mutate fields on an existing user-edited overlay element."""
    log.info(
        "api PATCH /devices/{key}/display/overlay-elements/{element_id}: "
        "key=%s element_id=%s",
        key, element_id,
    )
    result = request.app.state.trcc.dispatch(UpdateOverlayElement(
        key=key, element_id=element_id,
        x=body.x, y=body.y, color=body.color, size=body.size,
        bold=body.bold, italic=body.italic,
        text=body.text, metric=body.metric, format=body.format,
        show_unit=body.show_unit, source=body.source,
    ))
    http_error_if_failed(result)
    return result


@router.delete("/overlay-elements/{element_id}")
def overlay_delete(key: str, element_id: str,
                   request: Request) -> OverlayElementDeleteResult:
    """Remove an overlay element by id."""
    log.info(
        "api DELETE /devices/{key}/display/overlay-elements/{element_id}: "
        "key=%s element_id=%s",
        key, element_id,
    )
    result = request.app.state.trcc.dispatch(
        DeleteOverlayElement(key=key, element_id=element_id),
    )
    http_error_if_failed(result)
    return result


@router.post("/overlay-elements/{element_id}/flash")
def overlay_flash(key: str, element_id: str,
                  body: OverlayFlashRequest,
                  request: Request) -> OverlayElementResult:
    """Briefly highlight an overlay element in the GUI."""
    log.info(
        "api POST /devices/{key}/display/overlay-elements/{element_id}/flash: "
        "key=%s element_id=%s duration_ms=%s",
        key, element_id, body.duration_ms,
    )
    result = request.app.state.trcc.dispatch(FlashOverlayElement(
        key=key, element_id=element_id, duration_ms=body.duration_ms,
    ))
    http_error_if_failed(result)
    return result


@router.put("/overlay-elements")
def overlay_set_config(key: str, body: OverlayConfigRequest,
                       request: Request) -> OverlayConfigResult:
    """Bulk replace the user-overlay element list."""
    log.info(
        "api PUT /devices/{key}/display/overlay-elements: key=%s count=%s",
        key, len(body.elements),
    )
    elements = tuple(e.model_dump() for e in body.elements)
    result = request.app.state.trcc.dispatch(
        SetOverlayConfig(key=key, elements=elements),
    )
    http_error_if_failed(result)
    return result


@router.post("/pause-video")
def pause_video(key: str, body: PauseVideoRequest,
                request: Request) -> PauseVideoResult:
    """Pause / resume video playback."""
    log.info(
        "api POST /devices/{key}/display/pause-video: key=%s paused=%s",
        key, body.paused,
    )
    result = request.app.state.trcc.dispatch(
        PauseVideo(key=key, paused=body.paused),
    )
    http_error_if_failed(result)
    return result


@router.post("/seek-video")
def seek_video(key: str, body: SeekVideoRequest,
               request: Request) -> SeekVideoResult:
    """Jump to a specific frame."""
    log.info(
        "api POST /devices/{key}/display/seek-video: key=%s frame=%s",
        key, body.frame,
    )
    result = request.app.state.trcc.dispatch(
        SeekVideo(key=key, frame=body.frame),
    )
    http_error_if_failed(result)
    return result


@router.post("/loop-video")
def loop_video(key: str, body: LoopVideoRequest,
               request: Request) -> LoopVideoResult:
    """Toggle whether playback wraps or sticks at the last frame."""
    log.info(
        "api POST /devices/{key}/display/loop-video: key=%s loop=%s",
        key, body.loop,
    )
    result = request.app.state.trcc.dispatch(
        LoopVideo(key=key, loop=body.loop),
    )
    http_error_if_failed(result)
    return result


@router.post("/upload-mask")
def upload_mask(key: str, body: MaskUploadRequest,
                request: Request) -> MaskUploadResult:
    """Upload a mask file (server-side path) + apply it."""
    log.info(
        "api POST /devices/{key}/display/upload-mask: key=%s source=%s",
        key, body.source,
    )
    result = request.app.state.trcc.dispatch(
        UploadCustomMask(key=key, source=Path(body.source)),
    )
    http_error_if_failed(result)
    return result


# ── Meta routes (no device key in path) ──────────────────────────────


meta_router = APIRouter(prefix="/display", tags=["display"])


@meta_router.get("/masks")
def list_masks(
    request: Request,
    key: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> MasksListResult:
    """List masks for a device resolution.

    Pass either ``?key=vid:pid`` (resolved through the connected
    device's handshake profile) or ``?width=W&height=H`` for an
    explicit override.
    """
    log.info(
        "api GET /display/masks: key=%s width=%s height=%s",
        key, width, height,
    )
    resolution: tuple[int, int] | None = None
    if key is not None:
        state = request.app.state.trcc.dispatch(DeviceState(key=key))
        if not state.ok or state.resolution is None:
            return MasksListResult(
                ok=False, directory="", masks=[],
                message=(f"Device {key} not connected — connect first "
                         "so we know the target resolution"),
            )
        resolution = state.resolution
    elif width is not None and height is not None:
        resolution = (width, height)
    result = request.app.state.trcc.dispatch(ListMasks(resolution=resolution))
    http_error_if_failed(result)
    return result
