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

import ast
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
    # A module is a FOLDER named by its key, which starts with a letter;
    # ``__init__`` and any ``_private`` file are the package's own finder.
    inside = (parts[:2] == ["trcc", MODULES] and len(parts) > 2
              and not parts[2].startswith("_"))
    return parts[2] if inside else ""


def resolve_import(node: ast.ImportFrom, module_name: str) -> str:
    """Resolve a (possibly relative) ``from ... import`` in *module_name* to
    an absolute module."""
    if node.level == 0:
        return node.module or ""
    parts = module_name.split(".")
    base = parts[: len(parts) - node.level]
    return ".".join(base + ([node.module] if node.module else []))


# ── Modules: mini hexagons ───────────────────────────────────────────────────
# A module (``trcc/modules/<key>/``) plugs into the main app through its host
# and is found by its manifest.  So deleting its folder must break nothing in
# the main app, and a module must never reach past its host for the App -- or
# into another module.  TYPE_CHECKING imports count here: a type-only import of
# a module still breaks the type check the day that module is deleted.
# Here, not in the test, because the module review (``review_module.py``)
# holds a submitted module to the very same check.

#: All a module may import from the main app (decision 14, strict): its
#: shared types (``core``), the UI it plugs its faces into (``ui`` -- the
#: layer gates keep a module's own core and services off it), and the module
#: SDK it is built on (``BaseModule``).  Services and hardware -- the App, the
#: Platform, any adapter -- come through its host, never by import.
MODULE_MAY_IMPORT = ("trcc.core", "trcc.ui", "trcc.adapters.modules")


def module_boundary_breaches(src: Path) -> list[str]:
    """Every import, under *src*``/trcc``, that crosses a module's boundary the
    wrong way -- fixture-pointable, so the self-test can prove it sees one."""
    breaches: list[str] = []
    for path in sorted((src / "trcc").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = ".".join(path.relative_to(src).with_suffix("").parts)
        owner = module_of(name)
        rel = path.relative_to(src).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                targets = [resolve_import(node, name)]
            elif isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            else:
                continue
            for target in targets:
                into = module_of(target)
                if not owner and into:
                    breaches.append(f"  {rel}:{node.lineno} -> {target} "
                                    "(the main app names a module)")
                elif owner and into and into != owner:
                    breaches.append(f"  {rel}:{node.lineno} -> {target} "
                                    "(one module reaches into another)")
                elif (owner and not into
                      and (target == "trcc" or target.startswith("trcc."))
                      and not any(target == p or target.startswith(p + ".")
                                  for p in MODULE_MAY_IMPORT)):
                    breaches.append(f"  {rel}:{node.lineno} -> {target} "
                                    "(a module reaches past its host)")
    log.debug("layers.module_boundary_breaches: %s -> %d", src, len(breaches))
    return breaches
