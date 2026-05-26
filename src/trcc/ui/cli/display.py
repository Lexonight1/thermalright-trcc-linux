"""CLI `display` group — orientation, brightness, theme, send."""
from __future__ import annotations

from pathlib import Path

import typer

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
    LoadImage,
    LoadTheme,
    LoadVideo,
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
    SetSlideshow,
    SetSplitMode,
    StopVideo,
    UpdateOverlayElement,
    UploadBootAnimation,
    UploadCustomMask,
)
from ._ctx import get_app

app = typer.Typer(help="Configure device display (theme / orientation / brightness).",
                  no_args_is_help=True)


@app.command("set-orientation")
def set_orientation(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
    degrees: int = typer.Argument(..., help="Rotation: 0, 90, 180, or 270"),
) -> None:
    """Set per-device rotation."""
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
    result = get_app().dispatch(SetBrightness(key=key, percent=percent))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


def _parse_hex_color(hex_str: str) -> tuple[int, int, int] | None:
    """Parse a 6-char hex color into ``(r, g, b)``. Accepts a leading ``#``."""
    s = hex_str.lstrip("#").strip()
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


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
    result = get_app().dispatch(PlayVideo(key=key, path=path, fps=fps))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("stop-video")
def stop_video(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """Clear the video playback override (returns to the active theme)."""
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
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    result = get_app().dispatch(
        PauseVideo(key=key, paused=state.lower() == "on"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("seek-video")
def seek_video(
    key: str = typer.Argument(..., help="Device key"),
    frame: int = typer.Argument(..., help="Frame index to jump to"),
) -> None:
    """Jump the playback cursor to a specific frame."""
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
    result = get_app().dispatch(RestoreLastTheme(key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("snapshot")
def snapshot(
    key: str = typer.Argument(..., help="Device key, e.g. 0402:3922"),
) -> None:
    """Print the persisted LCD state for a device."""
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
