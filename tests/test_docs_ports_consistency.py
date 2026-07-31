"""Docs must describe the ports that exist.

``doc/REFERENCE_PORTS.md`` is how a contributor learns what to extend: which
ABC, what to implement, what comes free, how to register.  Before it existed,
the only map was a hand-written table in CLAUDE.md that listed **four** ports
when the tree had **28** — and two of those four pointed at files that no
longer contained them (``UsbTransport`` in ``adapters/device/hid.py``, a class
that does not exist; ``SegmentDisplay`` in ``adapters/device/led_segment.py``,
which had moved to ``services/``).

Adding a cooler is two table rows and a new wire is three methods, but nothing
said so — so only the author knew.  A contract nobody can find is not a
contract, which is why the page is generated and these guard it.

Same contract as the man pages and the CLI reference: the committed copy must
match what the generator produces right now.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev"))

import gen_ports_reference  # noqa: E402  # pyright: ignore[reportMissingImports]

_DOC = _ROOT / "doc" / "REFERENCE_PORTS.md"


def test_committed_port_reference_is_current() -> None:
    """The committed page matches what the generator produces right now."""
    assert _DOC.exists(), (
        "doc/REFERENCE_PORTS.md is missing — run: "
        "PYTHONPATH=src python3 dev/gen_ports_reference.py"
    )
    assert _DOC.read_text() == gen_ports_reference.generate(), (
        "doc/REFERENCE_PORTS.md is stale — run: "
        "PYTHONPATH=src python3 dev/gen_ports_reference.py"
    )


def test_every_abc_in_the_tree_is_documented() -> None:
    """No port may be invisible — the old table covered 4 of 28."""
    import inspect

    gen_ports_reference._import_everything()
    abcs = {c.__name__ for c in gen_ports_reference._all_classes()
            if inspect.isabstract(c)}
    page = _DOC.read_text()
    missing = sorted(name for name in abcs if f"## {name}\n" not in page)
    assert not missing, (
        f"ports absent from doc/REFERENCE_PORTS.md: {missing} — run: "
        "PYTHONPATH=src python3 dev/gen_ports_reference.py"
    )


def test_the_cheapest_ports_come_first() -> None:
    """The page is ordered by cost to extend — that ordering IS the message.

    A contributor should be able to read down the summary table and see where
    the codebase welcomes them.  If the order stops meaning that, the page is
    just a list.
    """
    import inspect
    import re

    gen_ports_reference._import_everything()
    by_name = {c.__name__: c for c in gen_ports_reference._all_classes()
               if inspect.isabstract(c)}
    order = re.findall(r"^\| \[`(\w+)`\]", _DOC.read_text(), re.M)
    counts = [len(by_name[n].__abstractmethods__) for n in order if n in by_name]
    assert counts == sorted(counts), (
        "doc/REFERENCE_PORTS.md is not ordered cheapest-to-extend first"
    )
