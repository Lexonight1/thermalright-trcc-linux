#!/usr/bin/env python3
"""Generate `doc/REFERENCE_COMMANDS.md` from the live Command classes.

Same source of truth, and the same reason, as ``dev/gen_cli_reference.py`` and
``dev/gen_ports_reference.py``: what is hand-written drifts, what is generated
and gated does not.

This page is the one an outside contributor needs most and the one that did not
exist.  ``doc/REFERENCE_CLI.md`` documents one UI's spelling of the contract;
this documents **the contract** — the single surface every UI dispatches
against, and the only thing a new UI (a browser client, a VR panel, a TUI) has
to speak.  Until this page existed that surface had no documentation at all,
while a file named ``doc/TRCC_CONTRACT.md`` described a ``Trcc`` facade and
three Command classes deleted in the cutover.  Someone would have written code
against them before finding out; it was removed when this landed.

Derived from ``trcc.ipc.COMMAND_TYPES``, which is the registry the IPC layer
itself dispatches through, so the page cannot advertise a Command that daemon
mode could not run.  Everything on it is introspected: the kind
(Command vs Query), the Result type from the class's own generic base, the
fields with their types and whether they are required, and the docstring.
Nothing is written by hand, so nothing can drift.

Deterministic — grouped by the domain module the Command lives in, sorted
inside each group, no timestamp — so the committed file changes only when the
contract does.

    PYTHONPATH=src python3 dev/gen_commands_reference.py           # write it
    PYTHONPATH=src python3 dev/gen_commands_reference.py --check   # exit 1 if stale
"""
from __future__ import annotations

import dataclasses
import sys
import types
import typing
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

_DOC = _REPO / "doc" / "REFERENCE_COMMANDS.md"

#: Domain module -> the heading it gets.  A module absent here still appears,
#: under its own name — the page must never silently omit part of the contract
#: because nobody added a title for it.
_GROUPS = {
    "device": "Devices, display and frames",
    "theme": "Themes, masks and media",
    "led": "LED",
    "system": "System, settings and diagnostics",
}


def _render_type(hint: object) -> str:
    """A readable spelling of a field's type, e.g. ``list[tuple[int, int, int]]``."""
    if hint is type(None):
        return "None"
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    # A generic before a plain class: on Python 3.10 ``isinstance(tuple[int],
    # type)`` is True, so testing ``type`` first rendered ``tuple`` there and
    # ``tuple[int, int, int]`` on 3.11+ -- one page, two answers.
    if origin is None and isinstance(hint, type):
        return hint.__name__
    if origin in (types.UnionType, typing.Union):
        return " | ".join(_render_type(a) for a in args)
    if origin is not None:
        name = getattr(origin, "__name__", str(origin))
        if args:
            inner = ", ".join(_render_type(a) for a in args)
            return f"{name}[{inner}]"
        return name
    return str(hint).replace("typing.", "")


def _result_name(cls: type) -> str:
    """The ``R`` in ``Command[R]``, read off the class's own generic base."""
    for base in getattr(cls, "__orig_bases__", ()):
        args = typing.get_args(base)
        if args:
            return _render_type(args[0])
    return "Result"


def _summary(cls: type) -> str:
    """The docstring's first paragraph, collapsed to one line."""
    doc = (cls.__doc__ or "").strip()
    if not doc:
        return ""
    paragraph = doc.split("\n\n", 1)[0]
    return " ".join(part.strip() for part in paragraph.splitlines() if part.strip())


def _fields(cls: type) -> list[tuple[str, str, bool]]:
    """``(name, rendered type, required)`` for every field, in declared order."""
    hints = typing.get_type_hints(cls)
    rows: list[tuple[str, str, bool]] = []
    for field in dataclasses.fields(cls):
        required = (field.default is dataclasses.MISSING
                    and field.default_factory is dataclasses.MISSING)
        rows.append((field.name, _render_type(hints.get(field.name, object)), required))
    return rows


def generate() -> str:
    from trcc.core.commands._base import Query
    from trcc.ipc import COMMAND_TYPES

    entries = sorted(COMMAND_TYPES.items())
    queries = [c for _n, c in entries if issubclass(c, Query)]

    lines = [
        "# Command reference",
        "",
        "**Generated — do not edit.** "
        "`PYTHONPATH=src python3 dev/gen_commands_reference.py`",
        "",
        "Every capability in TRCC, as the one surface all four UIs dispatch "
        "against. A new UI — a browser client, a VR panel, a TUI — needs only "
        "this page and an event subscription; it never imports a service or an "
        "adapter.",
        "",
        f"**{len(entries)} total: {len(entries) - len(queries)} Commands "
        f"and {len(queries)} Queries.** A *Query* is a read and nothing else, "
        "which is why it is named separately — a missing read should be "
        "obvious rather than archaeological.",
        "",
        "## Dispatching one",
        "",
        "```python",
        "from trcc._boot import trcc",
        "from trcc.core.commands import SendColor",
        "",
        "app = trcc()                     # in-process, or a daemon client",
        'result = app.dispatch(SendColor(key="0402:3922", r=255, g=0, b=0))',
        "result.ok, result.message",
        "```",
        "",
        "`trcc()` returns an in-process `App`, or an `AppProxy` speaking to the "
        "daemon when `TRCC_DAEMON=1`. Both expose `dispatch(cmd) -> Result` and "
        "nothing else, so a UI written against this page works in either mode.",
        "",
        "Over the socket the same call is one line of JSON:",
        "",
        "```json",
        '{"command": "SendColor", '
        '"kwargs": {"key": "0402:3922", "r": 255, "g": 0, "b": 0}}',
        "```",
        "",
        "Every Command is a frozen dataclass, so its fields below *are* its "
        "keyword arguments. Results are dataclasses too, and every one carries "
        "`ok` and `message`.",
        "",
    ]

    by_group: dict[str, list[tuple[str, type]]] = {}
    for name, cls in entries:
        module = cls.__module__.rsplit(".", 1)[-1].lstrip("_")
        by_group.setdefault(module, []).append((name, cls))

    ordered = [g for g in _GROUPS if g in by_group]
    ordered += sorted(g for g in by_group if g not in _GROUPS)

    for group in ordered:
        lines += [f"## {_GROUPS.get(group, group)}", ""]
        for name, cls in sorted(by_group[group]):
            kind = "Query" if issubclass(cls, Query) else "Command"
            lines += [f"### `{name}`", ""]
            summary = _summary(cls)
            if summary:
                lines += [summary, ""]
            lines += [f"*{kind}* → `{_result_name(cls)}`", ""]
            rows = _fields(cls)
            if rows:
                lines += ["| Field | Type | Required |",
                          "|---|---|---|"]
                lines += [f"| `{f}` | `{t}` | {'yes' if req else 'no'} |"
                          for f, t, req in rows]
            else:
                lines.append("Takes no arguments.")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str]) -> int:
    content = generate()
    if "--check" in argv:
        current = _DOC.read_text() if _DOC.exists() else ""
        if current != content:
            print("stale doc/REFERENCE_COMMANDS.md "
                  "(run: PYTHONPATH=src python3 dev/gen_commands_reference.py)")
            return 1
        print("doc/REFERENCE_COMMANDS.md current")
        return 0
    _DOC.parent.mkdir(parents=True, exist_ok=True)
    _DOC.write_text(content)
    print(f"wrote {_DOC} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
