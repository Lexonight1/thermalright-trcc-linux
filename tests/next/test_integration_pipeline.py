"""Integration smoke under pytest — runs the same matrix as
``dev/smoke_full_pipeline.py`` so CI catches integration regressions
without anyone having to remember to invoke the dev script.

This test is the unified-UI contract in code: every UI on top of
next/ (CLI / API / GUI / future VR) builds Command objects and
dispatches them on App.  If the Command bus + EventBus carry the
traffic for every Command family here, every UI inherits the same
behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def smoke_results():
    """Run the dev smoke once + cache its step list for the test
    cases below.  Module-scoped — no need to rebuild App per step."""
    # Make the dev/ script importable for one process
    dev_dir = Path(__file__).resolve().parent.parent.parent / "dev"
    sys.path.insert(0, str(dev_dir))
    try:
        from smoke_full_pipeline import _run_steps  # type: ignore[import-not-found]
        return _run_steps()
    finally:
        sys.path.remove(str(dev_dir))


def test_every_step_passed(smoke_results) -> None:
    """Headline assertion: every integration step in the matrix
    succeeded.  Diagnostic detail is in the per-step tests below."""
    failed = [s for s in smoke_results if not s.passed]
    assert not failed, "failed: " + ", ".join(
        f"{s.label} ({s.detail})" for s in failed
    )


def test_every_command_family_present(smoke_results) -> None:
    """The matrix exercises at least one Command from every family
    so the harness itself can't silently regress to "no tests"."""
    labels = {s.label for s in smoke_results}
    # Discovery, display setters, LED setters, control-center setters
    for required in (
        "DiscoverDevices",
        "SetOrientation", "SetBrightness",
        "SetLedMode", "SetLedColor", "SetLedBrightness", "EnableLedTestMode",
        "SetTempUnit", "SetLanguage", "SetGpuDevice", "SetRefreshInterval",
    ):
        assert required in labels, f"smoke matrix dropped {required!r}"
