#!/usr/bin/env python3
"""Pack a module folder into its release bundle -- the same bytes on every machine.

Usage::

    PYTHONPATH=src python3.12 dev/tools/module_bundle.py <module folder> -o weather-3.zip

An author packs their release with this, and the review
(``review_module.py``) packs the reviewed commit again and compares: the
bundle on the author's release is listed only when it is byte for byte what
the reviewed source makes.  So the bytes must not depend on the machine:

* entries sorted, each under ``<key>/``, dated 1980-01-01, mode 0644;
* STORED, not deflated.  Two zlibs need not compress alike -- Fedora's
  zlib-ng does not match stock zlib -- and a byte that differs is a bundle
  that "was not built from the commit".

Only a module's fixed names go in (``BUNDLE_FILES``, ``BUNDLE_DIRS``):
the files the app reads, never the repo around them -- its tests, README, CI.
A symbolic link is refused, never followed: it could point anywhere on the
packer's disk.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

#: What a module is, at the top of its folder (``trcc.modules``).
BUNDLE_FILES = frozenset({"__init__.py", "module.json", "i18n.json", "icon.svg",
                          "icon.png", "app.py"})
BUNDLE_DIRS = frozenset({"core", "services", "adapters", "ui", "assets"})
#: 1980-01-01, the earliest time a zip can hold.
_EPOCH = (1980, 1, 1, 0, 0, 0)


class BundleError(Exception):
    """A folder that cannot be packed, and why."""


def module_key(folder: Path) -> str:
    """The key *folder*'s ``module.json`` names."""
    try:
        return str(json.loads((folder / "module.json").read_text(encoding="utf-8"))["key"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise BundleError(f"{folder}: no readable module.json key ({e})") from e


def members(folder: Path) -> list[Path]:
    """Every file of *folder* that goes in its bundle, relative, sorted --
    refusing a symbolic link anywhere in it."""
    found: list[Path] = []
    for top in sorted(folder.iterdir()):
        if top.is_symlink():
            raise BundleError(f"{top.name}: a symbolic link")
        if top.is_file() and top.name in BUNDLE_FILES:
            found.append(Path(top.name))
        elif top.is_dir() and top.name in BUNDLE_DIRS:
            for path in sorted(top.rglob("*")):
                if path.is_symlink():
                    raise BundleError(f"{path.relative_to(folder)}: a symbolic link")
                if path.is_file() and "__pycache__" not in path.parts:
                    found.append(path.relative_to(folder))
    return found


def left_out(folder: Path) -> list[str]:
    """What sits at the top of *folder* but stays out of its bundle."""
    return sorted(p.name for p in folder.iterdir()
                  if not (p.is_file() and p.name in BUNDLE_FILES)
                  and not (p.is_dir() and p.name in BUNDLE_DIRS)
                  and p.name != "__pycache__")


def pack(folder: Path) -> bytes:
    """*folder*'s bundle: its fixed names under ``<key>/``, deterministic."""
    key = module_key(folder)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as bundle:
        for rel in members(folder):
            info = zipfile.ZipInfo(f"{key}/{rel.as_posix()}", date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o644 << 16
            info.create_system = 3          # Unix, so the mode means the same everywhere
            bundle.writestr(info, (folder / rel).read_bytes())
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pack a module's release bundle.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data = pack(args.folder)
    except BundleError as e:
        print(f"STOP: {e}")
        return 1
    args.out.write_bytes(data)
    print(f"Wrote {args.out}: {len(members(args.folder))} files, {len(data)} bytes.")
    if extra := left_out(args.folder):
        print(f"Left out (not part of a module): {', '.join(extra)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
