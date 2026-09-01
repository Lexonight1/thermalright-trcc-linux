#!/usr/bin/env python3
"""Generate troff man pages for the TRCC CLI, straight from the Typer app.

Source of truth is the live command tree — `typer.main.get_command(app)` yields
the underlying Click group, which we walk to emit one man page per command
group (git-style: `trcc.1` + `trcc-display.1`, `trcc-led.1`, …).  Because the
pages come from the real `--help`, they cannot drift from the commands the way
hand-written prose does; `tests/test_manpages_current.py` regenerates in a temp
dir and fails CI if the committed pages are stale.

Output is **deterministic**: commands are sorted, and the `.TH` date field is
left empty (the app version identifies the revision) so the committed files
only change when the CLI actually changes — not once per day.

    PYTHONPATH=src python3 dev/gen_manpages.py           # write man/man1/*.1
    PYTHONPATH=src python3 dev/gen_manpages.py --check    # exit 1 if stale
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import typer.main

from _cli_tree import is_group as _is_group
from trcc.__version__ import __version__
from trcc.ui.cli.main import app

# Groups get their own page; these top-level entries are the root page's list.
_MAN_DIR = Path(__file__).resolve().parents[1] / "man" / "man1"
_RELEASE = f"trcc-linux {__version__}"
_MANUAL = "TRCC Manual"
_BUGS_URL = "https://github.com/Lexonight1/thermalright-trcc-linux/issues"


# Unicode punctuation the CLI help uses -> plain ASCII (portable across old
# troff; avoids "invalid input character" warnings under -Tascii).
_UNICODE = {
    "—": " - ", "–": "-", "‑": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "•": "*", "×": "x",
    "°": " deg", " ": " ", "→": "->", "≥": ">=",
    "≤": "<=", "≈": "~",
}
_UNICODE_RE = re.compile("|".join(map(re.escape, _UNICODE)))
# RST leftovers in docstrings: ``code`` -> code, :role:`x` prefix -> dropped.
_RST_ROLE_RE = re.compile(r":[a-z-]+:(?=`)")


def esc(text: str | None) -> str:
    """Escape a string for troff body text.

    Whitespace is collapsed to one line (so no help text can smuggle a leading
    ``.`` control line in), Unicode punctuation is folded to ASCII, RST markup
    (``code`` / :role:) is stripped, backslashes are doubled, and ASCII hyphens
    become ``\\-`` (so option names and ``set-brightness`` render as literal,
    copy-pasteable hyphens rather than a typographic minus).
    """
    if not text:
        return ""
    flat = " ".join(text.split())
    flat = _UNICODE_RE.sub(lambda m: _UNICODE[m.group()], flat)
    flat = _RST_ROLE_RE.sub("", flat).replace("``", "").replace("`", "")
    flat = flat.replace("\\", "\\\\").replace("-", "\\-")
    if flat[:1] in (".", "'"):
        flat = "\\&" + flat
    return flat


def _metavar(param: click.Argument) -> str:
    """UPPERCASE metavar for a positional argument (``hex_color`` -> HEX_COLOR)."""
    return param.name.upper() if param.name else "ARG"


def _visible_params(cmd: click.Command) -> tuple[list[click.Argument], list[click.Option]]:
    """Split a command's params into (arguments, options), dropping hidden/help."""
    args: list[click.Argument] = []
    opts: list[click.Option] = []
    for param in cmd.params:
        # `.param_type_name` ("argument"/"option") is stable click API and works
        # regardless of whether the object comes from click or typer's vendored
        # copy; isinstance against the installed click would miss Typer's tree.
        if param.param_type_name == "argument":
            args.append(param)
        elif param.param_type_name == "option" and not getattr(param, "hidden", False):
            opts.append(param)
    return args, opts


def _synopsis(prog: str, cmd: click.Command) -> str:
    """`.B prog` followed by italic metavars for a leaf command's synopsis."""
    args, opts = _visible_params(cmd)
    parts = [f"\\fB{esc(prog)}\\fR"]
    for arg in args:
        mv = f"\\fI{esc(_metavar(arg))}\\fR"
        parts.append(mv if arg.required else f"[{mv}]")
    if opts:
        parts.append("[\\fIOPTIONS\\fR]")
    return " ".join(parts)


def _arg_lines(cmd: click.Command) -> list[str]:
    """Troff ``.TP`` block describing a command's positional arguments."""
    args, _ = _visible_params(cmd)
    if not args:
        return []
    out = [".PP", "Arguments:", ".RS 4"]
    for arg in args:
        out.append(".TP")
        out.append(f"\\fB{esc(_metavar(arg))}\\fR")
        out.append(esc(getattr(arg, "help", None)) or "(no description)")
    out.append(".RE")
    return out


def _opt_entries(cmd: click.Command) -> list[str]:
    """The ``.TP`` entries for a command's options (always includes --help).

    Split from :func:`_opt_lines` so the ROOT page can render the same options
    as a real ``.SH OPTIONS`` section.  It could not before: `_opt_lines` wraps
    its entries in an indented ``Options:`` sub-block, which is right inside a
    subcommand entry and wrong as a page-level section.
    """
    _, opts = _visible_params(cmd)
    out: list[str] = []
    for opt in opts:
        flags = ", ".join(esc(o) for o in opt.opts)
        # A COUNT option takes no value -- `-v` is repeated, not assigned -- so
        # printing a metavar would tell a reader to type `trcc --verbose VERBOSE`.
        # click reports count options as `is_flag=False`, which is why this needs
        # its own test rather than riding on the flag check.
        if not opt.is_flag and not getattr(opt, "count", False) and opt.name:
            flags += f" \\fI{esc(opt.name.upper())}\\fR"
        out.append(".TP")
        out.append(f"\\fB{flags}\\fR")
        out.append(esc(opt.help) or "(no description)")
    out.append(".TP")
    out.append("\\fB\\-\\-help\\fR")
    out.append("Show this command's help and exit.")
    return out


def _opt_lines(cmd: click.Command) -> list[str]:
    """Indented ``Options:`` sub-block, for a subcommand entry."""
    return [".PP", "Options:", ".RS 4", *_opt_entries(cmd), ".RE"]


def _th(title: str) -> str:
    """The ``.TH`` header — empty date field keeps output deterministic."""
    return f'.TH "{title}" "1" "" "{esc(_RELEASE)}" "{esc(_MANUAL)}"'


def _footer() -> list[str]:
    return [
        ".SH REPORTING BUGS",
        f"Report bugs at \\fB{_BUGS_URL}\\fR \\(em include the output of \\fBtrcc "
        "report\\fR.",
        ".SH AUTHOR",
        "TRCC Linux contributors. Unofficial community project, not affiliated "
        "with Thermalright.",
    ]


def _short(cmd: click.Command, fallback: str) -> str:
    """First line of a command's help, for NAME / one-line summaries."""
    text = cmd.help or cmd.short_help or fallback
    return " ".join(text.split()).split(". ")[0].rstrip(".")


def _subcommand_lines(prog: str, group: click.Group, path: str) -> list[str]:
    """``.SS`` entries for every subcommand under *group*, recursing into groups.

    troff has ``.SS`` but no ``.SSS``, so a nested command is FLATTENED into a
    qualified subsection name — ``.SS autostart status`` — rather than nested.
    That keeps ``man trcc-system`` one page you can page through and search,
    which is what a reader does, and it means a nested group needs no page of
    its own.

    This used to be a flat loop that rendered every child as a leaf.
    ``trcc system autostart`` is a GROUP, so the page advertised it as a
    runnable command whose only option was ``--help`` — running it prints a
    usage screen — and hid the four commands that do run.
    """
    lines: list[str] = []
    for sub_name, sub in sorted(group.commands.items()):
        if getattr(sub, "hidden", False):
            continue
        qualified = f"{path} {sub_name}".strip()
        if _is_group(sub):
            lines.append(f".SS {esc(qualified)}")
            lines.append(".PP")
            lines.append(esc(sub.help) or esc(sub.short_help)
                         or f"The {esc(sub_name)} command group.")
            lines.extend(_subcommand_lines(prog, sub, qualified))
            continue
        lines.append(f".SS {esc(qualified)}")
        lines.append(".PP")
        lines.append(_synopsis(f"{prog} {qualified}", sub))
        lines.append(".PP")
        lines.append(esc(sub.help) or esc(sub.short_help) or "(no description)")
        lines.extend(_arg_lines(sub))
        lines.extend(_opt_lines(sub))
    return lines


def render_group_page(name: str, group: click.Group) -> str:
    """A full man page for one command group (e.g. ``trcc-display``)."""
    prog = f"trcc {name}"
    lines = [
        _th(f"TRCC\\-{name.upper()}"),
        ".SH NAME",
        f"trcc\\-{esc(name)} \\- {esc(_short(group, name + ' commands'))}",
        ".SH SYNOPSIS",
        f"\\fBtrcc {esc(name)}\\fR \\fISUBCOMMAND\\fR [\\fIARGS\\fR]...",
        ".SH DESCRIPTION",
        esc(group.help) or f"The {esc(name)} command group.",
        ".SH SUBCOMMANDS",
    ]
    lines.extend(_subcommand_lines(prog, group, ""))
    lines.append(".SH SEE ALSO")
    lines.append("\\fBtrcc\\fR(1)")
    lines.extend(_footer())
    return "\n".join(lines) + "\n"


def render_root_page(cli: click.Group, groups: list[str]) -> str:
    """The top-level ``trcc.1`` page: commands, groups, and the key convention."""
    lines = [
        _th("TRCC"),
        ".SH NAME",
        "trcc \\- Thermalright LCD/LED cooler control for Linux",
        ".SH SYNOPSIS",
        "\\fBtrcc\\fR [\\fIOPTIONS\\fR] \\fICOMMAND\\fR [\\fIARGS\\fR]...",
        ".SH DESCRIPTION",
        esc(cli.help) or "Control Thermalright LCD and LED cooler devices.",
        ".PP",
        "Device commands under \\fBtrcc display\\fR, \\fBtrcc led\\fR and "
        "\\fBtrcc theme\\fR take the device \\fIKEY\\fR (its USB VID:PID, shown "
        "by \\fBtrcc detect\\fR \\(em e.g. 0402:3922) as their first argument.",
        ".SH OPTIONS",
        # The SYNOPSIS above has promised ``[OPTIONS]`` since this generator was
        # written, and the page documented none of them -- so `--verbose` and
        # the whole -v/-vv/-vvv ladder were invisible to anyone reading
        # `man trcc`.  That ladder is how a reporter turns up per-tick and
        # per-frame detail, and `trcc report` is the entire diagnosis for
        # hardware we do not own.
        *_opt_entries(cli),
        ".SH COMMANDS",
    ]
    for name, cmd in sorted(cli.commands.items()):
        if getattr(cmd, "hidden", False) or _is_group(cmd):
            continue
        lines.append(".TP")
        lines.append(f"\\fB{esc(name)}\\fR")
        lines.append(esc(cmd.help) or esc(cmd.short_help) or "(no description)")
    lines.append(".SH COMMAND GROUPS")
    for name in groups:
        grp = cli.commands[name]
        lines.append(".TP")
        lines.append(f"\\fB{esc(name)}\\fR")
        lines.append(
            f"{esc(_short(grp, name + ' commands'))}. See \\fBtrcc\\-"
            f"{esc(name)}\\fR(1)."
        )
    lines.append(".SH FILES")
    lines.append(".TP")
    lines.append("\\fB~/.trcc/\\fR")
    lines.append("Program + cloud data and config (config.json, logs).")
    lines.append(".TP")
    lines.append("\\fB~/.trcc\\-user/\\fR")
    lines.append("User-authored themes, backgrounds, and masks.")
    lines.append(".SH SEE ALSO")
    lines.append(", ".join(f"\\fBtrcc\\-{esc(g)}\\fR(1)" for g in groups))
    lines.extend(_footer())
    return "\n".join(lines) + "\n"


def generate() -> dict[str, str]:
    """Build every man page as {filename: troff}, deterministically."""
    cli = typer.main.get_command(app)
    assert _is_group(cli), f"expected a command group, got {type(cli).__name__}"
    groups = sorted(
        name for name, cmd in cli.commands.items()
        if _is_group(cmd) and not getattr(cmd, "hidden", False)
    )
    pages = {"trcc.1": render_root_page(cli, groups)}
    for name in groups:
        grp = cli.commands[name]
        assert _is_group(grp)
        pages[f"trcc-{name}.1"] = render_group_page(name, grp)
    return pages


def main(argv: list[str]) -> int:
    pages = generate()
    check = "--check" in argv
    stale: list[str] = []
    for filename, content in sorted(pages.items()):
        target = _MAN_DIR / filename
        if check:
            current = target.read_text() if target.exists() else ""
            if current != content:
                stale.append(filename)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
    if check:
        if stale:
            print(f"stale man pages (run dev/gen_manpages.py): {', '.join(stale)}")
            return 1
        print(f"man pages current ({len(pages)} pages)")
        return 0
    print(f"wrote {len(pages)} man pages to {_MAN_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
