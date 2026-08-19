"""Logging coverage may only improve — the ratchet.

**Why this is enforced rather than remembered.**  Users diagnose through
``trcc report``, which pastes the log file.  For hardware we do not own that
paste IS the diagnosis, so a function with no log line is a bug report we
cannot answer.  The rule is therefore: *every function, new or old, gets a log
line* — and a rule nobody can fail is a rule that rots.

It cannot start green: 1451 of 3074 countable functions were silent when this
landed.  Failing on all of them would put CI permanently red, which is how a
gate gets ignored.  So it is a **ratchet**:

* add a silent function -> the count rises -> **fail**
* give a silent function a log line -> the count falls -> **fail**, asking you
  to lower :data:`MAX_SILENT` so the ground you gained cannot be lost

The number lives here, in the test, so every diff that moves it shows the
direction of travel.  It should only ever go down.

Exclusions are in ``dev/tools/logging_coverage.py`` and each has a cause:
abstract methods and stubs never ran, and dunders the logger invokes while
formatting a record (``__repr__``, ``__len__``, …) would recurse forever.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "tools"))

import logging_coverage  # noqa: E402  # pyright: ignore[reportMissingImports]

#: Silent functions as of 2026-07-31 (landed at 1451; -5 by deleting
#: six dead functions, which is the cheapest way to improve this number).  LOWER THIS as
#: coverage improves; never raise it.  Worst areas at that point:
#: ui 693, adapters 444, core 147, services 143.
MAX_SILENT = 1434


def test_logging_coverage_only_improves() -> None:
    silent = logging_coverage.silent_functions()
    total = logging_coverage.countable_total()
    actual = len(silent)

    assert actual <= MAX_SILENT, (
        f"{actual - MAX_SILENT} new function(s) with no logging "
        f"({actual} silent of {total}).\n"
        f"Every function gets a log line — users diagnose through "
        f"`trcc report`, which pastes the log; a silent function is a bug "
        f"report we cannot answer.\n"
        f"List them:  PYTHONPATH=src python3 dev/tools/logging_coverage.py --list"
    )

    assert actual >= MAX_SILENT, (
        f"Logging coverage improved — {MAX_SILENT - actual} function(s) gained "
        f"a log line ({actual} silent of {total}).\n"
        f"Lower MAX_SILENT to {actual} in tests/test_logging_coverage.py so the "
        f"improvement cannot be lost."
    )


def test_recursion_risk_dunders_are_excluded_for_a_reason() -> None:
    """The exclusion list is a technical constraint, not a convenience.

    A log call inside ``__repr__`` recurses: the logger formats its arguments,
    which calls ``__repr__``, which logs.  Pinned so nobody 'tidies' the list
    into something arbitrary.
    """
    assert "__repr__" in logging_coverage._RECURSION_RISK
    assert "__len__" in logging_coverage._RECURSION_RISK
    # Things that are NOT recursion risks must not hide in there.
    for name in ("__init__", "__enter__", "__exit__", "__call__",
                 "__getitem__", "__init_subclass__"):
        assert name not in logging_coverage._RECURSION_RISK, (
            f"{name} is not invoked by log formatting — it must be counted"
        )
