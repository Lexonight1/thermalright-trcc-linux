"""CLI `led` group — set LED colors on RGB LED controllers."""
from __future__ import annotations

import typer

from ...core.commands import RenderLed, SetLedColors
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
    color: str = typer.Option("ff0000", "--color", "-c",
                              help="Hex color (#rrggbb) for the lit segments"),
    phase: int = typer.Option(0, "--phase", "-p",
                              help="Rotation phase for multi-phase displays"),
) -> None:
    """Render one segment-display frame from current sensors + send.

    Pulls the handshake-resolved style, computes the on/off LED mask
    from the current sensor snapshot, and sends the requested color to
    every lit LED.  Stateless — call repeatedly (or on a timer) to
    animate.  Use ``set-colors`` instead for direct per-LED control.
    """
    parsed = _parse_hex_color(color)
    result = get_app().dispatch(RenderLed(
        key=key, color=parsed, phase=phase,
    ))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
