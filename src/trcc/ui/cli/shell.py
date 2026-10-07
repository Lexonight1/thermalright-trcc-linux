"""Interactive REPL — share one App across many Commands.

Each Typer subcommand normally pays full Python startup + an App build.
The shell keeps a single App alive for the session, runs each line
through the same Typer dispatcher, and provides command/path
completion via prompt_toolkit.

Lifecycle::

    $ trcc shell
    trcc> device discover
    trcc> device connect 0402:3922
    trcc> display color 0402:3922 ff0000
    trcc> ^D            # or `exit`

The cached App is the shared one (an ``AppProxy``), so every line
round-trips to it; with ``TRCC_DAEMON=0`` it is in-process and reused
across commands so handshakes don't repeat per invocation.  When the shared
App quits (``trcc kill``) the shell leaves too; when it dies, the next line
starts a new one.
"""
from __future__ import annotations

import logging
import os
import shlex
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import NestedCompleter
from prompt_toolkit.history import FileHistory

from . import config, device, display, led, system, theme
from ._ctx import compose_app, get_app

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


_PROMPT = "trcc> "
_EXIT_COMMANDS: frozenset[str] = frozenset({"exit", "quit"})
_HELP_COMMANDS: frozenset[str] = frozenset({"help", "?"})


def _history_path() -> Path:
    """Persist REPL history across sessions under ``$XDG_STATE_HOME``."""
    log.debug("_history_path")
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    target = base / "trcc"
    target.mkdir(parents=True, exist_ok=True)
    return target / "shell_history"


def _build_completer() -> NestedCompleter:
    """Build a NestedCompleter from the registered Typer sub-apps.

    Two-level menu — the top level lists every group (``device``,
    ``display``, …) and each group lists its subcommands.  Arguments
    don't get completion (we'd need to introspect device keys live —
    nice-to-have, not blocking).
    """
    log.debug("_build_completer")
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


class _AppWatch:
    """How the shell's App went away, if it did -- set from the proxy's thread."""

    def __init__(self) -> None:
        log.debug("_AppWatch.__init__")
        self.stopped = threading.Event()
        self.lost = threading.Event()

    def watch(self) -> None:
        """Watch the App the shell dispatches on now."""
        log.debug("_AppWatch.watch")
        get_app().on_app_gone(self.stopped.set, self.lost.set)

    def renew(self) -> None:
        """After a crash: drop the dead App so the next line starts a new one."""
        log.info("_AppWatch.renew: the App died — the next command starts one")
        self.lost.clear()
        get_app.cache_clear()
        compose_app.cache_clear()
        self.watch()


def run_shell(typer_app: typer.Typer) -> int:
    """Launch the interactive shell.  Returns process exit code."""
    log.debug("run_shell: typer_app=%s", typer_app)
    # Warm the App so the first command isn't slowed by a fresh build.
    watch = _AppWatch()
    watch.watch()

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(_history_path())),
        completer=_build_completer(),
    )
    typer.echo(
        "trcc interactive shell — `help` for command list, "
        "`exit` (or Ctrl-D) to quit.",
    )

    while True:
        if watch.stopped.is_set():
            typer.echo("TRCC was stopped — leaving the shell.")
            break
        if watch.lost.is_set():
            typer.echo("TRCC's background App stopped unexpectedly — the "
                       "next command starts a new one.", err=True)
            watch.renew()
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
    """Standalone entry point for ``python -m trcc.ui.cli.shell``."""
    log.debug("main")
    from .main import app as typer_app
    return run_shell(typer_app)


if __name__ == "__main__":
    sys.exit(main())
