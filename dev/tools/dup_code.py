#!/usr/bin/env python3
"""How much code is written twice -- whole functions and copied runs of statements.

**Why not ``dup_bodies``.**  That tool answers a narrower question on purpose:
siblings of ONE base writing ONE method name identically (pull-up candidates).
Asked "how much duplicate code do we have?" it reported 84 lines for the whole
tree and missed ``UCRgbSkin`` / ``QtRgbSkin`` -- nine methods, line for line the
same -- for two reasons this tool removes:

* **The logging rule hides copies.**  Every function logs, and a copy logs its
  OWN name (``"UCRgbSkin.lights"`` vs ``"QtRgbSkin.lights"``), so two copies
  differ by one string.  Here every log call and every docstring is stripped
  before comparing: they are what a copy changes, not what it does.
* **Copies cross bases and names.**  A gui page and a qtgui page are not
  siblings of anything; the same body under another name is still the same
  body.  Here a body is compared with every other body in the tree.

Two measures:

``functions``  whole function bodies identical after stripping.  Bodies under
               ``MIN_STATEMENTS`` are skipped -- ``return self._x`` written
               twice is not a copy anyone would pull out.
``runs``       ``WINDOW`` consecutive statements (inside any block, nested ones
               too) found in two or more places; overlapping windows merge into
               one run, so a 20-statement copy counts once, not 15 times.

Lines counted are the copies BEYOND the first: a body written three times is
two copies.

    PYTHONPATH=src python3 dev/tools/dup_code.py                  # summary + top clusters
    PYTHONPATH=src python3 dev/tools/dup_code.py --all            # every cluster
    PYTHONPATH=src python3 dev/tools/dup_code.py --area ui        # one area
    PYTHONPATH=src python3 dev/tools/dup_code.py --gate           # prove the tool works

**Run ``--gate`` before trusting a number.**  It re-proves the tool on a
synthetic fixture: copies that differ only by log text and name ARE found, a
one-literal change is NOT, a docstring difference is ignored, a copied run
inside two longer functions IS found, and a short body is skipped.
"""
from __future__ import annotations

import ast
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "trcc"

#: A whole body shorter than this (after stripping) is not reported.
MIN_STATEMENTS = 3
#: Consecutive statements a copied run must share.
WINDOW = 6
#: Names a log call is made on -- ``log.info(...)``, ``frame_log.debug(...)``.
_LOGGERS = frozenset({"log", "logger", "frame_log", "_log", "LOG"})
#: Bare functions that only log.
_LOG_FUNCTIONS = frozenset({"trace"})


@dataclass(frozen=True, slots=True)
class Site:
    """Where one copy lives."""
    path: Path
    name: str
    line: int
    end: int

    @property
    def lines(self) -> int:
        return self.end - self.line + 1

    def __str__(self) -> str:
        rel = self.path.relative_to(_SRC.parent.parent) if self.path.is_relative_to(
            _SRC.parent.parent) else self.path
        return f"{rel}:{self.line}  {self.name}"


def _is_log(stmt: ast.stmt) -> bool:
    """A statement that only logs."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    func = stmt.value.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id in _LOGGERS
    return isinstance(func, ast.Name) and func.id in _LOG_FUNCTIONS


def _is_docstring(stmt: ast.stmt) -> bool:
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str))


def _strip(body: list[ast.stmt]) -> list[ast.stmt]:
    """*body* without its log lines and docstrings."""
    return [s for s in body if not _is_log(s) and not _is_docstring(s)]


#: ``--renamed``: names do not count -- a copy whose variables, attributes and
#: arguments were renamed is still a copy (a "type-2 clone").  Each statement
#: is renamed on its own, so it is lenient across statements.
RENAMED = "--renamed" in sys.argv


class _Renamer(ast.NodeTransformer):
    """Every identifier becomes ``n0``, ``n1`` ... in first-seen order."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    def _n(self, name: str) -> str:
        return self.seen.setdefault(name, f"n{len(self.seen)}")

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node.id = self._n(node.id)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        node.attr = self._n(node.attr)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = self._n(node.arg)
        node.annotation = None
        return node


def _key(stmt: ast.stmt) -> str:
    """What a statement DOES: its tree, without positions, log lines removed
    from any block nested in it (names replaced under ``--renamed``)."""
    clone = _Stripper().visit(ast.parse(ast.unparse(stmt)).body[0])
    if RENAMED:
        clone = _Renamer().visit(clone)
    return ast.dump(clone, annotate_fields=False, include_attributes=False)


def _class_key(cls: ast.ClassDef) -> str | None:
    """A class by what its methods do -- name, base and words aside; None for
    a class with fewer than two methods."""
    methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(methods) < 2:
        return None
    return "||".join(sorted(f"{m.name}:" + "|".join(_key(s) for s in _strip(m.body))
                            for m in methods))


class _Stripper(ast.NodeTransformer):
    """Drops log lines and docstrings from every nested block."""

    def generic_visit(self, node: ast.AST) -> ast.AST:
        super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                kept = _strip(block)
                setattr(node, field, kept or [ast.Pass()])
        return node


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _blocks(tree: ast.AST) -> Iterator[list[ast.stmt]]:
    """Every statement list inside a function -- its body and nested blocks."""
    for fn in _functions(tree):
        for node in ast.walk(fn):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                    yield _strip(block)


def _area(path: Path, root: Path) -> str:
    """``ui/gui``, ``modules/rgb``, ``services`` ... -- where a file sits."""
    parts = path.relative_to(root).parts
    if parts[0] in ("ui", "modules") and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if len(parts) > 1 else "(top)"


def scan_classes(root: Path) -> list[list[Site]]:
    """Classes whose methods all do the same, written 2+ times."""
    found: dict[str, list[Site]] = defaultdict(list)
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and (key := _class_key(node)):
                found[key].append(Site(path, node.name, node.lineno,
                                       node.end_lineno or node.lineno))
    return [sites for sites in found.values() if len(sites) > 1]


def scan(root: Path) -> tuple[list[list[Site]], list[list[Site]]]:
    """(function clusters, run clusters) under *root*."""
    bodies: dict[str, list[Site]] = defaultdict(list)
    windows: dict[tuple[str, ...], list[tuple[Path, ast.stmt, ast.stmt]]] = defaultdict(list)
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in _functions(tree):
            body = _strip(fn.body)
            if len(body) >= MIN_STATEMENTS:
                key = "|".join(_key(s) for s in body)
                bodies[key].append(Site(path, fn.name, body[0].lineno,
                                        body[-1].end_lineno or body[-1].lineno))
        for block in _blocks(tree):
            keys = [_key(s) for s in block]
            for i in range(len(block) - WINDOW + 1):
                windows[tuple(keys[i:i + WINDOW])].append(
                    (path, block[i], block[i + WINDOW - 1]))
    functions = [sites for sites in bodies.values() if len(sites) > 1]
    return functions, _merge_runs(windows)


def _merge_runs(windows: dict[tuple[str, ...], list[tuple[Path, ast.stmt, ast.stmt]]]
                ) -> list[list[Site]]:
    """Windows seen in 2+ places, overlapping ones merged per file into runs."""
    spans: dict[Path, list[tuple[int, int]]] = defaultdict(list)
    groups: list[list[tuple[Path, int, int]]] = []
    for places in windows.values():
        distinct = {(p, a.lineno) for p, a, _ in places}
        if len(distinct) < 2:
            continue
        group = [(p, a.lineno, b.end_lineno or b.lineno) for p, a, b in places]
        groups.append(group)
        for p, start, end in group:
            spans[p].append((start, end))
    merged: dict[Path, list[tuple[int, int]]] = {}
    for path, ranges in spans.items():
        out: list[list[int]] = []
        for start, end in sorted(ranges):
            if out and start <= out[-1][1]:
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([start, end])
        merged[path] = [(a, b) for a, b in out]

    def run_of(path: Path, line: int) -> tuple[int, int]:
        return next(r for r in merged[path] if r[0] <= line <= r[1])

    clusters: dict[frozenset[tuple[Path, int, int]], None] = {}
    for group in groups:
        clusters[frozenset((p, *run_of(p, s)) for p, s, _ in group)] = None
    # A run is reported once, in the largest cluster it belongs to.
    seen: set[tuple[Path, int, int]] = set()
    result: list[list[Site]] = []
    for cluster in sorted(clusters, key=len, reverse=True):
        fresh = [c for c in cluster if c not in seen]
        if len(fresh) > 1:
            seen.update(fresh)
            result.append([Site(p, "run", a, b) for p, a, b in sorted(fresh)])
    return result


def _lines_of(site: Site) -> int:
    return site.lines


def copied_lines(clusters: list[list[Site]]) -> int:
    """Lines beyond the first copy of each cluster."""
    return sum(sum(s.lines for s in sites) - max(s.lines for s in sites)
               for sites in clusters)


def _copies(clusters: list[list[Site]]) -> Iterator[Site]:
    """Every copy beyond the first (the longest stays)."""
    for sites in clusters:
        first = max(sites, key=_lines_of)
        yield from (s for s in sites if s is not first)


def _report(root: Path, show_all: bool, area: str | None) -> None:
    functions, runs = scan(root)
    classes = scan_classes(root)
    measures = (("classes", classes), ("functions", functions), ("runs", runs))
    if area:
        measures = tuple((t, [c for c in cs if any(_area(s.path, root).startswith(area)
                                                    for s in c)]) for t, cs in measures)
    total = sum(len(p.read_text(encoding="utf-8").splitlines())
                for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    print(f"{root}: {total} lines of Python"
          f"{' (names ignored: --renamed)' if RENAMED else ''}")
    for title, clusters in measures:
        print(f"  {title:<9} written 2+ times: {len(clusters):>3} cluster(s), "
              f"{copied_lines(clusters):>5} copied line(s)")
    # The measures overlap (a copied class holds copied functions), so the
    # total counts each copied LINE once.
    lines: dict[str, set[tuple[Path, int]]] = defaultdict(set)
    for _, clusters in measures:
        for s in _copies(clusters):
            lines[_area(s.path, root)].update((s.path, n) for n in range(s.line, s.end + 1))
    print(f"  distinct copied lines: {sum(len(v) for v in lines.values())}, by area:")
    for name, found in sorted(lines.items(), key=lambda kv: -len(kv[1])):
        print(f"    {len(found):>5}  {name}")
    for title, clusters in measures:
        ranked = sorted(clusters, key=lambda c: -copied_lines([c]))
        print(f"\n== {title}: largest first ==")
        for sites in ranked if show_all else ranked[:12]:
            print(f"[{copied_lines([sites]):>4} copied]")
            for s in sites:
                print(f"    {s}  ({s.lines} lines)")


_FIXTURE = '''
import logging
log = logging.getLogger(__name__)

class A:
    def lights(self):
        """A's."""
        log.debug("A.lights")
        x = self.app.dispatch(1)
        y = x + 1
        return y * 2

class B:
    def glow(self):
        log.debug("B.glow -- other words")
        x = self.app.dispatch(1)
        y = x + 1
        return y * 2

class C:
    def lights(self):
        x = self.app.dispatch(1)
        y = x + 2
        return y * 2

class SkinOne:
    def lights(self):
        log.debug("SkinOne.lights")
        return self.app.dispatch(Lights())

    def devices(self):
        log.debug("SkinOne.devices")
        return self.app.dispatch(Devices()).devices

class SkinTwo:
    def lights(self):
        log.debug("SkinTwo.lights -- its own words")
        return self.app.dispatch(Lights())

    def devices(self):
        return self.app.dispatch(Devices()).devices

def renamed_a(self):
    first = self.load()
    second = first.scale(2)
    return second.save()

def renamed_b(me):
    one = me.load()
    two = one.scale(2)
    return two.save()

def short_a(self):
    return self.x

def short_b(self):
    return self.x

def long_one(a):
    q = 0
    a1 = a + 1
    a2 = a1 + 1
    a3 = a2 + 1
    a4 = a3 + 1
    log.info("one")
    a5 = a4 + 1
    a6 = a5 + 1
    return q

def long_two(a):
    z = 9
    a1 = a + 1
    a2 = a1 + 1
    a3 = a2 + 1
    a4 = a3 + 1
    log.info("two, different words")
    a5 = a4 + 1
    a6 = a5 + 1
    return z
'''


def gate() -> int:
    """Prove the tool on known answers."""
    global RENAMED
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "fixture.py").write_text(_FIXTURE, encoding="utf-8")
        RENAMED = False
        functions, runs = scan(root)
        classes = [sorted(s.name for s in c) for c in scan_classes(root)]
        RENAMED = True
        try:
            renamed = [sorted(s.name for s in c) for c in scan(root)[0]]
        finally:
            RENAMED = "--renamed" in sys.argv
    names = [sorted(s.name for s in c) for c in functions]
    checks = [
        ("copies differing only by log text, name and docstring ARE found",
         ["glow", "lights"] in names),
        ("a one-literal change is NOT a copy (C.lights)",
         not any(len([s for s in c if s.name == "lights"]) > 1 for c in functions)),
        ("classes of tiny methods differing only by name and log text ARE found",
         ["SkinOne", "SkinTwo"] in classes),
        ("a renamed copy is NOT found by default",
         not any("renamed_a" in n for n in names)),
        ("a renamed copy IS found under --renamed",
         ["renamed_a", "renamed_b"] in renamed),
        ("a body under MIN_STATEMENTS is skipped",
         not any("short_a" in n for n in names)),
        ("a copied run inside two longer functions IS found",
         any(len(c) == 2 and c[0].lines >= WINDOW for c in runs)),
        ("the run is counted once, not once per window",
         len(runs) == 1),
    ]
    for text, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {text}")
    failed = sum(not ok for _, ok in checks)
    print(f"\n{failed} of {len(checks)} gate check(s) FAILED." if failed
          else f"\nAll {len(checks)} gate check(s) passed.")
    return 1 if failed else 0


def main() -> int:
    if "--gate" in sys.argv:
        return gate()
    area = sys.argv[sys.argv.index("--area") + 1] if "--area" in sys.argv else None
    _report(_SRC, "--all" in sys.argv, area)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
