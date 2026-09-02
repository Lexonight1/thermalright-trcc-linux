#!/usr/bin/env python3
"""Command-surface completeness audit — could someone build their own UI?

The "unified UI" promise is that CLI / API / GUI / qtgui all drive the core the
SAME way: build a Command, ``app.dispatch(cmd)``, read the Result — plus a few
read-only port properties (``device.profile``, ``device.handshake``, …).  A
third-party UI (a TUI, a web front-end, a Stream Deck plugin) can do everything
gui does *iff* every capability is reachable through that contract.

This tool finds where a UI reaches **around** the contract — importing
``services`` / ``adapters`` symbols directly instead of dispatching a Command.
Each such import is a candidate **contract hole**: a capability a command-only
UI would be missing (e.g. gui calling ``FileContentStore.discover_masks`` for mask
previews that ``ListMasks`` doesn't carry).

It surfaces candidates; a human judges which are real holes vs. legitimate
infrastructure (a GUI importing ``QtRenderer`` is fine; importing
``FileContentStore`` to compute data a Command should carry is a hole).

**The contract is Commands *and* Queries.**  It read as 102 Commands until
2026-09-02 while the true surface was 135, because the denominator matched only
classes whose literal base was ``Command`` — every one of the 34 ``Query``
subclasses inherits ``Query`` instead and was invisible, and the abstract
``Query`` base was counted as a capability in their place.  The printed result
was self-evidently impossible and shipped anyway: *"api dispatches 117
command(s)"* against a *"102 Command"* contract.  Reads are half of what a UI
does; a contract audit that cannot see them is measuring its own universe.

**One collector, shared with the gate.**  ``reach_by_command`` is imported by
``tests/test_ui_parity.py``, which ratchets the answer.  It used to be written
twice — once here over the AST, once there over the runtime classes — and the
two had already drifted apart by 34 commands, because this copy could not see
past a dispatch helper (``dispatch_echo(SomeCommand())`` in the CLI,
``_dispatch(cmd)`` in both GUIs' LED handlers) or into an ``IfExp``
(``dispatch(Enable() if on else Disable())`` recorded neither branch).

Usage::
    PYTHONPATH=src python3 dev/tools/ui_contract.py
"""
from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "trcc"

# Runnable without PYTHONPATH=src — the contract is read from the live classes,
# not re-derived from the AST, so the import has to resolve.
sys.path.insert(0, str(_REPO / "src"))

_UIS = {
    "cli": _SRC / "ui" / "cli",
    "api": _SRC / "ui" / "api",
    "gui": _SRC / "ui" / "gui",
    "qtgui": _SRC / "ui" / "qtgui",
}

#: A floor, not a target — 135 today.  Guards the DENOMINATOR: a collector that
#: silently returns little makes every UI look complete.  See
#: ``project_a_measurement_that_names_its_own_universe``.
_MIN_CONTRACT = 100

# Colours
_G, _Y, _R, _GREY, _B, _RST = (
    "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[1m", "\033[0m",
)


def contract_classes() -> tuple[set[str], set[str]]:
    """``(commands, queries)`` — the dispatchable surface, from the live classes.

    Runtime introspection rather than an AST walk, because inheritance is the
    question being asked and only the interpreter answers it reliably: ``Query``
    subclasses ``Command``, so an AST match on the literal base name misses
    every read.  It also sidesteps a collision the AST cannot see — two classes
    named ``DeviceState`` exist (the Query, and a presentation dataclass).
    """
    import trcc.core.commands as commands
    from trcc.core.commands._base import Command, Query

    concrete = {
        name
        for name in dir(commands)
        if isinstance(obj := getattr(commands, name), type)
        and issubclass(obj, Command)
        and obj not in (Command, Query)   # the bases themselves are not capabilities
    }
    if len(concrete) < _MIN_CONTRACT:
        raise SystemExit(
            f"contract collector returned {len(concrete)} classes, under the "
            f"floor of {_MIN_CONTRACT} — trcc.core.commands exported nothing "
            f"useful.  Every UI would score as complete against it."
        )
    queries = {n for n in concrete if issubclass(getattr(commands, n), Query)}
    return concrete - queries, queries


def reach_by_command() -> dict[str, set[str]]:
    """Which UI trees reach each contract class, by AST — never by regex.

    A reference is an ``ast.Name`` (``dispatch(Foo(...))``) or an ``ast.alias``
    (``from ... import Foo``).  Deliberately broader than "an inline call in a
    dispatch argument": a UI that hands a Command to a helper is still reaching
    the capability, and matching only the inline shape undercounted the CLI by
    34.  Matching the class NAME in UI source *text* would over-count the other
    way — ``SendFrame`` appears in two UI trees and is dispatched by neither,
    only mentioned in comments.
    """
    commands, queries = contract_classes()
    reach: dict[str, set[str]] = {n: set() for n in commands | queries}
    for ui, root in _UIS.items():
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                seen = None
                if isinstance(node, ast.Name):
                    seen = node.id
                elif isinstance(node, ast.alias):
                    seen = node.name
                if seen in reach:
                    reach[seen].add(ui)
    if not any(reach.values()):
        raise SystemExit(
            f"reach collector found no Command referenced by any of "
            f"{sorted(_UIS)} — the UI roots are wrong or empty."
        )
    return reach


def dispatched_by(ui: str, reach: dict[str, set[str]]) -> set[str]:
    """The contract classes *ui* reaches — one collector, one answer."""
    return {name for name, uis in reach.items() if ui in uis}


@dataclass(slots=True)
class UiSurface:
    """What one UI touches: Commands dispatched vs. layers reached around."""

    name: str
    dispatched: set[str] = field(default_factory=set)
    service_imports: dict[str, str] = field(default_factory=dict)   # symbol → file:line
    adapter_imports: dict[str, str] = field(default_factory=dict)


def scan_ui(name: str, root: Path, dispatched: set[str]) -> UiSurface:
    surface = UiSurface(name=name, dispatched=dispatched)
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        rel = path.relative_to(_SRC)
        for node in ast.walk(tree):
            _record_bypass(node, rel, surface)
    return surface


def _record_bypass(node: ast.AST, rel: Path, surface: UiSurface) -> None:
    """``from ...services.x import Y`` / ``...adapters.x`` → record the symbol."""
    if not isinstance(node, ast.ImportFrom):
        return
    parts = (node.module or "").split(".")
    where = f"{rel}:{node.lineno}"
    if "services" in parts:
        for alias in node.names:
            surface.service_imports.setdefault(alias.name, where)
    if "adapters" in parts:
        for alias in node.names:
            surface.adapter_imports.setdefault(alias.name, where)


def _print_ui(surface: UiSurface) -> None:
    svc, adp = surface.service_imports, surface.adapter_imports
    holes = len(svc) + len(adp)
    tone = _G if holes == 0 else (_Y if holes <= 4 else _R)
    print(f"{_B}{surface.name}{_RST}  "
          f"reaches {len(surface.dispatched)} of the contract  ·  "
          f"{tone}{holes} contract bypass(es){_RST}")
    for symbol, where in sorted(svc.items()):
        print(f"    {_R}services{_RST}  {symbol:<28} {_GREY}{where}{_RST}")
    for symbol, where in sorted(adp.items()):
        print(f"    {_Y}adapters{_RST}  {symbol:<28} {_GREY}{where}{_RST}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-bypasses", type=int, default=None, metavar="N",
                    help="exit non-zero if the total contract bypass count "
                         "exceeds N.  A ratchet, like MAX_SILENT: a UI that "
                         "reaches past the Command bus for something new "
                         "fails the build, and fixing one lets you lower N.")
    args = ap.parse_args()
    commands, queries = contract_classes()
    reach = reach_by_command()
    print(f"{_B}Command-surface completeness audit{_RST}")
    print(f"  contract = {len(commands)} Command(s) + {len(queries)} Query(ies) "
          f"= {len(commands) + len(queries)} + read-only port properties\n")

    surfaces = [scan_ui(name, root, dispatched_by(name, reach))
                for name, root in _UIS.items() if root.is_dir()]
    for surface in surfaces:
        _print_ui(surface)

    # The delta: symbols gui/qtgui reach for that the pure command UIs don't.
    pure = set()
    for s in surfaces:
        if s.name in ("cli", "api"):
            pure |= set(s.service_imports) | set(s.adapter_imports)
    print(f"{_B}Candidate holes — reached by a GUI, not by cli/api{_RST}")
    print(f"{_GREY}(a symbol the graphical UIs pull from services/adapters but the "
          f"command-only UIs never need = a likely gap in the contract){_RST}")
    flagged = False
    for s in surfaces:
        if s.name not in ("gui", "qtgui"):
            continue
        gui_only = (set(s.service_imports) | set(s.adapter_imports)) - pure
        for symbol in sorted(gui_only):
            where = s.service_imports.get(symbol) or s.adapter_imports.get(symbol)
            print(f"  {_R}{s.name}{_RST}  {symbol:<28} {_GREY}{where}{_RST}")
            flagged = True
    if not flagged:
        print(f"  {_G}none — every service/adapter a GUI reaches, cli/api reach too{_RST}")

    total = sum(len(s.service_imports) + len(s.adapter_imports)
                for s in surfaces)
    print(f"\n{_B}total contract bypasses:{_RST} {total}")
    if args.max_bypasses is not None and total > args.max_bypasses:
        print(f"{_R}FAIL{_RST} — {total} bypass(es) exceeds the "
              f"--max-bypasses ceiling of {args.max_bypasses}.  A UI is "
              f"reaching past the Command bus; route it through a Command "
              f"or lower the ceiling if you fixed one.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
