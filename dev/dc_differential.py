"""DC differential — current ``_dc`` reader vs the LEGACY parser, on real files.

Legacy matched the Windows app on hardware, so it is the reference.  This
catches "present but WRONG" cutover bugs that a presence-inventory can't:
mistranscribed labels, discarded date/time formats, dropped/moved elements,
colour drift — across EVERY on-disk theme, for every device resolution.

    PYTHONPATH=src python3.12 dev/dc_differential.py [dc_root] [legacy_src]

Defaults: dc_root=~/.trcc, legacy_src=/tmp/trcc-legacy/src (a worktree of the
``legacy`` branch:  git worktree add /tmp/trcc-legacy legacy).
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from trcc.services import _dc as Dc

_CANON = {
    "cpu_temp": "cpu_temp", "cpu_percent": "cpu_usage", "cpu_usage": "cpu_usage",
    "cpu_freq": "cpu_freq", "cpu_power": "cpu_power",
    "gpu_temp": "gpu_temp", "gpu_usage": "gpu_usage", "gpu_clock": "gpu_clock",
    "gpu_power": "gpu_power",
}


def _canon_metric(m: str) -> str:
    return _CANON.get(m, m.replace(":primary", "").replace(":", "_"))


def _canonical(el: dict) -> dict:
    x, y = el.get("x", 0), el.get("y", 0)
    color = (el.get("color") or "").lower()
    t = el.get("type")
    if t == "text":
        return {"kind": "text", "id": el.get("text", ""), "fmt": None,
                "x": x, "y": y, "color": color}
    if t == "clock":
        # Current discards the DC's format int — fmt is always None here.
        return {"kind": "clock", "id": el.get("source", ""),
                "fmt": el.get("format"), "x": x, "y": y, "color": color}
    if t == "metric":
        return {"kind": "metric", "id": _canon_metric(el.get("metric", "")),
                "fmt": None, "x": x, "y": y, "color": color}
    return {"kind": "?", "id": repr(el)[:40], "fmt": None,
            "x": x, "y": y, "color": color}


def _key(e: dict) -> tuple:
    return (e["kind"], str(e["id"]), e["x"], e["y"])


def dump_current(root: Path) -> dict:
    out: dict = {}
    for dc in sorted(root.rglob("config1.dc")):
        try:
            cfg = Dc.File(dc).read()
        except Exception as e:
            out[str(dc)] = {"__error__": f"{type(e).__name__}: {e}"}
            continue
        out[str(dc)] = [_canonical(el) for el in cfg.get("elements", [])]
    return out


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".trcc"
    legacy_src = sys.argv[2] if len(sys.argv) > 2 else "/tmp/trcc-legacy/src"

    here = Path(__file__).parent
    proc = subprocess.run(
        [sys.executable, str(here / "_dc_legacy_dump.py"), str(root)],
        env={"PYTHONPATH": legacy_src, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        print("legacy dump failed:\n", proc.stderr[-2000:], file=sys.stderr)
        return 2
    legacy = json.loads(proc.stdout)
    current = dump_current(root)

    divergent = 0
    clean = 0
    cat: Counter = Counter()
    samples: dict[str, list[str]] = {}

    for path in sorted(set(legacy) | set(current)):
        L = legacy.get(path, [])
        C = current.get(path, [])
        if isinstance(L, dict) or isinstance(C, dict):  # parse error one side
            cat["parse_error_one_side"] += 1
            samples.setdefault("parse_error_one_side", []).append(path)
            divergent += 1
            continue
        lk = Counter(_key(e) for e in L)
        ck = Counter(_key(e) for e in C)
        only_legacy = list((lk - ck).elements())
        only_current = list((ck - lk).elements())
        # Matched-by-key elements whose clock format differs (the discard bug).
        lfmt = {_key(e): e["fmt"] for e in L if e["kind"] == "clock"}
        cfmt = {_key(e): e["fmt"] for e in C if e["kind"] == "clock"}
        fmt_drift = [k for k in lfmt.keys() & cfmt.keys() if lfmt[k] != cfmt[k]]

        if not only_legacy and not only_current and not fmt_drift:
            clean += 1
            continue
        divergent += 1
        # Categorise the dominant divergence kind for this file.
        if fmt_drift:
            cat["clock_format_discarded"] += 1
            samples.setdefault("clock_format_discarded", []).append(
                f"{Path(path).parent.name}: legacy={[lfmt[k] for k in fmt_drift]} "
                f"current={[cfmt[k] for k in fmt_drift]}")
        if only_legacy or only_current:
            kinds = {e[0] for e in only_legacy} | {e[0] for e in only_current}
            tag = "element_" + "_".join(sorted(kinds)) + "_mismatch"
            cat[tag] += 1
            if len(samples.setdefault(tag, [])) < 6:
                samples[tag].append(
                    f"{Path(path).parent.name}: legacy_only={only_legacy[:3]} "
                    f"current_only={only_current[:3]}")

    print(f"\n=== DC differential: {clean} clean, {divergent} divergent "
          f"of {len(set(legacy) | set(current))} themes ===\n")
    for tag, n in cat.most_common():
        print(f"  [{n:4}]  {tag}")
        for s in samples.get(tag, [])[:6]:
            print(f"          - {s}")
    return 1 if divergent else 0


if __name__ == "__main__":
    raise SystemExit(main())
