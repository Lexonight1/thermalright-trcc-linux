"""Shared CLI entry point — runs the new ``trcc`` (formerly ``next``)
by default; falls back to the legacy tree under ``trcc.legacy`` when
``TRCC_LEGACY=1`` is set.

Used by both invocations:

    python -m trcc        →  __main__.py  →  this _entry.main()
    trcc (console script) →  pyproject `trcc = "trcc._entry:main"`  →  this

Legacy escape hatch stays until the new code is verified on every
device; after that it gets deleted.
"""
from __future__ import annotations

import os


def _legacy_opt_in_requested() -> bool:
    return os.environ.get("TRCC_LEGACY", "").strip().lower() in ("1", "true", "yes")


def main() -> int | None:
    """Dispatch to the new tree (default) or legacy (``TRCC_LEGACY=1``)."""
    if _legacy_opt_in_requested():
        from trcc.legacy.ui.cli import main as _legacy_main
        return _legacy_main()
    from trcc.ui.cli.main import main as _next_main
    return _next_main()
