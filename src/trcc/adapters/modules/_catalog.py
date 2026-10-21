"""Finding modules without importing them: their ``module.json`` manifests.

A built-in module's manifest -- and its icon -- live in
``src/trcc/assets/modules/<key>/``, the one data folder every build carries
(the frozen Windows and macOS apps collect ``trcc``'s code and ``assets/``,
nothing else).  Its code lives in ``adapters/modules/<key>/`` and is imported
only when the module is switched on.  A downloaded bundle will carry the same
two files in its own folder, read by the same :func:`read_manifest`.
"""
from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core.errors import TrccError
from ...core.models import MODULE_CAPABILITIES, ModuleManifest
from ...core.ports import Module
from ...services.modules import ModuleSpec
from ._base import MODULES

log = logging.getLogger(__name__)

MANIFEST = "module.json"
#: Where the built-in modules' manifests are.
BUILTIN = Path(__file__).resolve().parents[2] / "assets" / "modules"


class ManifestError(TrccError):
    """A ``module.json`` that cannot be used, and why."""


def read_manifest(path: Path) -> ModuleManifest:
    """The manifest at *path* -- or ``ManifestError`` saying what is wrong."""
    log.debug("read_manifest: %s", path)
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"{path}: not readable JSON ({e})") from e
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: not a JSON object")
    missing = [f for f in ("key", "title", "summary", "entry")
               if not isinstance(data.get(f), str) or not data[f]]
    if missing:
        raise ManifestError(f"{path}: missing {', '.join(missing)}")
    if data["key"] != path.parent.name:
        raise ManifestError(f"{path}: key {data['key']!r} is not its folder's name")
    caps = data.get("capabilities", [])
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        raise ManifestError(f"{path}: capabilities must be a list of names")
    if unknown := sorted(set(caps) - set(MODULE_CAPABILITIES)):
        raise ManifestError(f"{path}: no such capability: {', '.join(unknown)}")
    icon = data.get("icon", "")
    return ModuleManifest(
        key=data["key"], title=data["title"], summary=data["summary"],
        entry=data["entry"], version=str(data.get("version", "0")),
        default_on=bool(data.get("default_on", True)),
        icon=str(path.parent / icon) if icon else "",
        guide=str(data.get("guide", "")), capabilities=tuple(caps))


def specs(root: Path | None = None) -> dict[str, ModuleSpec]:
    """Every module whose manifest is under *root* (the built-in folder by
    default -- looked up now, not at import, so a test can point it
    elsewhere), none of them imported.  A broken manifest is logged and left
    out -- it never stops the others."""
    root = BUILTIN if root is None else root
    log.info("catalog.specs: %s", root)
    found: dict[str, ModuleSpec] = {}
    for path in sorted(root.glob(f"*/{MANIFEST}")):
        try:
            manifest = read_manifest(path)
        except ManifestError as e:
            log.warning("catalog.specs: skipping %s", e)
            continue
        found[manifest.key] = ModuleSpec(manifest, _loader(manifest))
    log.info("catalog.specs: %s", sorted(found))
    return found


def _loader(manifest: ModuleManifest) -> Callable[[], type[Module]]:
    """How to load *manifest*'s class: import its package, which registers the
    class under its key."""
    log.debug("catalog._loader: %s", manifest.key)

    def load() -> type[Module]:
        log.info("catalog: importing %s for module %s", manifest.entry, manifest.key)
        importlib.import_module(manifest.entry)
        return MODULES[manifest.key]
    return load
