"""FastAPI REST API — Driving adapter for headless/remote control.

Package structure mirrors cli/ — one module per domain:
    __init__.py — app, auth middleware, health, shared state
    models.py   — Pydantic request/response models
    devices.py  — device detection, selection, image send
    display.py  — LCD display settings (brightness, rotation, color, etc.)
    i18n.py     — language listing and selection
    led.py      — LED RGB control (color, mode, zones, segments)
    themes.py   — theme listing, load, save, import
    system.py   — system metrics, diagnostic report

Security:
    - Localhost-only by default (bind 127.0.0.1)
    - Optional token auth via --token flag (X-API-Token header)
    - Optional TLS via --tls flag (auto-generates self-signed cert)
    - 10 MB upload limit with image format validation
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trcc.core.device.lcd import LCDDevice

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from trcc.__version__ import __version__
from trcc.adapters.infra.dc_config import DcConfig
from trcc.adapters.infra.dc_parser import load_config_json
from trcc.services import MediaService, OverlayService
from trcc.services.system import SystemService

log = logging.getLogger(__name__)

# ── App ────────────────────────────────────────────────────────────────

app = FastAPI(title="TRCC Linux", version=__version__)

# ── Shared state ───────────────────────────────────────────────────────

# Device registry now lives on the Trcc singleton (api._boot.trcc()).
# Endpoints look up devices via Trcc; this module owns only the
# in-flight per-process state below.

# System service — None until configure_app() is called by trcc serve
_system_svc: SystemService | None = None

# Lazy-initialized device (set when device is selected)
_device_dispatcher: LCDDevice | None = None

# Last frame sent to LCD — updated by display/theme endpoints for preview
_current_image = None  # QImage | None


def set_current_image(img) -> None:
    """Update the tracked LCD frame (called by display/theme endpoints)."""
    global _current_image
    _current_image = img


def configure_app() -> None:
    """Initialize platform, renderer, system service, and connected devices.

    Called once by ``trcc serve`` before uvicorn starts. Not called at
    import time so tests can import the module without triggering
    side effects.
    """
    global _system_svc
    from trcc._boot import trcc as _boot_trcc
    from trcc.core.events import Topic

    t = _boot_trcc(discover_now=True)
    t.events.subscribe(
        Topic.BOOTSTRAP_PROGRESS, lambda msg: print(msg, flush=True))
    _system_svc = t._system_svc


# ── Video playback (background thread) ───────────────────────────────

_media_service: MediaService | None = None
_video_thread: threading.Thread | None = None
_video_stop_event: threading.Event | None = None


def start_video_playback(
    video_path: str, width: int, height: int, *, loop: bool = True,
) -> bool:
    """Start background video playback — pumps frames to LCD and _current_image.

    Uses MediaService to decode frames, DeviceService to send to LCD,
    and set_current_image() to feed the WebSocket preview stream.
    """
    global _media_service, _video_thread, _video_stop_event

    stop_video_playback()
    stop_screencast()

    from trcc.adapters.infra.media_player import ThemeZtDecoder, VideoDecoder
    media = MediaService(
        video_decoder_cls=VideoDecoder,
        zt_decoder_cls=ThemeZtDecoder,
    )
    media.set_target_size(width, height)
    if not media.load(Path(video_path)):
        return False

    media._state.loop = loop
    media.play()

    _media_service = media
    _video_stop_event = threading.Event()
    stop_event = _video_stop_event  # Local ref for closure

    lcd = _device_dispatcher  # capture for closure

    def _pump() -> None:
        interval = media.frame_interval_ms / 1000.0
        while not stop_event.is_set() and media.is_playing:
            frame, should_send, _ = media.tick()
            if frame is None:
                break
            if should_send and lcd is not None:
                lcd.send_frame(frame)
            stop_event.wait(interval)

    _video_thread = threading.Thread(target=_pump, daemon=True, name="api-video")
    _video_thread.start()
    log.info("Video playback started: %s (%dx%d)", video_path, width, height)
    return True


def stop_video_playback() -> None:
    """Stop background video playback if running."""
    global _media_service, _video_thread, _video_stop_event

    if _video_stop_event:
        _video_stop_event.set()
    if _video_thread and _video_thread.is_alive():
        _video_thread.join(timeout=2)
    if _media_service:
        _media_service.stop()
    _media_service = None
    _video_thread = None
    _video_stop_event = None


def pause_video_playback() -> None:
    """Toggle pause on background video playback."""
    if _media_service:
        _media_service.toggle()


# ── Overlay metrics loop (background thread for static themes) ────────

_overlay_svc: OverlayService | None = None
_overlay_thread: threading.Thread | None = None
_overlay_stop_event: threading.Event | None = None


def start_overlay_loop(
    background, dc_path: str, width: int, height: int,
) -> bool:
    """Start background overlay rendering — polls metrics, re-renders, sends to LCD.

    For static themes with config1.dc overlay configs. Updates _current_image
    so the WebSocket preview stream shows live metrics.
    """
    global _overlay_svc, _overlay_thread, _overlay_stop_event

    stop_overlay_loop()
    stop_screencast()

    from trcc.services.image import ImageService
    overlay = OverlayService(
        width, height, renderer=ImageService.renderer(),
        load_config_json_fn=load_config_json,
        dc_config_cls=DcConfig,
    )
    overlay.set_background(background)
    overlay.load_from_dc(Path(dc_path))
    overlay.enabled = True

    _overlay_svc = overlay
    _overlay_stop_event = threading.Event()
    stop_event = _overlay_stop_event
    lcd = _device_dispatcher  # capture for closure

    def _loop() -> None:
        while not stop_event.is_set():
            if _system_svc is None:
                stop_event.wait(2.0)
                continue
            metrics = _system_svc.all_metrics
            if overlay.would_change(metrics):
                overlay.update_metrics(metrics)
                frame = overlay.render(metrics=metrics)
                if frame is not None and lcd is not None:
                    lcd.send_frame(frame)
            stop_event.wait(2.0)

    _overlay_thread = threading.Thread(target=_loop, daemon=True, name="api-overlay")
    _overlay_thread.start()
    log.info("Overlay loop started: %s (%dx%d)", dc_path, width, height)
    return True


def stop_overlay_loop() -> None:
    """Stop background overlay rendering if running."""
    global _overlay_svc, _overlay_thread, _overlay_stop_event

    if _overlay_stop_event:
        _overlay_stop_event.set()
    if _overlay_thread and _overlay_thread.is_alive():
        _overlay_thread.join(timeout=2)
    _overlay_svc = None
    _overlay_thread = None
    _overlay_stop_event = None


# ── Static frame keepalive loop (bulk/LY devices don't retain frames) ──

_keepalive_thread: threading.Thread | None = None
_keepalive_stop_event: threading.Event | None = None


def start_keepalive_loop(image, width: int, height: int) -> bool:
    """Re-send a static frame every 150 ms to keep bulk/LY displays alive."""
    global _keepalive_thread, _keepalive_stop_event

    stop_keepalive_loop()

    _keepalive_stop_event = threading.Event()
    stop_event = _keepalive_stop_event
    lcd = _device_dispatcher  # capture for closure

    def _loop() -> None:
        while not stop_event.is_set():
            if lcd is not None:
                lcd.send_frame(image)
            stop_event.wait(0.150)

    _keepalive_thread = threading.Thread(target=_loop, daemon=True, name="api-keepalive")
    _keepalive_thread.start()
    log.info("Keepalive loop started (%dx%d)", width, height)
    return True


def stop_keepalive_loop() -> None:
    """Stop background frame keepalive if running."""
    global _keepalive_thread, _keepalive_stop_event

    if _keepalive_stop_event:
        _keepalive_stop_event.set()
    if _keepalive_thread and _keepalive_thread.is_alive():
        _keepalive_thread.join(timeout=2)
    _keepalive_thread = None
    _keepalive_stop_event = None


# ── Screencast (background thread — X11 or PipeWire) ──────────────────

_screencast_thread: threading.Thread | None = None
_screencast_stop_event: threading.Event | None = None
_screencast_proc: object | None = None  # subprocess.Popen (X11)
_screencast_cast: object | None = None  # PipeWireScreenCast (Wayland)
_screencast_frames: int = 0
_screencast_params: dict | None = None


def _is_wayland() -> bool:
    return (os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
            or bool(os.environ.get('WAYLAND_DISPLAY')))


def start_screencast(
    x: int = 0, y: int = 0, w: int = 0, h: int = 0, fps: int = 10,
) -> dict:
    """Start background screen capture — pumps frames to LCD and preview.

    Auto-detects backend: ffmpeg x11grab on X11, PipeWire on Wayland.
    """
    global _screencast_thread, _screencast_stop_event
    global _screencast_proc, _screencast_cast
    global _screencast_frames, _screencast_params

    # Boundary validation — all values are integers from Pydantic,
    # but clamp explicitly so static analysis can verify safety.
    x = max(0, int(x))
    y = max(0, int(y))
    w = max(0, min(int(w), 7680))
    h = max(0, min(int(h), 4320))
    fps = max(1, min(int(fps), 60))

    stop_screencast()
    stop_video_playback()
    stop_overlay_loop()
    stop_keepalive_loop()

    if not _device_dispatcher or not _device_dispatcher.connected:
        return {"success": False, "error": "No device connected"}

    lcd = _device_dispatcher  # capture for closures
    lcd_w, lcd_h = lcd.resolution  # type: ignore[union-attr]
    _screencast_stop_event = threading.Event()
    stop_event = _screencast_stop_event
    _screencast_frames = 0
    _screencast_params = {"x": x, "y": y, "w": w, "h": h, "fps": fps}

    # Try X11 (ffmpeg x11grab) — works on X11 and XWayland
    display = os.environ.get('DISPLAY')
    if display:
        import shutil
        if not shutil.which('ffmpeg'):
            return {"success": False, "error": "ffmpeg not found"}

        from trcc._boot import trcc
        capture = trcc().os.screen_capture_params(x, y, w, h)
        if capture is None:
            return {"success": False, "error": "Screencast not supported on this platform"}
        fmt, inp, region_args = capture

        import subprocess
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-f', fmt, '-framerate', str(fps),
            *region_args,
            '-i', inp,
            '-vf', f'scale={lcd_w}:{lcd_h}',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            'pipe:1',
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            return {"success": False, "error": "ffmpeg not found"}

        _screencast_proc = proc

        def _x11_pump() -> None:
            global _screencast_frames
            from PySide6.QtGui import QImage
            frame_size = lcd_w * lcd_h * 3
            assert proc.stdout is not None
            while not stop_event.is_set():
                raw = proc.stdout.read(frame_size)
                if len(raw) < frame_size:
                    break
                qimg = QImage(
                    raw, lcd_w, lcd_h, lcd_w * 3,
                    QImage.Format.Format_RGB888).copy()
                lcd.send_frame(qimg)
                set_current_image(qimg)
                _screencast_frames += 1

        _screencast_thread = threading.Thread(
            target=_x11_pump, daemon=True, name="api-screencast")
        _screencast_thread.start()
        _screencast_params["backend"] = "x11"
        log.info("Screencast started (x11grab): %dx%d @ %dfps", lcd_w, lcd_h, fps)
        return {"success": True, "backend": "x11"}

    # Fallback: PipeWire (pure Wayland, no $DISPLAY)
    if _is_wayland():
        from trcc.ui.gui.pipewire_capture import PIPEWIRE_AVAILABLE, PipeWireScreenCast
        if not PIPEWIRE_AVAILABLE:
            return {"success": False,
                    "error": "Wayland detected but PipeWire deps missing (dbus, PyGObject, GStreamer)"}

        cast = PipeWireScreenCast()
        _screencast_cast = cast

        def _pipewire_pump() -> None:
            global _screencast_frames
            from PySide6.QtGui import QImage
            if not cast.start(timeout=30):
                log.error("PipeWire screencast failed to start")
                return
            interval = 1.0 / fps
            while not stop_event.is_set():
                if (frame := cast.grab_frame()) is not None:
                    fw, fh, rgb_bytes = frame
                    qimg = QImage(
                        rgb_bytes, fw, fh, fw * 3,
                        QImage.Format.Format_RGB888).copy()
                    if w and h:
                        qimg = qimg.copy(x, y, min(w, fw - x), min(h, fh - y))
                    qimg = qimg.scaled(lcd_w, lcd_h)
                    lcd.send_frame(qimg)
                    set_current_image(qimg)
                    _screencast_frames += 1
                stop_event.wait(interval)
            cast.stop()

        _screencast_thread = threading.Thread(
            target=_pipewire_pump, daemon=True, name="api-screencast")
        _screencast_thread.start()
        _screencast_params["backend"] = "pipewire"
        log.info("Screencast started (PipeWire): %dx%d @ %dfps", lcd_w, lcd_h, fps)
        return {"success": True, "backend": "pipewire"}

    return {"success": False,
            "error": "No display server detected (no DISPLAY or WAYLAND_DISPLAY set)"}


def stop_screencast() -> None:
    """Stop background screencast if running."""
    global _screencast_thread, _screencast_stop_event
    global _screencast_proc, _screencast_cast
    global _screencast_frames, _screencast_params

    if _screencast_stop_event:
        _screencast_stop_event.set()

    if _screencast_proc is not None:
        import subprocess
        proc = _screencast_proc
        if isinstance(proc, subprocess.Popen):
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    if _screencast_cast is not None:
        try:
            _screencast_cast.stop()  # type: ignore[union-attr]
        except (RuntimeError, AttributeError, OSError) as e:
            log.debug("api: screencast cast.stop() raised: %s", e)

    if _screencast_thread and _screencast_thread.is_alive():
        _screencast_thread.join(timeout=2)

    _screencast_thread = None
    _screencast_stop_event = None
    _screencast_proc = None
    _screencast_cast = None
    _screencast_frames = 0
    _screencast_params = None


# ── Static file mounts (resolution-aware, remounted on device select) ─

_mounted_routes: list[str] = []  # Track mounted paths for remount


def mount_static_dirs(width: int, height: int) -> None:
    """Mount theme/web/mask directories as static files for the given resolution.

    Called after device select when resolution is known. Remounts if resolution
    changes (e.g., user switches device).
    Reads dirs from the active device's geometry delegates (per device).
    """
    from trcc.adapters.infra.data_repository import DataManager
    from trcc.core.paths import resolve_theme_dir

    # Remove previous mounts
    for path in _mounted_routes:
        app.routes[:] = [r for r in app.routes if getattr(r, 'path', '') != path]
    _mounted_routes.clear()

    # Read from device delegates when available, fallback to resolve
    dev = _device_dispatcher
    if dev is not None and not hasattr(dev, 'theme_dir'):
        dev = None  # proxy/mock without geometry surface
    td = dev.theme_dir if dev else None
    theme_dir = str(td.path) if td else resolve_theme_dir(width, height)
    web_dir = str(dev.web_dir) if dev and dev.web_dir else DataManager.get_web_dir(width, height)
    masks_dir = str(dev.masks_dir) if dev and dev.masks_dir else DataManager.get_web_masks_dir(width, height)

    if os.path.isdir(theme_dir):
        app.mount("/static/themes", StaticFiles(directory=theme_dir), name="themes")
        _mounted_routes.append("/static/themes")
    if os.path.isdir(web_dir):
        app.mount("/static/web", StaticFiles(directory=web_dir), name="web")
        _mounted_routes.append("/static/web")
    if os.path.isdir(masks_dir):
        app.mount("/static/masks", StaticFiles(directory=masks_dir), name="masks")
        _mounted_routes.append("/static/masks")

    log.info("Mounted static dirs for %dx%d: themes=%s web=%s masks=%s",
             width, height, theme_dir, web_dir, masks_dir)


# ── Token auth + pairing ──────────────────────────────────────────────

_api_token: str | None = None
_pairing_code: str | None = None  # Ephemeral 6-char code, shown in terminal


def configure_auth(token: str | None) -> None:
    """Set the API token. Called by CLI serve command."""
    global _api_token
    _api_token = token


def set_pairing_code(code: str) -> None:
    """Set the ephemeral pairing code (displayed in terminal)."""
    global _pairing_code
    _pairing_code = code


# Paths exempt from token auth — pairing needs to work before the phone has a token
_AUTH_EXEMPT = {"/health", "/pair"}


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every HTTP request with method, path, status, and latency."""
    import time
    start = time.monotonic()
    response = await call_next(request)
    ms = (time.monotonic() - start) * 1000
    log.info("API %s %s → %d (%.0fms)",
             request.method, request.url.path, response.status_code, ms)
    return response


@app.middleware("http")
async def check_token(request: Request, call_next):
    """Reject requests without valid token (if token is configured)."""
    if _api_token and request.url.path not in _AUTH_EXEMPT:
        header_token = request.headers.get("X-API-Token", "")
        if not hmac.compare_digest(header_token, _api_token):
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})
    return await call_next(request)


# ── Health endpoint (always accessible) ────────────────────────────────

@app.get("/health")
def health() -> dict:
    """Health check (always accessible, no auth required)."""
    return {"status": "ok", "version": __version__}


# ── Pairing endpoint (no auth required) ───────────────────────────────

@app.post("/pair")
def pair_device(code: str):
    """Pair a remote device using the 6-char code shown in the terminal.

    Returns the persistent API token on success. The remote app stores
    this token and uses it for all future requests (X-API-Token header).
    No re-pairing needed after server restart.
    """
    if not _pairing_code:
        return JSONResponse(
            status_code=503,
            content={"detail": "Pairing not available (server started with --token)"},
        )

    if not hmac.compare_digest(code.upper(), _pairing_code.upper()):
        log.warning("Pairing attempt with wrong code")
        return JSONResponse(
            status_code=403, content={"detail": "Invalid pairing code"},
        )

    if not _api_token:
        return JSONResponse(
            status_code=500, content={"detail": "No API token configured"},
        )

    log.info("Remote device paired successfully")
    return {"success": True, "token": _api_token}


# ── Register routers ──────────────────────────────────────────────────

from trcc.ui.api.control_center import router as control_center_router  # noqa: E402
from trcc.ui.api.devices import router as devices_router  # noqa: E402
from trcc.ui.api.display import router as display_router  # noqa: E402
from trcc.ui.api.i18n import router as i18n_router  # noqa: E402
from trcc.ui.api.led import router as led_router  # noqa: E402
from trcc.ui.api.system import router as system_router  # noqa: E402
from trcc.ui.api.themes import router as themes_router  # noqa: E402
from trcc.ui.api.trcc import router as trcc_router  # noqa: E402

app.include_router(control_center_router)
app.include_router(devices_router)
app.include_router(display_router)
app.include_router(i18n_router)
app.include_router(led_router)
app.include_router(themes_router)
app.include_router(system_router)
app.include_router(trcc_router)
