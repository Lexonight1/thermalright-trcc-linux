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

Usage::
    PYTHONPATH=src python3 dev/tools/ui_contract.py
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "trcc"

_UIS = {
    "cli": _SRC / "ui" / "cli",
    "api": _SRC / "ui" / "api",
    "gui": _SRC / "ui" / "gui",
    "qtgui": _SRC / "ui" / "qtgui",
}

# Colours
_G, _Y, _R, _GREY, _B, _RST = (
    "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[1m", "\033[0m",
)


def command_names() -> set[str]:
    """Every ``Command`` subclass name — the contract's action surface."""
    names: set[str] = set()
    for path in (_SRC / "core" / "commands").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                target = base.value if isinstance(base, ast.Subscript) else base
                name = getattr(target, "id", getattr(target, "attr", ""))
                if name == "Command":
                    names.add(node.name)
    return names


@dataclass(slots=True)
class UiSurface:
    """What one UI touches: Commands dispatched vs. layers reached around."""

    name: str
    dispatched: set[str] = field(default_factory=set)
    service_imports: dict[str, str] = field(default_factory=dict)   # symbol → file:line
    adapter_imports: dict[str, str] = field(default_factory=dict)


def scan_ui(name: str, root: Path) -> UiSurface:
    surface = UiSurface(name=name)
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        rel = path.relative_to(_SRC)
        for node in ast.walk(tree):
            _record_dispatch(node, surface)
            _record_bypass(node, rel, surface)
    return surface


def _record_dispatch(node: ast.AST, surface: UiSurface) -> None:
    """``x.dispatch(SomeCommand(...))`` → record 'SomeCommand'."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dispatch"
        and node.args
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
    ):
        surface.dispatched.add(node.args[0].func.id)


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
          f"dispatches {len(surface.dispatched)} command(s)  ·  "
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
    commands = command_names()
    print(f"{_B}Command-surface completeness audit{_RST}")
    print(f"  contract = {len(commands)} Command(s) + read-only port properties\n")

    surfaces = [scan_ui(name, root) for name, root in _UIS.items() if root.is_dir()]
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
