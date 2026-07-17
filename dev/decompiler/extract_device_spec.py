"""Extract the device wire-spec from the Thermalright C# decompile — every family.

The C# "oracle" is *source you read*, not a spec that emits bytes — so the moment
nobody transcribes a wire fact (type-2's 512-byte chunked delivery, #150), a green
suite stays silent on it until a reporter sees a boot logo.  This tool mines the
mechanical, unambiguous facts into a *regenerable* ``oracle-spec.json`` and runs
**every device family through one pipeline** so coverage is visible, not assumed.

Shape (this IS the manifold, decompile-derived):
  * ``frame_headers`` — keyed by RESOLUTION (``is320x320`` …). Shared by every LCD
    wire, because the C# builds frames in ImageToJpg / ImageTo565 regardless of how
    the bytes are then delivered.
  * ``families`` — keyed by WIRE (hid2 / hid3 / led / scsi / bulk / ly). Each row
    carries its handshake + delivery framing + encoder, OR an honest status flag.

Why regenerable: Thermalright ships new versions. Re-run against a new decompile →
diff the spec to see what THEY changed; the parity check shows where WE drift.

Honesty rails:
  * STATIC extraction only (headers / chunk-sizes / markers / magic). Runtime golden
    bytes still need the C# executed on hardware.
  * A family whose C# is not in THIS decompile is ``NOT_IN_THIS_DECOMPILE`` — never
    silently skipped. SCSI/Bulk/LY deliver via a shared-memory bridge to a separate
    USBLCD component absent here; they are flagged, not faked.

Usage:
    python dev/decompiler/extract_device_spec.py [DECOMPILE_DIR] [-o oracle-spec.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.csharp import CSharpSource

_DEFAULT_DECOMPILE = Path.home() / "Downloads/TRCCCAPEN/TRCC_decompiled"

_FORM_CZTV = "TRCC.CZTV/FormCZTV.cs"      # LCD frame encoders (all wires)
_UC_DEVICE = "TRCC/UCDevice.cs"           # HID USB handshake + chunk threads
_FORM_LED = "TRCC.LED/FormLED.cs"         # LED colour/segment wire

_COND_RE = re.compile(r"\b(?:else\s+if|if)\s*\((.+)\)\s*$")
_ELSE_RE = re.compile(r"^\s*else\s*$")
_ARR_RE = re.compile(r"new\s+byte\[(\d+)\]")


@dataclass
class ByteArray:
    method: str
    condition: str
    length: int
    values: list[int]


@dataclass
class Family:
    """One device family's C# locations + what we expect to mine."""
    wire: str
    source: str | None          # decompiled file, or None if absent here
    handshake_method: str | None
    delivery_method: str | None
    note: str = ""


# The full device matrix, each routed through the same extractor.  A None source
# = the family's wire code is NOT in this decompile (visible gap, not skipped).
FAMILIES: dict[str, Family] = {
    "hid2": Family("hid", _UC_DEVICE, "DeviceOnConnected2", "ThreadSendDeviceData2",
                   "0416:5302 DA-magic; frames from FormCZTV ImageTo565/Jpg"),
    "hid3": Family("hid", _UC_DEVICE, None, None,
                   "0418:5303/4 ALi F5 — handshake/delivery not cleanly isolated "
                   "in UCDevice; trace before trusting HidType3Protocol"),
    "led":  Family("led", _FORM_LED, None, None,
                   "colour/segment; SendHidVal perceptual *0.4 — mine separately"),
    "scsi": Family("scsi", None, None, None,
                   "share-memory bridge → USBLCD component, absent from this decompile"),
    "bulk": Family("bulk", None, None, None,
                   "share-memory bridge → USBLCD component, absent from this decompile"),
    "ly":   Family("ly", None, None, None,
                   "Trofeo Vision LY wire — not in this decompile"),
}


@dataclass
class OracleSpec:
    decompile: str
    frame_headers: list[dict[str, object]] = field(default_factory=list)
    markers: list[dict[str, object]] = field(default_factory=list)
    families: dict[str, dict[str, object]] = field(default_factory=dict)
    coverage: dict[str, str] = field(default_factory=dict)


# ── byte-array line scanner ─────────────────────────────────────────────────


def _parse_bytes(inner: str) -> list[int]:
    out: list[int] = []
    for tok in inner.replace("\n", " ").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok, 0))
        except ValueError:
            out.append(-1)   # runtime-filled slot (e.g. `bytes[0]`) — keep position
    return out


def _byte_arrays(src: CSharpSource) -> list[ByteArray]:
    lines = src.text.splitlines()
    method, condition = "?", "?"
    out: list[ByteArray] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (found := CSharpSource.definition_at(line)):
            method, condition = found, "?"
        if (c := _COND_RE.search(line.strip())):
            condition = c.group(1).strip()
        elif _ELSE_RE.match(line):
            condition = "else_default"
        if (a := _ARR_RE.search(line)):
            length = int(a.group(1))
            buf, j = line, i
            while "{" not in buf and j < len(lines) - 1:
                j += 1
                buf += "\n" + lines[j]
            while buf.count("{") > buf.count("}") and j < len(lines) - 1:
                j += 1
                buf += "\n" + lines[j]
            if "{" in buf and "}" in buf:
                inner = buf[buf.index("{") + 1 : buf.rindex("}")]
                out.append(ByteArray(method, condition, length, _parse_bytes(inner)))
            i = j
        i += 1
    return out


def _chunk_size(src: CSharpSource, method: str) -> int | None:
    found = src.method(method)
    if found and (m := re.search(r"num2\s*>\s*(\d+)", found.body)):
        return int(m.group(1))
    return None


def _u16(v: list[int], off: int) -> int | None:
    if off + 1 < len(v) and v[off] >= 0 and v[off + 1] >= 0:
        return v[off] | (v[off + 1] << 8)
    return None


def _header_fact(ba: ByteArray) -> dict[str, object]:
    v = ba.values
    if ba.length >= 64:
        w, h, slot = _u16(v, 8), _u16(v, 12), "60:64"
    else:
        w, h, slot = _u16(v, 8), _u16(v, 10), "16:20"
    return {"condition": ba.condition, "header_len": ba.length, "magic": v[:4],
            "declared_w": w, "declared_h": h, "length_slot": slot}


def _is_marker(ba: ByteArray) -> bool:
    return ba.length == 4 and ba.values[-1:] == [30]


# ── extraction ──────────────────────────────────────────────────────────────


def extract(decompile: Path) -> OracleSpec:
    # Record only the decompile's leaf name for provenance — the committed
    # baseline must not carry a machine-specific absolute path.
    spec = OracleSpec(decompile=decompile.name)

    # Shared LCD frame headers + markers (every LCD wire uses these encoders).
    form = CSharpSource.read(decompile / _FORM_CZTV)
    for ba in _byte_arrays(form):
        if ba.method not in ("ImageToJpg", "ImageTo565"):
            continue
        if _is_marker(ba):
            spec.markers.append({"encoder": ba.method, "condition": ba.condition,
                                 "bytes": ba.values})
        elif ba.length in (20, 64) and ba.values[:4] in (
            [18, 52, 86, 120], [218, 219, 220, 221],
        ):
            spec.frame_headers.append({"encoder": ba.method, **_header_fact(ba)})

    # Every family through the same pipe.
    for name, fam in FAMILIES.items():
        row: dict[str, object] = {"wire": fam.wire, "note": fam.note}
        if fam.source is None:
            spec.coverage[name] = "NOT_IN_THIS_DECOMPILE"
            row["status"] = "NOT_IN_THIS_DECOMPILE"
            spec.families[name] = row
            continue
        src = CSharpSource.read(decompile / fam.source)
        located = False
        if fam.handshake_method:
            for ba in _byte_arrays(src):
                if ba.method == fam.handshake_method and ba.values[:4] == [218, 219, 220, 221]:
                    row["handshake_init"] = {"len": ba.length, "magic": ba.values[:4]}
                    located = True
        if fam.delivery_method:
            cs = _chunk_size(src, fam.delivery_method)
            if cs is not None:
                row["delivery_chunk"] = cs
                located = True
        spec.coverage[name] = "MINED" if located else "NEEDS_REVIEW"
        row["status"] = spec.coverage[name]
        spec.families[name] = row

    return spec


# ── parity: extracted spec vs our shipping code ─────────────────────────────


def parity(spec: OracleSpec) -> list[str]:
    gaps: list[str] = []
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from trcc.adapters.device import hid_lcd as H

    cs = spec.families.get("hid2", {}).get("delivery_chunk")
    gaps.append(f"{'ok' if cs == H._USB_BULK_ALIGNMENT else '⚠'} CHUNK hid2: "
                f"C#={cs} ours={H._USB_BULK_ALIGNMENT}")

    magic = spec.families.get("hid2", {}).get("handshake_init", {}).get("magic")
    gaps.append(f"{'ok' if magic == list(H._TYPE2_MAGIC) else '⚠'} MAGIC hid2: "
                f"C#={magic} ours={list(H._TYPE2_MAGIC)}")

    big = sorted({str(h["condition"]) for h in spec.frame_headers
                  if h["header_len"] == 64})
    if big:
        gaps.append(f"⚠ HEADER: C# uses 64-byte 0x12345678 headers for {big} — "
                    f"our _build_frame_type2 only emits 20-byte DA. Confirm which "
                    f"of these are type-2 (HID) devices vs SCSI/other wires.")
    return gaps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("decompile", nargs="?", type=Path, default=_DEFAULT_DECOMPILE)
    ap.add_argument("-o", "--out", type=Path,
                    default=Path(__file__).with_name("oracle-spec.json"))
    args = ap.parse_args()
    if not (args.decompile / _FORM_CZTV).exists():
        print(f"decompile not found under {args.decompile}", file=sys.stderr)
        return 2

    spec = extract(args.decompile)
    args.out.write_text(json.dumps(asdict(spec), indent=2))
    print(f"wrote {args.out}  ({len(spec.frame_headers)} headers, "
          f"{len(spec.markers)} markers)\n")
    print("── coverage (every device through it) ──")
    for name, status in spec.coverage.items():
        print(f"  {status:22} {name:5} — {FAMILIES[name].note}")
    print("\n── parity vs shipping code ──")
    for line in parity(spec):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
