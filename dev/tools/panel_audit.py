#!/usr/bin/env python3
"""Is the PanelSpec conversion tractable — and which panel is next?

A gui panel is a background plus controls at coordinates, and the project
already keeps three tables for that: ``Layout`` (rects, mined from the
Windows ``InitializeComponent()``), ``Assets`` (images) and ``Styles``
(QSS).  Converting a panel to a ``PanelSpec`` means stating it as data
instead of imperative widget code.

Two questions decide whether that is worth doing, and both are measurable
rather than matters of taste:

    vocab      Does the control vocabulary CLOSE?  Plots the novelty curve
               — new control kinds and properties introduced by each
               additional panel.  Decaying to zero means a finite spec can
               describe the whole skin; a curve that keeps climbing means
               every panel is a special case and the abstraction is wrong.

    intents    Does the INTENT vocabulary close?  Same curve, applied to
               what activating a control DOES.  A spec control has to say
               that; if it resolves to a handful of intents the wiring can
               be declared, and if every handler is a snowflake it cannot.

    readiness  Which panels are ready NOW?  A panel is convertible when
               everything it needs is already NAMED.  Anything still written
               as a one-off literal is work to do first — countable, so
               conversion order stops being a guess.

Usage::

    PYTHONPATH=src python3 dev/tools/panel_audit.py vocab
    PYTHONPATH=src python3 dev/tools/panel_audit.py intents
    PYTHONPATH=src python3 dev/tools/panel_audit.py readiness

Run ``readiness`` after each conversion: the blocker total is the backlog
and should visibly fall.
"""
from __future__ import annotations

import ast
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "trcc" / "ui" / "gui"

# Properties a spec carries SEMANTICALLY — meaningful to any skin.
SEMANTIC = {
    "setGeometry", "setText", "setToolTip", "setChecked", "setCheckable",
    "setAlignment", "setWordWrap", "setEnabled", "setVisible", "setIcon",
    "setPlaceholderText", "setMaxLength", "setValidator", "setRange",
    "setValue", "setCurrentIndex", "addItems", "setFixedSize",
}
# Pure skin polish — belongs in a named ``Styles`` entry, not a spec field.
COSMETIC = {
    "setStyleSheet", "setPalette", "setFont", "setCursor", "setFlat",
    "setFrameShape", "setFrameShadow", "setAutoFillBackground",
    "setScaledContents", "setContentsMargins", "setSpacing",
}


def _panels() -> list[Path]:
    return sorted(SRC.glob("uc_*.py"))


def _is_named(node: ast.AST) -> bool:
    """``Styles.X`` / ``Layout.Y`` / a module constant — already named."""
    return isinstance(node, (ast.Attribute, ast.Name))


def _controls(tree: ast.Module) -> dict[str, str]:
    """``self.<attr> = <Widget|helper>(...)`` -> the constructed kind."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)):
            continue
        for tgt in node.targets:
            if not (isinstance(tgt, ast.Attribute)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"):
                continue
            fn = node.value.func
            name = (fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute) else "")
            if name.startswith("Q") or "button" in name or "checkbox" in name:
                out[tgt.attr] = name
    return out


def _properties(tree: ast.Module) -> dict[str, set[str]]:
    """``self.<attr>.setX(...)`` -> the methods called per control."""
    props: dict[str, set[str]] = defaultdict(set)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "self"):
            props[node.func.value.attr].add(node.func.attr)
    return props


def vocab() -> int:
    """Novelty curve — does the vocabulary close?"""
    seen_kinds: set[str] = set()
    seen_props: set[str] = set()
    kinds: Counter[str] = Counter()
    props: Counter[str] = Counter()
    total = hatch = 0

    ordered = sorted(_panels(), key=lambda p: p.stat().st_size, reverse=True)
    print(f"{'panel':24}{'ctrls':>7}{'new kinds':>11}{'new props':>11}")
    print("-" * 53)
    for path in ordered:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        ctrls, pr = _controls(tree), _properties(tree)
        if not ctrls:
            continue
        new_k = set(ctrls.values()) - seen_kinds
        new_p = {p for a in ctrls for p in pr.get(a, set())} - seen_props
        seen_kinds |= new_k
        seen_props |= new_p
        for attr, kind in ctrls.items():
            kinds[kind] += 1
            props.update(pr.get(attr, set()))
            total += 1
            if pr.get(attr, set()) & COSMETIC:
                hatch += 1
        print(f"{path.stem:24}{len(ctrls):7}{len(new_k):11}{len(new_p):11}")

    print(f"\n{total} controls, {len(seen_kinds)} distinct kinds, "
          f"{len(seen_props)} distinct properties")
    print("\ncontrol kinds:")
    for k, v in kinds.most_common(12):
        print(f"  {v:4}  {k}")
    print("\nsemantic properties (spec fields):")
    for p, v in sorted(props.items(), key=lambda x: -x[1]):
        if p in SEMANTIC:
            print(f"  {v:4}  {p}")
    print("\ncosmetic properties (belong in a named Styles entry):")
    for p, v in sorted(props.items(), key=lambda x: -x[1]):
        if p in COSMETIC:
            print(f"  {v:4}  {p}")
    pct = hatch * 100 // max(total, 1)
    print(f"\n{hatch}/{total} controls ({pct}%) touch a cosmetic property.")
    print("A decaying novelty curve means the vocabulary closes; a flat "
          "nonzero one means every panel is a special case.")
    return 0


def _intent_of(fn: ast.FunctionDef) -> str:
    """What does this handler ultimately DO?

    Most specific first: a handler that opens a dialog AND dispatches is a
    "dialog -> dispatch", not a plain dispatch.  "widget-local only" is not
    an intent — it is a slider updating its own label, which no spec needs
    to describe.
    """
    body = ast.unparse(fn)
    dialog = bool(re.search(
        r"QFileDialog|QMessageBox|QColorDialog|QInputDialog|\.exec\(\)", body))
    dispatch = "_app.dispatch(" in body or "self.dispatch(" in body
    handler = bool(re.search(r"\bh\.\w+\(|_active_lcd\(\)|_handlers\[", body))
    navigate = bool(re.search(
        r"_show_panel\(|setCurrentIndex\(|\.show\(\)|\.hide\(\)", body))

    if dialog and dispatch:
        return "dialog -> dispatch"
    if dialog:
        return "dialog only"
    if dispatch:
        return "dispatch"
    if handler:
        return "call device handler"
    if "invoke_delegate(" in body:
        return "invoke_delegate (bubble up)"
    if re.search(r"\.emit\(", body):
        return "emit signal (bubble up)"
    if navigate:
        return "navigate / show-hide"
    if "settings.set_" in body or "conf.save" in body:
        return "write settings"
    return "widget-local only"


def intents() -> int:
    """Novelty curve over handler INTENTS — can the wiring be declared?"""
    files = sorted(SRC.glob("*.py"), key=lambda p: p.stat().st_size,
                   reverse=True)
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    total = 0

    print(f"{'file':26}{'handlers':>9}{'new intents':>13}")
    print("-" * 50)
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        kinds = [
            _intent_of(n) for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name.startswith("_on_")
        ]
        if not kinds:
            continue
        new = set(kinds) - seen
        seen |= new
        counts.update(kinds)
        total += len(kinds)
        print(f"{path.stem:26}{len(kinds):9}{len(new):13}")

    print(f"\n{total} handlers, {len(seen)} distinct intents\n")
    widest = max(counts.values()) if counts else 1
    for kind, n in counts.most_common():
        print(f"  {n:4} {kind:28} {'#' * (n * 40 // widest)}")

    tail = sum(n for k, n in counts.items() if n <= 2)
    local = counts.get("widget-local only", 0)
    print(f"\nlong tail (intents used <=2 times): {tail}/{total} handlers")
    print(f"widget-local (need no intent at all): {local}/{total}")
    print(f"actual wiring surface: {total - local} handlers")
    print("\nA decaying curve with a small tail means the wiring can be "
          "declared.\nA flat curve, or a fat tail, means every handler is a "
          "special case.")
    return 0


@dataclass
class Readiness:
    name: str
    lines: int
    controls: int = 0
    styles_named: int = 0
    styles_literal: int = 0
    styles_computed: int = 0
    rects_named: int = 0
    rects_literal: int = 0

    @property
    def blockers(self) -> int:
        return self.styles_literal + self.styles_computed + self.rects_literal

    @property
    def verdict(self) -> str:
        if self.blockers == 0:
            return "READY"
        if self.blockers <= 3:
            return f"near ({self.blockers} to name)"
        return f"blocked ({self.blockers} to name)"


def _readiness(path: Path) -> Readiness:
    text = path.read_text(encoding="utf-8")
    r = Readiness(name=path.stem, lines=len(text.splitlines()))
    tree = ast.parse(text)
    r.controls = len(_controls(tree))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.args):
            continue
        arg = node.args[0]
        if node.func.attr == "setStyleSheet":
            if isinstance(arg, ast.Constant):
                r.styles_literal += 1
            elif _is_named(arg):
                r.styles_named += 1
            else:
                r.styles_computed += 1
        elif node.func.attr == "setGeometry":
            target = arg.value if isinstance(arg, ast.Starred) else arg
            if _is_named(target):
                r.rects_named += 1
            else:
                r.rects_literal += 1
    return r


def readiness() -> int:
    panels = [_readiness(p) for p in _panels()]
    panels = [p for p in panels if p.controls]
    panels.sort(key=lambda p: (p.blockers, p.controls))

    print(f"{'panel':22}{'ln':>6}{'ctl':>5}{'rect!':>7}{'style!':>8}"
          f"{'named':>7}  verdict")
    print("-" * 74)
    for p in panels:
        print(f"{p.name:22}{p.lines:6}{p.controls:5}{p.rects_literal:7}"
              f"{p.styles_literal + p.styles_computed:8}"
              f"{p.styles_named + p.rects_named:7}  {p.verdict}")

    ready = sum(1 for p in panels if p.blockers == 0)
    near = sum(1 for p in panels if 0 < p.blockers <= 3)
    backlog = sum(p.blockers for p in panels)
    print(f"\n{ready} READY, {near} near, {len(panels) - ready - near} blocked")
    print(f"{backlog} literals to name across the skin — the whole backlog.")
    print("\nrect!  = setGeometry with a literal, not a Layout entry")
    print("style! = setStyleSheet with a literal or computed value, "
          "not a Styles entry")
    print("\nREADY means rects and styles are NAMED — not that every control "
          "kind\nalready exists in the spec vocabulary.  New kinds are "
          "expected growth;\nunnamed literals are the blocker.")
    return 0


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "vocab":
        return vocab()
    if action == "intents":
        return intents()
    if action == "readiness":
        return readiness()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
