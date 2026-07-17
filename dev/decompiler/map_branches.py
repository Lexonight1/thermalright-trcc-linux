"""Map every decision branch in the wire-relevant C# methods.

Literals tell us WHAT bytes the C# emits; branches tell us WHEN — the if/switch
decisions that route a device to a header, a mode, a delivery, a rotation.  Each
branch is a "part" our architecture must replicate (a manifold row, a protocol
selection, an encoder choice).  Knowing every branch up front turns the refactor
from "discover parts as bugs" into "implement a known checklist".

Scope: enumerates SYNTACTIC branches (if / else-if / switch / case / switch-
expression arms / guarding ternaries) inside a configured set of wire-relevant
methods across the clean 2.1.6 C#.  It does NOT cover the USBLCD.exe.c Ghidra
dump (scsi/bulk/ly) — that's noisy decompiled C, flagged not mined — and it maps
source branches, not runtime-only behaviour.

Usage:
    python dev/decompiler/map_branches.py [DECOMPILE_DIR] [-o branch-map.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.csharp import CSharpSource, Method

_DEFAULT = Path.home() / "Downloads/TRCCCAPEN"

# (relative-path, [wire-relevant methods]) — the decision points that shape the
# wire.  Everything else in these 7000-line forms is UI noise we skip.
TARGETS: list[tuple[str, list[str]]] = [
    ("TRCC_decompiled/TRCC.CZTV/FormCZTV.cs",
     ["FormCZTVInit", "ImageToJpg", "ImageTo565"]),
    ("TRCC_decompiled/TRCC/UCDevice.cs",
     ["DeviceOnConnected2", "DeviceDataReceived2",
      "ThreadSendDeviceData1", "ThreadSendDeviceData2"]),
    ("USBLCDNEW.decompiled.cs/USBLCDNEW.decompiled.cs", ["*"]),  # F5 type-3 wire
    ("TRCC_decompiled/TRCC.LED/FormLED.cs", ["SendHidVal"]),
]

# What a branch guards — the first meaningful statement inside it.
_ACTION_RE = re.compile(
    r"(new byte\[\d+\]|delegateForm\?\.Invoke|SendMessage|RotateImg\w*"
    r"|myDeviceMode\s*=|myDevicePingMu\s*=|fbl\s*=|return\b|Concat)",
)
_IF_RE = re.compile(r"\b(else\s+if|if)\s*\((.+)\)\s*$")
_SWITCH_RE = re.compile(r"\bswitch\s*\((.+?)\)")
_CASE_RE = re.compile(r"\bcase\s+(.+?):")
_ARM_RE = re.compile(r"^\s*(\d+|_)\s*=>")     # switch-expression arm


@dataclass
class Branch:
    method: str
    line: int
    kind: str          # if | else-if | switch | case | arm
    on: str            # condition / switch-expr / case value / arm key
    guards: str        # nearest guarded action, or ""


@dataclass
class BranchMap:
    decompile: str
    branches: list[dict[str, object]] = field(default_factory=list)
    per_method: dict[str, int] = field(default_factory=dict)
    not_mined: list[str] = field(default_factory=list)


def _guarded_action(lines: list[str], idx: int) -> str:
    """First meaningful statement in the next few lines after a branch."""
    for k in range(idx, min(idx + 4, len(lines))):
        if (a := _ACTION_RE.search(lines[k])):
            return a.group(1)
    return ""


def _branches_in(found: Method) -> list[Branch]:
    out: list[Branch] = []
    lines = found.body.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        ln = found.body_line + i
        if (m := _IF_RE.search(line)):
            kind = "else-if" if m.group(1).startswith("else") else "if"
            out.append(Branch(found.name, ln, kind, m.group(2).strip(),
                              _guarded_action(lines, i)))
        elif (m := _SWITCH_RE.search(line)):
            out.append(Branch(found.name, ln, "switch", m.group(1).strip(), ""))
        elif (m := _CASE_RE.search(line)):
            out.append(Branch(found.name, ln, "case", m.group(1).strip(),
                              _guarded_action(lines, i)))
        elif (m := _ARM_RE.match(raw)):
            out.append(Branch(found.name, ln, "arm", m.group(1),
                              _guarded_action(lines, i)))
    return out


def build(decompile: Path) -> BranchMap:
    bmap = BranchMap(decompile=decompile.name)
    for rel, methods in TARGETS:
        path = decompile / rel
        if not path.exists():
            bmap.not_mined.append(f"{rel} — file not found")
            continue
        src = CSharpSource.read(path)
        named = methods != ["*"]
        wanted = methods if named else src.method_names()
        for method in wanted:
            found = src.method(method)
            if not found:
                if named:
                    bmap.not_mined.append(f"{rel}::{method} — no definition found")
                continue
            brs = _branches_in(found)
            if brs:
                bmap.per_method[f"{path.name}::{method}"] = len(brs)
                bmap.branches.extend(asdict(b) for b in brs)
            elif named:
                # A named wire method with zero branches is either genuinely
                # straight-line or a mining failure — the two are indistinguishable
                # from the outside, so say so instead of dropping it silently.
                bmap.not_mined.append(
                    f"{rel}::{method} — 0 branches (body at L{found.body_line}); "
                    f"confirm by hand",
                )
    bmap.not_mined.append(
        "USBLCD.exe.c (scsi/bulk/ly) — Ghidra C dump, not branch-mined here",
    )
    return bmap


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("decompile", nargs="?", type=Path, default=_DEFAULT)
    ap.add_argument("-o", "--out", type=Path,
                    default=Path(__file__).with_name("branch-map.json"))
    args = ap.parse_args()
    if not args.decompile.exists():
        print(f"decompile not found: {args.decompile}", file=sys.stderr)
        return 2

    bmap = build(args.decompile)
    args.out.write_text(json.dumps(asdict(bmap), indent=2))
    total = len(bmap.branches)
    print(f"wrote {args.out}  ({total} branches across "
          f"{len(bmap.per_method)} wire methods)\n")
    print("── decision branches per wire method ──")
    for name, n in sorted(bmap.per_method.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3}  {name}")
    print("\n── not mined ──")
    for r in bmap.not_mined:
        print(f"  • {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
