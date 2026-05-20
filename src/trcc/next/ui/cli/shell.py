"""Interactive REPL — share one App across many Commands.

Each Typer subcommand normally pays full Python startup + an App build.
The shell keeps a single App alive for the session, runs each line
through the same Typer dispatcher, and provides command/path
completion via prompt_toolkit.

Lifecycle::

    $ trcc-next shell
    trcc-next> device discover
    trcc-next> device connect 0402:3922
    trcc-next> display color 0402:3922 ff0000
    trcc-next> ^D            # or `exit`

In daemon mode (``TRCC_NEXT_DAEMON=1``) the cached App is the
``AppProxy`` so every line round-trips to the daemon.  In default mode
the App is in-process and reused across commands so handshakes don't
repeat per invocation.
"""
from __future__ import annotations

import logging
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.history import FileHistory

from . import config, device, display, led, system, theme
from ._ctx import get_app

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


_PROMPT = "trcc-next> "
_EXIT_COMMANDS: frozenset[str] = frozenset({"exit", "quit"})
_HELP_COMMANDS: frozenset[str] = frozenset({"help", "?"})


def _history_path() -> Path:
    """Persist REPL history across sessions under XDG_STATE_HOME."""
    base = Path.home() / ".local" / "state" / "trcc-next"
    base.mkdir(parents=True, exist_ok=True)
    return base / "shell_history"


def _build_completer() -> NestedCompleter:
    """Build a NestedCompleter from the registered Typer sub-apps.

    Two-level menu — the top level lists every group (``device``,
    ``display``, …) and each group lists its subcommands.  Arguments
    don't get completion (we'd need to introspect device keys live —
    nice-to-have, not blocking).
    """
    sub_apps: dict[str, typer.Typer] = {
        "device":  device.app,
        "display": display.app,
        "led":     led.app,
        "system":  system.app,
        "config":  config.app,
        "theme":   theme.app,
    }
    options: dict[str, dict[str, None] | None] = {}
    for name, sub in sub_apps.items():
        options[name] = {info.name or "": None
                         for info in sub.registered_commands}
    # Top-level direct commands (status, daemon, kill, gui, api, shell)
    for cmd in ("status", "daemon", "kill", "gui", "api", "shell",
                "help", "exit", "quit"):
        options[cmd] = None
    return NestedCompleter.from_nested_dict(options)


def _run_typer_line(typer_app: typer.Typer, argv: list[str]) -> int:
    """Run a line of CLI args through *typer_app*.

    Typer's ``__call__`` raises ``SystemExit`` for normal termination —
    we catch and surface the exit code so the REPL stays alive.
    """
    try:
        typer_app(argv, standalone_mode=False)
        return 0
    except typer.Exit as e:
        return e.exit_code
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 1
    except KeyboardInterrupt:
        typer.echo("(interrupted)", err=True)
        return 130
    except Exception as e:
        # Surface the error without killing the REPL — same shape as
        # Typer's standalone error handler.
        typer.echo(f"Error: {type(e).__name__}: {e}", err=True)
        log.debug("REPL exception", exc_info=True)
        return 1


def run_shell(typer_app: typer.Typer) -> int:
    """Launch the interactive shell.  Returns process exit code."""
    # Warm the App so the first command isn't slowed by a fresh build.
    _ = get_app()

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(_history_path())),
        completer=_build_completer(),
    )
    typer.echo(
        "trcc-next interactive shell — `help` for command list, "
        "`exit` (or Ctrl-D) to quit.",
    )

    while True:
        try:
            line = session.prompt(_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo()
            break
        if not line:
            continue
        if line in _EXIT_COMMANDS:
            break
        if line in _HELP_COMMANDS:
            _run_typer_line(typer_app, ["--help"])
            continue
        try:
            argv = shlex.split(line)
        except ValueError as e:
            typer.echo(f"Parse error: {e}", err=True)
            continue
        _run_typer_line(typer_app, argv)

    return 0


def main() -> int:
    """Standalone entry point for ``python -m trcc.next.ui.cli.shell``."""
    from .main import app as typer_app
    return run_shell(typer_app)


if __name__ == "__main__":
    sys.exit(main())
