"""CLI `system` group — setup, sensors, diagnostics."""
from __future__ import annotations

from pathlib import Path

import typer

from ...core.commands import (
    CheckForUpdate,
    ControlCenterSnapshot,
    DisableAutostart,
    EnableAutostart,
    GenerateDebugReport,
    GetAutostartStatus,
    GetFirstRunStatus,
    GetPlatformInfo,
    ListDisks,
    ListFonts,
    ListGpus,
    ListLanguages,
    MarkFirstRunDone,
    ReadSensors,
    RunDoctor,
    RunHealthCheck,
    RunSetup,
    RunUpgrade,
    SetHddEnabled,
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


@app.command("list-gpus")
def list_gpus() -> None:
    """List GPUs exposed by the sensors aggregator."""
    result = get_app().dispatch(ListGpus())
    typer.echo(result.message)
    for g in result.gpus:
        discrete = "discrete" if g.is_discrete else "integrated"
        typer.echo(f"  {g.key:20} {g.name}  ({discrete})")


@app.command("list-fonts")
def list_fonts() -> None:
    """List font families Qt can see."""
    result = get_app().dispatch(ListFonts())
    typer.echo(result.message)
    for name in result.fonts:
        typer.echo(f"  {name}")


@app.command("list-disks")
def list_disks() -> None:
    """List disk partitions (for use with `led disk-index`)."""
    result = get_app().dispatch(ListDisks())
    typer.echo(result.message)
    for d in result.disks:
        typer.echo(f"  [{d.index}] {d.device:20} -> {d.mountpoint}")


@app.command("list-languages")
def list_languages() -> None:
    """List every UI language the i18n table supports."""
    result = get_app().dispatch(ListLanguages())
    typer.echo(result.message)
    for lang in result.languages:
        typer.echo(
            f"  {lang.code:6} {lang.name:25} "
            f"{lang.translated_keys} translated",
        )


@app.command("doctor")
def doctor() -> None:
    """Run health checks — exits 1 on any FAIL.

    The reporter-friendly summary tells you what's wrong + how to fix
    it.  For a copy-paste GitHub-issue dump, use `system debug-report`
    instead.
    """
    result = get_app().dispatch(RunDoctor())
    typer.echo(result.rendered)
    raise typer.Exit(code=result.exit_code)


@app.command("debug-report")
def debug_report(
    output: Path | None = typer.Option(
        None, "--output", "-o",
        help=(
            "Write the report to this path instead of stdout.  "
            "Recommended when filing a GitHub issue — attach the file."
        ),
    ),
    log_lines: int = typer.Option(
        1000, "--log-lines",
        help="How many trailing log lines to include (default 1000).",
    ),
) -> None:
    """Generate a debug report bundle for GitHub issues."""
    result = get_app().dispatch(GenerateDebugReport(
        output_path=output, log_tail_lines=log_lines,
    ))
    if output is None:
        typer.echo(result.rendered_text)
    else:
        typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("check-update")
def check_update() -> None:
    """Ask GitHub Releases whether a newer version is available."""
    r = get_app().dispatch(CheckForUpdate())
    typer.echo(r.message)
    if r.release_url:
        typer.echo(f"  Release page: {r.release_url}")
    if not r.ok:
        raise typer.Exit(code=1)


@app.command("upgrade")
def upgrade(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Skip confirmation and run the upgrade subprocess.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the command that would run, don't execute it.",
    ),
) -> None:
    """Upgrade trcc-linux via the detected package manager."""
    if not yes and not dry_run:
        typer.echo("Refusing to run upgrade without --yes (sudo subprocess).")
        typer.echo("Re-run with --dry-run to see the command, or --yes to confirm.")
        raise typer.Exit(code=2)
    r = get_app().dispatch(RunUpgrade(dry_run=dry_run))
    typer.echo(r.message)
    if r.stdout:
        typer.echo(r.stdout)
    if r.stderr:
        typer.echo(r.stderr, err=True)
    if not r.ok:
        raise typer.Exit(code=r.exit_code or 1)


@app.command("first-run-status")
def first_run_status() -> None:
    """Show whether trcc has been set up on this machine yet."""
    r = get_app().dispatch(GetFirstRunStatus())
    typer.echo(r.message)
    typer.echo(f"  marker: {r.marker_path}")


@app.command("mark-setup-done")
def mark_setup_done() -> None:
    """Tell trcc the welcome flow has been completed."""
    r = get_app().dispatch(MarkFirstRunDone())
    typer.echo(r.message)


@app.command("health")
def health() -> None:
    """Quick read-only health report — same checks as `doctor`, no exit code."""
    result = get_app().dispatch(RunHealthCheck())
    typer.echo(result.message)
    for c in result.checks:
        typer.echo(f"  [{c.severity:4}] {c.name:22}  {c.message}")


@app.command("hdd-enabled")
def hdd_enabled(
    state: str = typer.Argument(..., help="'on' or 'off'"),
) -> None:
    """Toggle inclusion of HDD metrics in sensor broadcasts."""
    if state.lower() not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    result = get_app().dispatch(
        SetHddEnabled(enabled=state.lower() == "on"),
    )
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("snapshot")
def snapshot() -> None:
    """Print the AppSettings snapshot (language, GPU, refresh interval)."""
    r = get_app().dispatch(ControlCenterSnapshot())
    typer.echo(r.message)
    typer.echo(f"  language          {r.language}")
    typer.echo(f"  temp_unit         {r.temp_unit}")
    typer.echo(f"  active_device     {r.active_device}")
    typer.echo(f"  active_gpu        {r.active_gpu}")
    typer.echo(f"  refresh_interval  {r.refresh_interval_s}s")


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
