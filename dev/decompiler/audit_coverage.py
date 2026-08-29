#!/usr/bin/env python3
"""Behavioral-audit coverage meter — turns "is the C# audited?" into a number.

Cross-references the structural map (control-flow.json — every method) against
the behavioral docs (AUDIT_*.md subsystem prose + BEHAVIOR_*.md per-method
grind) by line citation, parsed by ``core.citations`` — which knows all four of
our citation styles.  It used to know two, so `AUDIT_LED_CORE.md` (line-prefixed
code fences) and `AUDIT_VIDEO.md` (prose ``(line N)``) contributed nothing and
their subjects read as DARK: a formatting accident reported as an audit gap.

A method counts as "covered" when a doc cites at least one line inside it. That
is a LOWER bound on understanding and an UPPER bound on completeness (a cited
method may still have undocumented branches), so it never flatters us. The
mandate: know how the C# does everything before consolidating its poorly-written
methods — so this meter must reach ~100% on behavior-bearing files before the
consolidation starts.

The number is only meaningful against the release the docs describe.  These
docs were written against TRCC 2.0.3 (`audit_provenance.py` measures that) while
the miners now read 2.1.6, so the same docs score 100% of the older map and 54%
of the current one.  The docs did not get worse; the denominator became the
right one.

Run:  python3.12 dev/decompiler/audit_coverage.py            # overall %
      python3.12 dev/decompiler/audit_coverage.py --dark FormCZTV.cs   # worklist
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.citations import by_file, parse_all
from core.csharp import ORACLE_PRODUCT
from core.releases import BOILERPLATE as _BOILERPLATE

DEC = Path(__file__).resolve().parent
_HUGE = 10**9

def _is_covered(method: dict, nxt: int, lines: list[int]) -> bool:
    return any(method["line"] <= c < nxt for c in lines)


def _report(product: str, cf: dict, cited: dict) -> int:
    """Print one binary's coverage; return its behaviour-bearing percentage."""
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
    print(f"{product} — ALL methods:  {tot_c}/{tot_m} cited = "
          f"{100 * tot_c // tot_m if tot_m else 0}%")
    print(f"{product} — BEHAVIOUR:    {bhv_c}/{bhv_m} cited = "
          f"{100 * bhv_c // bhv_m if bhv_m else 0}%  "
          f"(excludes {tot_m - bhv_m} designer/boilerplate methods)")
    return 100 * bhv_c // bhv_m if bhv_m else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dark", help="list undocumented methods for this .cs basename")
    ap.add_argument("--fail-under", type=int, default=None, metavar="PCT",
                    help="exit non-zero if behaviour-bearing coverage drops "
                         "below PCT.  This is what makes the meter a RATCHET "
                         "instead of a number nobody reads: audited C# that "
                         "stops being audited fails the build.")
    args = ap.parse_args()
    cf = json.loads((DEC / "control-flow.json").read_text())
    cited = by_file(parse_all(
        list(DEC.glob("AUDIT_*.md")) + list(DEC.glob("BEHAVIOR_*.md"))))

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

    # Each binary is scored against ITS OWN methods and never pooled: the
    # application is several binaries, and one number over the union would move
    # the ported program's figure the moment a component is extracted, making it
    # incomparable with every measurement recorded before.
    scores = {ORACLE_PRODUCT: _report(ORACLE_PRODUCT, cf, cited)}
    for extra in sorted(DEC.glob("control-flow-*.json")):
        product = extra.name[len("control-flow-"):-len(".json")]
        print()
        scores[product] = _report(product, json.loads(extra.read_text()), cited)
    print("\nCoverage = a method has >=1 line cited in an AUDIT_*.md / BEHAVIOR_*.md doc.")

    # The floor applies to EVERY binary, not just the ported program.  It used to
    # gate only the latter, with the component excused "until its real number is
    # known" — but an ungated number is one nothing defends: the wire audit could
    # lose every citation it has and this would still exit 0.  Both binaries are
    # above 95 today (96 and 100), so one floor covers both and CI is unchanged.
    if args.fail_under is None:
        return 0
    if below := {k: v for k, v in scores.items() if v < args.fail_under}:
        for product, pct in sorted(below.items()):
            print(f"\nFAIL — {product} behaviour-bearing coverage {pct}% is below "
                  f"the --fail-under floor of {args.fail_under}%.  Either the audit "
                  f"lost citations, or new C# arrived unaudited.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
