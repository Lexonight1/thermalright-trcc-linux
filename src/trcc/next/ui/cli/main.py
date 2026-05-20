"""TRCC CLI — top-level typer app.

Every CLI verb builds a Command and hands it to App.dispatch.  The
rendering of results (stdout / exit codes) is the only logic that lives
here; all business rules live in Commands.
"""
from __future__ import annotations

import logging

import typer

from . import config, device, display, led, system, theme

app = typer.Typer(
    help="TRCC — Thermalright LCD/LED cooler control (clean-slate build).",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(device.app, name="device")
app.add_typer(display.app, name="display")
app.add_typer(led.app, name="led")
app.add_typer(system.app, name="system")
app.add_typer(config.app, name="config")
app.add_typer(theme.app, name="theme")


@app.command("gui")
def gui() -> None:
    """Launch the desktop GUI (PySide6)."""
    from ..gui import launch
    raise typer.Exit(code=launch())


@app.command("api")
def api(
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind address"),
    port: int = typer.Option(8080, "--port", "-p", help="Bind port"),
) -> None:
    """Launch the REST API (FastAPI + uvicorn)."""
    from ..api.main import serve
    serve(host=host, port=port)


@app.command("daemon")
def daemon() -> None:
    """Run the background daemon that owns USB + serves CLI/API clients.

    One process per user.  Binds a Unix socket at
    ``$XDG_RUNTIME_DIR/trcc-next.sock`` and serves Commands until
    SIGTERM / SIGINT or a remote ``trcc-next kill``.  Sets
    ``TRCC_NEXT_DAEMON=1`` to route clients through this daemon.
    """
    from ...daemon import run_daemon
    raise typer.Exit(code=run_daemon())


@app.command("kill")
def kill() -> None:
    """Ask the running daemon to shut down, return when its socket is gone."""
    from ...daemon import kill_daemon
    if kill_daemon():
        typer.echo("Daemon stopped.")
    else:
        typer.echo("Daemon failed to stop within timeout.", err=True)
        raise typer.Exit(code=1)


@app.command("status")
def status() -> None:
    """Report whether the daemon is currently reachable."""
    from ...ipc import daemon_running, socket_path
    if daemon_running():
        typer.echo(f"Daemon is running (socket: {socket_path()}).")
    else:
        typer.echo(f"No daemon reachable at {socket_path()}.")
        raise typer.Exit(code=1)


@app.callback()
def _root(
    verbose: bool = typer.Option(False, "--verbose", "-v",
                                 help="Enable DEBUG-level logging"),
) -> None:
    """Root callback — sets up logging for every subcommand."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    """Entry point for console_scripts and python -m trcc.next.ui.cli."""
    app()


if __name__ == "__main__":
    main()
