#!/usr/bin/env python3
"""Count functions in ``src/trcc`` that emit no log line.

**Why this is a gate and not a style note.**  Users send us ``trcc report``,
which pastes the log file.  That paste is the whole diagnosis for hardware we
do not own and cannot reproduce on.  A function with no log line is therefore
not "untidy" — it is a bug report we cannot answer, and a round-trip asking
someone to reproduce with a flag.

Exclusions, each for a CAUSE rather than for convenience:

* **abstract methods and stubs** — no body ran, so nothing happened to report.
* **dunders the logger itself calls while formatting** (``__repr__``,
  ``__str__``, ``__eq__``, ``__len__``, …).  Logging inside these recurses:
  the logger formats its arguments, which calls ``__repr__``, which logs,
  which formats.  This is a technical impossibility, not a preference.

Everything else counts.  A property getter counts.  ``__init__`` counts.

    PYTHONPATH=src python3 dev/tools/logging_coverage.py            # summary
    PYTHONPATH=src python3 dev/tools/logging_coverage.py --list     # name them
    PYTHONPATH=src python3 dev/tools/logging_coverage.py --area ui  # one area
"""
from __future__ import annotations

import ast
import collections
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "trcc"

_LOG_CALLS = frozenset({
    "info", "debug", "warning", "error", "exception", "critical", "log",
})

#: Invoked by the logging machinery itself while formatting a record — a log
#: call inside one of these recurses until the stack ends.
_RECURSION_RISK = frozenset({
    "__repr__", "__str__", "__format__", "__eq__", "__hash__",
    "__len__", "__iter__", "__next__", "__contains__", "__bool__",
})


def _emits_log(fn: ast.AST) -> bool:
    """True if *fn* calls anything that looks like a logger method."""
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in _LOG_CALLS
        for n in ast.walk(fn)
    )


def _is_stub(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the body is only a docstring / ``pass`` / ``...``."""
    body = [
        s for s in fn.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    return not body or all(isinstance(s, ast.Pass) for s in body)


def _is_abstract(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "abstractmethod")
        or (isinstance(d, ast.Attribute) and d.attr == "abstractmethod")
        for d in fn.decorator_list
    )


def silent_functions() -> list[str]:
    """Every countable function with no log call, as ``path::name``."""
    out: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        rel = path.relative_to(_SRC)
        for fn in [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            if fn.name in _RECURSION_RISK or _is_stub(fn) or _is_abstract(fn):
                continue
            if not _emits_log(fn):
                out.append(f"{rel}::{fn.name}")
    return out


def countable_total() -> int:
    """How many functions the rule applies to at all."""
    total = 0
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for fn in [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            if fn.name in _RECURSION_RISK or _is_stub(fn) or _is_abstract(fn):
                continue
            total += 1
    return total


def main(argv: list[str]) -> int:
    silent = silent_functions()
    total = countable_total()
    area = ""
    if "--area" in argv:
        area = argv[argv.index("--area") + 1]
        silent = [s for s in silent if s.startswith(area)]

    print(f"countable functions : {total}")
    print(f"  with logging      : {total - len(silent_functions())} "
          f"({100 * (total - len(silent_functions())) / total:.0f}%)")
    print(f"  SILENT            : {len(silent_functions())} "
          f"({100 * len(silent_functions()) / total:.0f}%)")

    if "--list" in argv or area:
        print()
        for name in silent:
            print(f"  {name}")
    else:
        print()
        by_area: collections.Counter[str] = collections.Counter(
            s.split("/")[0] for s in silent_functions()
        )
        for a, n in by_area.most_common():
            print(f"    {a:14} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
