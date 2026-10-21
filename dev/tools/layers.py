"""Where each layer's files are: the main app's, and every module's.

A module (``src/trcc/modules/<key>/``) is a mini hexagon: it repeats the main
tree's layers inside it -- ``core/``, ``services/``, ``adapters/``,
``ui/<face>/``.  Every gate that reads one layer reads it through here, so a
module's code is held to the same rules as the main app's and none of it can
slip past a gate by living in a folder the gate never opened.

:func:`logical` names a module's file by the layer it sits in, so an import
rule written for ``trcc.adapters`` also catches ``trcc.modules.<key>.adapters``.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: The ``trcc`` package in this checkout.
TRCC = Path(__file__).resolve().parents[2] / "src" / "trcc"
#: Where the modules are, inside it.
MODULES = "modules"


def roots(layer: str, trcc: Path = TRCC) -> list[Path]:
    """``trcc/<layer>`` and every ``trcc/modules/<key>/<layer>`` there is --
    *layer* as a path inside the package: ``"ui"``, ``"ui/gui"``,
    ``"core/commands"``."""
    found = [trcc / layer, *sorted(d for d in (trcc / MODULES).glob(f"*/{layer}")
                                    if d.is_dir())]
    log.debug("layers.roots: %s -> %s", layer, found)
    return found


def files(layer: str, trcc: Path = TRCC) -> list[Path]:
    """Every ``.py`` file of *layer*, the main app's first, then each module's.
    A module small enough to keep a layer in one file (``core/commands.py``
    where the main app has ``core/commands/``) counts too."""
    found = [path for root in roots(layer, trcc)
             for path in sorted(root.rglob("*.py"))
             if "__pycache__" not in path.parts]
    found += sorted(p for p in (trcc / MODULES).glob(f"*/{layer}.py") if p.is_file())
    log.debug("layers.files: %s -> %d", layer, len(found))
    return found


def logical(dotted: str) -> str:
    """*dotted* as the layer it sits in: ``trcc.modules.<key>.adapters.x`` ->
    ``trcc.adapters.x``.  Anything outside a module comes back unchanged."""
    parts = dotted.split(".")
    if parts[:2] == ["trcc", MODULES] and len(parts) > 3:
        return ".".join(["trcc", *parts[3:]])
    return dotted


def module_of(dotted: str) -> str:
    """The module *dotted* belongs to -- ``trcc.modules.<key>...`` -> ``<key>``;
    "" for the main app."""
    parts = dotted.split(".")
    inside = (parts[:2] == ["trcc", MODULES] and len(parts) > 2
              and parts[2] != "__init__")
    return parts[2] if inside else ""
