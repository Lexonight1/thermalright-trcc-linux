#!/usr/bin/env python3
"""Generate the host setup files the distro packages ship, from the code.

``trcc system setup`` writes udev rules, a usb-storage quirk and two
modules-load entries (``_udev.host_files``).  The deb, rpm, Arch and Nix
packages install the same files from ``packaging/`` -- which were kept "in
step" by hand, and were not: after 0416:5406 became HID (2026-08-19) the
packaged rules still granted it ``scsi_generic`` and no ``hidraw``, the quirk
still forced it onto usb-storage, and the RAPL module entry was never packaged.
Every package install got the stale copy.  The tests checked chosen lines of
the file, never that it matched, so nothing noticed.

So the packaged copies are derived, and ``tests/test_udev.py`` gates them equal.

    PYTHONPATH=src python3 dev/gen_packaging_setup.py           # write them
    PYTHONPATH=src python3 dev/gen_packaging_setup.py --check   # exit 1 if stale
"""
from __future__ import annotations

import sys
from pathlib import Path

from trcc.adapters.system._udev import host_files

_ROOT = Path(__file__).resolve().parents[1]

# Host directory -> where the packages keep that kind of file.
_PACKAGING_DIR = {
    "rules.d": "udev",
    "modprobe.d": "modprobe",
    "modules-load.d": "modprobe",
}


def generate() -> dict[Path, str]:
    """Every packaged setup file, keyed by its path in the repository."""
    return {
        _ROOT / "packaging" / _PACKAGING_DIR[host.parent.name] / host.name: content
        for host, content in host_files().items()
    }


def main(argv: list[str]) -> int:
    stale = [path for path, content in generate().items()
             if not path.exists() or path.read_text(encoding="utf-8") != content]
    if "--check" in argv:
        for path in stale:
            print(f"stale: {path.relative_to(_ROOT)}")
        return 1 if stale else 0
    for path, content in generate().items():
        path.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {len(generate())} packaging file(s), {len(stale)} changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
