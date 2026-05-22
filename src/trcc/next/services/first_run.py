"""First-run detection — was this the first time trcc-next started on this user's machine?

Used by the GUI to surface a welcome / setup flow only on the first
launch, and by the CLI's ``status`` to remind users about the doctor
if they haven't run it.

State is a single marker file under ``Paths.config_dir()``:
``.first-run-done``.  Empty file — its presence is the signal.
Wiping ``trcc.json`` doesn't re-trigger first-run by design (a
user's settings reset shouldn't replay the onboarding wizard); deleting
the marker file specifically does.

Why a service rather than a function: gives tests a clean seam
(``service.mark_completed()`` or ``service.reset()``) and keeps the
filesystem call behind one wrapper.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..core.ports import Paths

log = logging.getLogger(__name__)


_MARKER_NAME = ".first-run-done"


class FirstRunService:
    """First-run marker — single source of truth for "has this user
    completed onboarding yet?".

    Trivial implementation: presence of a marker file under config_dir
    is the signal.  Reading is cheap (one stat call); writing is one
    create.  No content — the filename is the entire state.
    """

    def __init__(self, paths: Paths) -> None:
        self._paths = paths

    @property
    def marker_path(self) -> Path:
        return self._paths.config_dir() / _MARKER_NAME

    def is_first_run(self) -> bool:
        """True if the marker hasn't been written yet."""
        return not self.marker_path.exists()

    def mark_completed(self) -> None:
        """Write the marker — the welcome / wizard has finished.

        Idempotent — second call is a no-op.  Failures are logged but
        not raised; first-run UI is a nice-to-have, not a hard gate.
        """
        path = self.marker_path
        if path.exists():
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError as e:
            log.warning("Couldn't write first-run marker at %s: %s", path, e)

    def reset(self) -> None:
        """Re-trigger the first-run flow on next launch.

        Used by ``trcc-next system reset-first-run`` for users who
        want to see the welcome screen again.
        """
        try:
            self.marker_path.unlink(missing_ok=True)
        except OSError as e:
            log.warning(
                "Couldn't remove first-run marker at %s: %s",
                self.marker_path, e,
            )
