"""``refuse_while_trcc_runs`` -- with no import-time side effects.

Its own module because ``_mock_bootstrap`` changes ``sys.path``, creates the
dev folders and sets ``TRCC_DAEMON`` the moment it is imported, so the dev
tools that only need this check each carried a lazy-import wrapper for it --
four copies.  ``_mock_bootstrap`` re-exports it.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def refuse_while_trcc_runs(what: str) -> None:
    """Exit when a running TRCC App already owns the panels.

    For a dev run that builds an App on REAL USB -- ``--hardware``, the
    profilers.  Beside a running App that is two owners of one panel: they
    fight over it, and whichever exits blanks it (2026-10-02, the user's
    panel).  Going through the running App instead would measure the wrong
    process, so the only right answer is "not now".
    """
    # This tree's src, as importing the bootstrap used to arrange: a bare
    # ``import trcc`` could find an installed copy instead.
    src = str(Path(__file__).resolve().parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from trcc import ipc

    if ipc.daemon_running():
        sys.exit(f"{what}: TRCC is running and owns the panels "
                 f"({ipc.socket_path()}).  This would build a second App on "
                 "the same USB -- the two fight, and the panel blanks when one "
                 "exits.  Quit TRCC first: `trcc kill`.")
    log.info("refuse_while_trcc_runs: %s -- no TRCC App running", what)
