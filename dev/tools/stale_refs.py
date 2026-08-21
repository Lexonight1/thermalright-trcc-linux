#!/usr/bin/env python3
"""Find references from ``dev/`` and ``tests/`` to ``trcc`` symbols that are gone.

**Why this cannot be an import check.**  The obvious implementation — import the
module, ``hasattr`` the name — is defeated by the two things this repo does
most.  Optional dependencies make a module unimportable on a machine that lacks
them, and every per-OS harness guards on ``sys.platform`` *before* it reaches
its imports.  So a harness whose imports have been dead since a rename exits 0
on the CI runner, reports "Skipping cleanly", and only detonates on the OS where
it is the sole verification we have.

Measured 2026-08-21, which is why this exists: ``dev/smoke_bsd.py`` imports
``trcc.adapters.system.bsd_platform.BSDPlatform``.  The module has been
``bsd.py`` and the class ``BsdOS`` since the OS-family split.  ``smoke_macos``
and ``smoke_windows`` are broken the same way.  CI runs all three on Linux and
fails the build on any non-zero exit; all three are permanently green.  Their
targets are macOS and BSD, for which there is no VM and no reporter — the
harness IS the verification.

So: STATIC.  Resolve the dotted name against the files on disk and read the
module's AST.  No import, nothing to guard, nothing to be absent.

    PYTHONPATH=src python3 dev/tools/stale_refs.py           # report
    PYTHONPATH=src python3 dev/tools/stale_refs.py --check   # exit 1 if any
    PYTHONPATH=src python3 dev/tools/stale_refs.py --gate    # self-test only

**Run ``--gate`` before trusting a run.**  Four hand-written versions of this
check were produced on 2026-08-21 and every one reported a confident wrong
answer — submodule imports read as missing, lazily-imported symbols read as
unused, registry-dispatched classes read as dead.  Each was caught by noticing a
known-true case in the output, never by the tool.  The gate encodes those cases
so the tool cannot be believed before it has proven it can answer them.
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
_SCANNED = ("dev", "tests")


@dataclass(frozen=True)
class Stale:
    """One reference that cannot resolve against the tree."""

    where: str          # file:line making the reference
    module: str         # the dotted module named in the import
    name: str           # the symbol, or "" when the whole module is gone
    reason: str

    def __str__(self) -> str:
        what = f"{self.module}.{self.name}" if self.name else self.module
        return f"{self.where}: {what} — {self.reason}"


def module_file(dotted: str) -> Path | None:
    """The file backing ``trcc.a.b``, or None when no such module exists.

    A package resolves to its ``__init__.py``; both spellings are checked
    because either can hold the symbol.
    """
    parts = dotted.split(".")
    base = _SRC.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _defines(tree: ast.Module) -> set[str]:
    """Top-level names a module binds — definitions AND re-exports.

    Re-exports count: ``from ._udev import RULES_PATH`` at module level makes
    ``RULES_PATH`` importable from here, and a check that only looked for
    ``def``/``class`` would call every façade import stale.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.If):
            # ``if TYPE_CHECKING:`` blocks still bind names for importers.
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    names.update(a.asname or a.name.split(".")[0]
                                 for a in inner.names)
    return names


def _is_submodule(package: str, name: str) -> bool:
    """``from trcc.adapters.system import spd`` — a module, not an attribute.

    This is the trap that made the first draft report nine false positives:
    ``hasattr(package, "spd")`` is False until the submodule is imported, so an
    import check calls a perfectly good reference stale.
    """
    return module_file(f"{package}.{name}") is not None


#: References to the ``legacy`` branch that are CORRECT and permanent.  Keyed
#: by (file, module) with the reason, and printed as "expected" rather than
#: hidden -- a gate whose exclusions are invisible is a gate nobody can audit.
#:
#: An entry matching nothing FAILS, so an exemption cannot outlive its cause.
_EXPECTED_LEGACY: dict[tuple[str, str], str] = {
    ("dev/_dc_legacy_dump.py", "trcc.legacy.adapters.infra.dc_parser"):
        "reference dumper, documented as run with the legacy worktree on "
        "PYTHONPATH — the whole point is to parse with the OLD parser",
    ("dev/smoke_color_path_bytes.py", "trcc.legacy.adapters.render.qt"):
        "one arm of a cutover-vs-legacy comparison; the other arm is the "
        "current renderer and runs by default",
}


def expected_legacy(finding: Stale) -> str | None:
    """The recorded reason this reference is deliberate, or None."""
    return _EXPECTED_LEGACY.get((finding.where.split(":")[0], finding.module))


def scan() -> list[Stale]:
    """Every unresolvable ``trcc`` reference under :data:`_SCANNED`."""
    out: list[Stale] = []
    cache: dict[str, set[str] | None] = {}

    def symbols(dotted: str) -> set[str] | None:
        if dotted not in cache:
            path = module_file(dotted)
            if path is None:
                cache[dotted] = None
            else:
                try:
                    cache[dotted] = _defines(ast.parse(path.read_text("utf-8")))
                except SyntaxError:
                    cache[dotted] = set()
        return cache[dotted]

    for area in _SCANNED:
        for file in sorted((_ROOT / area).rglob("*.py")):
            rel = file.relative_to(_ROOT).as_posix()
            try:
                tree = ast.parse(file.read_text("utf-8"))
            except SyntaxError as e:
                out.append(Stale(rel, "", "", f"does not parse: {e}"))
                continue
            # ast.walk reaches imports nested in functions and ``try`` blocks —
            # the lazy-import idiom this codebase uses throughout.
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("trcc") and \
                                module_file(alias.name) is None:
                            out.append(Stale(f"{rel}:{node.lineno}", alias.name,
                                             "", "module does not exist"))
                    continue
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level or not node.module or \
                        not node.module.startswith("trcc"):
                    continue
                defined = symbols(node.module)
                if defined is None:
                    out.append(Stale(f"{rel}:{node.lineno}", node.module, "",
                                     "module does not exist"))
                    continue
                for alias in node.names:
                    if alias.name == "*" or alias.name in defined:
                        continue
                    if _is_submodule(node.module, alias.name):
                        continue
                    out.append(Stale(f"{rel}:{node.lineno}", node.module,
                                     alias.name, "symbol does not exist"))
    return out


def platform_attribute_names() -> set[str]:
    """Every attribute ANY registered OS answers, plus the port's own.

    "Does any OS have this?" rather than "does this OS have this": a per-OS
    harness cannot know which class it will get, and ``current_platform()``
    returns a different one on every machine.  A name absent from all of them
    is absent everywhere.
    """
    import trcc.adapters.system  # noqa: F401 - populates PLATFORMS
    from trcc.adapters.system._base import PLATFORMS
    from trcc.core.ports import Platform

    names = set(dir(Platform))
    for cls in PLATFORMS.values():
        names |= set(dir(cls))
    return names


def _bindings(tree: ast.AST) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Where each name is bound, and where it is bound to ``current_platform()``.

    Rebinding is why this exists rather than a name lookup.  ``smoke_linux``
    binds ``p`` to a Platform at :78 and :96 and to a ``Path`` in a
    comprehension at :152 -- a checker that only knows the name reports
    ``p.name`` and ``p.is_dir()`` as missing Platform methods.  That was a real
    false positive from a hand-written draft, and it is now a gate case.
    """
    bound: dict[str, list[int]] = {}
    platform_bound: dict[str, list[int]] = {}

    def mark(target: ast.AST, line: int, is_platform: bool = False) -> None:
        for n in ast.walk(target):
            if isinstance(n, ast.Name):
                bound.setdefault(n.id, []).append(line)
                if is_platform:
                    platform_bound.setdefault(n.id, []).append(line)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            is_plat = (isinstance(node.value, ast.Call)
                       and getattr(node.value.func, "id", "") == "current_platform")
            for t in node.targets:
                mark(t, node.lineno, is_plat)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            mark(node.target, node.lineno)
        elif isinstance(node, ast.comprehension):
            # No lineno on a comprehension; the target Name carries one.
            mark(node.target, getattr(node.target, "lineno", 0))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            mark(node.optional_vars, getattr(node.optional_vars, "lineno", 0))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.setdefault(node.name, []).append(node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.setdefault(a.asname or a.name.split(".")[0],
                                 []).append(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in (*node.args.posonlyargs, *node.args.args,
                      *node.args.kwonlyargs):
                bound.setdefault(a.arg, []).append(node.lineno)
    return bound, platform_bound


def _attributes_in(source: str, rel: str, known: set[str]) -> list[Stale]:
    """The attribute pass over one source text.  The gate feeds it snippets.

    One implementation, two callers: a gate that re-implements the rule proves
    the copy works, not the tool.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    bound, platform_bound = _bindings(tree)
    if not platform_bound:
        return []
    out: list[Stale] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)):
            continue
        name = node.value.id
        if name not in platform_bound or node.attr in known:
            continue
        # Nearest PRECEDING binding decides the type; same line wins for the
        # binder, which is what makes the comprehension case come out right.
        prior = [ln for ln in bound.get(name, []) if ln <= node.lineno]
        if not prior or max(prior) not in platform_bound[name]:
            continue
        out.append(Stale(f"{rel}:{node.lineno}", "current_platform()",
                         node.attr, "no OS answers this attribute"))
    return out


def scan_attributes() -> list[Stale]:
    """Attributes read off a ``current_platform()`` result that no OS answers.

    The import check cannot see these.  Six of the thirteen stale references
    found on 2026-08-21 were of this shape -- ``p.detect_devices()`` and
    ``p._make_sensor_enumerator()`` -- sitting in files it had declared clean.
    """
    known = platform_attribute_names()
    out: list[Stale] = []
    for area in _SCANNED:
        for file in sorted((_ROOT / area).rglob("*.py")):
            out += _attributes_in(file.read_text("utf-8"),
                                  file.relative_to(_ROOT).as_posix(), known)
    return out


# ── the gate ──────────────────────────────────────────────────────────────
#
# Known answers, each one a wrong result a hand-written draft actually produced
# on 2026-08-21.  A tool that cannot answer these is not to be believed about
# anything else.

_MUST_RESOLVE = [
    ("trcc.adapters.system", "spd",
     "submodule import — hasattr() says no until imported"),
    ("trcc.adapters.sensors", "bsd", "submodule import"),
    ("trcc.adapters.infra", "data_install_runner", "submodule import"),
    ("trcc.adapters.device.scsi_lcd", "ScsiLcd",
     "registry-dispatched: never named in src, reached via DEVICES[wire]"),
    ("trcc.adapters.system.windows", "WindowsPlatform",
     "registry-dispatched via PLATFORMS[key]"),
    ("trcc.adapters.diagnostics.health", "package_install_hint",
     "DEAD but present — deadness is a different question from existence"),
]

_MUST_BE_STALE = [
    ("trcc.adapters.system.bsd_platform", "BSDPlatform",
     "module renamed to bsd.py and class to BsdOS"),
    ("trcc.adapters.system.macos_platform", "MacOSPlatform",
     "module renamed to macos.py"),
    ("trcc.adapters.system.windows.sources", "WindowsSensorSource",
     "package no longer exists"),
]


#: Snippets with known answers, run through the REAL attribute pass.  The last
#: two are false positives a hand-written draft actually produced on
#: 2026-08-21: the stdlib ``platform`` module shares a name with our port, and
#: ``smoke_linux`` rebinds ``p`` from a Platform to a Path in a comprehension.
_ATTR_CASES: list[tuple[str, str, int, str]] = [
    ("p = current_platform()\np.detect_devices()\n",
     "detect_devices", 1, "renamed to scan_devices — no OS answers it"),
    ("p = current_platform()\np.scan_devices()\n",
     "scan_devices", 0, "the current name, on the port"),
    ("import platform\np = current_platform()\narch = platform.machine()\n",
     "platform.machine", 0, "stdlib module, not our Platform — name collision"),
    ("p = current_platform()\nx = sorted(p.name for p in d.iterdir())\n",
     "p.name after rebinding", 0, "comprehension rebinds p to a Path"),
]


def gate() -> int:
    """Prove the detector answers the known cases before anyone trusts it."""
    failures: list[str] = []

    for module, name, why in _MUST_RESOLVE:
        defined = None
        path = module_file(module)
        if path is not None:
            defined = _defines(ast.parse(path.read_text("utf-8")))
        resolves = defined is not None and (
            name in defined or _is_submodule(module, name))
        mark = "ok  " if resolves else "FAIL"
        print(f"  [{mark}] resolves: {module}.{name} — {why}")
        if not resolves:
            failures.append(f"{module}.{name} should resolve")

    for module, name, why in _MUST_BE_STALE:
        stale = module_file(module) is None
        mark = "ok  " if stale else "FAIL"
        print(f"  [{mark}] stale:    {module}.{name} — {why}")
        if not stale:
            failures.append(f"{module}.{name} should be stale")

    known = platform_attribute_names()
    for source, label, expected, why in _ATTR_CASES:
        got = len(_attributes_in(source, "<gate>", known))
        ok = got == expected
        mark = "ok  " if ok else "FAIL"
        verdict = "flagged" if expected else "not flagged"
        print(f"  [{mark}] attr:     {label} — {verdict}: {why}")
        if not ok:
            failures.append(f"{label} expected {expected} finding(s), got {got}")

    print()
    if failures:
        print(f"GATE FAILED — {len(failures)}: " + "; ".join(failures))
        return 1
    print(f"GATE PASSED — {len(_MUST_RESOLVE)} resolve, "
          f"{len(_MUST_BE_STALE)} stale, {len(_ATTR_CASES)} attribute, "
          f"as expected")
    return 0


def main(argv: list[str]) -> int:
    if "--gate" in argv:
        return gate()
    if gate() != 0:                    # never report without proving itself
        return 2
    print()
    all_found = scan() + scan_attributes()
    expected = [f for f in all_found if expected_legacy(f)]
    findings = [f for f in all_found if not expected_legacy(f)]

    for f in expected:
        print(f"  [expected] {f.where}: {f.module}")
        print(f"             {expected_legacy(f)}")
    for f in findings:
        print(f"  {f}")

    # An exemption that matches nothing has outlived its cause: the file was
    # fixed or deleted and the entry is now a licence nobody is using.
    matched = {(f.where.split(":")[0], f.module) for f in expected}
    orphaned = sorted(set(_EXPECTED_LEGACY) - matched)
    for path, module in orphaned:
        print(f"  ORPHANED EXEMPTION {path}: {module} — no longer present; "
              f"remove it from _EXPECTED_LEGACY")

    print(f"\n{len(findings)} stale reference(s), {len(expected)} expected, "
          f"{len(orphaned)} orphaned exemption(s) "
          f"in {'/, '.join(_SCANNED)}/")
    if "--check" in argv and (findings or orphaned):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
