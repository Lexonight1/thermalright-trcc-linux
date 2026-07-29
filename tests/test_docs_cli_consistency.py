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


# Only the guides a user follows to RUN something.  CHANGELOG and the
# HISTORY/STATUS files are deliberately excluded: they are a record of what
# was true at the time, and "v9.5 renamed trcc send" must stay readable.
_GUIDES = [
    *sorted(_DOC_DIR.glob("GUIDE_*.md")),
    _DOC_DIR / "REFERENCE_DEVICES.md",
    _DOC_DIR / "REFERENCE_TECHNICAL.md",
]

# `trcc <word>` optionally followed by a subcommand.  Options are excluded by
# the character class, so `trcc report -o x` reads as `trcc report`.
_INVOCATION = re.compile(r"\btrcc\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?")


def _command_tree() -> tuple[set[str], dict[str, set[str]]]:
    """(top-level command names, {group: {subcommand names}}) from the app.

    Walks the resolved Click tree rather than Typer's ``registered_commands``
    /``registered_groups`` lists, because a group can itself hold groups —
    ``system autostart`` is one — and the registration lists keep those in a
    separate bucket, so reading only the command list reports a real command
    as missing.

    Nesting deeper than ``group sub`` is not validated; the invocation regex
    reads two words, so ``trcc system autostart enable`` is checked as far as
    ``system autostart``.
    """
    import typer.main

    from trcc.ui.cli.main import app

    cli = typer.main.get_command(app)
    tops: set[str] = set()
    groups: dict[str, set[str]] = {}
    for name, cmd in cli.commands.items():          # pyright: ignore[reportAttributeAccessIssue]
        if gen_cli_reference._is_group(cmd):
            groups[name] = set(cmd.commands)
        else:
            tops.add(name)
    return tops, groups


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


@pytest.mark.parametrize("guide", _GUIDES, ids=lambda p: p.name)
def test_guide_only_names_real_commands(guide: Path) -> None:
    """Every `trcc …` a guide tells a user to run must resolve."""
    tops, groups = _command_tree()
    offenders: list[str] = []
    for number, line in _fenced_lines(guide.read_text(encoding="utf-8")):
        for match in _INVOCATION.finditer(line):
            name, sub = match.group(1), match.group(2)
            if name in groups:
                if sub is not None and sub not in groups[name]:
                    offenders.append(
                        f"{guide.name}:{number}: `trcc {name} {sub}` — "
                        f"no such subcommand of `{name}`"
                    )
            elif name not in tops:
                offenders.append(
                    f"{guide.name}:{number}: `trcc {name}` — no such command"
                )
    assert not offenders, (
        "guides name commands the CLI does not have (check `trcc --help`; "
        "the cutover renamed the flat commands into groups):\n  "
        + "\n  ".join(offenders)
    )
