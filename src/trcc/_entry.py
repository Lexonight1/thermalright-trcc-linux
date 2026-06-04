"""Shared CLI entry point — runs ``trcc`` (the new top-level tree).

Used by both invocations:

    python -m trcc        →  __main__.py  →  this _entry.main()
    trcc (console script) →  pyproject `trcc = "trcc._entry:main"`  →  this

The legacy tree (and its ``TRCC_LEGACY=1`` escape hatch) was moved to the
``legacy`` branch; this entry point dispatches straight to the new tree.
"""
from __future__ import annotations


def main() -> int | None:
    """Dispatch to the new top-level CLI."""
    import sys
    if "--version" in sys.argv or "-v" in sys.argv:
        from trcc import __version__
        print(__version__)
        return 0
    from trcc.ui.cli.main import main as _next_main
    return _next_main()
