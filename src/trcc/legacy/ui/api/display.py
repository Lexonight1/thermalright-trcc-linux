"""LCD display control endpoints — brightness, rotation, color, mask, overlay, preview."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import Response

from trcc.legacy.ui.api.models import (
    BrightnessRequest,
    HexColorRequest,
    RotationRequest,
    ScreencastRequest,
    ScreencastStatusResponse,
    SplitRequest,
    VideoStatusResponse,
    dispatch_result,
    parse_hex_or_400,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/display", tags=["display"])


def _get_display():
    """Get the active device, raise 409 if not connected."""
    from trcc.legacy.ui.api import _device_dispatcher

    if not _device_dispatcher or not _device_dispatcher.connected:
        raise HTTPException(status_code=409, detail="No device selected. POST /devices/{id}/select first.")
    return _device_dispatcher



def _result(result) -> dict:
    """Return asdict(result) or raise HTTPException on failure."""
    from dataclasses import asdict
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error or 'failed')
    return asdict(result)


@router.post("/color")
def set_color(body: HexColorRequest, lcd: int = 0) -> dict:
    """Send solid color to LCD."""
    from trcc.legacy._boot import trcc
    from trcc.legacy.ui.api import stop_overlay_loop, stop_video_playback

    stop_video_playback()
    stop_overlay_loop()
    r, g, b = parse_hex_or_400(body.hex)
    return _result(trcc().lcd.send_color(lcd, r, g, b))


@router.post("/brightness")
def set_brightness(body: BrightnessRequest, lcd: int = 0) -> dict:
    """Set display brightness (1=25%, 2=50%, 3=100%). Persists to config."""
    from trcc.legacy._boot import trcc
    return _result(trcc().lcd.set_brightness(lcd, body.level))


@router.post("/rotation")
def set_rotation(body: RotationRequest, lcd: int = 0) -> dict:
    """Set display rotation (0, 90, 180, 270). Persists to config."""
    from trcc.legacy._boot import trcc
    return _result(trcc().lcd.set_rotation(lcd, body.degrees))


@router.post("/split")
def set_split(body: SplitRequest, lcd: int = 0) -> dict:
    """Set split mode (0=off, 1-3=Dynamic Island). Persists to config."""
    from trcc.legacy._boot import trcc
    return _result(trcc().lcd.set_split_mode(lcd, body.mode))


@router.post("/reset")
def reset_display(lcd: int = 0) -> dict:
    """Reset device by sending solid red frame."""
    from trcc.legacy._boot import trcc
    from trcc.legacy.ui.api import stop_overlay_loop, stop_video_playback

    stop_video_playback()
    stop_overlay_loop()
    return _result(trcc().lcd.reset(lcd))


@router.post("/mask")
async def load_mask(image: UploadFile, lcd: int = 0) -> dict:
    """Upload and apply mask overlay (PNG)."""
    import tempfile
    from pathlib import Path

    from trcc.legacy._boot import trcc

    data = await image.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Mask image exceeds 10 MB limit")

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        return _result(trcc().lcd.apply_mask(lcd, tmp_path))
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/overlay")
async def render_overlay(dc_path: str, send: bool = True) -> dict:
    """Render overlay from DC config path and optionally send to device."""
    import os

    from trcc.legacy._boot import trcc as _trcc

    # Validate path is within the data directory — prevent traversal

    if '\0' in dc_path:
        raise HTTPException(status_code=400, detail="Invalid overlay path")
    allowed_dir = os.path.realpath(str(_trcc().settings.user_data_dir))
    # Resolve to canonical path — handles both absolute and relative input
    safe_path = os.path.realpath(os.path.join(allowed_dir, dc_path))
    if not safe_path.startswith(allowed_dir + os.sep) and safe_path != allowed_dir:
        raise HTTPException(status_code=400, detail="Invalid overlay path")


    _get_display()
    result = _get_display().render_overlay_from_dc(safe_path, send=send)
    return dispatch_result(result)


@router.get("/status")
def display_status(lcd: int = 0) -> dict:
    """Backward-compat status — connected + resolution + device path.

    New callers should use /display/snapshot for the full typed state.
    """
    from trcc.legacy._boot import trcc
    snap = trcc().lcd.snapshot(lcd)
    # pylint: disable=protected-access
    dev = trcc()._lcd_devices[lcd] if lcd < len(trcc()._lcd_devices) else None
    device_path = dev.device_path if dev else None
    return {
        "connected": snap.connected,
        "resolution": list(snap.resolution),
        "device_path": device_path,
    }


@router.get("/snapshot")
def display_snapshot(lcd: int = 0) -> dict:
    """Full LCD snapshot via Trcc — all state in one call."""
    from dataclasses import asdict

    from trcc.legacy._boot import trcc
    return asdict(trcc().lcd.snapshot(lcd))


# ── Video playback endpoints ──────────────────────────────────────────


@router.post("/video/stop")
def video_stop() -> dict:
    """Stop background video playback."""
    from trcc.legacy.ui.api import stop_video_playback

    stop_video_playback()
    return {"success": True, "message": "Video playback stopped"}


@router.post("/video/pause")
def video_pause() -> dict:
    """Toggle pause on background video playback."""
    from trcc.legacy.ui.api import _media_service, pause_video_playback

    if not _media_service:
        raise HTTPException(status_code=409, detail="No video playing")
    pause_video_playback()
    return {"success": True, "paused": not _media_service.is_playing}


@router.get("/video/status")
def video_status() -> VideoStatusResponse:
    """Get current video playback state."""
    from trcc.legacy.core.models import PlaybackState
    from trcc.legacy.ui.api import _media_service

    if not _media_service:
        return VideoStatusResponse()

    state = _media_service.state
    return VideoStatusResponse(
        playing=state.state == PlaybackState.PLAYING,
        paused=state.state == PlaybackState.PAUSED,
        progress=state.progress,
        current_time=state.current_time_str,
        total_time=state.total_time_str,
        fps=state.fps,
        source=str(_media_service.source_path or ""),
        loop=state.loop,
    )


# ── Test endpoint ─────────────────────────────────────────────────────


@router.post("/test")
def test_display() -> dict:
    """Send a color cycle test to the LCD device.

    Cycles through 7 colors (red, green, blue, yellow, magenta, cyan, white),
    sending each as a solid frame with a 1-second pause between them.
    """
    import time

    import trcc.legacy.ui.api as api

    lcd = _get_display()
    api.stop_video_playback()
    api.stop_overlay_loop()

    w, h = lcd.resolution  # type: ignore[union-attr]

    colors = [
        (255, 0, 0, "Red"),
        (0, 255, 0, "Green"),
        (0, 0, 255, "Blue"),
        (255, 255, 0, "Yellow"),
        (255, 0, 255, "Magenta"),
        (0, 255, 255, "Cyan"),
        (255, 255, 255, "White"),
    ]

    from trcc.legacy.services import ImageService

    lcd = _get_display()
    for r, g, b, _name in colors:
        img = ImageService.solid_color(r, g, b, w, h)
        lcd.send_color(r, g, b)
        time.sleep(1)

    # Update preview with last frame
    api.set_current_image(img)  # type: ignore[possibly-undefined]

    return {"success": True, "message": f"Test complete — cycled {len(colors)} colors on {w}x{h}"}


# ── Upload endpoint ───────────────────────────────────────────────────

_ALLOWED_UPLOAD_SUFFIXES = frozenset({
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.mp4', '.zt', '.webm', '.avi', '.mkv', '.mov',
})


@router.post("/upload")
async def upload_file(file: UploadFile) -> dict:
    """Upload an image or video file to the server for use with create-theme.

    Returns the server-side path to pass as ``background`` or ``mask`` in
    subsequent ``POST /display/create-theme`` calls.
    """
    from trcc.legacy._boot import trcc as _trcc

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(_ALLOWED_UPLOAD_SUFFIXES)}",
        )

    uploads_dir = _trcc().settings.user_data_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    dest = uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    content = await file.read()
    dest.write_bytes(content)

    log.info("Uploaded %s → %s (%d bytes)", file.filename, dest, len(content))
    return {"path": str(dest), "filename": dest.name, "size": len(content)}


# ── Create-theme endpoint ─────────────────────────────────────────────

_VIDEO_SUFFIXES = frozenset({'.mp4', '.zt', '.webm', '.avi', '.mkv'})


def _is_animated(path: Path) -> bool:
    """Return True if path is a video or an animated GIF (nb_frames > 1)."""
    suffix = path.suffix.lower()
    if suffix in _VIDEO_SUFFIXES:
        return True
    if suffix == '.gif':
        from trcc.legacy.adapters.infra.media_player import is_animated_gif
        try:
            return is_animated_gif(path)
        except Exception as e:
            log.warning("GIF animation check failed for %s: %s", path, e)
    return False


@router.post("/create-theme")
async def create_theme(
    background: UploadFile,
    mask: UploadFile | None = None,
    overlay: UploadFile | None = None,
    metric: list[str] = Form(default=[]),
    loop: bool = Form(True),
    font_size: int = Form(14),
    color: str = Form("ffffff"),
    font: str = Form("Microsoft YaHei"),
    font_style: str = Form("regular"),
    temp_unit: int = Form(0),
    time_format: int = Form(0),
    date_format: int = Form(0),
) -> dict:
    """Send a custom theme to the LCD device via file upload.

    Upload ``background`` (image or video), optional ``mask`` (PNG), and
    optional ``overlay`` (JSON overlay config file) as multipart form files.
    Alternatively use repeatable ``metric`` form fields instead of an overlay file.
    Auto-detects animated backgrounds (video, animated GIF).

    ``metric`` is repeatable: ``metric=cpu_temp:10,20`` ``metric=time:150,10:ffffff:24``

    Metric spec format: ``key:x,y[:color[:size[:font[:style]]]]``

    ``overlay`` JSON format: ``{"elements": [{"key": "cpu_temp", "x": 10, "y": 20, ...}]}``
    """
    import trcc.legacy.ui.api as api
    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.core.models import build_overlay_config
    from trcc.legacy.services import ImageService

    lcd = _get_display()
    api.stop_video_playback()
    api.stop_overlay_loop()

    # Save uploads to ~/.trcc/uploads/. Path() cast keeps types tight
    # past the Trcc.settings → Any boundary.
    uploads_dir = Path(_trcc().settings.user_data_dir) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    bg_suffix = Path(background.filename or "").suffix.lower() or ".jpg"
    bg_path = uploads_dir / f"{uuid.uuid4().hex}{bg_suffix}"
    bg_path.write_bytes(await background.read())

    mask_path: Path | None = None
    if mask is not None:
        mask_path = uploads_dir / f"{uuid.uuid4().hex}.png"
        mask_path.write_bytes(await mask.read())

    w, h = lcd.resolution  # type: ignore[union-attr]
    animated = _is_animated(bg_path)

    overlay_config = None
    if overlay is not None:
        # Uploaded JSON overlay config takes precedence over metric strings
        try:
            overlay_config = json.loads(await overlay.read())
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid overlay JSON: {e}") from e
    elif metric:
        try:
            overlay_config = build_overlay_config(
                metric,
                default_color=color,
                default_font_size=font_size,
                default_font=font,
                default_style=font_style,
                temp_unit=temp_unit,
                time_format=time_format,
                date_format=date_format,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    if animated:
        ok = api.start_video_playback(str(bg_path), w, h, loop=loop)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to start video playback")
        return {"success": True, "animated": True, "loop": loop, "resolution": f"{w}x{h}"}

    # Static image
    img = ImageService.open_and_resize(bg_path, w, h)
    if img is None:
        raise HTTPException(status_code=400, detail="Failed to open background image")

    if mask_path:
        mask_result = _get_display().load_mask_standalone(str(mask_path))
        if not mask_result.get("success"):
            raise HTTPException(status_code=400, detail=mask_result.get("error", "Mask load failed"))
        img = mask_result.get("image", img)

    lcd_device = _get_display()

    # Load background into DisplayService state
    lcd_device.load_image(bg_path)

    if overlay_config:
        lcd_device.set_config(overlay_config)
        lcd_device.enable_overlay(True)
        result = lcd_device.render_and_send()
        frame = result.get("image", img)
        api.set_current_image(frame)
    else:
        lcd_device.send(img)
        api.set_current_image(img)

    return {"success": True, "animated": False, "resolution": f"{w}x{h}"}


# ── Screencast endpoints ──────────────────────────────────────────────


@router.post("/screencast/start")
def screencast_start(body: ScreencastRequest) -> dict:
    """Start streaming screen capture to LCD device.

    Auto-detects backend: ffmpeg x11grab on X11, PipeWire on Wayland.
    Mutually exclusive with video playback and overlay loops.
    """
    import trcc.legacy.ui.api as api

    _get_display()
    result = api.start_screencast(body.x, body.y, body.w, body.h, body.fps)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Screencast failed"))
    return result


@router.post("/screencast/stop")
def screencast_stop() -> dict:
    """Stop screen capture streaming."""
    from trcc.legacy.ui.api import stop_screencast

    stop_screencast()
    return {"success": True, "message": "Screencast stopped"}


@router.get("/screencast/status")
def screencast_status() -> ScreencastStatusResponse:
    """Check current screencast state."""
    from trcc.legacy.ui.api import _screencast_frames, _screencast_params, _screencast_stop_event

    running = (_screencast_stop_event is not None
               and not _screencast_stop_event.is_set())
    params = _screencast_params or {}
    return ScreencastStatusResponse(
        running=running,
        backend=params.get("backend", ""),
        fps=params.get("fps", 0),
        region={k: params[k] for k in ("x", "y", "w", "h") if k in params},
        frames=_screencast_frames,
    )


# ── Preview helpers ───────────────────────────────────────────────────


def _encode_frame(frame: object, fmt: str = 'JPEG', quality: int = 85) -> bytes | None:
    """Encode a frame (QImage or raw bytes) to image bytes."""
    from PySide6.QtGui import QImage

    if isinstance(frame, bytes):
        return frame  # Already encoded (IPC path)
    if isinstance(frame, QImage):
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        buf = QByteArray()
        qbuf = QBuffer(buf)
        qbuf.open(QIODevice.OpenModeFlag.ReadWrite)  # PNG needs seek
        frame.save(qbuf, fmt, quality)  # type: ignore[call-overload]
        qbuf.close()
        return bytes(buf.data())
    return None


def _get_lcd_frame():
    """Return the current in-process LCD frame (set by on_frame_sent capture)."""
    from trcc.legacy.ui.api import _current_image
    return _current_image


# ── Preview endpoints ─────────────────────────────────────────────────


@router.get("/preview")
def display_preview() -> Response:
    """Return the current LCD frame as a PNG image."""
    frame = _get_lcd_frame()
    if frame is None:
        raise HTTPException(status_code=503, detail="No image available")

    try:
        data = _encode_frame(frame, fmt='PNG')
    except Exception:
        log.warning("Preview encode failed (frame type: %s)", type(frame).__name__, exc_info=True)
        raise HTTPException(status_code=503, detail="Frame encode failed") from None
    if data is None:
        raise HTTPException(status_code=503, detail="Frame encode failed")
    return Response(content=data, media_type="image/png")


@router.websocket("/preview/stream")
async def preview_stream(websocket: WebSocket):
    """Live JPEG stream of the current LCD frame — like a screen capture.

    Reads the LCD frame at a steady framerate and sends it as binary JPEG.
    Frames come from the on_frame_sent capture (or, in daemon mode, from
    a future TrccProxy event subscription — not wired yet).

    Auth: ``?token=`` query param (checked against configured API token).
    Client control: send JSON ``{"fps": N}``, ``{"quality": N}``, ``{"pause": bool}``.
    """
    from trcc.legacy.ui.api import _api_token

    # ── Auth ──────────────────────────────────────────────────────────
    if _api_token:
        query_token = websocket.query_params.get("token", "")
        if not hmac.compare_digest(query_token, _api_token):
            await websocket.close(code=4001, reason="Unauthorized")
            return

    await websocket.accept()

    fps = 10
    quality = 85
    paused = False

    try:
        while True:
            # ── Check for client control messages (non-blocking) ──────
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=1.0 / fps,
                )
                try:
                    msg = json.loads(raw)
                    if "fps" in msg:
                        fps = max(1, min(30, int(msg["fps"])))
                    if "quality" in msg:
                        quality = max(10, min(100, int(msg["quality"])))
                    if "pause" in msg:
                        paused = bool(msg["pause"])
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass
                continue  # restart loop after processing message
            except asyncio.TimeoutError:
                pass  # no message — proceed to frame read

            if paused:
                continue

            # ── Read current frame directly from source ───────────────
            from trcc.legacy.ui.api import _current_image
            frame = _current_image
            if frame is None:
                continue

            # ── Encode and send ───────────────────────────────────────
            if (data := _encode_frame(frame, fmt='JPEG', quality=quality)):
                await websocket.send_bytes(data)

    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("Preview stream closed", exc_info=True)
