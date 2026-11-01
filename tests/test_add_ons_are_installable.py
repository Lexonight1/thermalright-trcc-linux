"""An add-on a build leaves out must be one a user can install.

TRCC's own modules come two ways: RGB is **built in**, and the Audio
Visualizer is an **add-on** -- no wheel, distro package or frozen build
carries it (``test_build_windows_matches_ci``), so a user installs it from
the signed module index.

Those two halves are one decision, and they can be landed apart.  They were,
on 2026-11-01: the builds stopped carrying the Visualizer hours before
anything published it, and a release in that window would have taken the
visuals off every upgrader's panel with no way to get them back -- the one
thing decision 5 ("upgrades change nothing") exists to prevent.  Nothing in
the tree said so; it was a sentence in a plan.

``dev/tools/add_ons.py`` is now the one place that knows, read by both halves:
this file, and the release workflow, which refuses to build while anything is
unpublished.  The tool proves itself with ``--gate`` (``test_tool_gates``);
these are the rules about the REAL tree.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import add_ons

_RELEASE = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
            / "release.yml")


def test_every_add_on_is_installable_or_named_as_pending() -> None:
    """MUTATION CHECK: empty ``PENDING_PUBLISH`` while the index is empty."""
    stranded = add_ons.stranded()
    assert not stranded, (
        f"{', '.join(stranded)}: no build carries it and the index does not "
        "offer it, so a user who had it cannot get it back.  Publish it (see "
        'GUIDE_MODULES, "Getting your module listed") or name it in '
        "add_ons.PENDING_PUBLISH with the reason -- which blocks releases "
        "until it is published, on purpose"
    )


def test_a_published_add_on_leaves_the_pending_list() -> None:
    """The list is for a gap that is still open; a stale entry would keep
    blocking releases after the reason was gone.  MUTATION CHECK: add a key
    the index already lists."""
    stale = sorted(set(add_ons.PENDING_PUBLISH) & add_ons.listed())
    assert not stale, (
        f"{', '.join(stale)}: the index offers it now -- take it out of "
        "add_ons.PENDING_PUBLISH so releases are no longer blocked"
    )


@pytest.mark.parametrize("key", sorted(add_ons.PENDING_PUBLISH))
def test_every_pending_entry_says_why_and_is_an_add_on(key: str) -> None:
    """A list of bare keys stops being worth reading, and an entry for a
    module every build carries would block releases for nothing."""
    assert len(add_ons.PENDING_PUBLISH[key]) > 30, f"{key}: say what is missing"
    assert key in add_ons.add_ons(), f"{key}: every build carries it -- not an add-on"


def test_a_release_refuses_while_an_add_on_is_unpublished() -> None:
    """The checks above run in the suite; this is what actually stops a
    release, in the workflow that builds one -- before it builds anything.
    MUTATION CHECK: drop the step from release.yml, or move it after the
    build."""
    lines = _RELEASE.read_text(encoding="utf-8").splitlines()
    gate = [n for n, line in enumerate(lines) if "add_ons.py --release" in line]
    build = [n for n, line in enumerate(lines) if "python -m build" in line]
    assert len(gate) == 1 and build, (
        "release.yml must run `dev/tools/add_ons.py --release`: a release "
        "while an add-on is unpublished is the failure this file is about"
    )
    assert gate[0] < min(build), "the refusal must come before the build"
