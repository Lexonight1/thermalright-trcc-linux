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
import sys
from pathlib import Path
from types import ModuleType

from ..core.models import MODULE_KEY

log = logging.getLogger(__name__)

#: A module's manifest and its words, at the top of its folder.
MANIFEST = "module.json"
TABLE = "i18n.json"
#: A key is also a Python package name (``core.models.MODULE_KEY``).
KEY = MODULE_KEY
#: What a module is, at the top of its folder -- all a release bundle holds
#: (``dev/tools/module_bundle.py`` packs these, the installer takes only these).
BUNDLE_FILES = frozenset({"__init__.py", MANIFEST, TABLE, "icon.svg", "icon.png",
                          "app.py"})
BUNDLE_DIRS = frozenset({"core", "services", "adapters", "ui", "assets"})


#: The folders :func:`use_folder` added -- the user's, whose modules run only
#: when the App says so.
_USERS: set[str] = set()


def use_folder(folder: Path) -> None:
    """Find modules in *folder* too -- the user's installed ones.  Each
    process does this once (``UserInterface.start``); again is a no-op."""
    _USERS.add(str(folder))
    if str(folder) in __path__:
        log.debug("modules.use_folder: %s already searched", folder)
        return
    __path__.append(str(folder))
    log.info("modules.use_folder: %s", folder)


def forget(key: str) -> None:
    """Drop module *key*'s imported code, so the next import reads its files
    again -- after an update or a removal -- and its words with it.  What a
    window already built from the old code stays until the window is
    reopened."""
    from ..core.i18n import drop_translations

    gone = [name for name in sys.modules
            if name == f"{__name__}.{key}" or name.startswith(f"{__name__}.{key}.")]
    for name in gone:
        del sys.modules[name]
    drop_translations(key)
    log.info("modules.forget: %s -- %d imported part(s) dropped", key, len(gone))


def folders() -> dict[str, Path]:
    """Every module folder -- one holding a ``module.json`` -- on this
    package's search path, by key.  A test adds a folder to ``__path__``.
    The first folder of a key wins, as an import of it does: one in the
    user's folder never stands in for TRCC's own."""
    found: dict[str, Path] = {}
    for root in __path__:
        for path in sorted(Path(root).glob(f"*/{MANIFEST}")):
            if KEY.fullmatch(path.parent.name):
                found.setdefault(path.parent.name, path.parent)
    log.debug("modules.folders: %s", sorted(found))
    return found


def shipped() -> dict[str, Path]:
    """The module folders found without the user's (:func:`use_folder`) --
    TRCC's own, and a test's.  Which of the user's may run is the App's to
    say, never a UI's."""
    found = {key: folder for key, folder in folders().items()
             if str(folder.parent) not in _USERS}
    log.debug("modules.shipped: %s", sorted(found))
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
    own words (``tr(..., module=key)``) -- the light half, for any process."""
    from .. import ipc
    from ..core.i18n import add_translations

    log.info("modules.load_core: %s", key)
    core = part(key, "core")
    if core is not None:
        ipc.register_types(core)
    table = folders().get(key, Path()) / TABLE
    if table.is_file():
        add_translations(key, json.loads(table.read_text(encoding="utf-8")))
    return core


def load_face(key: str, face: str) -> ModuleType | None:
    """What module *key* adds to *face* (``gui``, ``qtgui``, ``cli``, ``api``),
    imported -- which registers it -- or ``None`` when it adds nothing."""
    log.info("modules.load_face: %s %s", key, face)
    load_core(key)
    return part(key, f"ui.{face}")
