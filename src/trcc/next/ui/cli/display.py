"""CLI `display` group — orientation, brightness, theme, send."""
from __future__ import annotations

from pathlib import Path

import typer

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
