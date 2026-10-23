"""How the App finds and registers modules -- the features a user switches on,
off, in or out on the Modules page.

The modules themselves live in :mod:`trcc.modules`, one folder each.  A
module is found by its manifest (:mod:`._catalog`) and its ``app.py`` is
imported only when it is switched on; that import registers its class in
``MODULES``, the way importing ``adapters.device`` registers every wire.
"""
from __future__ import annotations

from ._base import MODULES, BaseModule, ModuleNotFound
from ._bundle import BundleRefused, read_bundle
from ._catalog import MANIFEST, ManifestError, read_manifest, spec, specs
from ._signing import Ed25519Checker
from ._store import FolderModuleStore

__all__ = ["MANIFEST", "MODULES", "BaseModule", "BundleRefused", "Ed25519Checker",
           "FolderModuleStore", "ManifestError", "ModuleNotFound", "read_bundle",
           "read_manifest", "spec", "specs"]
