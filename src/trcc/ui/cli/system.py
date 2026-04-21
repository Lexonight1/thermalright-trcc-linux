"""CLI `system` group — setup, sensors, diagnostics."""
from __future__ import annotations

import typer

from ...core.commands import (
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    GetPlatformInfo,
    ReadSensors,
    RunSetup,
)
from ._ctx import get_app

app = typer.Typer(help="System-level operations (setup, sensors, info).",
                  no_args_is_help=True)


@app.command("setup")
def setup(
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="Non-interactive (assume yes to prompts)"),
) -> None:
    """Run the OS-specific setup (udev rules on Linux, WinUSB guide on Windows)."""
    result = get_app().dispatch(RunSetup(interactive=not yes))
    typer.echo(result.message)
    for warning in result.warnings:
        typer.echo(f"  warning: {warning}", err=True)
    raise typer.Exit(code=result.exit_code)


@app.command("sensors")
def sensors() -> None:
    """Print current sensor readings."""
    result = get_app().dispatch(ReadSensors())
    if not result.readings:
        typer.echo("No sensor readings available.")
        return
    for reading in result.readings:
        typer.echo(
            f"  {reading.sensor_id}  {reading.value:.2f} {reading.unit}"
            f"  ({reading.category})"
        )


@app.command("info")
def info() -> None:
    """Show platform info (distro, install method, config dir, permissions)."""
    r = get_app().dispatch(GetPlatformInfo())
    typer.echo(f"Distro:   {r.distro_name}")
    typer.echo(f"Install:  {r.install_method}")
    typer.echo(f"Config:   {r.config_dir}")
    typer.echo(f"Data:     {r.data_dir}")
    typer.echo(f"Logs:     {r.log_file}")
    if r.permission_warnings:
        typer.echo("\nWarnings:")
        for w in r.permission_warnings:
            typer.echo(f"  {w}")
    else:
        typer.echo("\nPermissions: OK")


# ── Autostart subcommands ────────────────────────────────────────────

autostart_app = typer.Typer(
    help="Manage auto-launch-on-login (XDG .desktop on Linux).",
    no_args_is_help=True,
)
app.add_typer(autostart_app, name="autostart")


@autostart_app.command("status")
def autostart_status() -> None:
    """Show whether auto-launch-on-login is enabled."""
    r = get_app().dispatch(GetAutostartStatus())
    state = "enabled" if r.enabled else "disabled"
    typer.echo(f"Autostart: {state}")
    if r.path:
        typer.echo(f"Path:      {r.path}")


@autostart_app.command("enable")
def autostart_enable() -> None:
    """Install the autostart entry (per-user, no sudo required)."""
    r = get_app().dispatch(EnableAutostart())
    typer.echo(r.message)
    typer.echo(f"Path: {r.path}")
    if not r.enabled:
        raise typer.Exit(code=1)


@autostart_app.command("disable")
def autostart_disable() -> None:
    """Remove the autostart entry."""
    r = get_app().dispatch(DisableAutostart())
    typer.echo(r.message)
