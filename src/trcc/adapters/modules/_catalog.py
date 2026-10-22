"""Finding modules without importing them: their ``module.json`` manifests.

Everything a module owns lives in its own folder, ``trcc/modules/<key>/``
(see :mod:`trcc.modules`): the manifest and icon at its top, its root in
``app.py``, imported only when the module is switched on.  Reading the
catalog loads each module's light ``core/`` -- its Commands, Results and
events -- so they are known everywhere, and refused while it is off.  A
downloaded bundle will be the same folder, read by the same
:func:`read_manifest`.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ... import modules as module_folders
from ...core.errors import TrccError
from ...core.models import MODULE_CAPABILITIES, ModuleManifest
from ...core.ports import Module
from ...services.modules import ModuleSpec
from ._base import MODULES

log = logging.getLogger(__name__)

MANIFEST = module_folders.MANIFEST


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
    missing = [f for f in ("key", "title", "summary")
               if not isinstance(data.get(f), str) or not data[f]]
    if missing:
        raise ManifestError(f"{path}: missing {', '.join(missing)}")
    if data["key"] != path.parent.name:
        raise ManifestError(f"{path}: key {data['key']!r} is not its folder's name")
    if not module_folders.KEY.fullmatch(data["key"]):
        raise ManifestError(f"{path}: key {data['key']!r} is not 2-31 lowercase "
                            "letters, digits or _ -- it is also a package name")
    caps = data.get("capabilities", [])
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        raise ManifestError(f"{path}: capabilities must be a list of names")
    if unknown := sorted(set(caps) - set(MODULE_CAPABILITIES)):
        raise ManifestError(f"{path}: no such capability: {', '.join(unknown)}")
    backgrounds = data.get("backgrounds", [])
    if not isinstance(backgrounds, list) or not all(
            isinstance(b, str) and b for b in backgrounds):
        raise ManifestError(f"{path}: backgrounds must be a list of ref prefixes")
    icon = data.get("icon", "")
    return ModuleManifest(
        key=data["key"], title=data["title"], summary=data["summary"],
        version=str(data.get("version", "0")),
        default_on=bool(data.get("default_on", True)),
        icon=str(path.parent / icon) if icon else "",
        guide=str(data.get("guide", "")), capabilities=tuple(caps),
        backgrounds=tuple(backgrounds))


def specs(root: Path | None = None) -> dict[str, ModuleSpec]:
    """Every module: each folder of :mod:`trcc.modules` with a manifest -- or
    each under *root*, for a test that has added *root* to that package's
    path.  Nothing is imported but each module's light ``core/``.  A broken
    manifest, or a ``core/`` that will not load, is logged and that module
    left out -- one bad module never stops the others."""
    paths = (sorted(root.glob(f"*/{MANIFEST}")) if root is not None
             else [folder / MANIFEST for folder in module_folders.folders().values()])
    log.info("catalog.specs: %d manifest(s)%s", len(paths),
             f" under {root}" if root is not None else "")
    found: dict[str, ModuleSpec] = {}
    for path in paths:
        try:
            manifest = read_manifest(path)
        except ManifestError as e:
            log.warning("catalog.specs: skipping %s", e)
            continue
        try:
            module_folders.load_core(manifest.key)
        except Exception:
            log.exception("catalog.specs: skipping %s -- its core/ did not load",
                          manifest.key)
            continue
        found[manifest.key] = ModuleSpec(manifest, _loader(manifest),
                                         _icon_reader(manifest))
    log.info("catalog.specs: %s", sorted(found))
    return found


#: The symbol types a manifest may name, and how they are served.
_ICON_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}


def _icon_reader(manifest: ModuleManifest) -> Callable[[], tuple[bytes, str] | None]:
    """How to read *manifest*'s symbol: its file's bytes and media type, or
    None -- no symbol, a type no window draws, or a file that is gone."""
    log.debug("catalog._icon_reader: %s %r", manifest.key, manifest.icon)

    def read() -> tuple[bytes, str] | None:
        path = Path(manifest.icon) if manifest.icon else None
        media_type = _ICON_TYPES.get(path.suffix.lower()) if path is not None else None
        if path is None or media_type is None:
            log.debug("catalog: %s has no symbol to show (%s)", manifest.key, path)
            return None
        try:
            image = path.read_bytes()
        except OSError as e:
            log.warning("catalog: %s's symbol %s unreadable -- %s", manifest.key,
                        path, e)
            return None
        log.debug("catalog: %s's symbol %d bytes %s", manifest.key, len(image),
                  media_type)
        return image, media_type
    return read


def _loader(manifest: ModuleManifest) -> Callable[[], type[Module]]:
    """How to load *manifest*'s class: import its ``app.py``, which registers
    the class under its key."""
    log.debug("catalog._loader: %s", manifest.key)

    def load() -> type[Module]:
        log.info("catalog: importing module %s's app.py", manifest.key)
        if module_folders.part(manifest.key, "app") is None:
            raise ModuleNotFoundError(f"module {manifest.key} has no app.py")
        return MODULES[manifest.key]
    return load
