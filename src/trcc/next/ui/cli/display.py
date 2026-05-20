"""CLI `display` group — orientation, brightness, theme, send."""
from __future__ import annotations

from pathlib import Path

import typer

from ...core.commands import (
    ApplyMask,
    EnableOverlay,
    LoadTheme,
    PlayVideo,
    RenderAndSend,
    SendColor,
    SetBrightness,
    SetFitMode,
    SetMaskPosition,
    SetMaskVisible,
    SetOrientation,
    SetSplitMode,
    StopVideo,
    UploadBootAnimation,
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


@app.command("slideshow")
def slideshow(
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
    """Cycle through every theme under *themes_dir* on a fixed interval.

    Blocking — runs until Ctrl-C. Each tick dispatches ``LoadTheme``
    for the next theme in alphabetical order. Themes that fail to load
    are skipped with a warning; the loop continues.
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
