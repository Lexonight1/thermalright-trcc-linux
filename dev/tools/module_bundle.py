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
import sys
from pathlib import Path

from trcc.adapters.modules._bundle import (
    BundleRefused,
    left_out,
    members,
    module_key,
    pack,
)
from trcc.modules import BUNDLE_DIRS, BUNDLE_FILES

#: What the review (``review_module.py``) and the tests read through this tool.
__all__ = ["BUNDLE_DIRS", "BUNDLE_FILES", "BundleRefused", "left_out", "main",
           "members", "module_key", "pack"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pack a module's release bundle.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        data = pack(args.folder)
    except BundleRefused as e:
        print(f"STOP: {e}")
        return 1
    args.out.write_bytes(data)
    print(f"Wrote {args.out}: {len(members(args.folder))} files, {len(data)} bytes.")
    if extra := left_out(args.folder):
        print(f"Left out (not part of a module): {', '.join(extra)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
