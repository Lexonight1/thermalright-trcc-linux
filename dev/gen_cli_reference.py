#!/usr/bin/env python3
"""Generate `doc/REFERENCE_CLI.md` from the live Typer app.

Same source of truth, and the same reason, as ``dev/gen_manpages.py``: the
hand-written reference had drifted a whole cutover behind the code.  It
documented ``trcc send``, ``trcc theme-load``, ``trcc led-color`` and
``trcc detect --all`` -- flat names that the grouped CLI replaced, plus two
commands (``uninstall``, ``perf``) that no longer exist at all -- while
covering only about a third of the commands that DO exist.  Every user
following it hit "No such command" on their first copy-paste.

Prose cannot be kept in lockstep with 150+ commands by discipline alone, so
this derives the reference from ``typer.main.get_command(app)`` exactly like
the man pages.  Help text is written once, in the command's docstring, and
feeds ``--help``, the man page and this page alike.

Deterministic: commands are sorted and no timestamp is emitted, so the
committed file changes only when the CLI actually changes.

    PYTHONPATH=src python3 dev/gen_cli_reference.py           # write the page
    PYTHONPATH=src python3 dev/gen_cli_reference.py --check   # exit 1 if stale
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import typer.main

from _cli_tree import is_group as _is_group
from trcc.ui.cli.main import app

_DOC = Path(__file__).resolve().parents[1] / "doc" / "REFERENCE_CLI.md"

# Sphinx roles in docstrings (:class:`X`, :command:`trcc status`) read as
# noise on a rendered page — keep the target, drop the role prefix.
_RST_ROLE_RE = re.compile(r":[a-z-]+:(?=`)")

# Devices are addressed by VID:PID everywhere; say it once, up top.
_KEY_NOTE = (
    "Commands that act on a device take its **`KEY`** — the USB `VID:PID` "
    "shown by `trcc detect`, e.g. `0402:3922` — as the first argument."
)


def _visible_params(
    cmd: click.Command,
) -> tuple[list[click.Argument], list[click.Option]]:
    """Split a command's params into (arguments, options); drop hidden ones."""
    args: list[click.Argument] = []
    opts: list[click.Option] = []
    for param in cmd.params:
        if param.param_type_name == "argument":
            args.append(param)
        elif param.param_type_name == "option" and not getattr(
            param, "hidden", False,
        ):
            opts.append(param)
    return args, opts


def _metavar(param: click.Argument) -> str:
    """UPPERCASE metavar for a positional argument (``hex_color`` -> HEX_COLOR)."""
    return param.name.upper() if param.name else "ARG"


def _text(raw: str | None, fallback: str = "") -> str:
    """Collapse a help string to one line and strip RST leftovers."""
    if not raw:
        return fallback
    flat = " ".join(raw.split()).replace("``", "`")
    return _RST_ROLE_RE.sub("", flat)


def _synopsis(prog: str, cmd: click.Command) -> str:
    """The copy-pasteable usage line for a leaf command."""
    args, opts = _visible_params(cmd)
    parts = [prog]
    if opts:
        parts.append("[OPTIONS]")
    for arg in args:
        mv = _metavar(arg)
        parts.append(mv if arg.required else f"[{mv}]")
    return " ".join(parts)


def _param_table(cmd: click.Command) -> list[str]:
    """Markdown tables for a command's arguments and options."""
    args, opts = _visible_params(cmd)
    out: list[str] = []
    if args:
        out += ["", "| Argument | Description |", "|---|---|"]
        for arg in args:
            required = "" if arg.required else " *(optional)*"
            desc = _text(getattr(arg, "help", None), "--") + required
            out.append(f"| `{_metavar(arg)}` | {desc} |")
    if opts:
        out += ["", "| Option | Description |", "|---|---|"]
        for opt in opts:
            flags = ", ".join(f"`{o}`" for o in opt.opts)
            if not opt.is_flag and opt.name:
                flags += f" `{opt.name.upper()}`"
            out.append(f"| {flags} | {_text(opt.help, '--')} |")
    return out


def _render_command(prog: str, cmd: click.Command, level: str) -> list[str]:
    """One command's section: heading, description, synopsis, param tables."""
    lines = [f"{level} `{prog}`", ""]
    body = _text(cmd.help) or _text(cmd.short_help) or "_(no description)_"
    lines += [body, "", "```bash", _synopsis(prog, cmd), "```"]
    lines += _param_table(cmd)
    lines.append("")
    return lines


def _render_group(name: str, group: click.Group, level: str = "##") -> list[str]:
    """A group's section, with one subsection per subcommand.

    Recurses, because a group can hold a group: ``trcc system autostart`` has
    four subcommands.  This loop used to call ``_render_command`` on every
    child unconditionally, so a nested group was rendered as though it were a
    leaf — a heading, the group's help, and a bare synopsis — while the
    commands underneath it appeared nowhere on the page.

    Markdown has six heading levels, so nesting is free: the group's leaves sit
    one level below it.
    """
    prog = f"trcc {name}"
    lines = [
        f"{level} `{prog}`",
        "",
        _text(group.help, f"The {name} command group."),
        "",
    ]
    for sub_name, sub in sorted(group.commands.items()):
        if getattr(sub, "hidden", False):
            continue
        if _is_group(sub):
            lines += _render_group(f"{name} {sub_name}", sub, level + "#")
        else:
            lines += _render_command(f"{prog} {sub_name}", sub, level + "#")
    return lines


def generate() -> str:
    """Build the whole reference page, deterministically."""
    cli = typer.main.get_command(app)
    assert _is_group(cli), f"expected a command group, got {type(cli).__name__}"

    groups = sorted(
        name for name, cmd in cli.commands.items()
        if _is_group(cmd) and not getattr(cmd, "hidden", False)
    )
    leaves = sorted(
        name for name, cmd in cli.commands.items()
        if not _is_group(cmd) and not getattr(cmd, "hidden", False)
    )

    lines = [
        "# CLI Reference",
        "",
        "<!-- GENERATED FILE -- do not edit by hand.",
        "     Source: the Typer command tree in src/trcc/ui/cli/.",
        "     Regenerate: PYTHONPATH=src python3 dev/gen_cli_reference.py",
        "     A command's text here IS its docstring; edit that. -->",
        "",
        _text(cli.help, "Control Thermalright LCD and LED cooler devices."),
        "",
        _KEY_NOTE,
        "",
        "```bash",
        "trcc [OPTIONS] COMMAND [ARGS]...",
        "```",
        "",
    ]

    root_opts = _param_table(cli)
    if root_opts:
        lines += ["## Global options", *root_opts, ""]

    lines += ["## Contents", ""]
    for name in leaves:
        lines.append(f"- [`trcc {name}`](#trcc-{name})")
    for name in groups:
        lines.append(f"- [`trcc {name}`](#trcc-{name}) — command group")
    lines.append("")

    lines += ["## Commands", ""]
    for name in leaves:
        lines += _render_command(f"trcc {name}", cli.commands[name], "###")

    for name in groups:
        grp = cli.commands[name]
        assert _is_group(grp)
        lines += _render_group(name, grp)

    lines += [
        "## Files",
        "",
        "| Path | Contents |",
        "|---|---|",
        "| `~/.trcc/` | Program + cloud data and config (`config.json`, logs) |",
        "| `~/.trcc-user/` | User-authored themes, backgrounds, and masks |",
        "",
        "Report bugs at "
        "<https://github.com/Lexonight1/thermalright-trcc-linux/issues> — "
        "include the output of `trcc report`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    content = generate()
    if "--check" in argv:
        current = _DOC.read_text() if _DOC.exists() else ""
        if current != content:
            print("stale doc/REFERENCE_CLI.md "
                  "(run: PYTHONPATH=src python3 dev/gen_cli_reference.py)")
            return 1
        print("doc/REFERENCE_CLI.md current")
        return 0
    _DOC.parent.mkdir(parents=True, exist_ok=True)
    _DOC.write_text(content)
    print(f"wrote {_DOC} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
