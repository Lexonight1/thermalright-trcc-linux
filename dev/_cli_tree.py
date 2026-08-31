#!/usr/bin/env python3
"""Walking the live Typer command tree — shared by the generators and their gates.

Both `gen_cli_reference.py` and `gen_manpages.py` render the same tree into
different artifacts, and both had defined `_is_group` themselves.  The two
copies had identical logic and **already-divergent prose** — one of them
carrying a stray troff escape inside a Python docstring — which is what one
fact expressed twice looks like just before it drifts in substance too.

`iter_leaves` exists because a *group can contain a group*: `trcc system
autostart` holds four commands, and every consumer that assumed exactly two
levels silently dropped them.  That assumption lived in four places at once —
both renderers, the reference-coverage test, and the prose checker's
two-word regex — and produced a man page advertising `trcc system autostart`
as a runnable command whose only option was `--help`, when it actually
requires a subcommand.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def is_group(cmd: Any) -> bool:
    """True for a command group.

    Duck-typed on the ``.commands`` mapping rather than
    ``isinstance(cmd, click.Group)``: Typer (>=0.13) vendors its own click as
    ``typer._click``, so the tree objects are not instances of the installed
    ``click`` package's classes.
    """
    return getattr(cmd, "commands", None) is not None


def iter_leaves(cmd: Any, prefix: str = "trcc") -> Iterator[tuple[str, Any]]:
    """Every non-hidden LEAF under *cmd*, at any depth, as ``(path, command)``.

    ``path`` is the full invocation a user types — ``trcc system autostart
    refresh`` — so a caller can assert it appears verbatim in a document.
    Groups are recursed into and never yielded themselves: a group is not a
    command anyone can run to completion, which is precisely the fiction the
    generators used to print.
    """
    for name, sub in sorted(getattr(cmd, "commands", {}).items()):
        if getattr(sub, "hidden", False):
            continue
        path = f"{prefix} {name}"
        if is_group(sub):
            yield from iter_leaves(sub, path)
        else:
            yield path, sub
