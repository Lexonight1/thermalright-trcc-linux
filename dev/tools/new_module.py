#!/usr/bin/env python3
"""Start a new built-in module from the ``hello`` skeleton.

Usage::

    PYTHONPATH=src python3.12 dev/tools/new_module.py weather --title "Weather" \\
        --summary "Today's forecast as a panel background."

Copies ``dev/module_template/`` into the two places a built-in module lives:

* ``src/trcc/assets/modules/<key>/`` -- ``module.json`` and ``icon.svg``.  The
  only data folder every build carries, so the manifest reaches the Windows
  and macOS apps too.
* ``src/trcc/adapters/modules/<key>/`` -- the code, imported only when the
  module is switched on.

with the key, title, summary, class name and guide link filled in.  The new
module starts OFF by default.  Then: write its ``_enable``/``start``, declare
its capabilities in module.json, add its section to doc/GUIDE_MODULES.md and
its tests.  doc/GUIDE_MODULES.md ("Writing a module") walks through it.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "dev" / "module_template"
#: A key is also its Python package's name, so it must be an identifier.
KEY = re.compile(r"[a-z][a-z0-9_]{1,30}")
GUIDE = ("https://github.com/Lexonight1/thermalright-trcc-linux/blob/main/"
         "doc/GUIDE_MODULES.md#")


def class_name(key: str) -> str:
    """``rgb_lights`` -> ``RgbLightsModule``."""
    return "".join(part.capitalize() for part in key.split("_")) + "Module"


def create(key: str, title: str, summary: str, repo: Path = REPO) -> tuple[Path, Path]:
    """Copy the skeleton into *repo* as module *key*; the two folders made."""
    if not KEY.fullmatch(key):
        raise SystemExit(f"{key!r}: a key is 2-31 lowercase letters, digits or _, "
                         "starting with a letter -- it is also a Python package name")
    manifest_dir = repo / "src" / "trcc" / "assets" / "modules" / key
    code_dir = repo / "src" / "trcc" / "adapters" / "modules" / key
    for folder in (manifest_dir, code_dir):
        if folder.exists():
            raise SystemExit(f"{folder} already exists -- pick another key")

    shutil.copytree(TEMPLATE / "manifest" / "hello", manifest_dir)
    manifest_path = manifest_dir / "module.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(key=key, title=title, summary=summary, default_on=False,
                    entry=f"trcc.adapters.modules.{key}",
                    guide=GUIDE + title.lower().replace(" ", "-"))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    code_dir.mkdir(parents=True)
    code = (TEMPLATE / "code" / "hello" / "__init__.py").read_text(encoding="utf-8")
    code = (code.replace('key="hello"', f'key="{key}"')
                .replace("HelloModule", class_name(key))
                .replace('title, summary = "Hello", "The smallest module there is."',
                         f"title, summary = {title!r}, {summary!r}")
                .replace('"hello: ', f'"{key}: ')
                .replace("Hello -- the module skeleton.", f"{title} -- a TRCC module."))
    (code_dir / "__init__.py").write_text(code, encoding="utf-8")
    return manifest_dir, code_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start a new built-in module.")
    parser.add_argument("key", help="its key and package name, e.g. weather")
    parser.add_argument("--title", required=True, help='e.g. "Weather"')
    parser.add_argument("--summary", required=True,
                        help="one plain sentence on what it does for the user")
    args = parser.parse_args(argv)
    manifest_dir, code_dir = create(args.key, args.title, args.summary)
    print(f"Created {manifest_dir.relative_to(REPO)}/ and {code_dir.relative_to(REPO)}/")
    print("Next: write _enable/start in the code, declare its capabilities in")
    print("module.json, draw its icon.svg, add its section to doc/GUIDE_MODULES.md,")
    print("and its tests.  It is off by default until you change default_on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
