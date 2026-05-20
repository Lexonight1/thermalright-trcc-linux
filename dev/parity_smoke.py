#!/usr/bin/env python3
"""Developer smoke runner for Phase C parity tests.

Single command, single window of output.  Each parity subject prints
PASS or FAIL with a compact byte-diff summary.  Built on top of the
pytest tests in ``tests/parity/`` so there's one source of truth — the
runner just shells out to pytest and surfaces a developer-friendly view.

Usage::

    $ PYTHONPATH=src python dev/parity_smoke.py
    Phase C parity smoke
    ════════════════════════════════════════════════════════════
      LED packet (canary)         ✓ PASS    10 tests
    ────────────────────────────────────────────────────────────
    Result: 10 / 10 parity tests green

Exit code is 0 on full green, 1 on any failure — works as a pre-tag
check or a tight feedback loop while investigating a real diff.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARITY_DIR = _REPO_ROOT / "tests" / "parity"


@dataclass(frozen=True, slots=True)
class _ParitySubject:
    """One test file's worth of parity coverage."""
    label: str            # Human label printed in the header column
    path: Path            # Test file under tests/parity/


def _subjects() -> Iterator[_ParitySubject]:
    """Discover every test_*.py under tests/parity/, alphabetically."""
    for path in sorted(_PARITY_DIR.glob("test_*.py")):
        # Strip the ``test_`` prefix + ``.py`` suffix for the label
        label = path.stem.removeprefix("test_").replace("_", " ")
        yield _ParitySubject(label=label, path=path)


def _run_pytest(test_path: Path) -> tuple[int, int, int, str]:
    """Run pytest against one file.  Returns (rc, passed, failed, tail).

    The tail is the last 30 lines of pytest output — surfaces the
    diff context when a parity check fails.
    """
    cmd = [
        sys.executable, "-m", "pytest",
        str(test_path),
        "-q", "--no-header", "--tb=short",
        "-p", "no:cacheprovider",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    output = proc.stdout + proc.stderr

    # Tail summary line shape: ``N passed in 0.12s`` or ``M failed, N passed ...``
    passed = _count_token(output, "passed")
    failed = _count_token(output, "failed")
    return proc.returncode, passed, failed, output


def _count_token(output: str, token: str) -> int:
    """Pull the number from a pytest summary line like ``10 passed in 0.12s``.

    pytest prints summaries inside ``=`` border lines and mixes
    several counts on one line (``2 failed, 8 passed in ...``).
    Walk every whitespace-bounded token and grab the integer that
    immediately precedes the target word.
    """
    needle = re.compile(rf"(\d+)\s+{re.escape(token)}\b")
    for line in output.splitlines():
        if (match := needle.search(line)) is not None:
            return int(match.group(1))
    return 0


# =========================================================================
# Output formatting — friendly + grep-friendly
# =========================================================================


_OK = "✓"     # ✓
_FAIL = "✗"   # ✗
_LINE_THICK = "═" * 60
_LINE_THIN = "─" * 60


def _print_header() -> None:
    print("Phase C parity smoke")
    print(_LINE_THICK)


def _print_row(label: str, ok: bool, passed: int, failed: int) -> None:
    glyph = _OK if ok else _FAIL
    status = "PASS" if ok else "FAIL"
    counts = f"{passed} test(s)" if ok else f"{passed} pass / {failed} fail"
    print(f"  {label:<32s} {glyph} {status:<6s} {counts}")


def _print_failure_tail(label: str, tail: str) -> None:
    print(_LINE_THIN)
    print(f"  details: {label}")
    print(_LINE_THIN)
    # Print the last ~30 lines so the byte-diff context surfaces.
    for line in tail.splitlines()[-30:]:
        print(f"  {line}")
    print(_LINE_THIN)


def _print_summary(green: int, total: int) -> None:
    print(_LINE_THICK)
    label = "Result"
    if green == total:
        print(f"{label}: {green} / {total} parity tests green")
    else:
        print(f"{label}: {green} / {total} parity tests green — {total - green} divergence(s)")


# =========================================================================
# Main
# =========================================================================


def main() -> int:
    if shutil.which(sys.executable) is None:
        print("Cannot resolve current Python interpreter; aborting.", file=sys.stderr)
        return 2

    _print_header()
    subjects = list(_subjects())
    if not subjects:
        print("  (no parity tests discovered under tests/parity/)")
        return 0

    failures: list[tuple[str, str]] = []
    total_green = 0
    total_subjects = 0

    for subject in subjects:
        rc, passed, failed, tail = _run_pytest(subject.path)
        total_subjects += 1
        ok = rc == 0 and failed == 0
        _print_row(subject.label, ok, passed, failed)
        if ok:
            total_green += 1
        else:
            failures.append((subject.label, tail))

    for label, tail in failures:
        _print_failure_tail(label, tail)

    _print_summary(total_green, total_subjects)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
