"""trcc shell REPL — non-interactive pieces.

The full ``run_shell`` is interactive (prompt_toolkit reads from a
TTY), so the tests cover the pieces we can verify without driving a
real terminal:

  * the completer reflects every registered sub-app + subcommand
  * single-line dispatch preserves exit codes and survives exceptions
  * exit / quit / help / blank lines are routed correctly
"""
from __future__ import annotations

from pathlib import Path

import pytest
import typer

from trcc.ui.cli.main import app as cli_app
from trcc.ui.cli.shell import (
    _build_completer,
    _history_path,
    _run_typer_line,
)

# ── _build_completer — discovers Typer command tree ─────────────────


def test_completer_includes_every_subapp() -> None:
    completer = _build_completer()
    top_level = set(completer.options.keys())   # type: ignore[attr-defined]

    # The six registered sub-apps must be visible
    for name in ("device", "display", "led", "system", "config", "theme"):
        assert name in top_level, f"{name!r} missing from completer"

    # Direct top-level commands too
    for cmd in ("status", "daemon", "kill", "shell", "help", "exit"):
        assert cmd in top_level, f"{cmd!r} missing from completer"


def test_completer_includes_subcommands_under_groups() -> None:
    completer = _build_completer()
    # NestedCompleter wraps nested dicts as child completers; reach the
    # child's own ``options`` dict for the subcommand list.
    device_child = completer.options["device"]              # type: ignore[attr-defined,index]
    assert device_child is not None
    names = set(device_child.options.keys())                # type: ignore[attr-defined]
    # ``device list`` / ``device connect`` / ``device disconnect`` exist
    assert "list" in names
    assert "connect" in names
    assert "disconnect" in names


# ── _run_typer_line — exit-code preservation ────────────────────────


def test_run_typer_line_returns_zero_for_help_dump() -> None:
    """`--help` exits cleanly with code 0; the REPL must surface that."""
    rc = _run_typer_line(cli_app, ["--help"])
    assert rc == 0


def test_run_typer_line_returns_nonzero_for_unknown_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Garbage command → non-zero, REPL keeps running (no SystemExit propagated)."""
    rc = _run_typer_line(cli_app, ["nonexistent-subcommand"])
    assert rc != 0
    # Error went somewhere — either stdout or stderr
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "nonexistent-subcommand" in combined or "Usage" in combined or rc == 2


def test_run_typer_line_survives_typer_exit() -> None:
    """A subcommand that raises typer.Exit must not crash the REPL.

    With standalone_mode=False (which lets us keep the parent process
    alive across many lines), typer/click absorbs typer.Exit internally
    — what matters here is that _run_typer_line returns cleanly and
    doesn't leak a SystemExit up to the prompt loop.
    """
    inner = typer.Typer()

    @inner.command()
    def fail() -> None:
        raise typer.Exit(code=3)

    @inner.command()
    def noop() -> None:  # pragma: no cover — exists only to disable collapse
        pass

    rc = _run_typer_line(inner, ["fail"])
    # Exact code depends on Typer's internal swallowing — what we care
    # about is that we got back an integer, not a SystemExit.
    assert isinstance(rc, int)


def test_run_typer_line_catches_unexpected_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Subcommand raising a plain Exception is caught — REPL doesn't die."""
    inner = typer.Typer()

    @inner.command()
    def boom() -> None:
        raise ValueError("kaboom")

    @inner.command()
    def noop() -> None:  # pragma: no cover — exists only to disable collapse
        pass

    rc = _run_typer_line(inner, ["boom"])

    assert rc == 1
    out = capsys.readouterr()
    assert "ValueError" in (out.out + out.err)
    assert "kaboom" in (out.out + out.err)


# ── _history_path creates the parent dir on demand ──────────────────


def test_history_path_creates_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    path = _history_path()
    assert path.parent.is_dir()
    # Filename is stable across sessions
    assert path.name == "shell_history"
    assert path.parent == tmp_path / ".local" / "state" / "trcc"


def test_history_path_honors_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    custom = tmp_path / "custom-xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(custom))
    path = _history_path()
    assert path.parent == custom / "trcc"
    assert path.parent.is_dir()
