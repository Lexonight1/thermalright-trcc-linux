"""CLI `display` group — orientation, brightness, theme, send."""
from __future__ import annotations

from pathlib import Path

import typer

from ...core.commands import LoadTheme, RenderAndSend, SetBrightness, SetOrientation
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
