"""CLI `led` group — set LED colors on RGB LED controllers."""
from __future__ import annotations

import typer

from ...core.commands import (
    EnableLedTestMode,
    RenderLed,
    SetLedBrightness,
    SetLedColor,
    SetLedColors,
    SetLedLoadSource,
    SetLedMode,
    SetLedTempSource,
)
from ...core.led_models import LEDMode
from ._ctx import get_app

app = typer.Typer(help="RGB LED control.", no_args_is_help=True)


def _parse_hex_color(raw: str) -> tuple[int, int, int]:
    """Parse '#rrggbb' or 'rrggbb' → (r, g, b)."""
    raw = raw.lstrip("#").strip()
    if len(raw) != 6:
        raise typer.BadParameter(f"Invalid hex color: {raw!r}")
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError as e:
        raise typer.BadParameter(f"Invalid hex color: {raw!r}") from e


def _parse_mode(raw: str) -> LEDMode:
    """Parse a mode name (case-insensitive) into a LEDMode."""
    key = raw.upper()
    try:
        return LEDMode[key]
    except KeyError as e:
        names = ", ".join(m.name.lower() for m in LEDMode)
        raise typer.BadParameter(
            f"Unknown LED mode {raw!r}; expected one of: {names}"
        ) from e


@app.command("set-colors")
def set_colors(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    colors: list[str] = typer.Argument(..., help="Hex colors (#rrggbb), one per LED"),
    brightness: int = typer.Option(100, "--brightness", "-b",
                                   help="Global brightness 0–100"),
    off: bool = typer.Option(False, "--off",
                             help="Force all LEDs off (overrides colors)"),
) -> None:
    """Push a full LED color update."""
    parsed = [_parse_hex_color(c) for c in colors]
    result = get_app().dispatch(SetLedColors(
        key=key, colors=parsed,
        global_on=not off, brightness=brightness,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("render")
def render(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    color: str = typer.Option(None, "--color", "-c",
                              help="Override hex color (#rrggbb); omit to use the saved color"),
    phase: int = typer.Option(0, "--phase", "-p",
                              help="Rotation phase for multi-phase displays"),
) -> None:
    """Render one LED frame from current settings + sensors and send.

    Reads the device's saved mode / color / brightness from Settings,
    advances the engine's phase counters on ``app.led_runtime``, and
    sends one tick.  Pass ``--color`` to override the saved color
    (treated as STATIC at full brightness — diagnostic shape).
    """
    override = _parse_hex_color(color) if color else None
    result = get_app().dispatch(RenderLed(
        key=key, color=override, phase=phase,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("mode")
def set_mode(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    mode: str = typer.Argument(
        ..., help="One of: static, breathing, colorful, rainbow, temp_linked, load_linked",
    ),
) -> None:
    """Set the LED animation mode (persists)."""
    result = get_app().dispatch(SetLedMode(key=key, mode=_parse_mode(mode)))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("color")
def set_color(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    color: str = typer.Argument(..., help="Hex color (#rrggbb)"),
) -> None:
    """Set the LED color used by STATIC / BREATHING / COLORFUL modes."""
    parsed = _parse_hex_color(color)
    result = get_app().dispatch(SetLedColor(key=key, color=parsed))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("brightness")
def set_brightness(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    percent: int = typer.Argument(..., min=0, max=100, help="Brightness 0-100"),
) -> None:
    """Set the global LED brightness (persists)."""
    result = get_app().dispatch(SetLedBrightness(key=key, percent=percent))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("test-mode")
def test_mode(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    on: bool = typer.Argument(..., help="Enable (true) or disable (false)"),
) -> None:
    """Toggle the 4-color diagnostic test cycle."""
    result = get_app().dispatch(EnableLedTestMode(key=key, enabled=on))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("temp-source")
def temp_source(
    key: str = typer.Argument(..., help="LED device key"),
    source: str = typer.Argument(..., help="'cpu' or 'gpu'"),
) -> None:
    """Pick the sensor source for TEMP_LINKED mode."""
    result = get_app().dispatch(SetLedTempSource(key=key, source=source))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("load-source")
def load_source(
    key: str = typer.Argument(..., help="LED device key"),
    source: str = typer.Argument(..., help="'cpu' or 'gpu'"),
) -> None:
    """Pick the sensor source for LOAD_LINKED mode."""
    result = get_app().dispatch(SetLedLoadSource(key=key, source=source))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("play")
def play(
    key: str = typer.Argument(..., help="LED device key, e.g. 0416:8001"),
    interval: float = typer.Option(
        None, "--interval", "-i",
        help="Tick interval in seconds (default: AppSettings.refresh_interval_s)",
    ),
) -> None:
    """Run the LED render ticker until Ctrl-C.

    Mirrors ``display play`` — dispatches ``RenderLed`` every tick so
    BREATHING / COLORFUL / RAINBOW animations advance.  Stops cleanly
    on SIGINT.
    """
    import time

    app_obj = get_app()
    tick_s = interval if interval is not None else app_obj.settings.app.refresh_interval_s
    tick_s = max(0.05, tick_s)

    typer.echo(f"Animating LED on {key} at {tick_s:.2f}s intervals (Ctrl-C to stop)…")
    try:
        while True:
            result = app_obj.dispatch(RenderLed(key=key))
            if not result.ok:
                typer.echo(f"  tick failed: {result.message}", err=True)
                raise typer.Exit(code=1)
            time.sleep(tick_s)
    except KeyboardInterrupt:
        typer.echo("\nStopped.")
