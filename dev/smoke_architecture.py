#!/usr/bin/env python3
"""Architecture boundary smoke — the hexagon's dependency law, runnable locally.

A thin LOCAL runner over ``tests/test_architecture_boundaries.py`` (the
CI-enforced source of truth).  It auto-discovers every zero-argument ``test_*``
structural check in that module and runs it, printing OK/FAIL with exit 0/1 —
same convention as :mod:`smoke_factories`.  No logic is duplicated here: the
pytest gate IS the law, this just gives fast local feedback without spinning up
pytest, and stays in sync automatically as checks are added.

Why both?  The pytest gate runs in CI on every push (so the law can't erode);
this smoke is for the inner-loop "did I just break a boundary?" check while
editing.  Fixture-dependent checks (e.g. ones needing a built ``App``) stay
pytest-only and are reported here as SKIP.

Run:
    PYTHONPATH=src python dev/smoke_architecture.py

Exit code 0 on full green, 1 on any divergence.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))


def main() -> int:
    import test_architecture_boundaries as gate

    checks: list[tuple[str, object]] = []
    skipped: list[str] = []
    for name, fn in sorted(vars(gate).items()):
        if not (name.startswith("test_") and inspect.isfunction(fn)):
            continue
        if inspect.signature(fn).parameters:
            skipped.append(name)          # needs a pytest fixture — pytest-only
        else:
            checks.append((name, fn))

    failures: list[tuple[str, str]] = []
    for name, fn in checks:
        try:
            fn()                          # type: ignore[operator]
        except AssertionError as e:
            failures.append((name, str(e)))
            print(f"FAIL  {name}")
        else:
            print(f"OK    {name}")

    for name in skipped:
        print(f"SKIP  {name} (needs pytest fixtures)")

    if failures:
        print()
        for name, msg in failures:
            print(f"FAIL  {name}:\n{msg}\n")
        print(f"{len(failures)} architecture check(s) failed.")
        return 1
    print(f"\nAll {len(checks)} architecture boundary check(s) passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
