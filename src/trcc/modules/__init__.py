"""Modules: the features a user switches on and off -- each a mini hexagon in
its own folder here.

``<key>/`` holds everything one module owns, at fixed names:

* ``module.json``, ``icon.svg``, ``i18n.json`` -- what the Modules page shows
  and translates, read without importing the module;
* ``app.py`` -- its root, the ``Module`` class, imported only when switched on;
* ``core/`` -- its Commands, Results and events: light, loaded with the
  manifest so its Commands are known (and refused while it is off) in every
  process;
* ``services/``, ``adapters/`` -- its own layers;
* ``ui/<face>/`` -- what it adds to the gui, qtgui, CLI and API.

This package finds them by those names.  Nothing else in the main app imports
a module (``tests/test_architecture_boundaries.py``); a module reaches the app
through its host.  The key is its folder's name and its Python package's.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import re
from pathlib import Path
from types import ModuleType

log = logging.getLogger(__name__)

#: A module's manifest and its words, at the top of its folder.
MANIFEST = "module.json"
TABLE = "i18n.json"
#: A key is also a Python package name: 2-31 lowercase letters, digits or _.
KEY = re.compile(r"[a-z][a-z0-9_]{1,30}")


def folders() -> dict[str, Path]:
    """Every module folder -- one holding a ``module.json`` -- on this
    package's search path, by key.  A test adds a folder to ``__path__``."""
    found = {path.parent.name: path.parent
             for root in __path__ for path in sorted(Path(root).glob(f"*/{MANIFEST}"))
             if KEY.fullmatch(path.parent.name)}
    log.debug("modules.folders: %s", sorted(found))
    return found


def part(key: str, name: str) -> ModuleType | None:
    """Module *key*'s *name* (``"app"``, ``"core"``, ``"ui.cli"``), imported --
    ``None`` when it has none."""
    dotted = f"{__name__}.{key}.{name}"
    try:
        found = importlib.util.find_spec(dotted) is not None
    except ModuleNotFoundError:      # a parent package is missing
        found = False
    log.debug("modules.part: %s %s", dotted, "found" if found else "absent")
    return importlib.import_module(dotted) if found else None


def load_core(key: str) -> ModuleType | None:
    """Module *key*'s Commands, Results and events, known to IPC, and its
    words added to the translations -- the light half, for any process."""
    from .. import ipc
    from ..core.i18n import add_translations

    log.info("modules.load_core: %s", key)
    core = part(key, "core")
    if core is not None:
        ipc.register_types(core)
    table = folders().get(key, Path()) / TABLE
    if table.is_file():
        add_translations(json.loads(table.read_text(encoding="utf-8")))
    return core


def load_face(key: str, face: str) -> ModuleType | None:
    """What module *key* adds to *face* (``gui``, ``qtgui``, ``cli``, ``api``),
    imported -- which registers it -- or ``None`` when it adds nothing."""
    log.info("modules.load_face: %s %s", key, face)
    load_core(key)
    return part(key, f"ui.{face}")
