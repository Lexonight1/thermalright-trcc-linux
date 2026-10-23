"""A module's release bundle, read and unpacked -- every entry checked first.

A bundle is the zip ``dev/tools/module_bundle.py`` packs: one folder named for
the module's key, holding only a module's fixed names (``trcc.modules``), each
entry STORED.  Anything else is refused before a byte is written:

* a name outside ``<key>/``, absolute, with ``..``, a backslash or a drive;
* a symbolic link -- it could point anywhere on the user's disk;
* a top-level name a module does not have;
* a compressed entry -- stored, the bundle's size bounds what it unpacks to,
  so no small download unpacks into a full disk;
* more than ``_MAX_FILES`` entries.

The sha256 the signed index lists is checked before this runs
(``ModuleTrust.check_bundle``); this guards the user's disk even so.
"""
from __future__ import annotations

import io
import json
import logging
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ... import modules as module_folders
from ...core.errors import TrccError

log = logging.getLogger(__name__)

_MAX_FILES = 4000


class BundleRefused(TrccError):
    """A bundle that will not be unpacked, and why."""


def _entries(bundle: zipfile.ZipFile) -> tuple[str, list[zipfile.ZipInfo]]:
    """The module's key and its file entries -- or ``BundleRefused``."""
    infos = bundle.infolist()
    log.debug("bundle._entries: %d entries", len(infos))
    if not infos or len(infos) > _MAX_FILES:
        raise BundleRefused(f"{len(infos)} entries -- not a module bundle")
    files: list[zipfile.ZipInfo] = []
    keys: set[str] = set()
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        if ("\\" in name or ":" in name or name.startswith("/")
                or ".." in parts or len(parts) < 2):
            raise BundleRefused(f"{name!r}: not a path inside the module")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise BundleRefused(f"{name}: a symbolic link")
        if info.compress_type != zipfile.ZIP_STORED:
            raise BundleRefused(f"{name}: compressed -- a bundle is stored")
        top = parts[1]
        is_dir = name.endswith("/")
        if not ((len(parts) == 2 and not is_dir and top in module_folders.BUNDLE_FILES)
                or top in module_folders.BUNDLE_DIRS):
            raise BundleRefused(f"{name}: not part of a module")
        keys.add(parts[0])
        if not is_dir:
            files.append(info)
    if len(keys) != 1:
        raise BundleRefused(f"not one module's folder: {sorted(keys)}")
    if not module_folders.KEY.fullmatch(key := keys.pop()):
        raise BundleRefused(f"{key!r}: not a module key")
    return key, files


def read_bundle(data: bytes) -> tuple[str, dict[str, Any]]:
    """The bundle's key and its ``module.json``, as written -- unpacking
    nothing."""
    log.info("read_bundle: %d bytes", len(data))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            key, _ = _entries(bundle)
            manifest = json.loads(bundle.read(f"{key}/{module_folders.MANIFEST}"))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise BundleRefused(f"not a readable module bundle ({e})") from e
    if not isinstance(manifest, dict):
        raise BundleRefused(f"{key}: module.json is not a JSON object")
    log.info("read_bundle: %s %s", key, manifest.get("version"))
    return key, manifest


def unpack(data: bytes, into: Path) -> Path:
    """Unpack the bundle under *into*: ``into/<key>/``, the folder made.  Each
    file written by name, never by ``extract`` -- the names were checked."""
    log.info("unpack: %d bytes into %s", len(data), into)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            key, files = _entries(bundle)
            for info in files:
                target = into / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(info))
    except zipfile.BadZipFile as e:
        raise BundleRefused(f"not a readable module bundle ({e})") from e
    log.info("unpack: %s, %d file(s)", key, len(files))
    return into / key
