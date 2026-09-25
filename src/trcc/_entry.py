"""Shared CLI entry point — runs ``trcc`` (the new top-level tree).

Used by both invocations:

    python -m trcc        →  __main__.py  →  this _entry.main()
    trcc (console script) →  pyproject `trcc = "trcc._entry:main"`  →  this

The legacy tree (and its ``TRCC_LEGACY=1`` escape hatch) was moved to the
``legacy`` branch; this entry point dispatches straight to the new tree.
"""
from __future__ import annotations


def put_bundled_tools_on_path() -> str | None:
    """A frozen app runs the tools it ships: its own folder goes first on PATH.

    The macOS DMG and the Windows installer both put 7-Zip and ffmpeg beside
    the executable, and the toolchain finds tools through PATH -- which never
    included that folder.  An app launched from Finder gets a PATH of
    /usr/bin:/bin:/usr/sbin:/sbin, so the macOS DMG's bundled tools were never
    run at all (#219).  Returns the folder added, or None when not frozen.
    """
    import logging
    import os
    import sys
    from pathlib import Path

    if not getattr(sys, "frozen", False):
        return None
    here = str(Path(sys.executable).parent)
    os.environ["PATH"] = here + os.pathsep + os.environ.get("PATH", "")
    logging.getLogger(__name__).info("put_bundled_tools_on_path: %s", here)
    return here


def main() -> int | None:
    """Dispatch to the new top-level CLI, with startup crashes recorded.

    ``python -m trcc`` gets crash logging from ``__main__.py``.  The console
    script does NOT go through that file — ``[project.scripts]`` binds ``trcc``
    straight here — so until now every packaged install (rpm, deb, pacman,
    pipx, PyPI) had none.  Measured with a simulated import failure:
    ``python -m trcc`` wrote a log with the CRITICAL in it; ``trcc`` wrote
    nothing at all.

    Buffering here closes that, and costs one light import: the logging adapter
    pulls 7 trcc modules and no third-party package, ~6.6 ms.  Everything
    heavy — and every import that can realistically fail — happens on the line
    below, inside the guard.
    """
    import logging

    from trcc.adapters.infra.logging import ensure_configured, start_early_logging

    start_early_logging()
    put_bundled_tools_on_path()
    log = logging.getLogger(__name__)
    log.info("main: dispatching to the CLI")
    try:
        from trcc.ui.cli.main import main as _next_main
        return _next_main()
    except Exception:
        log.critical("Fatal startup error", exc_info=True)
        try:
            ensure_configured()
        except Exception:
            log.exception("Fatal startup error: could not write the log either")
        raise
