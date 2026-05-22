"""Blocking video / static keepalive loops for the CLI and API adapters.

These were methods on DisplayService but are pure orchestration — they
take a DisplayService and a callback set, then run a blocking ``while``
loop that ticks media, polls metrics, runs the rendering pipeline, and
fires the caller's ``on_frame`` / ``on_progress`` hooks.  Moving them to
module-level keeps DisplayService focused on state ownership and lets
these loops be reused (or substituted) without subclassing the service.

DisplayService still exposes ``run_video_loop`` and ``run_static_loop``
as one-line delegates so CLI / API call sites are unchanged.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .overlay import OverlayService

if TYPE_CHECKING:
    from .display import DisplayService


def run_video_loop(
    svc: DisplayService,
    video_path: Path,
    *,
    overlay_config: dict | None = None,
    mask_path: Path | None = None,
    metrics_fn: Any | None = None,
    on_frame: Any | None = None,
    on_progress: Any | None = None,
    loop: bool = True,
    duration: float = 0,
) -> dict:
    """Unified video+overlay pipeline for CLI and API adapters.

    Loads video, sets up overlay (config + mask + metrics polling), runs
    the tick loop, and calls ``on_frame`` per processed frame.

    Args:
        svc: DisplayService whose media + overlay drive the loop.
        video_path: Video/GIF/ZT file to play.
        overlay_config: Overlay element config dict (from
            ``build_overlay_config()``).  Enables overlay if provided.
        mask_path: Mask PNG file or directory.  Auto-resized to LCD dims.
        metrics_fn: Callable returning ``HardwareMetrics`` — polled once
            per second for live overlay updates.
        on_frame: Callback ``(processed_image)`` — adapter sends to device.
        on_progress: Callback ``(percent, current_time, total_time)``.
        loop: Whether to loop the video.
        duration: Stop after N seconds (0 = no limit).

    Returns:
        Result dict with success/error/message.
    """
    svc.log.info(
        "run_video_loop: path=%s overlay=%s mask=%s loop=%s duration=%s",
        video_path, bool(overlay_config), bool(mask_path), loop, duration,
    )

    # 1. Load video
    w, h = svc.canvas_size
    svc.media.set_target_size(w, h)
    if not svc.media.load(video_path):
        svc.log.error("run_video_loop: failed to load %s", video_path)
        return {"success": False, "error": f"Failed to load: {video_path}"}

    svc.convert_media_frames()

    total = svc.media.state.total_frames
    fps = svc.media.state.fps
    svc.log.info("run_video_loop: loaded %d frames, %.0ffps, %dx%d",
                 total, fps, w, h)

    # 2. Set up overlay if config or mask provided
    if overlay_config or mask_path:
        if overlay_config:
            svc.log.debug("run_video_loop: overlay config with %d elements",
                          len(overlay_config))
            svc.overlay.set_config(overlay_config)
        if mask_path:
            mask_img = OverlayService.load_mask_from_path(
                Path(mask_path), svc.overlay._renderer, w, h)
            if mask_img is not None:
                svc.log.debug("run_video_loop: mask loaded from %s", mask_path)
                svc.overlay.set_theme_mask(mask_img)
        svc.overlay.enabled = True

    # 3. Start playback + run tick loop
    svc.media._state.loop = loop
    svc.media.play()
    return _run_tick_loop(
        svc,
        metrics_fn=metrics_fn,
        on_frame=on_frame,
        on_progress=on_progress,
        duration=duration,
    )


def _run_tick_loop(
    svc: DisplayService,
    *,
    metrics_fn: Any | None = None,
    on_frame: Any | None = None,
    on_progress: Any | None = None,
    duration: float = 0,
) -> dict:
    """Blocking tick loop — shared by ``run_video_loop`` and theme-load.

    Assumes media is already loaded + playing, overlay already configured.
    Polls metrics, composites overlay, applies adjustments, calls callbacks.
    """
    total = svc.media.state.total_frames
    fps = svc.media.state.fps
    interval = svc.media.frame_interval_ms / 1000.0
    start = time.monotonic()
    last_metrics = 0.0

    try:
        while svc.media.is_playing:
            frame, should_send, progress = svc.media.tick()
            if frame is None:
                break

            # Poll metrics once per second
            if metrics_fn and svc.overlay.enabled:
                now = time.monotonic()
                if now - last_metrics >= 1.0:
                    svc.overlay.update_metrics(metrics_fn())
                    last_metrics = now

            # Composite overlay
            if svc.overlay.enabled:
                frame = svc.overlay.render(frame)

            # Apply brightness/rotation
            processed = svc._apply_adjustments(frame)

            # Send to device
            if on_frame and should_send:
                on_frame(processed)

            # Progress callback
            if on_progress and progress:
                on_progress(*progress)

            # Duration limit
            if duration and (time.monotonic() - start) >= duration:
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        return {"success": True, "message": "Stopped",
                "frames": total, "fps": fps}

    return {"success": True, "message": "Done",
            "frames": total, "fps": fps}


def run_static_loop(
    svc: DisplayService,
    *,
    interval: float = 0.150,
    duration: float = 0,
    metrics_fn: Any | None = None,
    on_frame: Any | None = None,
) -> dict:
    """Re-send the current static image at *interval* seconds until interrupted.

    Bulk/LY devices don't retain frames — firmware reverts to the built-in
    logo unless frames keep arriving.  The GUI metrics loop handles this
    automatically; CLI and API call this instead.

    The DeviceService encoding cache makes repeated sends cheap — only the
    USB write happens, no image re-encoding.
    """
    image = svc.current_image
    if not image:
        return {"success": False, "error": "No image loaded"}

    w, h = svc.lcd_size
    start = time.monotonic()
    last_metrics = 0.0

    try:
        while True:
            if metrics_fn and svc.overlay.enabled:
                now = time.monotonic()
                if now - last_metrics >= 1.0:
                    svc.overlay.update_metrics(metrics_fn())
                    last_metrics = now

            if svc.overlay.enabled:
                frame = svc.overlay.render(image)
            else:
                frame = image
            processed = svc._apply_adjustments(frame)
            if on_frame:
                on_frame(processed)
            svc.devices.send_frame(processed, w, h,
                                   encode_angle=svc._encode_angle())
            if duration and (time.monotonic() - start) >= duration:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        return {"success": True, "message": "Stopped"}

    return {"success": True, "message": "Done"}
