#!/usr/bin/env python3
"""Extract embedded button-image PNGs from a Thermalright C# ``.resx``.

The Windows app stores its sidebar button images as WinForms resources in
``TRCC.Properties.Resources.resx`` — each a BinaryFormatter-serialized
``System.Drawing.Bitmap`` (``mimetype=application/x-microsoft.net.object
.binary.base64``).  The raw PNG bytes sit *inside* that blob, so we slice
from the PNG magic to the IEND chunk — no .NET deserializer needed.

Reusable after every Thermalright update: re-decompile in project mode
(``ilspycmd -p -o <dir> TRCC.exe``) to get the ``.resx``, then run this to
pull any NEW model button images into both asset dirs (the colour ``ui/gui``
copy + the ``assets/qtgui`` copy, which the qtgui loader greyscales at runtime).
Model resource names are already English (``A1LC10`` …) so they map straight to
``button_image`` values in ``core/variants.py`` — no rename needed (that's
``rename_assets.py``'s job, for the Chinese-named chrome assets).

Usage::

    python dev/tools/extract_resx_images.py \\
        --resx /tmp/trcc216_proj/TRCC.Properties.Resources.resx \\
        --names A1LC10,A1LC10a,A1LC13,A1LC13a
    # add --dry-run to preview without writing
"""
from __future__ import annotations

import argparse
import base64
import xml.etree.ElementTree as ET
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ASSET_DIRS = (
    _PROJECT_ROOT / "src" / "trcc" / "ui" / "gui" / "assets",
    _PROJECT_ROOT / "src" / "trcc" / "assets" / "qtgui",
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"IEND\xae\x42\x60\x82"
_BINARY_MIME = "application/x-microsoft.net.object.binary.base64"


def _slice_png(blob: bytes) -> bytes | None:
    """Return the PNG embedded in a serialized Bitmap blob, or None."""
    start = blob.find(_PNG_MAGIC)
    end = blob.find(_PNG_END)
    if start == -1 or end == -1:
        return None
    return blob[start:end + len(_PNG_END)]


def extract(resx: Path, names: set[str]) -> dict[str, bytes]:
    """Slice the PNG for each requested resource name in *resx*."""
    root = ET.parse(resx).getroot()
    out: dict[str, bytes] = {}
    for data in root.findall("data"):
        name = data.get("name")
        if name not in names or _BINARY_MIME not in (data.get("mimetype") or ""):
            continue
        value = data.find("value")
        if value is None or not value.text:
            continue
        blob = base64.b64decode("".join(value.text.split()))
        png = _slice_png(blob)
        if png is None:
            print(f"  WARN {name}: no PNG found in blob ({len(blob)} B) — skipped")
            continue
        out[name] = png
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--resx", required=True, type=Path,
                    help="path to TRCC.Properties.Resources.resx")
    ap.add_argument("--names", required=True,
                    help="comma-separated resource names (e.g. A1LC10,A1LC10a)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, touch nothing")
    args = ap.parse_args()

    names = {n.strip() for n in args.names.split(",") if n.strip()}
    images = extract(args.resx, names)

    missing = names - images.keys()
    if missing:
        print(f"NOT FOUND in resx: {sorted(missing)}")

    for name, png in sorted(images.items()):
        for d in _ASSET_DIRS:
            dest = d / f"{name}.png"
            if args.dry_run:
                print(f"  [dry-run] would write {dest} ({len(png)} B)")
            else:
                dest.write_bytes(png)
                print(f"  wrote {dest} ({len(png)} B)")

    print(f"\n{len(images)} image(s) extracted"
          + (" (dry-run)" if args.dry_run else f" → {len(_ASSET_DIRS)} dir(s) each"))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
