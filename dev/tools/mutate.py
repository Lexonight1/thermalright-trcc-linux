#!/usr/bin/env python3
"""Mutation-check tests: break the code on purpose and prove each test notices.

**Why this is a tool and not a scratch script.**  Every session wrote its own
``mutate.py``, and the same two instrument bugs kept coming back:

* ``pytest -p no:xdist`` -- ``pyproject.toml`` ``addopts`` carries ``-n auto``,
  so pytest exits **4** (usage error) before collecting a single test.  Across
  the session transcripts that happened 19 times in 16 sessions; it was
  noticed and re-fixed by hand five times and never written down.
* **A verdict read off the exit code.**  On 2026-10-05 a harness counted
  ``rc != 0`` as "caught" and reported 7 of 7 mutations caught while every run
  was the usage error above.  An import error, a collection error and "no tests
  ran" are not kills either.

So the rules live here, once:

* serial is ``-n 0``, never ``-p no:xdist``;
* a **control** run -- nothing mutated -- must PASS first, or no verdict is
  trusted (it is the only row that tests the harness itself, and it is what
  caught the 2026-10-05 bug);
* **KILLED** = exit 1, every named test in a ``FAILED`` line, no ``error`` in
  the summary.  Anything else is SURVIVED (exit 0) or INVALID;
* the first ``E`` line of each kill is printed, so the reason is read, not
  inferred;
* each target is snapshotted the moment it is mutated and restored byte-for-
  byte (hash-checked), and ``git status`` must read the same at the end.

A spec is a Python file defining ``MUTATIONS``, a list of dicts::

    MUTATIONS = [
        {"name": "export drops the mask",
         "file": "src/trcc/core/commands/theme.py",
         "old": ") if s.mask_visible else None)",
         "new": ") if False else None)",
         "tests": ["tests/test_theme_persistence.py::test_export_current_theme_carries_the_panel_not_the_library"]},
    ]

``old`` must occur exactly once in ``file``.

    PYTHONPATH=src python3.12 dev/tools/mutate.py SPEC.py
    PYTHONPATH=src python3.12 dev/tools/mutate.py --gate   # prove the verdicts
"""
from __future__ import annotations

import hashlib
import os
import runpy
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent

KILLED, SURVIVED, INVALID = "KILLED", "SURVIVED", "INVALID"


@dataclass(frozen=True)
class Mutation:
    """One deliberate break, and the tests that must notice it."""
    name: str
    file: str
    old: str
    new: str
    tests: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    """What one pytest run proves, and why."""
    kind: str
    reason: str


def verdict(returncode: int, output: str, tests: tuple[str, ...]) -> Verdict:
    """Classify a pytest run under a mutation.  Pure, so ``--gate`` can prove it.

    Only exit 1 can be a kill (pytest: 0 passed, 1 tests failed, 2 interrupted
    or collection error, 3 internal error, 4 usage error, 5 no tests ran).
    """
    lines = output.splitlines()
    summary = next((ln.strip("= ") for ln in reversed(lines)
                    if " passed" in ln or " failed" in ln or " error" in ln
                    or "no tests ran" in ln), "")
    if returncode == 0:
        return Verdict(SURVIVED, summary or "exit 0")
    if returncode != 1:
        first = next((ln.strip() for ln in lines if "error" in ln.lower()), "")
        return Verdict(INVALID, f"exit {returncode}: {first or summary}")
    if " error" in summary:
        return Verdict(INVALID, f"errors, not failures: {summary}")
    # "FAILED <id> - <message>": a parametrized id can hold spaces, so the
    # id is everything up to the " - ", not the first whitespace token.
    failed = [ln.removeprefix("FAILED ").split(" - ", 1)[0]
              for ln in lines if ln.startswith("FAILED ")]
    named = [t for t in tests if "::" in t]
    missing = [t for t in named
               if not any(test_id.startswith(t) for test_id in failed)]
    if not failed or missing:
        return Verdict(INVALID, f"the named test did not fail: {missing or tests}")
    reason = next((ln.strip() for ln in lines if ln.startswith("E ")), summary)
    return Verdict(KILLED, reason)


def apply(path: Path, old: str, new: str) -> bytes:
    """Mutate *path* in place; return its original bytes for :func:`restore`.

    Refuses an anchor that is missing or ambiguous -- a substring that also
    matches elsewhere mutates the wrong function (it happened, 2026-09-21).
    """
    original = path.read_bytes()
    text = original.decode("utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"anchor occurs {count} times in {path}, need exactly 1")
    path.write_text(text.replace(old, new), encoding="utf-8")
    return original


def restore(path: Path, original: bytes) -> None:
    """Put *path* back byte-for-byte, and prove it."""
    path.write_bytes(original)
    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(original).digest():
        raise RuntimeError(f"{path} did not restore")


def compile_error(path: Path) -> str | None:
    """Why *path* does not compile, or None -- WITHOUT writing bytecode.

    ``py_compile.compile`` wrote the MUTATED ``.pyc`` into ``__pycache__``,
    stamped with the mutated file's size and mtime.  A same-length mutation
    (``return 1`` -> ``return 0``) restored within the same second left that
    stamp matching the restored source, so Python kept running the mutation
    after this tool reported "the tree is as it was" (2026-10-06).
    """
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as e:
        return str(e.msg)
    return None


def _pytest_env() -> dict[str, str]:
    """No bytecode written under a mutation: see :func:`compile_error`."""
    env = dict(os.environ, PYTHONPATH=str(_ROOT / "src"),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("WAYLAND_DISPLAY", None)
    return env


def _pytest(tests: tuple[str, ...]) -> tuple[int, str]:
    env = _pytest_env()
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n", "0", *tests],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=900,
        check=False,
    )
    return run.returncode, run.stdout + run.stderr


def _git_state() -> str:
    run = subprocess.run(["git", "status", "--porcelain"], cwd=_ROOT,
                         capture_output=True, text=True, check=True)
    diff = subprocess.run(["git", "diff"], cwd=_ROOT, capture_output=True,
                          check=True).stdout
    return run.stdout + hashlib.sha256(diff).hexdigest()


def load(spec: Path) -> list[Mutation]:
    """Read ``MUTATIONS`` from a spec file."""
    rows = runpy.run_path(str(spec))["MUTATIONS"]
    return [Mutation(r["name"], r["file"], r["old"], r["new"],
                     tuple([r["tests"]] if isinstance(r["tests"], str)
                           else r["tests"]))
            for r in rows]


def run(mutations: list[Mutation]) -> int:
    """Control first, then each mutation.  Exit 0 only if every one is KILLED."""
    before = _git_state()
    every = tuple(dict.fromkeys(t for m in mutations for t in m.tests))
    code, out = _pytest(every)
    control = verdict(code, out, every)
    print(f"{'CONTROL':9} {control.kind:9} {control.reason}")
    if control.kind != SURVIVED:
        print("\nThe unmutated tests do not pass, so no verdict below would "
              "mean anything.  Fix that first.")
        return 2

    results: list[str] = []
    for m in mutations:
        path = _ROOT / m.file
        try:
            original = apply(path, m.old, m.new)
        except ValueError as e:
            print(f"{INVALID:9} {m.name}: {e}")
            results.append(INVALID)
            continue
        try:
            broken = compile_error(path) if path.suffix == ".py" else None
            v = (Verdict(INVALID, f"the mutation does not compile: {broken}")
                 if broken is not None else verdict(*_pytest(m.tests), m.tests))
        finally:
            restore(path, original)
        print(f"{v.kind:9} {m.name}: {v.reason[:200]}")
        results.append(v.kind)

    if _git_state() != before:
        print("\nTHE TREE CHANGED during the run -- a restore missed something. "
              "Inspect `git status` before anything else.")
        return 2
    killed = results.count(KILLED)
    print(f"\n{killed} of {len(results)} killed; the tree is as it was.")
    return 0 if killed == len(results) else 1


# --gate — prove the verdicts before trusting one

_USAGE_ERROR = """ERROR: usage: __main__.py [options] [file_or_dir] [file_or_dir] [...]
__main__.py: error: unrecognized arguments: -n
  inifile: pyproject.toml
"""
_KILL = """FAILED tests/test_x.py::test_a - AssertionError: assert 1 == 2
E   AssertionError: assert 1 == 2
============================== 1 failed in 0.70s ===============================
"""
_KILL_SPACED = """FAILED tests/test_x.py::test_a[a b] - AssertionError
E   AssertionError
============================== 1 failed in 0.70s ===============================
"""
_OTHER_TEST = """FAILED tests/test_x.py::test_b - AssertionError
============================== 1 failed in 0.70s ===============================
"""
_COLLECTION = """ERROR tests/test_x.py - ImportError: cannot import name 'gone'
=============================== 1 error in 0.30s ===============================
"""
#: The named test failed AND pytest then died (exit 3, an internal error in
#: teardown): every line looks like a kill, so only the exit code can refuse it.
_INTERNAL = _KILL + "INTERNALERROR> RuntimeError: teardown blew up\n"
_PASSED = "============================== 1 passed in 0.40s ==============================="
_NO_TESTS = "============================ no tests ran in 0.01s ============================="


def gate() -> int:
    """Re-prove the verdicts and the snapshot/restore.  Offline and instant."""
    named = ("tests/test_x.py::test_a",)
    checks = [
        ("the -p no:xdist usage error (rc 4) is INVALID, never a kill",
         verdict(4, _USAGE_ERROR, named).kind == INVALID),
        ("a failure of the named test is KILLED, with its E line",
         verdict(1, _KILL, named) == Verdict(
             KILLED, "E   AssertionError: assert 1 == 2")),
        ("a parametrized id with a space is KILLED, not INVALID",
         verdict(1, _KILL_SPACED, ("tests/test_x.py::test_a[a b]",)).kind
         == KILLED),
        ("a failure of a DIFFERENT test is INVALID",
         verdict(1, _OTHER_TEST, named).kind == INVALID),
        ("a kill-shaped run that exited 3 is INVALID -- only the exit code "
         "decides this one",
         verdict(3, _INTERNAL, named).kind == INVALID),
        ("a collection error is INVALID",
         verdict(2, _COLLECTION, named).kind == INVALID),
        ("an error in an exit-1 summary is INVALID",
         verdict(1, _KILL.replace("1 failed", "1 failed, 1 error"),
                 named).kind == INVALID),
        ("no tests ran (rc 5) is INVALID",
         verdict(5, _NO_TESTS, named).kind == INVALID),
        ("a passing run SURVIVED", verdict(0, _PASSED, named).kind == SURVIVED),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "target.py"
        target.write_text("x = 1\ny = 1\n", encoding="utf-8")
        try:
            apply(target, "= 1", "= 2")
            ambiguous_refused = False
        except ValueError:
            ambiguous_refused = True
        checks.append(("an ambiguous anchor is refused", ambiguous_refused))
        original = apply(target, "x = 1", "x = 2")
        mutated = target.read_text(encoding="utf-8") == "x = 2\ny = 1\n"
        restore(target, original)
        checks.append(("apply mutates exactly the anchor", mutated))
        checks.append(("restore puts the bytes back",
                       target.read_bytes() == b"x = 1\ny = 1\n"))
        checks.append(("the compile check writes no bytecode",
                       compile_error(target) is None
                       and not (Path(tmp) / "__pycache__").exists()))
        target.write_text("x = (\n", encoding="utf-8")
        checks.append(("a mutation that does not compile is caught",
                       compile_error(target) is not None))
    checks.append(("pytest under a mutation writes no bytecode",
                   _pytest_env().get("PYTHONDONTWRITEBYTECODE") == "1"))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = [label for label, ok in checks if not ok]
    if failed:
        print(f"\n{len(failed)} gate check(s) FAILED — do not trust this "
              f"tool's verdicts until they pass.")
        return 1
    print(f"\nAll {len(checks)} gate checks passed.")
    return 0


def main(argv: list[str]) -> int:
    if "--gate" in argv:
        return gate()
    if len(argv) != 1:
        print(__doc__)
        return 2
    return run(load(Path(argv[0])))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
