"""Docs must describe the CLI that exists.

Two guards, one problem.  ``doc/REFERENCE_CLI.md`` had drifted a whole cutover
behind the code: it documented the pre-cutover flat names (``trcc send``,
``trcc theme-load``, ``trcc led-color``), a ``trcc detect --all`` flag that
never existed, and two commands that had been deleted outright — while
covering roughly a third of the commands that DO exist.  Anyone following it
hit "No such command" on their first copy-paste (#247).

* The reference is now GENERATED from the Typer tree, so the first test just
  keeps the committed copy in lockstep — same contract as the man pages.
* The prose guides stay hand-written, so the second test resolves every
  ``trcc …`` command they tell a user to run against the real command tree.

Prose can't be kept true across 150+ commands by discipline alone; that is
what these automate.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev"))

import gen_cli_reference  # noqa: E402  # pyright: ignore[reportMissingImports]

_DOC_DIR = _ROOT / "doc"


# =========================================================================
# 1. The generated reference is current
# =========================================================================


def test_committed_cli_reference_is_current() -> None:
    """The committed page matches what the generator produces right now."""
    target = _DOC_DIR / "REFERENCE_CLI.md"
    assert target.exists(), (
        "doc/REFERENCE_CLI.md is missing — run: "
        "PYTHONPATH=src python3 dev/gen_cli_reference.py"
    )
    assert target.read_text() == gen_cli_reference.generate(), (
        "doc/REFERENCE_CLI.md is stale — run: "
        "PYTHONPATH=src python3 dev/gen_cli_reference.py"
    )


def test_generated_reference_covers_every_command() -> None:
    """Every non-hidden command appears — the old page documented ~1 in 3."""
    import typer.main

    from trcc.ui.cli.main import app

    page = gen_cli_reference.generate()
    cli = typer.main.get_command(app)
    missing: list[str] = []
    for name, cmd in cli.commands.items():          # pyright: ignore[reportAttributeAccessIssue]
        if getattr(cmd, "hidden", False):
            continue
        if gen_cli_reference._is_group(cmd):
            for sub_name, sub in cmd.commands.items():
                if not getattr(sub, "hidden", False) and (
                    f"`trcc {name} {sub_name}`" not in page
                ):
                    missing.append(f"trcc {name} {sub_name}")
        elif f"`trcc {name}`" not in page:
            missing.append(f"trcc {name}")
    assert not missing, f"commands absent from the reference: {missing}"


# =========================================================================
# 2. Hand-written guides only name commands that exist
# =========================================================================


# Only text a user follows to RUN something.  CHANGELOG and the
# HISTORY/STATUS files are deliberately excluded: they are a record of what
# was true at the time, and "v9.5 renamed trcc send" must stay readable.
#
# ``install.sh`` earned its place the hard way.  It printed `trcc detect
# --all` and `trcc test` — neither of which exists — at the end of every
# single install, and this gate never saw them because it only ever read
# ``doc/``.  A wrong command hurts most where it is handed to someone who
# has just finished installing, wherever that text happens to live (#247).
_SOURCES = [
    *sorted(_DOC_DIR.glob("GUIDE_*.md")),
    _DOC_DIR / "REFERENCE_DEVICES.md",
    _DOC_DIR / "REFERENCE_TECHNICAL.md",
    _ROOT / "install.sh",
]

# `trcc <word>` optionally followed by a subcommand.  Options are excluded by
# the character class, so `trcc report -o x` reads as `trcc report` and the
# `-o` is checked separately by _flags_after.
_INVOCATION = re.compile(r"\btrcc\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?")

# A flag as written on a command line.  Requires a letter after the dashes so
# a bare `--` separator and negative numbers are not read as options.
_FLAG = re.compile(r"(?<![\w-])(--?[a-zA-Z][\w-]*)")

# Where an invocation stops: a comment, a pipe, a separator, or `&&`.  Without
# this, `trcc detect   # --all was removed` would read `--all` as a flag, and
# `cmd-a | cmd-b --x` would blame `--x` on cmd-a.
_TAIL_STOP = re.compile(r"[#|;]|&&")


def _command_tree() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """({top-level name: command}, {group: {subcommand name: command}}).

    Walks the resolved Click tree rather than Typer's ``registered_commands``
    /``registered_groups`` lists, because a group can itself hold groups —
    ``system autostart`` is one — and the registration lists keep those in a
    separate bucket, so reading only the command list reports a real command
    as missing.

    Returns the Click objects, not just their names, so the caller can also
    ask each one which options it accepts.

    Nesting deeper than ``group sub`` is not validated; the invocation regex
    reads two words, so ``trcc system autostart enable`` is checked as far as
    ``system autostart``.
    """
    import typer.main

    from trcc.ui.cli.main import app

    cli = typer.main.get_command(app)
    tops: dict[str, Any] = {}
    groups: dict[str, dict[str, Any]] = {}
    for name, cmd in cli.commands.items():          # pyright: ignore[reportAttributeAccessIssue]
        if gen_cli_reference._is_group(cmd):
            groups[name] = dict(cmd.commands)
        else:
            tops[name] = cmd
    return tops, groups


def _accepted_flags(cmd: Any) -> set[str]:
    """Every option spelling *cmd* accepts, ``--help`` included."""
    flags = {"--help"}
    for param in getattr(cmd, "params", ()):
        flags.update(param.opts)
        flags.update(param.secondary_opts)
    return flags


def _flags_after(line: str, end: int) -> set[str]:
    """Flags belonging to the invocation that ended at *end* in *line*."""
    tail = line[end:]
    stop = _TAIL_STOP.search(tail)
    return set(_FLAG.findall(tail[:stop.start()] if stop else tail))


def _fenced_lines(text: str):
    """Yield (lineno, line) for lines inside ``` fenced blocks only.

    Prose is skipped on purpose — "trcc reads the config" is English, not an
    invocation, and a gate that cries wolf on prose gets switched off.
    """
    inside = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside:
            yield number, line


_ECHO = re.compile(r"""^\s*echo\s+(?:-e\s+)?["'](.*)["']\s*$""")


def _echoed_lines(text: str):
    """Yield (lineno, printed text) for each ``echo`` in a shell script.

    A script has no fenced blocks, so the markdown rule has no equivalent —
    but it has a sharper one.  What the script *prints* is precisely its
    user-facing half; its comments are addressed to us and mention commands
    freely ("delegates to `trcc setup`", "Resolve trcc command") without
    ever telling anyone to run them.
    """
    for number, line in enumerate(text.splitlines(), 1):
        match = _ECHO.match(line)
        if match:
            yield number, match.group(1)


def _user_facing_lines(path: Path):
    """The lines of *path* that actually instruct a user."""
    text = path.read_text(encoding="utf-8")
    return _echoed_lines(text) if path.suffix == ".sh" else _fenced_lines(text)


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.name)
def test_user_facing_text_only_names_real_commands(source: Path) -> None:
    """Every `trcc …` we hand a user must resolve — options included.

    Checking only the command name is what let #247 through: `trcc detect`
    exists, so `trcc detect --all` read as valid and the bogus flag was
    invisible.  The promise a user relies on is that the whole line runs,
    not that its first word does.
    """
    tops, groups = _command_tree()
    offenders: list[str] = []
    for number, line in _user_facing_lines(source):
        for match in _INVOCATION.finditer(line):
            name, sub = match.group(1), match.group(2)
            if name in groups:
                if sub is None:
                    continue                    # bare group — prints help
                if sub not in groups[name]:
                    offenders.append(
                        f"{source.name}:{number}: `trcc {name} {sub}` — "
                        f"no such subcommand of `{name}`"
                    )
                    continue
                cmd, shown = groups[name][sub], f"{name} {sub}"
            elif name in tops:
                # A top-level command takes no subcommand word, so whatever
                # the regex swallowed as `sub` is an argument, not a name.
                cmd, shown = tops[name], name
            else:
                offenders.append(
                    f"{source.name}:{number}: `trcc {name}` — no such command"
                )
                continue
            unknown = _flags_after(line, match.end()) - _accepted_flags(cmd)
            if unknown:
                offenders.append(
                    f"{source.name}:{number}: `trcc {shown}` — no such "
                    f"option(s): {', '.join(sorted(unknown))}"
                )
    assert not offenders, (
        "user-facing text names commands or options the CLI does not have "
        "(check `trcc --help`; the cutover renamed the flat commands into "
        "groups):\n  " + "\n  ".join(offenders)
    )
