"""CLI `display` group — orientation, brightness, theme, send."""
from __future__ import annotations

import logging
from pathlib import Path

import typer

from ...core._colors import parse_hex
from ...core.commands import (
    AddOverlayElement,
    ApplyMask,
    ConfigureSlideshow,
    ConnectDevice,
    DeleteOverlayElement,
    DiscoverDevices,
    EnableOverlay,
    FlashOverlayElement,
    KeepAliveLoop,
    LcdSnapshot,
    ListMasks,
    LoadImage,
    LoadTheme,
    LoadVideo,
    LoopVideo,
    PauseVideo,
    PlayVideo,
    RenderAndSend,
    RenderDcStandalone,
    RestoreLastTheme,
    SeekVideo,
    SendColor,
    SendImage,
    SetBackgroundMode,
    SetBrightness,
    SetFitMode,
    SetMaskPosition,
    SetMaskVisible,
    SetOrientation,
    SetOverlayBackground,
    SetSlideshow,
    SetSplitMode,
    StartScreencast,
    StopScreencast,
    StopVideo,
    ToggleVideo,
    UpdateOverlayElement,
    UploadBootAnimation,
    UploadCustomMask,
)
from ._ctx import get_app

log = logging.getLogger(__name__)

app = typer.Typer(help="Configure device display (theme / orientation / brightness).",
                  no_args_is_help=True)


@app.command("set-orientation")
def set_orientation(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    degrees: int = typer.Argument(..., help="Rotation: 0, 90, 180, or 270"),
) -> None:
    """Set per-device rotation."""
    log.info("cli display set-orientation: key=%s degrees=%s", key, degrees)
    result = get_app().dispatch(SetOrientation(key=key, degrees=degrees))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("set-brightness")
def set_brightness(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    percent: int = typer.Argument(..., help="Brightness 0–100"),
) -> None:
    """Set per-device display brightness."""
    log.info("cli display set-brightness: key=%s percent=%s", key, percent)
    result = get_app().dispatch(SetBrightness(key=key, percent=percent))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


def _parse_hex_color(hex_str: str) -> tuple[int, int, int] | None:
    """Parse a 6-char hex color into ``(r, g, b)``; ``None`` on miss."""
    try:
        r, g, b, _a = parse_hex(hex_str)
    except ValueError:
        return None
    return (r, g, b)


@app.command("color")
def color(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    hex_color: str = typer.Argument(..., metavar="HEX",
                                    help="Hex color (e.g. ff0000 for red)"),
) -> None:
    """Display a single solid color on the LCD.

    Smallest path that exercises the full wire chain (handshake-derived
    profile + DisplayService encoder + Device.send). Useful diagnostic
    for confirming a device class works end-to-end on real hardware.
    """
    log.info("cli display color: key=%s hex=%s", key, hex_color)
    rgb = _parse_hex_color(hex_color)
    if rgb is None:
        typer.echo(f"Invalid hex color: {hex_color!r} "
                   "(expected 6 hex chars, e.g. ff0000)", err=True)
        raise typer.Exit(code=2)
    r, g, b = rgb
    result = get_app().dispatch(SendColor(key=key, r=r, g=g, b=b))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("set-fit-mode")
def set_fit_mode(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    mode: str = typer.Argument(
        ..., help="Fit mode: 'width' (letterbox), 'height' (pillarbox), 'stretch'",
    ),
) -> None:
    """Set how the background fits the canvas."""
    log.info("cli display set-fit-mode: key=%s mode=%s", key, mode)
    result = get_app().dispatch(SetFitMode(key=key, mode=mode.lower()))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("overlay")
def overlay(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    state: str = typer.Argument(..., help="'on' or 'off'"),
) -> None:
    """Toggle the metric overlay layer."""
    log.info("cli display overlay: key=%s state=%s", key, state)
    state_normalized = state.lower()
    if state_normalized in ("on", "true", "1", "yes"):
        enabled = True
    elif state_normalized in ("off", "false", "0", "no"):
        enabled = False
    else:
        typer.echo(f"Invalid state: {state!r} (expected on/off)", err=True)
        raise typer.Exit(code=2)
    result = get_app().dispatch(EnableOverlay(key=key, enabled=enabled))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("apply-mask")
def apply_mask(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    path: Path = typer.Argument(
        ..., help="Image file path (png/jpg/jpeg/bmp/webp)",
        exists=True, file_okay=True, dir_okay=False,
    ),
) -> None:
    """Override the active theme's mask with a user-supplied image."""
    log.info("cli display apply-mask: key=%s path=%s", key, path)
    result = get_app().dispatch(ApplyMask(key=key, path=path))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("mask-position")
def mask_position(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    x: int = typer.Argument(..., help="X offset in pixels (≥ 0)"),
    y: int = typer.Argument(..., help="Y offset in pixels (≥ 0)"),
) -> None:
    """Position the mask overlay within the canvas."""
    log.info("cli display mask-position: key=%s x=%s y=%s", key, x, y)
    result = get_app().dispatch(SetMaskPosition(key=key, x=x, y=y))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("mask-visible")
def mask_visible(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    state: str = typer.Argument(..., help="'on' or 'off'"),
) -> None:
    """Toggle mask visibility."""
    log.info("cli display mask-visible: key=%s state=%s", key, state)
    state_normalized = state.lower()
    if state_normalized in ("on", "true", "1", "yes", "show"):
        visible = True
    elif state_normalized in ("off", "false", "0", "no", "hide"):
        visible = False
    else:
        typer.echo(f"Invalid state: {state!r} (expected on/off)", err=True)
        raise typer.Exit(code=2)
    result = get_app().dispatch(SetMaskVisible(key=key, visible=visible))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("split-mode")
def split_mode(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    mode: int = typer.Argument(..., help="0 (off), 1 (style A), 2 (B), 3 (C)"),
) -> None:
    """Set the Dynamic Island style (widescreen panels only)."""
    log.info("cli display split-mode: key=%s mode=%s", key, mode)
    result = get_app().dispatch(SetSplitMode(key=key, mode=mode))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("load-image")
def load_image(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    path: Path = typer.Argument(
        ..., help="Image file (PNG / JPG / JPEG / BMP / WEBP)",
        exists=True, file_okay=True, dir_okay=False,
    ),
) -> None:
    """Show a single image on the LCD.

    Stages the image as a one-file theme so the existing render pipeline
    handles fit + brightness + rotation.  Re-runnable: subsequent loads
    of the same image are cheap (no re-copy).
    """
    log.info("cli display load-image: key=%s path=%s", key, path)
    result = get_app().dispatch(LoadImage(key=key, path=path))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("load-video")
def load_video(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    path: Path = typer.Argument(
        ..., help="Video file (MP4 / MOV / WEBM / MKV / AVI / ZT)",
        exists=True, file_okay=True, dir_okay=False,
    ),
    start_ms: int = typer.Option(
        0, "--start", "-s", min=0,
        help="Clip start in milliseconds (default: 0).",
    ),
    end_ms: int = typer.Option(
        None, "--end", "-e", min=1,
        help="Clip end in milliseconds (default: probe duration, fallback 10s).",
    ),
    rotation: int = typer.Option(
        0, "--rotation", "-r",
        help="Rotation in degrees: 0 / 90 / 180 / 270.",
    ),
) -> None:
    """Play a video on the LCD as a single-video theme.

    Transcodes the source to a ``Theme.zt`` matching the device's native
    resolution (.zt inputs are copied as-is), stages a one-file theme,
    then dispatches LoadTheme.  Device must be attached so we know the
    target resolution.
    """
    log.info(
        "cli display load-video: key=%s path=%s start_ms=%s end_ms=%s "
        "rotation=%s",
        key, path, start_ms, end_ms, rotation,
    )
    result = get_app().dispatch(LoadVideo(
        key=key, path=path, start_ms=start_ms, end_ms=end_ms,
        rotation=rotation,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("load-theme")
def load_theme(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    path: Path = typer.Argument(..., help="Theme directory",
                                exists=True, file_okay=False, dir_okay=True),
) -> None:
    """Load a theme: parse, persist, render+send if device is connected."""
    log.info("cli display load-theme: key=%s path=%s", key, path)
    result = get_app().dispatch(LoadTheme(key=key, path=path))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("play-video")
def play_video(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    path: Path = typer.Argument(
        ..., help="Video path (mp4/mov/webm/mkv/avi/zt)",
        exists=True, file_okay=True, dir_okay=False,
    ),
    fps: int = typer.Option(
        15, "--fps", help="Decode FPS (default: 15)",
    ),
) -> None:
    """Decode a video and start playing it on the device.

    Overrides the active theme's background until ``stop-video`` runs.
    Frames advance on each ``display play`` tick.
    """
    log.info("cli display play-video: key=%s path=%s fps=%s", key, path, fps)
    result = get_app().dispatch(PlayVideo(key=key, path=path, fps=fps))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("stop-video")
def stop_video(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """Clear the video playback override (returns to the active theme)."""
    log.info("cli display stop-video: key=%s", key)
    result = get_app().dispatch(StopVideo(key=key))
    typer.echo(result.message)


@app.command("slideshow-run")
def slideshow_run(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    themes_dir: Path = typer.Argument(
        ..., help="Directory containing theme subdirectories",
        exists=True, file_okay=False, dir_okay=True,
    ),
    interval: float = typer.Option(
        30.0, "--interval", "-i",
        help="Seconds between theme switches (default: 30.0)",
    ),
) -> None:
    """Foreground slideshow over a directory of themes.

    Different from ``slideshow`` / ``configure-slideshow`` (which persist
    state).  This is a one-shot loop: blocks until Ctrl-C, swaps to the
    next theme each tick.  Useful for demos + smoke tests; the persisted
    flow is what production users want.
    """
    log.info(
        "cli display slideshow-run: key=%s themes_dir=%s interval=%s",
        key, themes_dir, interval,
    )
    import time

    app_obj = get_app()
    themes = sorted(p for p in themes_dir.iterdir() if p.is_dir())
    if not themes:
        typer.echo(f"No theme subdirectories under {themes_dir}", err=True)
        raise typer.Exit(code=1)

    interval_s = max(1.0, interval)
    typer.echo(f"Slideshow on {key}: {len(themes)} themes, "
               f"{interval_s:.1f}s between switches (Ctrl-C to stop)…")
    idx = 0
    try:
        while True:
            theme_path = themes[idx]
            result = app_obj.dispatch(LoadTheme(key=key, path=theme_path))
            if result.ok:
                typer.echo(f"  → {theme_path.name}")
            else:
                typer.echo(f"  ! {theme_path.name}: {result.message}", err=True)
            idx = (idx + 1) % len(themes)
            time.sleep(interval_s)
    except KeyboardInterrupt:
        typer.echo("\nSlideshow stopped.")


_IMAGE_EXTS_FOR_ANIM: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".webp",
})


@app.command("boot-anim")
def boot_anim(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922 (SCSI only)"),
    frames_dir: Path = typer.Argument(
        ..., help="Directory of image frames (sorted alphabetically; 1–248 frames)",
        exists=True, file_okay=False, dir_okay=True,
    ),
    delay_ds: int = typer.Option(
        10, "--delay", "-d", min=1, max=25,
        help="Dwell time per frame in deciseconds (10 = 1.0 s, max 25 = 2.5 s)",
    ),
) -> None:
    """Upload a multi-frame compressed boot animation to a SCSI LCD's flash.

    The animation plays from device flash on every boot until overwritten.
    Only SCSI panels with 240×240 / 240×320 / 320×240 / 320×320 resolution
    support boot animations.

    Frame files are picked up in alphabetical order from *frames_dir* —
    PNG / JPG / JPEG / BMP / WebP.  Each frame uses the same dwell time
    via --delay (per-frame delays via the API only).
    """
    log.info(
        "cli display boot-anim: key=%s frames_dir=%s delay_ds=%s",
        key, frames_dir, delay_ds,
    )
    frame_paths = sorted(
        p for p in frames_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS_FOR_ANIM
    )
    if not frame_paths:
        typer.echo(f"No supported image frames found under {frames_dir}", err=True)
        raise typer.Exit(code=1)

    delays = [delay_ds] * len(frame_paths)
    typer.echo(f"Uploading {len(frame_paths)} boot-animation frames to {key} "
               f"({delay_ds * 0.1:.1f}s each)…")
    result = get_app().dispatch(UploadBootAnimation(
        key=key, frame_paths=frame_paths, delays_ds=delays,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("play")
def play(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    interval: float = typer.Option(
        None, "--interval", "-i",
        help="Tick interval in seconds (default: AppSettings.refresh_interval_s)",
    ),
) -> None:
    """Run the render-and-send ticker until Ctrl-C.

    Dispatches RenderAndSend every tick with live sensors.  Keeps SCSI
    devices from timing out (static-blink fix) and advances video
    playback.  Stops cleanly on SIGINT.
    """
    log.info("cli display play: key=%s interval=%s", key, interval)
    import time

    app_obj = get_app()
    tick_s = interval if interval is not None else app_obj.settings.app.refresh_interval_s
    tick_s = max(0.05, tick_s)

    typer.echo(f"Playing on {key} at {tick_s:.2f}s intervals (Ctrl-C to stop)…")
    try:
        while True:
            result = app_obj.dispatch(RenderAndSend(key=key))
            if not result.ok:
                typer.echo(f"  tick failed: {result.message}", err=True)
                raise typer.Exit(code=1)
            typer.echo(f"  sent {result.bytes_sent} bytes "
                       f"(theme={result.theme_name!r})")
            time.sleep(tick_s)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@app.command("pause-video")
def pause_video(
    key: str = typer.Argument(..., help="Device key"),
    state: str = typer.Argument(..., help="'on' (pause) or 'off' (resume)"),
) -> None:
    """Pause or resume video playback."""
    log.info("cli display pause-video: key=%s state=%s", key, state)
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    result = get_app().dispatch(
        PauseVideo(key=key, paused=state.lower() == "on"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("toggle-video")
def toggle_video(
    key: str = typer.Argument(..., help="Device key"),
) -> None:
    """Flip video playback between paused / playing (single-verb helper)."""
    log.info("cli display toggle-video: key=%s", key)
    result = get_app().dispatch(ToggleVideo(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("seek-video")
def seek_video(
    key: str = typer.Argument(..., help="Device key"),
    frame: int = typer.Argument(..., help="Frame index to jump to"),
) -> None:
    """Jump the playback cursor to a specific frame."""
    log.info("cli display seek-video: key=%s frame=%s", key, frame)
    result = get_app().dispatch(SeekVideo(key=key, frame=frame))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("loop-video")
def loop_video(
    key: str = typer.Argument(..., help="Device key"),
    state: str = typer.Argument(..., help="'on' (loop) or 'off' (single-pass)"),
) -> None:
    """Toggle whether video wraps at the end or sticks at the last frame."""
    log.info("cli display loop-video: key=%s state=%s", key, state)
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    result = get_app().dispatch(LoopVideo(key=key, loop=state.lower() == "on"))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("upload-mask")
def upload_mask(
    key: str = typer.Argument(..., help="Device key"),
    source: Path = typer.Argument(
        ..., help="Mask image file to copy + apply",
        exists=True, file_okay=True, dir_okay=False,
    ),
) -> None:
    """Copy a mask into user_content_dir/masks and apply it to the device."""
    log.info("cli display upload-mask: key=%s source=%s", key, source)
    result = get_app().dispatch(UploadCustomMask(key=key, source=source))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("list-masks")
def list_masks(
    key: str | None = typer.Argument(
        None,
        help=("Device key (e.g. 0402:3922) — its resolution scopes the scan. "
              "Required unless --dir is given."),
    ),
    directory: Path | None = typer.Option(
        None, "--dir", "-d",
        help="Override: scan an explicit directory instead of the device's mask dirs",
    ),
) -> None:
    """List mask images for the device's resolution.

    By default, scans both the cloud-downloaded mask dir
    (``data/web/zt{W}{H}``) and the user-created mask dir
    (``user_content_dir/data/web/zt{W}{H}``).
    """
    log.info("cli display list-masks: key=%s directory=%s", key, directory)
    if directory is not None:
        result = get_app().dispatch(ListMasks(directory=directory))
    elif key:
        app_obj = get_app()
        device = app_obj.devices.get(key)
        if device is None or device.profile is None:
            typer.echo(
                f"Device {key} not connected — connect first so we know "
                "the target resolution",
                err=True,
            )
            raise typer.Exit(code=1)
        result = app_obj.dispatch(
            ListMasks(resolution=device.profile.resolution),
        )
    else:
        typer.echo("Provide a device key, or --dir DIRECTORY.", err=True)
        raise typer.Exit(code=1)
    typer.echo(result.message)
    for entry in result.masks:
        typer.echo(f"  {entry.name:30} {entry.path}")


@app.command("overlay-add")
def overlay_add(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    type_: str = typer.Argument(
        ..., metavar="TYPE", help="'text' / 'metric' / 'clock'",
    ),
    x: int = typer.Option(0, "--x", help="X position"),
    y: int = typer.Option(0, "--y", help="Y position"),
    text: str = typer.Option("", "--text", help="Text content (type=text)"),
    metric: str = typer.Option("", "--metric", help="Metric id (type=metric)"),
    fmt: str = typer.Option(
        "{value}", "--format", help="Metric format string",
    ),
    source: str = typer.Option(
        "time", "--source", help="Clock source: time / weekday / date",
    ),
    color: str = typer.Option("#ffffff", "--color"),
    size: int = typer.Option(16, "--size"),
    bold: bool = typer.Option(False, "--bold"),
    italic: bool = typer.Option(False, "--italic"),
    element_id: str = typer.Option(
        "", "--id", help="Explicit element id (default: auto-generated UUID)",
    ),
) -> None:
    """Add a user-edited overlay element to a device."""
    log.info(
        "cli display overlay-add: key=%s type=%s x=%s y=%s metric=%s "
        "element_id=%s",
        key, type_, x, y, metric, element_id,
    )
    result = get_app().dispatch(AddOverlayElement(
        key=key, type=type_, x=x, y=y, text=text, metric=metric,
        format=fmt, source=source, color=color, size=size,
        bold=bold, italic=italic, element_id=element_id,
    ))
    typer.echo(result.message)
    if result.ok and result.element is not None:
        typer.echo(f"  id: {result.element.id}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("overlay-update")
def overlay_update(
    key: str = typer.Argument(...),
    element_id: str = typer.Argument(..., help="ID returned by overlay-add"),
    x: int | None = typer.Option(None, "--x"),
    y: int | None = typer.Option(None, "--y"),
    color: str | None = typer.Option(None, "--color"),
    size: int | None = typer.Option(None, "--size"),
    text: str | None = typer.Option(None, "--text"),
    metric: str | None = typer.Option(None, "--metric"),
    fmt: str | None = typer.Option(None, "--format"),
    source: str | None = typer.Option(None, "--source"),
    bold: bool | None = typer.Option(None, "--bold/--no-bold"),
    italic: bool | None = typer.Option(None, "--italic/--no-italic"),
) -> None:
    """Mutate fields on an existing user-edited overlay element."""
    log.info(
        "cli display overlay-update: key=%s element_id=%s", key, element_id,
    )
    result = get_app().dispatch(UpdateOverlayElement(
        key=key, element_id=element_id,
        x=x, y=y, color=color, size=size, text=text,
        metric=metric, format=fmt, source=source,
        bold=bold, italic=italic,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("overlay-delete")
def overlay_delete(
    key: str = typer.Argument(...),
    element_id: str = typer.Argument(..., help="ID returned by overlay-add"),
) -> None:
    """Remove a user-edited overlay element by id."""
    log.info(
        "cli display overlay-delete: key=%s element_id=%s", key, element_id,
    )
    result = get_app().dispatch(
        DeleteOverlayElement(key=key, element_id=element_id),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("overlay-flash")
def overlay_flash(
    key: str = typer.Argument(...),
    element_id: str = typer.Argument(...),
    duration_ms: int = typer.Option(
        1500, "--duration", "-d", min=100, max=10000,
        help="Flash duration in milliseconds",
    ),
) -> None:
    """Briefly highlight an overlay element in the GUI."""
    log.info(
        "cli display overlay-flash: key=%s element_id=%s duration_ms=%s",
        key, element_id, duration_ms,
    )
    result = get_app().dispatch(FlashOverlayElement(
        key=key, element_id=element_id, duration_ms=duration_ms,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("slideshow")
def slideshow(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    state: str = typer.Argument(..., help="'on' / 'off'"),
) -> None:
    """Toggle the per-device slideshow on/off."""
    log.info("cli display slideshow: key=%s state=%s", key, state)
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    result = get_app().dispatch(
        SetSlideshow(key=key, enabled=state.lower() == "on"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("configure-slideshow")
def configure_slideshow(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    themes: list[str] = typer.Argument(
        ..., help="Theme names (directories under user_content_dir) — order matters",
    ),
    interval: float = typer.Option(
        60.0, "--interval", "-i", min=1.0,
        help="Seconds between theme swaps (default 60).",
    ),
) -> None:
    """Set the slideshow theme list + interval."""
    log.info(
        "cli display configure-slideshow: key=%s themes=%s interval=%s",
        key, themes, interval,
    )
    result = get_app().dispatch(ConfigureSlideshow(
        key=key, themes=tuple(themes), interval_s=interval,
    ))
    typer.echo(result.message)
    for t in result.themes:
        typer.echo(f"  {t}")
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("keepalive")
def keepalive(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    interval: float = typer.Option(
        0.150, "--interval", "-i", min=0.05,
        help=("Seconds between resends.  Bulk/LY firmware reverts to "
              "the built-in logo after ~2-3 s without a frame; default "
              "0.150 s keeps the screen pinned."),
    ),
    count: int = typer.Option(
        0, "--count", "-c", min=0,
        help="Number of resends; 0 means loop forever (until Ctrl-C).",
    ),
    metric_interval: float = typer.Option(
        1.0, "--metric-interval", min=0.0,
        help=("Seconds between overlay re-renders (live sensor refresh).  "
              "0 disables — last frame's metrics stay frozen on screen."),
    ),
) -> None:
    """Periodically resend the device's last frame.

    Workaround for Bulk/LY firmware that drops the displayed image when
    the internal buffer ages out.  Render at least once before starting
    the loop so there's a cached frame to resend.

    ``count=0`` (default) runs open-ended and exits cleanly on Ctrl-C —
    the Command itself owns the loop + signal handling so the CLI
    doesn't need a user-space ``while`` wrapper.
    """
    log.info(
        "cli display keepalive: key=%s interval=%s count=%s "
        "metric_interval=%s",
        key, interval, count, metric_interval,
    )
    if count == 0:
        typer.echo(
            f"Keepalive on {key} every {interval:.3f}s "
            f"(metric refresh every {metric_interval:.1f}s, Ctrl-C to stop)…"
        )
    result = get_app().dispatch(KeepAliveLoop(
        key=key, count=count,
        interval_s=interval, metric_interval_s=metric_interval,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("background-mode")
def background_mode(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    mode: str = typer.Argument(
        ..., help="'theme' / 'color' / 'transparent'",
    ),
) -> None:
    """Pick what fills the LCD behind overlays."""
    log.info("cli display background-mode: key=%s mode=%s", key, mode)
    result = get_app().dispatch(SetBackgroundMode(key=key, mode=mode.lower()))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("overlay-background")
def overlay_background(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    hex_color: str = typer.Argument(
        ..., metavar="HEX", help="Hex color (e.g. 000000 for black)",
    ),
) -> None:
    """Set the solid color used when background-mode=color."""
    log.info("cli display overlay-background: key=%s hex=%s", key, hex_color)
    rgb = _parse_hex_color(hex_color)
    if rgb is None:
        typer.echo(f"Invalid hex color: {hex_color!r}", err=True)
        raise typer.Exit(code=2)
    result = get_app().dispatch(SetOverlayBackground(key=key, color=rgb))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("restore-theme")
def restore_theme(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """Reload the device's persisted theme — convenience after restart."""
    log.info("cli display restore-theme: key=%s", key)
    result = get_app().dispatch(RestoreLastTheme(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("resume")
def resume(
    retries: int = typer.Option(
        10, "--retries", min=1,
        help="Discovery attempts before giving up (1 attempt = 2 s delay)",
    ),
) -> None:
    """Send each detected device's last-used theme (headless, no GUI).

    Use case: cron / systemd unit / udev hook that runs at boot or
    after a suspend cycle.  Enumerates every TRCC-known device on the
    bus, connects, and replays the saved theme so the displays come
    back to their pre-boot / pre-suspend state without the GUI.

    Bulk/LY devices fade after ~2-3 s without a fresh frame — pair
    this with ``trcc display keepalive`` per device for those, or
    ``trcc display play`` for the full render-loop.
    """
    log.info("cli display resume: retries=%s", retries)
    import time

    app_obj = get_app()
    products: list = []
    for attempt in range(1, retries + 1):
        result = app_obj.dispatch(DiscoverDevices())
        if result.ok and result.products:
            products = result.products
            break
        typer.echo(
            f"Waiting for device... ({attempt}/{retries})", err=True,
        )
        time.sleep(2)

    if not products:
        typer.echo("No compatible TRCC device detected.", err=True)
        raise typer.Exit(code=1)

    sent = 0
    for product in products:
        key = f"{product.vid:04x}:{product.pid:04x}"
        connect_result = app_obj.dispatch(ConnectDevice(key=key))
        if not connect_result.ok:
            typer.echo(f"  [{key}] connect failed: {connect_result.message}",
                       err=True)
            continue
        theme_result = app_obj.dispatch(RestoreLastTheme(key=key))
        if not theme_result.ok:
            typer.echo(f"  [{key}] {theme_result.message}", err=True)
            continue
        typer.echo(f"  [{key}] resumed: {theme_result.theme_name}")
        sent += 1

    if sent == 0:
        typer.echo(
            "No themes were sent.  Use the GUI to set a theme first.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Resumed {sent} device(s).")


@app.command("test")
def test(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    seconds: float = typer.Option(
        1.0, "--seconds", "-s", min=0.1,
        help="Hold each color for this many seconds.",
    ),
) -> None:
    """Color-cycle the LCD: red → green → blue → black.

    Smallest end-to-end exercise of the wire chain.  Useful when
    porting a new device class to confirm handshake → frame build →
    USB send all work before fighting overlay/theme bugs.
    """
    log.info("cli display test: key=%s seconds=%s", key, seconds)
    import time

    app_obj = get_app()
    sequence = (
        ("red",   (0xFF, 0x00, 0x00)),
        ("green", (0x00, 0xFF, 0x00)),
        ("blue",  (0x00, 0x00, 0xFF)),
        ("black", (0x00, 0x00, 0x00)),
    )
    for name, (r, g, b) in sequence:
        typer.echo(f"  {name}...")
        result = app_obj.dispatch(SendColor(key=key, r=r, g=g, b=b))
        if not result.ok:
            typer.echo(result.message, err=True)
            raise typer.Exit(code=1)
        time.sleep(seconds)
    typer.echo("Color cycle complete.")


@app.command("test-lcd")
def test_lcd(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    cols: int = typer.Option(
        60, "--cols", "-c", min=10, max=200,
        help="Width of the ANSI preview in terminal cells.",
    ),
) -> None:
    """Print an ANSI true-color preview of the LCD's current render.

    Same pipeline as ``display play`` but stops at the renderer
    surface — no wire send.  Useful for headless / sshell debugging
    where you can't see the physical device.
    """
    log.info("cli display test-lcd: key=%s cols=%s", key, cols)
    from ...services._ansi import image_to_ansi
    from ...services._clock import compute_clock

    app_obj = get_app()
    try:
        device = app_obj.devices[key]
    except KeyError:
        typer.echo(f"Device {key} not attached", err=True)
        raise typer.Exit(code=1) from None

    theme = app_obj.active_themes.get(key)
    if theme is None:
        typer.echo(f"No active theme on {key} — load one first", err=True)
        raise typer.Exit(code=1)

    enum = app_obj.platform.sensors()
    sensors = enum.read_all()
    del compute_clock  # build_preview_surface computes its own clock
    surface = app_obj.display.build_preview_surface(
        device.info, theme, sensors, profile=device.profile,
    )
    typer.echo(image_to_ansi(app_obj.renderer, surface, cols=cols))


@app.command("send-image")
def send_image(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    path: Path = typer.Argument(
        ..., help="Image file (PNG/JPG/BMP/WEBP)",
        exists=True, file_okay=True, dir_okay=False,
    ),
) -> None:
    """Push an image to the LCD once — no theme staging, no persistence.

    Companion to ``load-image`` (which materialises a single-image
    theme and persists ``DeviceSettings.current_theme``).  Use this
    when you want ephemeral display: boot logos, quick previews, API
    upload pipelines.
    """
    log.info("cli display send-image: key=%s path=%s", key, path)
    result = get_app().dispatch(SendImage(key=key, path=path))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("overlay-render")
def overlay_render(
    dc_path: Path = typer.Argument(
        ..., help="DC file or theme directory containing config1.dc",
    ),
    output: Path = typer.Option(
        ..., "--output", "-o",
        help="Output PNG path for the rendered preview.",
    ),
    width: int = typer.Option(
        320, "--width", "-w", min=1, help="Render canvas width (px)",
    ),
    height: int = typer.Option(
        320, "--height", "-h", min=1, help="Render canvas height (px)",
    ),
) -> None:
    """Render a DC config to a PNG preview — no active device required.

    Mirrors legacy ``trcc overlay`` — composites every element from
    ``config1.dc`` onto a solid-black canvas at *width × height* and
    writes the result as PNG.  Useful when iterating on a theme's
    metric positions without unplugging the device or sending frames.
    """
    log.info(
        "cli display overlay-render: dc_path=%s output=%s width=%s height=%s",
        dc_path, output, width, height,
    )
    result = get_app().dispatch(RenderDcStandalone(
        dc_path=dc_path, output_path=output,
        width=width, height=height,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("screencast")
def screencast(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    x: int = typer.Argument(..., help="Top-left X coordinate of capture region (px)"),
    y: int = typer.Argument(..., help="Top-left Y coordinate of capture region (px)"),
    w: int = typer.Argument(..., min=1, help="Capture region width (px)"),
    h: int = typer.Argument(..., min=1, help="Capture region height (px)"),
    audio: bool = typer.Option(
        False, "--audio/--no-audio",
        help="Pipe system audio alongside the video feed (Linux: PipeWire)",
    ),
) -> None:
    """Stream a screen region to the LCD until interrupted.

    Wraps :class:`StartScreencast` — the GUI ``ScreencastHandler``
    subscriber drives the per-frame Qt capture timer.  Ctrl-C calls
    :class:`StopScreencast` for clean teardown.
    """
    log.info(
        "cli display screencast: key=%s x=%s y=%s w=%s h=%s audio=%s",
        key, x, y, w, h, audio,
    )
    import signal

    app_obj = get_app()
    result = app_obj.dispatch(StartScreencast(
        key=key, x=x, y=y, w=w, h=h, audio=audio,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)

    typer.echo(f"Capturing on {key} — Ctrl-C to stop.")
    stopped = {"flag": False}

    def _handle(*_args: object) -> None:
        stopped["flag"] = True

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    while not stopped["flag"]:
        signal.pause()

    stop_result = app_obj.dispatch(StopScreencast(key=key))
    typer.echo(stop_result.message)
    if not stop_result.ok:
        raise typer.Exit(code=1)


@app.command("stop-screencast")
def stop_screencast(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """Stop an active screencast started by another process (daemon/API)."""
    log.info("cli display stop-screencast: key=%s", key)
    result = get_app().dispatch(StopScreencast(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("snapshot")
def snapshot(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """Print the persisted LCD state for a device."""
    log.info("cli display snapshot: key=%s", key)
    result = get_app().dispatch(LcdSnapshot(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    typer.echo(f"  orientation      {result.orientation}")
    typer.echo(f"  brightness       {result.brightness}%")
    typer.echo(f"  current_theme    {result.current_theme}")
    typer.echo(f"  overlay_enabled  {result.overlay_enabled}")
    typer.echo(f"  mask_path        {result.mask_path}")
    typer.echo(f"  mask_visible     {result.mask_visible}")
    typer.echo(f"  mask_position    {result.mask_position}")
    typer.echo(f"  fit_mode         {result.fit_mode}")
    typer.echo(f"  split_mode       {result.split_mode}")
    typer.echo(f"  time_format      {result.time_format}")
    typer.echo(f"  date_format      {result.date_format}")
    typer.echo(f"  temp_unit        {result.temp_unit}")
