"""First-run detection — service + Command surface.

Legacy migration was removed when next/'s Paths port was rewired to
read legacy's content layout in place (no copy step needed).  See
`src/trcc/next/core/ports.py` for the resolution-aware path helpers.
"""
from __future__ import annotations

from pathlib import Path

from trcc.app import App
from trcc.core.commands import GetFirstRunStatus, MarkFirstRunDone
from trcc.services.first_run import FirstRunService

from .conftest import FakePaths

# =========================================================================
# FirstRunService
# =========================================================================


def test_first_run_starts_true_then_marks(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    svc = FirstRunService(paths)
    assert svc.is_first_run() is True
    svc.mark_completed()
    assert svc.is_first_run() is False
    # Idempotent — second call doesn't raise.
    svc.mark_completed()
    assert svc.is_first_run() is False


def test_first_run_reset_re_arms(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    svc = FirstRunService(paths)
    svc.mark_completed()
    svc.reset()
    assert svc.is_first_run() is True


def test_first_run_tolerates_unwritable_dir(tmp_path: Path) -> None:
    """If the config dir is read-only, mark_completed shouldn't raise."""
    paths = FakePaths(tmp_path / "nonexistent" / "deeply" / "nested")
    svc = FirstRunService(paths)
    # Should not raise even if the marker can't be written.
    svc.mark_completed()


# =========================================================================
# Commands
# =========================================================================


def test_get_first_run_status_command(fake_platform) -> None:
    app = App(fake_platform)
    r1 = app.dispatch(GetFirstRunStatus())
    assert r1.is_first_run is True
    assert "Welcome" in r1.message
    app.dispatch(MarkFirstRunDone())
    r2 = app.dispatch(GetFirstRunStatus())
    assert r2.is_first_run is False
