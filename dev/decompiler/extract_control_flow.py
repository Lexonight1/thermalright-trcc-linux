#!/usr/bin/env python3
"""Complete method + branch extractor for the TRCC C# decompile.

An *exhaustive* control-flow map: for every ``.cs`` file, every method, and
under each method every branch (``if`` / ``else if`` / ``switch`` / ``case`` /
switch-expression arm) with the CONDITION it tests — the variable(s) that drive
it.  A tool walks every line, so unlike a hand/agent audit it cannot sample or
skip a method: a method with zero branches is a real leaf, not an unread gap.

This is the completeness backstop the behavioural audits (AUDIT_*.md) sit on top
of — annotate meaning onto a skeleton the tool guarantees is whole.

Output:
  dev/decompiler/control-flow.json  — {file: {method: {line, branches:[...]}}}
  dev/decompiler/CONTROL_FLOW.md    — human-readable, per file → method → branch

Heuristic line scanner (decompiled ILSpy C# is tab-indented and regular): class
members start at one tab; branch keywords carry their condition on the line.
Multi-line conditions capture their first line (enough to see the driving var).

Run:  python3.12 dev/decompiler/extract_control_flow.py
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.csharp import DECOMPILE_ROOT as CS_ROOT
OUT_DIR = Path(__file__).resolve().parent
OUT_JSON = OUT_DIR / "control-flow.json"
OUT_MD = OUT_DIR / "CONTROL_FLOW.md"

_MOD = (r"public|private|protected|internal|static|virtual|override|sealed|"
        r"abstract|async|unsafe|extern|new|partial")
# A class-member method/ctor: one tab, ≥1 modifier, optional return type, name(
_METHOD_RE = re.compile(
    rf"^\t(?:(?:{_MOD})\s+)+(?:[\w<>\[\].,?]+\s+)?(?P<name>\w+)\s*\("
)
# State-variable identifiers worth surfacing as "drivers" of a branch.
_DRIVER_RE = re.compile(
    r"\b(is[A-Z]\w*|my[A-Z]\w*|directionB|pm|pmSub|sub|fbl|mode|angle|"
    r"nowLedStyle|myLddVal|NO|count|ysl|SPIMode|nowThemeLocal)\b"
)


@dataclass(slots=True)
class Branch:
    kind: str          # if | elif | switch | case | switch-expr
    line: int
    condition: str     # the tested expression (first line if multi-line)
    drivers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Method:
    name: str
    line: int
    branches: list[Branch] = field(default_factory=list)


def _condition_after(keyword_paren: str, text: str) -> str:
    """Balanced-paren capture of the first (...) after a keyword; fallback: rest."""
    i = text.find(keyword_paren)
    if i < 0:
        return text.strip()
    j = i + len(keyword_paren) - 1  # at the '('
    depth, out = 0, []
    for ch in text[j:]:
        if ch == "(":
            depth += 1
            if depth == 1:
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return "".join(out).strip() or text.strip()


# A switch-expression ARM key: number / hex / string / _ / tuple / identifier.
_ARM_RE = re.compile(r'^(?P<key>_|-?\d+|0x[0-9A-Fa-f]+|"[^"]*"|\(.+?\)|[A-Za-z_]\w*)\s*=>')
# A switch-EXPRESSION header: "<expr> switch" at end of line (optional brace).
_SWEXPR_RE = re.compile(r"([A-Za-z_]\w*(?:\.\w+)*)\s+switch\s*\{?\s*$")


def _drivers(text: str) -> list[str]:
    return list(dict.fromkeys(_DRIVER_RE.findall(text)))


def _scan_branches(body: str, base_line: int) -> list[Branch]:
    """Every decision point: if / else-if / switch / case / switch-expr header,
    each switch-expression ARM (``N =>``), and each ternary (` ? `). The arms and
    ternaries are where the decompiled rotation/encoder tables actually live —
    omitting them (v1 did) undercounts ImageToJpg 22 vs its real ~54."""
    out: list[Branch] = []
    for k, raw in enumerate(body.splitlines()):
        ln = base_line + k
        s = raw.strip()
        if re.search(r"\belse\s+if\s*\(", raw):
            c = _condition_after("if (", raw.replace("else if(", "else if ("))
            out.append(Branch("elif", ln, c, _drivers(c)))
        elif re.match(r"if\s*\(", s):
            c = _condition_after("if (", s.replace("if(", "if ("))
            out.append(Branch("if", ln, c, _drivers(c)))
        elif re.match(r"switch\s*\(", s):
            c = _condition_after("switch (", s.replace("switch(", "switch ("))
            out.append(Branch("switch", ln, c, _drivers(c)))
        elif re.match(r"case\s+", s) or s == "default:":
            c = "default" if s == "default:" else s[4:].strip().rstrip(":").strip()
            out.append(Branch("case", ln, c, _drivers(c)))
        # switch-EXPRESSION header (the switched variable is BEFORE 'switch')
        msw = _SWEXPR_RE.search(s)
        if msw:
            out.append(Branch("switch-expr", ln, msw.group(1), _drivers(msw.group(1))))
        # switch-expression ARM ("N => ...") — the table rows
        marm = _ARM_RE.match(s)
        if marm and "switch" not in s:
            out.append(Branch("arm", ln, marm.group("key"), []))
        # ternary decision points (spaced ' ? ' — nullable '?.'/'int?' have no space)
        for m in re.finditer(r"\S \? ", raw):
            out.append(Branch("ternary", ln, _test_before(s, m.start() + 1),
                              _drivers(s)))
    return out


def _test_before(stmt: str, q: int) -> str:
    """The condition a ternary tests, without the statement wrapped around it.

    This map documents "the condition a branch tests". Every other branch kind
    already emits just that; the ternary emitted the whole line, so entries read
    `delegateForm?.Invoke(0, (is320x320 ? (2 * j) : j) + 1, array3, num);` where
    the decision is `is320x320`. That over-capture reproduces the vendor's source
    rather than describing its behaviour — see AUDIT_INDEX.md "Source policy" —
    and it buries the fact under the plumbing.

    Walks left from the `?` to the start of the tested expression, balancing
    parentheses so a condition inside a call argument comes out whole.
    """
    depth = 0
    start = 0
    for i in range(q - 1, -1, -1):
        ch = stmt[i]
        if ch in ")]":
            depth += 1
        elif ch in "([":
            if depth == 0:
                start = i + 1
                break
            depth -= 1
        elif depth == 0 and ch in "=;,":
            start = i + 1
            break
    return stmt[start:q].strip().lstrip("> ").rstrip("?").strip()


def scan_file(path: Path) -> list[Method]:
    lines = path.read_text(errors="replace").splitlines(keepends=True)
    # 1) method signature lines (interval boundaries)
    sigs: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _METHOD_RE.match(line)
        if m and not line.rstrip().endswith(";"):
            sigs.append((i, m.group("name")))
    methods: list[Method] = []
    for idx, (i, name) in enumerate(sigs):
        end = sigs[idx + 1][0] if idx + 1 < len(sigs) else len(lines)
        body = "".join(lines[i + 1:end])
        methods.append(Method(name, i + 1, _scan_branches(body, i + 2)))
    return methods


def main() -> int:
    data: dict[str, list[dict]] = {}
    tot_m = tot_b = 0
    for cs in sorted(CS_ROOT.rglob("*.cs")):
        rel = str(cs.relative_to(CS_ROOT))
        methods = scan_file(cs)
        data[rel] = [asdict(m) for m in methods]
        tot_m += len(methods)
        tot_b += sum(len(m.branches) for m in methods)

    OUT_JSON.write_text(json.dumps(data, indent=1))

    md = ["# C# control-flow map — every method, every branch\n",
          "Machine-generated by `extract_control_flow.py` — exhaustive (the tool "
          "walks every line; nothing sampled). Each method lists every branch with "
          "the condition (variable) it tests. A method with no branches is a real "
          "leaf, not an unread gap.\n",
          f"**Totals: {len(data)} files · {tot_m} methods · {tot_b} branches.**\n",
          "## Per-file summary\n",
          "| File | methods | branches |", "|---|---|---|"]
    for rel, ms in sorted(data.items(), key=lambda kv: -sum(len(m["branches"]) for m in kv[1])):
        b = sum(len(m["branches"]) for m in ms)
        md.append(f"| {rel} | {len(ms)} | {b} |")
    md.append("\n## Detail\n")
    for rel, ms in sorted(data.items()):
        md.append(f"\n### {rel}\n")
        for m in ms:
            if not m["branches"]:
                md.append(f"- `{m['name']}` (L{m['line']}) — no branches")
                continue
            md.append(f"- `{m['name']}` (L{m['line']}) — {len(m['branches'])} branches:")
            for br in m["branches"]:
                drv = f"  ·drivers: {', '.join(dict.fromkeys(br['drivers']))}" if br["drivers"] else ""
                cond = br["condition"][:140]
                md.append(f"    - L{br['line']} `{br['kind']}` `{cond}`{drv}")
    OUT_MD.write_text("\n".join(md) + "\n")

    print(f"files={len(data)}  methods={tot_m}  branches={tot_b}")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
