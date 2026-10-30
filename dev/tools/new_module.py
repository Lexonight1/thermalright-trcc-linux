#!/usr/bin/env python3
"""Start a new built-in module from the ``hello`` skeleton.

Usage::

    PYTHONPATH=src python3.12 dev/tools/new_module.py weather --title "Weather" \\
        --summary "Today's forecast as a panel background."

Copies the ``hello`` skeleton (``dev/module_template/hello/``) to
``src/trcc/modules/<key>/`` -- the one folder a module lives in: its
manifest, icon and words at the top, its root in ``app.py``, its Commands in
``core/`` and what it adds to each UI in ``ui/<face>/`` -- with the key,
title, summary, names and guide link filled in.  The new module starts OFF by
default.  Then: write its ``_enable``/``start`` and its Commands, declare its
capabilities in module.json, add its section to doc/GUIDE_MODULES.md and its
tests.  doc/GUIDE_MODULES.md ("Writing a module") walks through it.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from trcc.core.models import MODULE_BUILT_IN_AUTHOR
from trcc.modules import KEY

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "dev" / "module_template" / "hello"
GUIDE = ("https://github.com/Lexonight1/thermalright-trcc-linux/blob/main/"
         "doc/GUIDE_MODULES.md#")


def class_prefix(key: str) -> str:
    """``rgb_lights`` -> ``RgbLights`` -- what ``Hello`` becomes in its names."""
    return "".join(part.capitalize() for part in key.split("_"))


def create(key: str, title: str, summary: str, repo: Path = REPO,
           author: str = MODULE_BUILT_IN_AUTHOR, into: Path | None = None) -> Path:
    """Copy the skeleton into *repo* as built-in module *key* -- or, given
    *into*, make *into* itself the module: the root of an author's own
    repository, as the review reads it.  The folder made."""
    if not KEY.fullmatch(key):
        raise SystemExit(f"{key!r}: a key is 2-31 lowercase letters, digits or _, "
                         "starting with a letter -- it is also a Python package name")
    folder = into if into is not None else repo / "src" / "trcc" / "modules" / key
    if folder.exists():
        raise SystemExit(f"{folder} already exists -- pick another key")
    shutil.copytree(TEMPLATE, folder,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for path in folder.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("Hello", class_prefix(key))
                            .replace("hello", key), encoding="utf-8")
    manifest_path = folder / "module.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Its guide link: a built-in's section of TRCC's guide; an author's own
    # module points at its own README, which only the author can name.
    manifest.update(key=key, title=title, summary=summary, author=author,
                    default_on=False,
                    guide="" if into is not None else GUIDE + title.lower().replace(" ", "-"))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start a new module: built-in, or "
                                     "with --out, your own, in its own folder.")
    parser.add_argument("key", help="its key and package name, e.g. weather")
    parser.add_argument("--title", required=True, help='e.g. "Weather"')
    parser.add_argument("--summary", required=True,
                        help="one plain sentence on what it does for the user")
    parser.add_argument("--out", type=Path,
                        help="make this NEW folder the module -- your repository's root")
    parser.add_argument("--author", help="your name, as the index will list it "
                        "(required with --out)")
    args = parser.parse_args(argv)
    if args.out is not None:
        if not args.author or args.author == MODULE_BUILT_IN_AUTHOR:
            parser.error(f"--out needs --author: your own name "
                         f"({MODULE_BUILT_IN_AUTHOR!r} is TRCC's)")
        folder = create(args.key, args.title, args.summary, author=args.author,
                        into=args.out)
        print(f"Created {folder}/")
        print("Next: write _enable/start in app.py and its Commands in core/, declare its")
        print("capabilities in module.json, set its \"guide\" to your README, draw icon.svg.")
        print("Try it: switch on developer mode and install the folder -- doc/GUIDE_MODULES.md")
        print('("Getting your module listed") says how to publish it.')
        return 0
    folder = create(args.key, args.title, args.summary)
    print(f"Created {folder.relative_to(REPO)}/")
    print("Next: write _enable/start in app.py and its Commands in core/, declare its capabilities in")
    print("module.json, draw its icon.svg, add its section to doc/GUIDE_MODULES.md,")
    print("and its tests.  It is off by default until you change default_on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
