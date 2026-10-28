"""The user's installed modules on disk: one folder each, under
``Paths.user_modules_dir()``.

Installing never leaves a half-written module: the bundle is unpacked into a
hidden staging folder beside the others (same disk, so the move is a rename),
its manifest is read and checked there, and only then does it take the
module's place -- the old copy moved aside first and deleted last.  A hidden
folder is never a module: a key starts with a letter (``trcc.modules.KEY``).
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from ... import modules as module_folders
from ...core.models import ModuleManifest
from ...core.ports import ModuleStore
from ._bundle import pack, unpack
from ._catalog import read_manifest

log = logging.getLogger(__name__)


class FolderModuleStore(ModuleStore):
    """Installed modules in *root*, one folder each."""

    def __init__(self, root: Path) -> None:
        log.debug("FolderModuleStore: %s", root)
        self._root = root

    def root(self) -> Path:
        log.debug("FolderModuleStore.root: %s", self._root)
        return self._root

    def install(self, bundle: bytes,
                check: Callable[[ModuleManifest], None]) -> ModuleManifest:
        log.info("FolderModuleStore.install: %d bytes", len(bundle))
        self._root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self._root))
        try:
            folder = unpack(bundle, staging)
            manifest = read_manifest(folder / module_folders.MANIFEST)
            check(manifest)
            target = self._root / folder.name
            old = None
            if target.exists():
                old = Path(tempfile.mkdtemp(prefix=f".old-{folder.name}-", dir=self._root))
                target.rename(old / folder.name)
            try:
                folder.rename(target)
            except OSError:
                if old is not None:          # the old copy goes back, whole
                    (old / folder.name).rename(target)
                    shutil.rmtree(old, ignore_errors=True)
                log.exception("FolderModuleStore.install: %s not moved in", folder.name)
                raise
            if old is not None:
                shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        log.info("FolderModuleStore.install: %s %s in %s", manifest.key,
                 manifest.version, target)
        return read_manifest(target / module_folders.MANIFEST)

    def pack(self, folder: Path) -> tuple[bytes, ModuleManifest]:
        log.info("FolderModuleStore.pack: %s", folder)
        manifest = read_manifest(folder / module_folders.MANIFEST)
        return pack(folder), manifest

    def remove(self, key: str) -> None:
        target = self._root / key
        log.info("FolderModuleStore.remove: %s (present=%s)", key, target.is_dir())
        if not module_folders.KEY.fullmatch(key):
            raise ValueError(f"{key!r} is not a module key")
        shutil.rmtree(target, ignore_errors=True)
