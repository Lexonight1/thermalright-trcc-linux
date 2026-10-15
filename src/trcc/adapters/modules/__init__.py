"""Modules: the features a user switches on or off on the Modules page.

Importing this package registers every module, as importing
``adapters.device`` registers every wire.  None is moved here yet.
"""
from __future__ import annotations

from ._base import MODULES, BaseModule, ModuleNotFound

__all__ = ["MODULES", "BaseModule", "ModuleNotFound"]
