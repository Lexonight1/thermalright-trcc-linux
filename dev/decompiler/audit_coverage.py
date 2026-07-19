#!/usr/bin/env python3
"""Behavioral-audit coverage meter — turns "is the C# audited?" into a number.

Cross-references the structural map (control-flow.json — every method) against
the behavioral docs (AUDIT_*.md subsystem prose + BEHAVIOR_*.md per-method
grind) by line citation. A method counts as "covered" when a doc cites at least
one line inside it. That is a LOWER bound on understanding and an UPPER bound on
completeness (a cited method may still have undocumented branches), so it never
flatters us. The mandate: know how the C# does everything before consolidating
its poorly-written methods — so this meter must reach ~100% on behavior-bearing
files before the consolidation starts.

Run:  python3.12 dev/decompiler/audit_coverage.py            # overall %
      python3.12 dev/decompiler/audit_coverage.py --dark FormCZTV.cs   # worklist
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DEC = Path(__file__).resolve().parent
_HUGE = 10**9

# WinForms designer/boilerplate — not ported to Linux, excluded from the
# behavior-bearing denominator. Marked, never silently skipped.
_BOILERPLATE = frozenset({
    "Resources.cs", "Program.cs", "FormStart.cs", "UCButton.cs", "UCScrollA.cs",
    "UCComboBoxA.cs", "UCComboBoxB.cs", "UCComboBoxC.cs", "UCColorA.cs",
    "UCColorB.cs", "UCColorC.cs", "FormGetColor.cs", "UCAbout.cs",
})


def _cited_lines() -> dict[str, set[int]]:
    """Line citations per .cs file, tolerant of both doc styles:
    explicit ``File.cs:123`` (AUDIT / BEHAVIOR_FORMCZTV), and a section header
    ``## … File.cs`` followed by bare ``(`:123-456`)`` bullets (BEHAVIOR_METRICS
    / LED_SCREEN). Bare refs bind to the nearest preceding .cs mention, so the
    meter measures real coverage instead of one citation format."""
    cited: dict[str, set[int]] = {}
    explicit = re.compile(r"([A-Za-z0-9_]+\.cs)[:\sL]*?(\d{2,5})")
    csfile = re.compile(r"([A-Za-z0-9_]+\.cs)")
    bare = re.compile(r"[`(]:(\d{2,5})")
    for pattern in ("AUDIT_*.md", "BEHAVIOR_*.md"):
        for md in DEC.glob(pattern):
            cur: str | None = None
            for line in md.read_text().splitlines():
                had_explicit = False
                for m in explicit.finditer(line):
                    cited.setdefault(m.group(1), set()).add(int(m.group(2)))
                    had_explicit = True
                files = csfile.findall(line)
                if files:
                    cur = files[-1]
                if cur and not had_explicit:
                    for m in bare.finditer(line):
                        cited.setdefault(cur, set()).add(int(m.group(1)))
    return cited


def _is_covered(method: dict, nxt: int, lines: list[int]) -> bool:
    return any(method["line"] <= c < nxt for c in lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dark", help="list undocumented methods for this .cs basename")
    args = ap.parse_args()
    cf = json.loads((DEC / "control-flow.json").read_text())
    cited = _cited_lines()

    if args.dark:
        for rel, methods in cf.items():
            if rel.split("/")[-1] != args.dark:
                continue
            lines = sorted(cited.get(args.dark, set()))
            starts = [m["line"] for m in methods]
            for i, m in enumerate(methods):
                nxt = starts[i + 1] if i + 1 < len(starts) else _HUGE
                if not _is_covered(m, nxt, lines):
                    print(f"  DARK  L{m['line']:<5} {m['name']}  "
                          f"({len(m['branches'])} branches)")
        return 0

    tot_m = tot_c = bhv_m = bhv_c = 0
    for rel, methods in cf.items():
        base = rel.split("/")[-1]
        lines = sorted(cited.get(base, set()))
        starts = [m["line"] for m in methods]
        cov = sum(
            _is_covered(m, starts[i + 1] if i + 1 < len(starts) else _HUGE, lines)
            for i, m in enumerate(methods)
        )
        tot_m += len(methods)
        tot_c += cov
        if base not in _BOILERPLATE:
            bhv_m += len(methods)
            bhv_c += cov
    print(f"ALL methods:            {tot_c}/{tot_m} cited = {100 * tot_c // tot_m}%")
    print(f"BEHAVIOR-BEARING only:  {bhv_c}/{bhv_m} cited = {100 * bhv_c // bhv_m}%  "
          f"(excludes {tot_m - bhv_m} designer/boilerplate methods)")
    print("Coverage = a method has >=1 line cited in an AUDIT_*.md / BEHAVIOR_*.md doc.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
