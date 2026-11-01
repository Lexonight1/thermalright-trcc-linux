#!/usr/bin/env python3
"""TRCC's add-on modules: the ones no build carries, so a user installs them.

Usage::

    python3.12 dev/tools/add_ons.py              # what is an add-on, and is it published
    python3.12 dev/tools/add_ons.py --release    # a release's gate: refuse an unpublished add-on
    python3.12 dev/tools/add_ons.py --gate       # prove the tool against known answers

RGB is **built in**.  The Audio Visualizer is an **add-on**: the wheel
excludes it (and so the deb, rpm, Arch and Nix packages, all built from it),
the frozen builds exclude it, and a user installs it from the signed module
index.

Those are two halves of one decision and they can be landed apart.  They
were, on 2026-11-01: the builds stopped carrying the Visualizer hours before
anything published it.  A release in that window would have taken the visuals
off every upgrader's panel with no way to get them back -- the exact thing
decision 5 ("upgrades change nothing") exists to prevent.  Nothing in the tree
said so; it was a sentence in a plan.

So this is the one place that knows, and both halves read it: the suite
(``tests/test_add_ons_are_installable.py``) and the release workflow, which
refuses to build while :data:`PENDING_PUBLISH` holds anything.

No project imports: it reads ``pyproject.toml`` and the shipped index, so a
release workflow can run it with nothing installed.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

#: The repository root -- this file is ``dev/tools/add_ons.py``.
REPO = Path(__file__).resolve().parents[2]
#: Where a module's exclusion from the wheel is declared.
PYPROJECT = REPO / "pyproject.toml"
#: The index TRCC ships, published to GitHub Pages by ``pages.yml``.
INDEX = REPO / "site" / "modules" / "index.json"

#: An add-on NOT yet in the index, and why.  A release refuses while this
#: holds anything: until the module is published there is no way for a user
#: who had it to get it back.  Emptying it is the last step of publishing one.
PENDING_PUBLISH: dict[str, str] = {
    "visualizer": (
        "left the base install 2026-11-01; its bundle is not packed, signed, "
        "reviewed or listed yet -- the author steps of GUIDE_MODULES"
    ),
}


def add_ons(pyproject: Path | None = None) -> set[str]:
    """Every module no build carries: the wheel's own excludes name each one
    as ``src/trcc/modules/<key>/**``, so this cannot drift from the build."""
    text = (pyproject or PYPROJECT).read_text(encoding="utf-8")
    excludes = tomllib.loads(text)["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"]
    found = {m.group(1) for line in excludes
             if (m := re.fullmatch(r"src/trcc/modules/(\w+)/\*\*", line))}
    print(f"add-ons (excluded from the wheel): {sorted(found) or 'none'}")
    return found


def listed(index: Path | None = None) -> set[str]:
    """Every module the shipped index offers -- what a user can install."""
    path = index or INDEX
    if not path.is_file():
        print(f"no index at {path}")
        return set()
    found = set(json.loads(path.read_text(encoding="utf-8"))["signed"]["modules"])
    print(f"the index offers: {sorted(found) or 'none'}")
    return found


def stranded(pyproject: Path | None = None, index: Path | None = None,
             pending: dict[str, str] | None = None) -> list[str]:
    """Add-ons a user cannot install and that nothing admits to: no build
    carries them, the index does not offer them, and they are not pending."""
    waiting = PENDING_PUBLISH if pending is None else pending
    found = sorted(add_ons(pyproject) - listed(index) - set(waiting))
    print(f"stranded: {found or 'none'}")
    return found


def release_blockers(pyproject: Path | None = None, index: Path | None = None,
                     pending: dict[str, str] | None = None) -> dict[str, str]:
    """Why a release must not go out: every add-on a user could not install
    after it, by key -- the pending ones, and any stranded without admitting
    it.  Empty means a release strands nobody."""
    waiting = dict(PENDING_PUBLISH if pending is None else pending)
    blockers = {key: waiting[key] for key in sorted(waiting)}
    for key in stranded(pyproject, index, waiting):
        blockers[key] = "no build carries it and the index does not offer it"
    print(f"release blockers: {sorted(blockers) or 'none'}")
    return blockers


# =========================================================================
# --gate — prove the tool before trusting its answer
# =========================================================================

_FIXTURE_PYPROJECT = """
[tool.hatch.build.targets.wheel]
exclude = [
    "**/*.7z",
    "src/trcc/modules/weather/**",
    "src/trcc/modules/visualizer/**",
]
"""
_FIXTURE_INDEX = '{"signed": {"modules": {"weather": {"version": "1"}}}}'


def gate() -> int:
    """Re-prove the tool against known answers.  Offline and instant."""
    import tempfile

    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        proj, index = root / "pyproject.toml", root / "index.json"
        proj.write_text(_FIXTURE_PYPROJECT, encoding="utf-8")
        index.write_text(_FIXTURE_INDEX, encoding="utf-8")
        pending = {"visualizer": "not published yet"}

        checks.append(("an excluded module is an add-on",
                       add_ons(proj) == {"weather", "visualizer"}))
        checks.append(("a non-module exclude is not a key",
                       "7z" not in add_ons(proj)))
        checks.append(("the index says what is installable",
                       listed(index) == {"weather"}))
        checks.append(("a published add-on is not stranded",
                       "weather" not in stranded(proj, index, pending)))
        checks.append(("a pending add-on is not stranded, but BLOCKS a release",
                       stranded(proj, index, pending) == []
                       and set(release_blockers(proj, index, pending)) == {"visualizer"}))
        checks.append(("an add-on in NEITHER is stranded AND blocks",
                       stranded(proj, index, {}) == ["visualizer"]
                       and set(release_blockers(proj, index, {})) == {"visualizer"}))
        everything = root / "all.json"
        everything.write_text(
            '{"signed": {"modules": {"weather": {}, "visualizer": {}}}}',
            encoding="utf-8")
        checks.append(("everything published blocks nothing",
                       release_blockers(proj, everything, {}) == {}))
        checks.append(("a missing index strands every add-on",
                       stranded(proj, root / "absent.json", {}) == ["visualizer", "weather"]))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = [label for label, ok in checks if not ok]
    if failed:
        print(f"\nGATE FAILED: {len(failed)} of {len(checks)}")
        return 1
    print(f"\nGATE PASSED — {len(checks)} checks")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--gate" in args:
        return gate()
    blockers = release_blockers()
    if "--release" not in args:
        return 0
    for key, why in blockers.items():
        print(f"::error::{key} is not installable -- {why}")
    if blockers:
        print("\nA release now would leave every user who had it with no way "
              'to get it back.  Publish it (GUIDE_MODULES, "Getting your '
              'module listed"), then take it out of PENDING_PUBLISH.')
        return 1
    print("every add-on is published -- a release strands nobody")
    return 0


if __name__ == "__main__":
    sys.exit(main())
