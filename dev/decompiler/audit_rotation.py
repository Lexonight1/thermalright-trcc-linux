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
guard on the rotation port.  "ours" comes from the real shipping function
``trcc.core.protocol.wire_angle`` (no reimplementation); "theirs" from
``encode_reference`` (the cited C# transcription).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from encode_reference import csharp_encode_base, csharp_wire_rotation

from trcc.adapters.device.bulk_lcd import _RGB565_PMS
from trcc.core.protocol import (
    DeviceProfile,
    get_profile,
    pm_to_fbl,
    resolve_encode_base,
    resolve_encode_sub,
    wire_angle,
)

_ORIENTS = (0, 90, 180, 270)

# Handshake fingerprints to sweep — labelled by fingerprint only; the true
# resolution/encoder/rotate is derived from the profile and printed per row
# (a label must never assert a resolution the profile might contradict).
_FAMILY: tuple[tuple[str, int, int], ...] = (
    ("Mjolnir (JPEG small)",   5,  1),
    ("RGB565 small",          50,  0),
    ("square pm6",             6,  0),
    ("widescreen A",          16,  0),
    ("widescreen 854x480",    11,  5),
    ("bulk pm1 sub48",         1, 48),
)


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    resolution: tuple[int, int]
    jpeg: bool
    pm: int
    ours: dict[int, int]
    theirs: dict[int, int]

    @property
    def diffs(self) -> list[int]:
        return [o for o in _ORIENTS if self.ours[o] != self.theirs[o]]


def _effective_profile(pm: int, sub: int) -> DeviceProfile:
    """Rebuild the profile the bulk handshake would produce (jpeg override +
    folded sub/pm encode bases) so the audit sees exactly what ships."""
    fbl = pm_to_fbl(pm, sub)
    base = get_profile(fbl, pm)
    return DeviceProfile(
        width=base.width, height=base.height,
        jpeg=(pm not in _RGB565_PMS),
        big_endian=base.big_endian, rotate=base.rotate,
        widescreen=base.widescreen,
        encode_baseline=resolve_encode_base(base, pm),
        encode_base=resolve_encode_sub(base, sub),
        encode_sub_bases=(), encode_pm_bases=base.encode_pm_bases,
        encode_invert=base.encode_invert,
    )


def audit(pm: int, sub: int, label: str = "") -> Row:
    p = _effective_profile(pm, sub)
    ours = {o: wire_angle(p, o, portrait_content=False) for o in _ORIENTS}
    theirs = {o: csharp_wire_rotation(p.resolution, jpeg=p.jpeg, pm=pm,
                                      orientation=o) for o in _ORIENTS}
    return Row(label or f"pm={pm} sub={sub}", p.resolution, p.jpeg, pm,
               ours, theirs)


def _print(row: Row) -> None:
    enc = "JPEG" if row.jpeg else "RGB565"
    base = csharp_encode_base(row.resolution, jpeg=row.jpeg, pm=row.pm)
    print(f"\n{row.label}  ({row.resolution[0]}x{row.resolution[1]} {enc}, "
          f"pm={row.pm}, C# base={base}°)")
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
