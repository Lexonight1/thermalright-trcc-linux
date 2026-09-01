"""Man pages must stay in lockstep with the CLI.

The pages under ``man/man1/`` are generated from the live Typer command tree by
``dev/gen_manpages.py``.  These tests fail if someone changes a command without
regenerating (the exact drift we hit with hand-written docs) and cover the troff
escaper, which is the easy-to-get-wrong part.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev"))

import gen_manpages  # noqa: E402  # pyright: ignore[reportMissingImports]
from _cli_tree import iter_leaves  # noqa: E402  # pyright: ignore[reportMissingImports]

_MAN_DIR = _ROOT / "man" / "man1"


def test_committed_manpages_are_current() -> None:
    """Every committed .1 matches what the generator produces right now."""
    for filename, content in gen_manpages.generate().items():
        target = _MAN_DIR / filename
        assert target.exists(), (
            f"{filename} is missing — run: PYTHONPATH=src python3 dev/gen_manpages.py"
        )
        assert target.read_text() == content, (
            f"{filename} is stale — run: PYTHONPATH=src python3 dev/gen_manpages.py"
        )


def test_every_command_appears_in_its_group_page() -> None:
    """A command a user can run must be findable in ``man``.

    The reference-page half of this lives in
    ``test_docs_cli_consistency::test_generated_reference_covers_every_command``.
    Two artifacts, two generators, so two assertions — but ONE walk
    (``iter_leaves``), because "what counts as a documentable command" is the
    single fact both depend on, and it is exactly the fact that was wrong.

    ``render_group_page`` used to emit every subcommand as a leaf without
    asking whether it was itself a group, so ``trcc system autostart`` appeared
    as a runnable command whose only option was ``--help`` — while its four
    real subcommands appeared nowhere.  Running it prints a usage screen.
    """
    import typer.main

    from trcc.ui.cli.main import app

    pages = gen_manpages.generate()
    missing: list[str] = []
    for path, _cmd in iter_leaves(typer.main.get_command(app)):
        parts = path.split()                      # ["trcc", group, ...leaf]
        if len(parts) < 3:
            continue                              # top-level: lives on trcc.1
        page = pages.get(f"trcc-{parts[1]}.1", "")
        # ``esc`` turns hyphens into ``\-``; compare on the escaped subpath.
        subpath = gen_manpages.esc(" ".join(parts[2:]))
        if f".SS {subpath}" not in page:
            missing.append(path)
    assert not missing, (
        "commands absent from their group's man page: " + ", ".join(missing)
    )


def test_no_orphan_manpages() -> None:
    """No committed .1 exists that the generator wouldn't produce."""
    generated = set(gen_manpages.generate())
    committed = {p.name for p in _MAN_DIR.glob("*.1")}
    assert committed == generated


def test_root_and_every_group_have_a_page() -> None:
    import typer.main

    from trcc.ui.cli.main import app

    pages = gen_manpages.generate()
    assert "trcc.1" in pages
    # DERIVED, not a list of six names.  A hardcoded tuple cannot notice a NEW
    # root group — the one case where a page would be missing — so it asserted
    # only that six known pages still exist, which they would.
    cli = typer.main.get_command(app)
    groups = sorted(
        name for name, cmd in cli.commands.items()
        if getattr(cmd, "commands", None) and not getattr(cmd, "hidden", False)
    )
    assert groups, "no command groups found — the CLI shape changed"
    for group in groups:
        assert f"trcc-{group}.1" in pages, f"no man page for group {group!r}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("set-brightness", "set\\-brightness"),   # hyphens -> literal \-
        ("run ``trcc gui``", "run trcc gui"),     # RST double-backticks stripped
        ("a\\b", "a\\\\b"),                        # backslash doubled
        ("  spaced\n\ttext  ", "spaced text"),     # whitespace collapsed
        ("", ""),
    ],
)
def test_esc(raw: str, expected: str) -> None:
    assert gen_manpages.esc(raw) == expected


def test_esc_folds_unicode_to_ascii() -> None:
    """Em-dashes / smart quotes must never reach troff as raw bytes."""
    out = gen_manpages.esc("temp—linked “ok” …done")
    assert out.isascii()
    assert "—" not in out and "“" not in out


def test_generated_pages_are_pure_ascii() -> None:
    """The whole output must be ASCII — troff -Tascii warns otherwise."""
    for filename, content in gen_manpages.generate().items():
        assert content.isascii(), f"{filename} contains non-ASCII bytes"
