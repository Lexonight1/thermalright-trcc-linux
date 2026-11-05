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

One more kind, for the Nix package only: a built-in module's device rule.
Everywhere else the module writes it when it is switched on
(``trcc-module-devices``); on NixOS /etc is built from the configuration and
nothing can write there, so the flake installs it.  Built by that helper's
own ``rule_text`` from the module's ``module.json`` -- one builder, one list.

    PYTHONPATH=src python3 dev/gen_packaging_setup.py           # write them
    PYTHONPATH=src python3 dev/gen_packaging_setup.py --check   # exit 1 if stale
"""
from __future__ import annotations

import sys
from pathlib import Path

from importlib.machinery import SourceFileLoader
from types import ModuleType

from trcc.adapters.modules._catalog import read_manifest
from trcc.adapters.system._udev import host_files, sysusers_conf
from trcc.modules import MANIFEST

_ROOT = Path(__file__).resolve().parents[1]

# Host directory -> where the packages keep that kind of file.
_PACKAGING_DIR = {
    "rules.d": "udev",
    "modprobe.d": "modprobe",
    "modules-load.d": "modprobe",
}


def generate() -> dict[Path, str]:
    """Every packaged setup file, keyed by its path in the repository --
    setup's host files, and the packages' own ``sysusers.d`` entry for the
    device group."""
    files = {
        _ROOT / "packaging" / _PACKAGING_DIR[host.parent.name] / host.name: content
        for host, content in host_files().items()
    }
    files[_ROOT / "packaging" / "sysusers" / "trcc.conf"] = sysusers_conf()
    return files | module_rules()


def module_rules() -> dict[Path, str]:
    """Each built-in module's device rule, as ``trcc-module-devices`` writes
    it -- for the Nix package, where nothing can write /etc."""
    loader = SourceFileLoader(
        "trcc_module_devices",
        str(_ROOT / "src" / "trcc" / "assets" / "trcc-module-devices"))
    helper = ModuleType(loader.name)
    loader.exec_module(helper)      # its main() runs only as __main__
    rules: dict[Path, str] = {}
    for path in sorted((_ROOT / "src" / "trcc" / "modules").glob(f"*/{MANIFEST}")):
        manifest = read_manifest(path)
        if manifest.devices:
            name = helper.rule_path(manifest.key).name
            rules[_ROOT / "packaging" / "udev" / name] = helper.rule_text(
                manifest.key, list(manifest.devices))
    return rules


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
