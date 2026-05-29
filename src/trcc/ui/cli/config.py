"""CLI ``config`` group — app-global preferences (control center)."""
from __future__ import annotations

import typer

from ...core.commands import (
    SetDateFormat,
    SetGpuDevice,
    SetLanguage,
    SetRefreshInterval,
    SetTempUnit,
    SetTimeFormat,
)
from ._ctx import get_app

app = typer.Typer(
    help="App-global preferences: temp unit, language, GPU, refresh interval.",
    no_args_is_help=True,
)


@app.command("temp-unit")
def temp_unit(
    unit: str = typer.Argument(..., help="Either 'C' or 'F'"),
) -> None:
    """Set the global temperature unit (propagates to every device)."""
    result = get_app().dispatch(SetTempUnit(unit=unit.upper()))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("language")
def language(
    lang: str = typer.Argument(..., help="ISO 639-1 code, e.g. 'en', 'zh', 'fr'"),
) -> None:
    """Set the UI language."""
    result = get_app().dispatch(SetLanguage(language=lang))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("gpu")
def gpu(
    key: str = typer.Argument(
        "", help="GPU sensor key (e.g. 'nvidia:0') or '' to clear",
    ),
) -> None:
    """Pick the primary GPU for sensor overlays.  Empty string = auto."""
    result = get_app().dispatch(SetGpuDevice(gpu_key=key))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("refresh-interval")
def refresh_interval(
    seconds: float = typer.Argument(
        ..., help="Seconds between metric refreshes (0.1 to 60.0)",
    ),
) -> None:
    """Set the global metrics-refresh / render-and-send tick interval."""
    result = get_app().dispatch(SetRefreshInterval(seconds=seconds))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("time-format")
def time_format(
    fmt: str = typer.Argument(..., help="LCD clock format: '12h' or '24h'"),
) -> None:
    """Set the global LCD overlay clock format."""
    result = get_app().dispatch(SetTimeFormat(fmt=fmt))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("date-format")
def date_format(
    fmt: str = typer.Argument(
        ..., help="LCD date format, e.g. 'yyyy/MM/dd', 'dd.MM.yyyy', 'MM/dd/yyyy'",
    ),
) -> None:
    """Set the global LCD overlay date format."""
    result = get_app().dispatch(SetDateFormat(fmt=fmt))
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
