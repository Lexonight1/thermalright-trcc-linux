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

    print()
    if failures:
        print(f"GATE FAILED — {len(failures)}: " + "; ".join(failures))
        return 1
    print(f"GATE PASSED — {len(_MUST_RESOLVE)} resolve, "
          f"{len(_MUST_BE_STALE)} stale, as expected")
    return 0


def main(argv: list[str]) -> int:
    if "--gate" in argv:
        return gate()
    if gate() != 0:                    # never report without proving itself
        return 2
    print()
    findings = scan()
    for f in findings:
        print(f"  {f}")
    print(f"\n{len(findings)} stale reference(s) in {'/, '.join(_SCANNED)}/")
    return 1 if findings and "--check" in argv else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
