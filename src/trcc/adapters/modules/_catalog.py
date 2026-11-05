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

import dataclasses
import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ... import modules as module_folders
from ...core.errors import ModuleUnavailable, TrccError
from ...core.models import (
    MODULE_CAPABILITIES,
    MODULE_DEVICE,
    MODULE_HOST,
    MODULE_METRIC_NAME,
    MODULE_TRCC_VERSION,
    ModuleManifest,
    ModuleMetric,
)
from ...core.ports import Module
from ...services.modules import ModuleSpec
from ._base import MODULES

log = logging.getLogger(__name__)

MANIFEST = module_folders.MANIFEST


class ManifestError(TrccError):
    """A ``module.json`` that cannot be used, and why."""


def _names(data: dict[str, Any], field: str, path: Path, wanted: str,
           valid: Callable[[str], object] | None = None) -> tuple[str, ...]:
    """Manifest *field*: a list of strings, each passing *valid* when given --
    or ``ManifestError`` saying it must be *wanted*."""
    value = data.get(field, [])
    log.debug("read_manifest: %s %s", field, value)
    if not isinstance(value, list) or not all(
            isinstance(v, str) and (valid is None or valid(v)) for v in value):
        raise ManifestError(f"{path}: {field} must be {wanted}")
    return tuple(value)


def _metrics(data: dict[str, Any], path: Path) -> tuple[ModuleMetric, ...]:
    """Manifest ``metrics``: the readings a module adds, each an object with
    a ``name`` (``MODULE_METRIC_NAME``, unique), a ``label`` and optionally a
    ``unit`` and an ``explanation`` -- or ``ManifestError`` saying what."""
    value = data.get("metrics", [])
    log.debug("read_manifest: metrics %s", value)
    wanted = (f"{path}: metrics must be a list of objects, each with a name "
              "(lowercase letters, digits or _), a label, and optionally a unit "
              "and an explanation")
    if not isinstance(value, list):
        raise ManifestError(wanted)
    found: list[ModuleMetric] = []
    for entry in value:
        if not (isinstance(entry, dict)
                and isinstance(entry.get("name"), str)
                and MODULE_METRIC_NAME.fullmatch(entry["name"])
                and isinstance(entry.get("label"), str) and entry["label"]
                and all(isinstance(entry.get(f, ""), str)
                        for f in ("unit", "explanation"))):
            raise ManifestError(wanted)
        found.append(ModuleMetric(name=entry["name"], label=entry["label"],
                                  unit=entry.get("unit", ""),
                                  explanation=entry.get("explanation", "")))
    names = [m.name for m in found]
    if len(set(names)) != len(names):
        raise ManifestError(f"{path}: metrics names must differ")
    return tuple(found)


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
    caps = _names(data, "capabilities", path, "a list of names")
    if unknown := sorted(set(caps) - set(MODULE_CAPABILITIES)):
        raise ManifestError(f"{path}: no such capability: {', '.join(unknown)}")
    backgrounds = _names(data, "backgrounds", path, "a list of ref prefixes", bool)
    hosts = _names(data, "network_hosts", path, "a list of public hostnames, "
                   "lowercase -- no localhost, no IP address", MODULE_HOST.fullmatch)
    if bool(hosts) != ("network" in caps):
        raise ManifestError(f"{path}: the network capability and network_hosts "
                            "go together -- name the sites it fetches from")
    devices = _names(data, "devices", path, "a list of USB ids, lowercase "
                     "vvvv:pppp as lsusb prints them", MODULE_DEVICE.fullmatch)
    metrics = _metrics(data, path)
    if bool(metrics) != ("metrics" in caps):
        raise ManifestError(f"{path}: the metrics capability and metrics go "
                            "together -- declare the readings it adds")
    if devices and "hardware" not in caps:
        raise ManifestError(f"{path}: devices needs the hardware capability -- it "
                            "is how a module reaches them")
    icon = data.get("icon", "")
    author = data.get("author", "")
    if not isinstance(author, str):
        raise ManifestError(f"{path}: author must be a name")
    min_trcc = data.get("min_trcc", "")
    log.debug("read_manifest: min_trcc %r", min_trcc)
    if not isinstance(min_trcc, str) or (min_trcc and not MODULE_TRCC_VERSION.fullmatch(min_trcc)):
        raise ManifestError(f"{path}: min_trcc must be the oldest TRCC it runs on, "
                            "as X.Y.Z (9.12.0)")
    return ModuleManifest(
        key=data["key"], title=data["title"], summary=data["summary"],
        version=str(data.get("version", "0")),
        default_on=bool(data.get("default_on", True)),
        icon=str(path.parent / icon) if icon else "",
        guide=str(data.get("guide", "")), capabilities=caps,
        backgrounds=backgrounds, network_hosts=hosts, author=author, devices=devices,
        min_trcc=min_trcc, metrics=metrics)


#: Why a workspace module does not run while developer mode is off.
WORKSPACE_OFF = ("in your module workspace -- switch on developer mode on the "
                 "Modules page to run it")


def specs(root: Path | None = None, *, user_root: Path | None = None,
          refuse: Callable[[str], str] | None = None,
          workspace_root: Path | None = None,
          developer: bool = False) -> dict[str, ModuleSpec]:
    """Every module: each folder of :mod:`trcc.modules` with a manifest, and
    each in *user_root*, the user's folder -- or each under *root*, for a test
    that has added *root* to that package's path.  Nothing is imported but
    each module's light ``core/``.  A broken manifest, or a ``core/`` that will
    not load, is logged and that module left out -- one bad module never stops
    the others.

    A module in *user_root* runs only if *refuse* (the App's word, from what
    it installed) says nothing against it; else it is its manifest alone,
    saying why -- nothing of it imported.  The first folder of a key wins, so
    one there never stands in for TRCC's own.

    One in *workspace_root*, the author's own folder, runs from there, live,
    while *developer* mode is on -- never as TRCC's own, and never without
    the switch (:func:`workspace_specs`)."""
    if root is not None:
        paths = sorted(root.glob(f"*/{MANIFEST}"))
    else:
        found = module_folders.folders()
        for extra in (user_root, workspace_root):
            for path in sorted(extra.glob(f"*/{MANIFEST}")) if extra else ():
                if found.setdefault(path.parent.name, path.parent) != path.parent:
                    log.warning("catalog.specs: %s is shadowed by %s", path.parent,
                                found[path.parent.name])
        paths = [folder / MANIFEST for folder in found.values()]
    log.info("catalog.specs: %d manifest(s)%s", len(paths),
             f" under {root}" if root is not None else "")
    result: dict[str, ModuleSpec] = {}
    for path in paths:
        try:
            if workspace_root is not None and path.parent.parent == workspace_root:
                found_spec = workspace_spec(path, workspace_root, developer)
            elif user_root is not None and path.parent.parent == user_root:
                found_spec = spec(path, origin="installed", user_root=user_root,
                                  refused=refuse(path.parent.name) if refuse else "")
            else:
                found_spec = spec(path)
        except ManifestError as e:
            log.warning("catalog.specs: skipping %s", e)
            continue
        except Exception:
            log.exception("catalog.specs: skipping %s -- its core/ did not load",
                          path.parent.name)
            continue
        result[found_spec.manifest.key] = found_spec
    log.info("catalog.specs: %s", sorted(result))
    return result


def spec(path: Path, *, origin: str = "built-in", refused: str = "",
         user_root: Path | None = None) -> ModuleSpec:
    """The module whose manifest is at *path*, its light ``core/`` loaded --
    ``ManifestError``, or whatever its ``core/`` raised, when it will not.
    *refused* says why TRCC will not run it: then it is its manifest alone,
    claiming no background, and none of it is imported.  One in the user's
    folder (*user_root*) has that folder searched first."""
    manifest = read_manifest(path)
    log.info("catalog.spec: %s %s (%s)", manifest.key, manifest.version, origin)
    if refused:
        log.warning("catalog.spec: %s is not run -- %s", manifest.key, refused)
        # An installed one TRCC will not run was copied in by hand; a
        # workspace one is still the author's, waiting for the switch.
        return ModuleSpec(dataclasses.replace(manifest, backgrounds=()),
                          _refusing_loader(refused), _icon_reader(manifest),
                          origin="by hand" if origin == "installed" else origin,
                          refused=refused)
    if user_root is not None:
        module_folders.use_folder(user_root)
    module_folders.load_core(manifest.key)
    return ModuleSpec(manifest, _loader(manifest), _icon_reader(manifest),
                      origin=origin)


def workspace_spec(path: Path, workspace_root: Path, developer: bool) -> ModuleSpec:
    """The workspace module whose manifest is at *path*: live from the
    author's folder while *developer* mode is on, else listed and refused."""
    log.info("catalog.workspace_spec: %s developer=%s", path.parent.name, developer)
    return spec(path, origin="workspace", user_root=workspace_root,
                refused="" if developer else WORKSPACE_OFF)


def workspace_specs(workspace_root: Path, developer: bool,
                    taken: Iterable[str] = ()) -> dict[str, ModuleSpec]:
    """Every module in *workspace_root* but those whose key is *taken* --
    TRCC's own and the installed ones win, as an import of the key does."""
    skip = set(taken)
    result: dict[str, ModuleSpec] = {}
    for path in sorted(workspace_root.glob(f"*/{MANIFEST}")):
        if path.parent.name in skip:
            log.warning("catalog.workspace_specs: %s is shadowed -- its key is taken",
                        path.parent)
            continue
        try:
            result[path.parent.name] = workspace_spec(path, workspace_root, developer)
        except ManifestError as e:
            log.warning("catalog.workspace_specs: skipping %s", e)
        except Exception:
            log.exception("catalog.workspace_specs: skipping %s -- its core/ did not load",
                          path.parent.name)
    log.info("catalog.workspace_specs: %s", sorted(result))
    return result


def _refusing_loader(refused: str) -> Callable[[], type[Module]]:
    """A loader that loads nothing: why, instead."""
    log.debug("catalog._refusing_loader: %s", refused)

    def load() -> type[Module]:
        log.warning("catalog: not loading -- %s", refused)
        raise ModuleUnavailable(refused)
    return load


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
