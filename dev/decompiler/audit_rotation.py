"""audit_rotation — run a device fingerprint through the C# rotation oracle
and diff it against what OUR pipeline actually sends to the wire.

The first extractor of the decompile-miner: instead of guessing on-glass and
waiting for a reporter, this bench-checks — for every orientation — whether our
``wire_angle`` matches the C# ``ImageToJpg`` / ``ImageTo565`` rotation, per
device family, with zero hardware.

    # one device by its handshake fingerprint
    PYTHONPATH=src python3 dev/decompiler/audit_rotation.py --pm 5 --sub 1

    # the whole known family (every rotation-relevant resolution/encoder)
    PYTHONPATH=src python3 dev/decompiler/audit_rotation.py --all

Exit code is non-zero if any DIFF is found, so it doubles as a CI/regression
guard on the rotation port.  "ours" is the FULL wire angle the render applies —
``trcc.core.protocol.wire_angle`` composed with the device-mount
``encode_baseline`` (the two rotations ``DisplayService`` applies at
display.py:384 + :1321), no reimplementation; "theirs" from ``encode_reference``
(the cited C# transcription, whose base already folds the pm=6 mount offset).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from encode_reference import csharp_encode_base, csharp_wire_rotation

from trcc.adapters.device.bulk_lcd import bulk_profile
from trcc.core.protocol import wire_angle

_ORIENTS = (0, 90, 180, 270)

# Handshake fingerprints to sweep, as the PM/SUB bytes a bulk panel actually
# reports.  Labels are descriptive only — every resolution/encoder/baseline is
# resolved by the shipping ``bulk_profile`` and printed per row, so a stale
# label can never make the audit assert a device that doesn't exist.  (An
# earlier revision hand-rolled this resolution, invented a phantom "square pm6"
# whose FBL 6 is not in the registry, and reported two false 180°/90° bugs
# against reporter-confirmed code.)
_FAMILY: tuple[tuple[str, int, int], ...] = (
    ("Mjolnir",              5,  1),   # → fbl 50, 320x240 JPEG
    ("FW360 Ultra",          6,  0),   # → fbl 72, 480x480, PM-keyed 180° baseline (#137)
    ("GrandVision 360",     50,  0),   # → fbl 72 (unknown PM → 480x480 base, #176)
    ("widescreen 854x480",  11,  5),   # → fbl 224
    ("widescreen 960x540",  10,  0),   # → fbl 224 (PM disambiguates, not FBL)
    ("bulk pm1 sub48",       1, 48),   # → fbl 114, 1600x720
)


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    fbl: int
    resolution: tuple[int, int]
    jpeg: bool
    pm: int
    ours: dict[int, int]
    theirs: dict[int, int]

    @property
    def diffs(self) -> list[int]:
        return [o for o in _ORIENTS if self.ours[o] != self.theirs[o]]


def audit(pm: int, sub: int, label: str = "") -> Row:
    # The SHIPPING handshake resolution — never a copy of it.  Reimplementing
    # these rules here is what made this tool cry wolf: it echoed the PM into
    # get_profile as an FBL (the exact bogus-FBL path bulk_profile guards for
    # #169), so pm=6 missed FBL 72 and lost the FW360 Ultra's 180° baseline.
    fbl, p = bulk_profile(pm, sub)
    # The FULL wire angle the render applies, mirroring DisplayService exactly:
    # wire_angle (build_frame, display.py:384) THEN the device-mount encode
    # baseline (_encode_for_wire, display.py:1321) — two same-origin clockwise
    # rotations compose additively.  The C# folds the pm6 mount offset into its
    # single base; we split it across wire_angle + encode_baseline, so only the
    # SUM is comparable — auditing either half alone reports a phantom 180°.
    ours = {o: (wire_angle(p, o, portrait_content=False) + p.encode_baseline) % 360
            for o in _ORIENTS}
    theirs = {o: csharp_wire_rotation(p.resolution, jpeg=p.jpeg, pm=pm,
                                      orientation=o) for o in _ORIENTS}
    return Row(label or f"pm={pm} sub={sub}", fbl, p.resolution, p.jpeg, pm,
               ours, theirs)


def _print(row: Row) -> None:
    enc = "JPEG" if row.jpeg else "RGB565"
    base = csharp_encode_base(row.resolution, jpeg=row.jpeg, pm=row.pm)
    print(f"\n{row.label}  (fbl={row.fbl}, {row.resolution[0]}x{row.resolution[1]} "
          f"{enc}, pm={row.pm}, C# base={base}°)")
    print(f"  {'orient':>7} {'ours':>6} {'C#':>6}   verdict")
    for o in _ORIENTS:
        ok = row.ours[o] == row.theirs[o]
        mark = "match" if ok else "**DIFF**"
        print(f"  {o:>7} {row.ours[o]:>5}° {row.theirs[o]:>5}°   {mark}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pm", type=int, help="handshake PM byte")
    ap.add_argument("--sub", type=int, default=0, help="handshake SUB byte")
    ap.add_argument("--all", action="store_true",
                    help="sweep the whole known device family")
    args = ap.parse_args()

    if args.all:
        rows = [audit(pm, sub, label) for label, pm, sub in _FAMILY]
    elif args.pm is not None:
        rows = [audit(args.pm, args.sub)]
    else:
        ap.error("pass --pm N [--sub N] for one device, or --all")

    for row in rows:
        _print(row)

    diffs = [r for r in rows if r.diffs]
    print(f"\n{'=' * 52}")
    if diffs:
        print(f"MISMATCH: {len(diffs)}/{len(rows)} device(s) diverge from the C#:")
        for r in diffs:
            print(f"  - {r.label}: orientations {r.diffs}")
        return 1
    print(f"OK: all {len(rows)} device(s) match the C# at every orientation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
