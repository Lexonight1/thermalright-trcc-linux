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

import gen_ports_reference  # noqa: E402

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


def test_every_module_imports_so_the_page_cannot_be_silently_truncated() -> None:
    """A module that refuses to import removes whatever it defined.

    ``_import_everything`` swallows the failure by design — one broken
    optional dependency must not abort the whole sweep — but a swallowed
    import shrinks the page, and the staleness gate above then reports
    "stale, regenerate", whose instruction COMMITS the truncation.  So the
    refusals are returned and gated here: a contributor whose environment is
    incomplete gets told which module and which exception, instead of being
    sent to regenerate a shorter page.

    Exposed from the field on 2026-09-21: a contributor's FastAPI refused
    ``ui/api/display`` at import, because that distro's ``python-multipart``
    ships the pre-rename ``multipart`` module.  Simulating it here left the
    page byte-identical — nothing under ``ui/api`` defines a port — so this
    guards the shape of the hazard, not one instance of it.
    """
    refused = gen_ports_reference._import_everything()
    assert refused == [], (
        "these trcc modules will not import here, so doc/REFERENCE_PORTS.md "
        f"cannot be generated completely: {refused}"
    )


def test_a_weakproxy_wearing_a_trcc_module_name_is_not_swept_up_as_a_class() -> None:
    """``inspect.isclass`` is ``isinstance(obj, type)``, and ``__class__`` lies.

    A weakref proxy forwards ``__class__`` AND ``__module__`` to its referent,
    so a proxy to a class passes both halves of the sweep's filter and then
    explodes in ``issubclass`` with ``arg 1 must be a class``.

    The real one was ctypes: it caches every array type behind such a proxy,
    and the cached type wears the ``__module__`` of whichever module first
    evaluated ``c_uint8 * 32``.  Usually that is ``pynvml``, whose name this
    sweep ignores — but when ``adapters/sensors/_smc.py`` got there first, the
    proxy wore ``trcc.`` and the generator crashed.  Who wins depends on
    import order, so under ``pytest -n`` it was whichever worker drew
    ``tests/test_sensors_macos.py``: the page the module header calls
    deterministic was a coin flip, and it cost a contributor a bug report they
    could only describe as an environment difference.

    Gating that race would mean racing.  This gates the invariant instead: a
    proxy is not a class, whoever made it.
    """
    import inspect
    import weakref

    class Decoy:
        """A real class, and then a proxy to it wearing the same name."""

    Decoy.__module__ = "trcc.decoy"
    proxy = weakref.proxy(Decoy)

    assert inspect.isclass(proxy), "the trap this guards is gone; so is the need"
    assert proxy.__module__ == "trcc.decoy", "__module__ no longer forwards"

    swept = gen_ports_reference._all_classes()
    assert Decoy in swept, "the sweep stopped seeing real classes"
    assert not any(type(o).__name__.endswith("ProxyType") for o in swept), (
        "a weakref proxy was swept up as a class"
    )
    gen_ports_reference.generate()
