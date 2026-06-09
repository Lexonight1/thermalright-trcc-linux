#!/usr/bin/env python3
"""Pack theme data into the per-resolution .7z archives the app serves.

The app fetches THREE archives per resolution from
``…/main/src/trcc/data/`` (see ``services/data_install.py::ensure_all``):

    src/trcc/data/theme{W}{H}.7z      stock themes   (Theme1/, Theme2/, … subdirs)
    src/trcc/data/web/{W}{H}.7z       cloud thumbs   (a001.png, a002.png, … flat)
    src/trcc/data/web/zt{W}{H}.7z     cloud masks    (000a/, 000b/, … subdirs)

This tool packs each from its unpacked source directory:

    src/trcc/data/Theme{W}{H}/        → theme{W}{H}.7z
    src/trcc/data/web/{W}{H}/         → web/{W}{H}.7z
    src/trcc/data/web/zt{W}{H}/       → web/zt{W}{H}.7z

Each archive holds its source dir's contents at the root (arcnames relative to
the source dir), matching the existing published archives.  Populate the source
dirs first — e.g. from a Thermalright install's ``Data/USBLCD/{Theme,Web}`` tree
— then run this; commit only the resulting ``.7z`` files (the unpacked source
dirs are build inputs, not committed).

Usage:
    python dev/tools/pack_theme_archives.py              # every discovered resolution
    python dev/tools/pack_theme_archives.py 320960       # one resolution
    python dev/tools/pack_theme_archives.py --themes 320960   # themes only
    python dev/tools/pack_theme_archives.py --thumbs     # cloud thumbs only
    python dev/tools/pack_theme_archives.py --masks      # masks only

Requires: py7zr (pip install py7zr) or the system ``7z`` command.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# dev/tools/pack_theme_archives.py → repo root is three parents up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# The shipping data path the app fetches from — NOT the pre-cutover ``src/data``.
DATA_DIR = PROJECT_ROOT / "src" / "trcc" / "data"
WEB_DIR = DATA_DIR / "web"

_RES = re.compile(r"^\d+$")  # a resolution dir/suffix is bare digits ("320960")


def _discover_resolutions() -> list[str]:
    """Resolutions with at least one unpacked source dir present."""
    found: set[str] = set()
    if DATA_DIR.is_dir():
        for d in DATA_DIR.iterdir():
            if d.is_dir() and d.name.startswith("Theme") and _RES.match(d.name[5:]):
                found.add(d.name[5:])
    if WEB_DIR.is_dir():
        for d in WEB_DIR.iterdir():
            if not d.is_dir():
                continue
            if d.name.startswith("zt") and _RES.match(d.name[2:]):
                found.add(d.name[2:])
            elif _RES.match(d.name):
                found.add(d.name)
    return sorted(found)


def _pack(source_dir: Path, archive: Path, label: str) -> bool:
    """Pack every file under ``source_dir`` into ``archive`` (arcnames relative
    to ``source_dir``, so the contents sit at the archive root)."""
    if not source_dir.is_dir() or not any(source_dir.iterdir()):
        print(f"SKIP: {label} — no source dir {source_dir}")
        return False
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    print(f"Packing {label}  {source_dir.name}/ -> {archive.name} ...", end=" ")

    try:
        import py7zr
        with py7zr.SevenZipFile(str(archive), "w") as z:
            for root, _, files in os.walk(source_dir):
                for f in files:
                    full = Path(root) / f
                    z.write(full, str(full.relative_to(source_dir)))
    except ImportError:
        # System 7z: add the dir's contents (``.``) from inside it.
        r = subprocess.run(["7z", "a", str(archive.resolve()), "."],
                           cwd=str(source_dir), capture_output=True)
        if r.returncode != 0:
            print("FAILED (need py7zr or 7z)")
            return False

    print(f"OK ({archive.stat().st_size / 1024:.0f} KB)")
    return True


def pack_themes(res: str) -> bool:
    return _pack(DATA_DIR / f"Theme{res}", DATA_DIR / f"theme{res}.7z",
                 f"themes  {res}")


def pack_thumbs(res: str) -> bool:
    return _pack(WEB_DIR / res, WEB_DIR / f"{res}.7z", f"thumbs  {res}")


def pack_masks(res: str) -> bool:
    return _pack(WEB_DIR / f"zt{res}", WEB_DIR / f"zt{res}.7z", f"masks   {res}")


def main() -> None:
    args = sys.argv[1:]
    flags = {a for a in args if a.startswith("--")}
    only = flags & {"--themes", "--thumbs", "--masks"}
    do_themes = not only or "--themes" in flags
    do_thumbs = not only or "--thumbs" in flags
    do_masks = not only or "--masks" in flags

    resolutions = [a for a in args if not a.startswith("--")] or _discover_resolutions()
    if not resolutions:
        print(f"No source dirs (Theme*/, web/*/, web/zt*/) found under {DATA_DIR}")
        sys.exit(1)
    print(f"Resolutions: {', '.join(resolutions)}\n")

    results: list[bool] = []
    for r in resolutions:
        if do_themes:
            results.append(pack_themes(r))
        if do_thumbs:
            results.append(pack_thumbs(r))
        if do_masks:
            results.append(pack_masks(r))

    ok = sum(results)
    print(f"\nDone: {ok}/{len(results)} archives created")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
