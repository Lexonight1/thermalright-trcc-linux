#!/usr/bin/env python3
"""Every control on a C# form, with where it sits and when it is visible.

**Why this exists.**  Features kept going missing without anyone noticing:
the C# local-theme panel's Game mode button and CPU box were never ported,
in 2.1.6 or 2.1.8, and nothing listed them.  The other decompiler tools map
METHODS (``extract_control_flow.py``) and art (``dev/tools/audit_csharp.py``);
none lists the CONTROLS a user sees.  This does, so a test can check each one
against our window (``tests/test_lcd_page_parity.py``) instead of waiting for
someone to stumble on the gap.

For one root form it walks the user controls the form embeds, recursively,
and for every designer control (a field of a control type) records:

* ``rect``            -- ``Location`` + ``Size`` from ``InitializeComponent``
* ``designer_hidden`` -- ``Visible = false`` in the designer
* ``shown_at`` / ``hidden_at`` -- every runtime ``Visible =`` / ``Show()`` /
  ``Hide()`` OUTSIDE the designer, as ``File:Method:line``
* ``events``          -- the handlers wired with ``+=``
* ``visibility``      -- ``visible``, ``hidden`` (for good) or ``conditional``

Three parsing traps, each hit while prototyping and each re-proved by
``--gate``: the decompile writes ``((Control)name).Location`` (a cast, not
``(name)``); two classes can own fields with the same name (``textBoxTimer``
is on ``UCThemeLocal`` AND ``FormLED``), so a toggle in another file counts
only when qualified (``instance.name``); and ``InitializeComponent`` is longer
than any fixed look-back, so the enclosing method is found by definition, not
by distance.

    PYTHONPATH=. python3.12 dev/decompiler/inventory_controls.py   # write JSON
    PYTHONPATH=. python3.12 dev/decompiler/inventory_controls.py --gate
"""
from __future__ import annotations

import bisect
import json
import re
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.csharp import DECOMPILE_ROOT, ORACLE_RELEASE, CSharpSource

ROOT_FORM = "FormCZTV"
OUT_JSON = Path(__file__).with_name("control-inventory.json")

#: WinForms types a user sees.  A field of one of these -- or of a ``UC*`` user
#: control -- is a designer control; anything else (timers, arrays, images) is
#: not on screen.
_CONTROL_TYPES = frozenset({
    "Button", "Label", "TextBox", "PictureBox", "Panel", "CheckBox",
    "ComboBox", "RadioButton", "TrackBar", "ListBox", "NumericUpDown",
    "RichTextBox", "FlowLayoutPanel", "ProgressBar",
})
_FIELD = re.compile(
    r"^\t(?:private|public|internal|protected)\s+([\w.]+)\s+(\w+);\s*$", re.M)
_DESIGNER = "InitializeComponent"


@dataclass(frozen=True)
class Control:
    """One designer control, as the C# builds and toggles it."""
    cls: str
    name: str
    type: str
    rect: list[int] | None
    designer_hidden: bool
    shown_at: list[str] = field(default_factory=list)
    hidden_at: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    moved_at: list[str] = field(default_factory=list)
    resized_at: list[str] = field(default_factory=list)

    @property
    def visibility(self) -> str:
        if self.designer_hidden:
            return "conditional" if self.shown_at else "hidden"
        return "conditional" if self.hidden_at else "visible"


class _Tree:
    """The decompile's classes, by file stem, with method lookup by line."""

    def __init__(self, root: Path) -> None:
        self.files = {p.stem: p for p in sorted(root.rglob("*.cs"))}
        self._text: dict[str, str] = {}
        self._defs: dict[str, tuple[list[int], list[str]]] = {}

    def text(self, cls: str) -> str:
        if cls not in self._text:
            self._text[cls] = self.files[cls].read_text(
                encoding="utf-8", errors="replace")
        return self._text[cls]

    def method_at(self, cls: str, line: int) -> str:
        """The method whose definition most recently precedes *line*."""
        if cls not in self._defs:
            lines, names = [], []
            for no, raw in enumerate(self.text(cls).splitlines(), 1):
                if raw.startswith("\t") and not raw.startswith("\t\t") and (
                        name := CSharpSource.definition_at(raw)):
                    lines.append(no)
                    names.append(name)
            self._defs[cls] = (lines, names)
        lines, names = self._defs[cls]
        i = bisect.bisect_right(lines, line) - 1
        return names[i] if i >= 0 else "?"


def _sites(tree: _Tree, cls: str, name: str,
           act: str) -> Iterator[tuple[str, re.Match[str]]]:
    """Every runtime use of *cls*'s field *name* matching *act*, outside the
    designer, as ``(File:Method:line, match)``."""
    for other in tree.files:
        # Own file: the bare field.  Any other file: only a QUALIFIED access
        # (``ucThemeLocal1.textBoxTimer``) -- a bare name there is that class's
        # own field of the same name.
        pre = r"(?<![\w.])" if other == cls else r"\w\."
        text = tree.text(other)
        if name not in text:
            continue
        for m in re.finditer(rf"{pre}{re.escape(name)}\)?\.{act}", text):
            line = text.count("\n", 0, m.start()) + 1
            method = tree.method_at(other, line)
            if not (other == cls and method == _DESIGNER):
                yield f"{other}:{method}:{line}", m


def _toggles(tree: _Tree, cls: str, name: str) -> tuple[list[str], list[str]]:
    """Runtime show / hide sites of *cls*'s field *name*."""
    shown: list[str] = []
    hidden: list[str] = []
    for site, m in _sites(tree, cls, name,
                          r"(Visible = ([^;]+);|Show\(\)|Hide\(\))"):
        off = m.group(2) == "false" or m.group(1) == "Hide()"
        (hidden if off else shown).append(site)
    return shown, hidden


_GEOMETRY = r"(Left|Top|Width|Height|Size|Location|Bounds) = "
_POSITION = frozenset({"Left", "Top", "Location", "Bounds"})


def _moves(tree: _Tree, cls: str, name: str,
           typ: str) -> tuple[list[str], list[str]]:
    """Where the C# MOVES and where it RESIZES the control at runtime --
    through the field (``name).Left = ``) or, a user control, itself
    (``this).Width = ``: ``UCScreenImage`` places and sizes the preview per
    panel; ``UCComboBoxA`` only grows its height to open the list.

    Kept apart because they mean different things to a check: a moved control
    has no fixed place, a resized one still sits where the designer put it.
    """
    moved: list[str] = []
    resized: list[str] = []
    hits = [(site, m.group(1)) for site, m in _sites(tree, cls, name, _GEOMETRY)]
    if typ in tree.files:
        text = tree.text(typ)
        for m in re.finditer(rf"\bthis\)\.{_GEOMETRY}", text):
            line = text.count("\n", 0, m.start()) + 1
            if (method := tree.method_at(typ, line)) != _DESIGNER:
                hits.append((f"{typ}:{method}:{line}", m.group(1)))
    for site, attr in hits:
        (moved if attr in _POSITION else resized).append(site)
    return moved, resized


def _controls(tree: _Tree, cls: str) -> list[Control]:
    text = tree.text(cls)
    out = []
    for typ, name in _FIELD.findall(text):
        base = typ.split(".")[-1]
        if base not in _CONTROL_TYPES and not base.startswith("UC"):
            continue
        n = re.escape(name)
        loc = re.search(rf"\b{n}\)\.Location = new Point\((-?\d+), (-?\d+)\)", text)
        size = re.search(rf"\b{n}\)\.Size = new Size\((\d+), (\d+)\)", text)
        rect = ([*map(int, loc.groups()), *map(int, size.groups())]
                if loc and size else None)
        shown, hidden = _toggles(tree, cls, name)
        moved, resized = _moves(tree, cls, name, base)
        events = [f"{ev}:{handler}" for ev, handler in re.findall(
            rf"\b{n}\)\.(\w+) \+= (?:new \w+\()?(\w+)\)?;", text)]
        out.append(Control(
            cls=cls, name=name, type=base, rect=rect,
            designer_hidden=bool(re.search(rf"\b{n}\)\.Visible = false;", text)),
            shown_at=shown, hidden_at=hidden, events=events,
            moved_at=moved, resized_at=resized))
    return out


def build(root: Path, form: str = ROOT_FORM) -> dict:
    """The control inventory of *form* and every user control it embeds."""
    tree = _Tree(root)
    classes: dict[str, list[dict]] = {}
    blind: list[str] = []
    queue = [form]
    while queue:
        cls = queue.pop(0)
        if cls in classes or cls not in tree.files:
            continue
        controls = _controls(tree, cls)
        classes[cls] = [dict(asdict(c), visibility=c.visibility) for c in controls]
        queue += [c.type for c in controls if c.type.startswith("UC")]
        blind += _generic_loops(tree, cls)
    return {"release": ORACLE_RELEASE, "root": form, "classes": classes,
            "unreadable_visibility": blind}


def _generic_loops(tree: _Tree, cls: str) -> list[str]:
    """Loops over ``Controls`` -- a show or hide in one names no control, so
    this parser cannot attribute it.  Reported, so a release that adds one
    turns the inventory's verdicts into a question instead of a silent lie."""
    text = tree.text(cls)
    return [f"{cls}:{tree.method_at(cls, text.count(chr(10), 0, m.start()) + 1)}:"
            f"{text.count(chr(10), 0, m.start()) + 1}"
            for m in re.finditer(r"foreach \((?:Control|object) \w+ in [^\n]*\bControls\)", text)]


def render(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def artifacts() -> dict[Path, str]:
    """{path: content} -- pure, so a test can compare it with the committed file."""
    return {OUT_JSON: render(build(DECOMPILE_ROOT))}


# --gate -- re-prove the three parsing traps on fixtures

_PAD = "\t\t// filler\n" * 3000      # makes InitializeComponent > 20k chars

_FIXTURE = {
    "UCPanel.cs": f"""public class UCPanel : UserControl
{{
\tprivate Button buttonGo;
\tprivate Button buttonOff;
\tprivate Button buttonLater;
\tprivate TextBox textBoxTimer;

\tprivate void InitializeComponent()
\t{{
\t\t((Control)buttonGo).Location = new Point(10, 20);
\t\t((Control)buttonGo).Size = new Size(40, 18);
\t\t((Control)buttonGo).Click += buttonGo_Click;
{_PAD}\t\t((Control)buttonOff).Visible = false;
\t\t((Control)buttonLater).Visible = false;
\t}}

\tpublic void Mode(bool on)
\t{{
\t\t((Control)buttonLater).Visible = on;
\t}}
}}
""",
    "FormOther.cs": """public class FormOther : Form
{
\tprivate TextBox textBoxTimer;

\tprivate void Tick()
\t{
\t\t((Control)textBoxTimer).Visible = true;
\t\t((Control)textBoxTimer).Hide();
\t}
}
""",
    "UCGrow.cs": """public class UCGrow : UserControl
{
\tprivate void InitializeComponent()
\t{
\t\t((Control)this).Size = new Size(320, 240);
\t}

\tpublic void Fit(int w)
\t{
\t\t((Control)this).Width = w;
\t}

\tpublic void Move(int x)
\t{
\t\t((Control)this).Left = x;
\t}
}
""",
    "FormRoot.cs": """public class FormRoot : Form
{
\tprivate UCPanel ucPanel1;
\tprivate UCGrow ucGrow1;

\tprivate void Load()
\t{
\t\t((Control)ucPanel1.textBoxTimer).Hide();
\t}

\tprivate void HideAll()
\t{
\t\tforeach (Control c in ((Control)this).Controls)
\t\t{
\t\t\tc.Visible = false;
\t\t}
\t}
}
""",
}


def gate() -> int:
    """Re-prove the parser on fixtures with known answers.  Offline, instant."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in _FIXTURE.items():
            (Path(tmp) / name).write_text(text, encoding="utf-8")
        data = build(Path(tmp), "FormRoot")
    by = {c["name"]: c for c in data["classes"].get("UCPanel", [])}
    grow = {c["name"]: c for c in data["classes"].get("FormRoot", [])}.get(
        "ucGrow1", {})
    checks = [
        ("the embedded user controls are walked",
         set(data["classes"]) == {"FormRoot", "UCPanel", "UCGrow"}),
        ("a cast ((Control)name).Location still gives the rect",
         by.get("buttonGo", {}).get("rect") == [10, 20, 40, 18]),
        ("an event handler is recorded",
         by.get("buttonGo", {}).get("events") == ["Click:buttonGo_Click"]),
        ("a designer hide past a 20k-char look-back is still the designer's",
         by.get("buttonOff", {}).get("visibility") == "hidden"
         and by.get("buttonOff", {}).get("hidden_at") == []),
        ("a runtime show makes a designer-hidden control conditional",
         by.get("buttonLater", {}).get("visibility") == "conditional"),
        ("a same-named field in ANOTHER class does not count",
         by.get("textBoxTimer", {}).get("shown_at") == []),
        ("a QUALIFIED toggle from another class does count",
         by.get("textBoxTimer", {}).get("hidden_at") == ["FormRoot:Load:8"]),
        ("a loop over Controls is reported as unreadable",
         data["unreadable_visibility"] == ["FormRoot:HideAll:13"]),
        ("a user control RESIZING itself is a resize, not a move",
         grow.get("resized_at") == ["UCGrow:Fit:10"]),
        ("a user control MOVING itself is a move",
         grow.get("moved_at") == ["UCGrow:Move:15"]),
        ("a designer Location / Size is neither",
         by.get("buttonGo", {}).get("moved_at") == []
         and by.get("buttonGo", {}).get("resized_at") == []),
    ]
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = [label for label, ok in checks if not ok]
    if failed:
        print(f"\n{len(failed)} gate check(s) FAILED — do not trust this "
              f"inventory until they pass.")
        return 1
    print(f"\nAll {len(checks)} gate checks passed.")
    return 0


def main(argv: list[str]) -> int:
    if "--gate" in argv:
        return gate()
    for path, content in artifacts().items():
        path.write_text(content, encoding="utf-8")
        data = json.loads(content)
        n = sum(len(v) for v in data["classes"].values())
        print(f"wrote {path}: {n} controls in {len(data['classes'])} classes "
              f"({data['root']}, TRCC {data['release']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
