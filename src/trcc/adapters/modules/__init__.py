"""Modules: the features a user switches on, off, in or out on the Modules page.

A module is found by its manifest (:mod:`._catalog`) and its code is imported
only when it is switched on; that import registers its class in ``MODULES``,
the way importing ``adapters.device`` registers every wire.  None is moved
here yet.
"""
from __future__ import annotations

from ._base import MODULES, BaseModule, ModuleNotFound
from ._catalog import BUILTIN, MANIFEST, ManifestError, read_manifest, specs

__all__ = ["BUILTIN", "MANIFEST", "MODULES", "BaseModule", "ManifestError",
           "ModuleNotFound", "read_manifest", "specs"]
