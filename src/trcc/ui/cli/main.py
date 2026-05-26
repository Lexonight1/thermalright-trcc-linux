"""TRCC CLI — top-level typer app.

Every CLI verb builds a Command and hands it to App.dispatch.  The
rendering of results (stdout / exit codes) is the only logic that lives
here; all business rules live in Commands.
"""
from __future__ import annotations

import logging
from pathlib import Path

import typer

from . import config, device, display, led, system, theme
from ._ctx import get_app

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


@app.command("quickstart")
def quickstart(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help=(
            "If a device is found, also connect + push a green test frame.  "
            "Default: stop after scan so you can inspect what's there."
        ),
    ),
) -> None:
    """Guided first-session flow for new users.

    Runs the doctor, scans for devices, and walks you through what to
    do next.  Pass ``--yes`` to also test-connect to the first device
    found.  Safe to re-run any time.
    """
    from ...core.commands import (
        ConnectDevice,
        MarkFirstRunDone,
        RunQuickstart,
        SendColor,
    )

    app_obj = get_app()
    result = app_obj.dispatch(RunQuickstart())

    for step in result.steps:
        marker = {"ok": "[ ok ]", "warn": "[warn]", "fail": "[fail]"}.get(
            step.status, "[ -- ]",
        )
        typer.echo(f"{marker}  {step.name:8}  {step.message}")
        if step.next_step_hint:
            typer.echo(f"          → {step.next_step_hint}")

    if not result.completed_ok:
        # If we stopped at a FAIL, exit non-zero so scripts can branch.
        if any(s.status == "fail" for s in result.steps):
            raise typer.Exit(code=1)
        return

    # Quickstart got through scan.  If --yes was passed and a device
    # exists, do the smallest possible end-to-end test: connect + green.
    if yes:
        scan_step = next(
            (s for s in result.steps if s.name == "scan"), None,
        )
        if scan_step is None or "First:" not in scan_step.message:
            return
        # Pull the key out of the scan step's message (we put it there
        # in the Service so any UI can re-parse).
        try:
            key = scan_step.message.split("First:")[1].strip().rstrip(".")
        except IndexError:
            return
        typer.echo("")
        typer.echo(f"Attempting handshake with {key} …")
        connect = app_obj.dispatch(ConnectDevice(key=key))
        typer.echo(f"  {connect.message}")
        if not connect.ok:
            raise typer.Exit(code=1)
        typer.echo("Pushing test frame (solid green) — your screen should "
                   "turn green for a moment…")
        color = app_obj.dispatch(SendColor(key=key, r=0, g=255, b=0))
        typer.echo(f"  {color.message}")
        if color.ok:
            typer.echo(
                "\nQuickstart succeeded on real hardware.  "
                "trcc is ready to use.",
            )
            app_obj.dispatch(MarkFirstRunDone())
        else:
            raise typer.Exit(code=1)


@app.command("qtgui")
def qtgui() -> None:
    """Launch the Qt-native GUI (clean-slate, layout-driven).

    This is the rebuild's GUI — built up over G1–G5 and used during
    development.  See ``gui`` for the legacy Windows-style port.
    """
    from ..qtgui import launch
    raise typer.Exit(code=launch())


@app.command("gui")
def gui() -> None:
    """Launch the legacy Windows-style GUI (port in progress).

    Today's shell hosts the device sidebar + a diagnostic content
    area — enough to prove the legacy-on-next/-bus pattern end to
    end on real hardware.  Real feature panels (LCD handler, theme
    settings, mask, video, LED) land in subsequent passes.
    """
    from ..gui import launch
    raise typer.Exit(code=launch())


@app.command("api")
def api(
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind address"),
    port: int = typer.Option(8080, "--port", "-p", help="Bind port"),
    token: str | None = typer.Option(
        None, "--token", "-t",
        help=(
            "Persistent API token.  When set, every request must carry "
            "`X-API-Token: <token>`.  When omitted with --host 127.0.0.1, "
            "the API is unauth'd (loopback dev mode).  Omitting it with "
            "any other --host is REJECTED — refusing to bind a public "
            "interface without auth.  Use --token random:<n> to generate."
        ),
    ),
    pair: bool = typer.Option(
        False, "--pair",
        help=(
            "Show a one-time 6-char pairing code in the terminal.  "
            "Remote devices POST it to /pair to exchange for the API "
            "token.  Requires --token."
        ),
    ),
) -> None:
    """Launch the REST API (FastAPI + uvicorn).

    Three operating modes:

      * ``trcc api`` — loopback only (127.0.0.1), no auth.  Dev default.
      * ``trcc api --host 0.0.0.0 --token <secret>`` — public bind,
        token required on every request.  Use a long random secret.
      * ``trcc api --host 0.0.0.0 --token <secret> --pair`` — same as
        above plus the pairing endpoint; a 6-char code is shown so a
        remote app can fetch the token without out-of-band copy/paste.

    Refusal: ``--host`` other than ``127.0.0.1`` / ``localhost`` without
    ``--token`` exits 2 — would otherwise expose every endpoint to LAN.
    """
    import secrets

    from ..api.main import configure_auth, serve, set_pairing_code

    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    resolved_token: str | None = token
    if resolved_token and resolved_token.startswith("random:"):
        try:
            n = int(resolved_token.split(":", 1)[1])
        except (ValueError, IndexError):
            n = 32
        resolved_token = secrets.token_urlsafe(max(16, n))
        typer.echo(f"Generated random token: {resolved_token}")

    if not is_loopback and not resolved_token:
        typer.echo(
            f"ERROR: refusing to bind {host} without --token — every "
            "endpoint would be reachable from the LAN.  Re-run with "
            "--token <secret> (or --token random:32 to generate one).",
            err=True,
        )
        raise typer.Exit(code=2)

    configure_auth(resolved_token)
    if pair:
        if not resolved_token:
            typer.echo(
                "ERROR: --pair requires --token (the pairing endpoint "
                "returns the token, so the token must be set).",
                err=True,
            )
            raise typer.Exit(code=2)
        code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
                       for _ in range(6))
        set_pairing_code(code)
        typer.echo("")
        typer.echo(f"Pairing code: {code}  (POST /pair?code={code})")
        typer.echo("")

    if host in ("0.0.0.0", "::"):
        from ...adapters.infra.network import get_lan_ip
        lan_ip = get_lan_ip()
        typer.echo(f"API reachable at: http://{lan_ip}:{port}")
    serve(host=host, port=port)


@app.command("daemon")
def daemon() -> None:
    """Run the background daemon that owns USB + serves CLI/API clients.

    One process per user.  Binds a Unix socket at
    ``$XDG_RUNTIME_DIR/trcc.sock`` and serves Commands until
    SIGTERM / SIGINT or a remote ``trcc kill``.  Sets
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


@app.command("shell")
def shell() -> None:
    """Open an interactive prompt sharing one App across commands.

    Each line is parsed as if it were a fresh ``trcc`` invocation,
    but the App is built once and reused — no per-command handshake.
    In daemon mode the App is an AppProxy that round-trips each line
    to the running daemon.  Ctrl-D or ``exit`` quits.
    """
    from .shell import run_shell
    raise typer.Exit(code=run_shell(app))


# =========================================================================
# Top-level aliases for the most-used legacy diagnostic commands.  Users
# don't have to remember which sub-typer holds them — `trcc report`,
# `trcc detect`, `trcc doctor`, `trcc sensors` all still work.
# =========================================================================


@app.command("report", rich_help_panel="Diagnostics")
def _alias_report(
    output: Path | None = typer.Option(
        None, "--output", "-o",
        help="Write the report to this path instead of stdout.",
    ),
    log_lines: int = typer.Option(
        1000, "--log-lines",
        help="How many trailing log lines to include.",
    ),
) -> None:
    """Alias for `trcc system debug-report` — full diagnostic dump."""
    from ...core.commands import GenerateDebugReport
    result = get_app().dispatch(GenerateDebugReport(
        output_path=output, log_tail_lines=log_lines,
    ))
    if output is None:
        typer.echo(result.rendered_text)
    else:
        typer.echo(result.message)


@app.command("detect", rich_help_panel="Diagnostics")
def _alias_detect() -> None:
    """Alias for `trcc device list` — list attached devices."""
    from .device import list_devices
    list_devices()


@app.command("doctor", rich_help_panel="Diagnostics")
def _alias_doctor() -> None:
    """Alias for `trcc system doctor` — health checks."""
    from .system import doctor
    doctor()


@app.command("sensors", rich_help_panel="Diagnostics")
def _alias_sensors() -> None:
    """Alias for `trcc system sensors` — print sensor readings."""
    from .system import sensors
    sensors()


@app.callback()
def _root(
    verbose: bool = typer.Option(False, "--verbose", "-v",
                                 help="Enable DEBUG-level logging"),
) -> None:
    """Root callback — sets up logging for every subcommand.

    Writes to the rotating file at ``paths.log_file()`` (legacy parity:
    ``~/.trcc/trcc.log``) AND mirrors WARNING+ to stderr.  ``-v`` flips
    both handlers to DEBUG.
    """
    from ...adapters.infra.logging import configure_logging
    from ...core.ports import Platform

    platform = Platform.detect()
    # Windows consoles default to cp1252 and crash on non-ASCII log
    # output — wrap stdout/stderr UTF-8 BEFORE configure_logging
    # attaches the StreamHandler.  No-op on other OSes.
    platform.configure_stdout()
    level = logging.DEBUG if verbose else logging.INFO
    configure_logging(
        platform.paths().log_file(),
        level=level,
        stderr_level=level,
    )


def main() -> None:
    """Entry point for console_scripts and python -m trcc.ui.cli."""
    app()


if __name__ == "__main__":
    main()
