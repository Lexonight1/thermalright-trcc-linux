"""Docs must describe the contract that exists.

``doc/REFERENCE_COMMANDS.md`` is the page an outside contributor needs to build
their own UI: the one surface every UI dispatches against, with each Command's
kind, fields and Result.  Before it existed there was no page for it at all —
while a file named ``doc/TRCC_CONTRACT.md`` described a ``Trcc`` facade and
three Command classes deleted in the cutover.  Someone would have written code
against them before finding out; it was removed when this landed.

Same contract as the man pages, the CLI reference and the port reference: the
committed copy must match what the generator produces right now.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev"))

import gen_commands_reference  # noqa: E402

_DOC = _ROOT / "doc" / "REFERENCE_COMMANDS.md"


def test_committed_command_reference_is_current() -> None:
    """The committed page matches what the generator produces right now."""
    assert _DOC.exists(), (
        "doc/REFERENCE_COMMANDS.md is missing — run: "
        "PYTHONPATH=src python3 dev/gen_commands_reference.py"
    )
    assert _DOC.read_text() == gen_commands_reference.generate(), (
        "doc/REFERENCE_COMMANDS.md is stale — run: "
        "PYTHONPATH=src python3 dev/gen_commands_reference.py"
    )


def test_the_page_documents_every_dispatchable_command() -> None:
    """No Command may be missing from the page.

    The generator derives from ``COMMAND_TYPES``, so this cannot drift by
    accident — it guards the generator itself, the way the ports gate does.
    A page that silently omitted part of the contract would send a UI author
    looking for a capability that is actually there.
    """
    from trcc.ipc import COMMAND_TYPES

    page = _DOC.read_text()
    missing = sorted(n for n in COMMAND_TYPES if f"### `{n}`" not in page)
    assert not missing, f"absent from the reference: {missing}"


def test_the_generator_is_deterministic() -> None:
    """Two runs produce the same bytes.

    The page is committed, so a generator that reordered anything would show
    up as a spurious diff on every unrelated commit and train people to ignore
    it.  The ports reference has the same guarantee for the same reason.
    """
    assert gen_commands_reference.generate() == gen_commands_reference.generate()
