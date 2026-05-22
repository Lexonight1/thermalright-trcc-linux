"""Windows OS adapter — sub-package for sensor sources + the chain enumerator.

Sibling of ``trcc/adapters/system/macos/`` and the file-scoped
``linux_platform.py`` / ``bsd_platform.py``.  Exists as a sub-package
because Windows has more moving parts:

- a strategy chain of sensor sources (``sources/``)
- LHM subprocess lifecycle, MSAcpi WMI, HWiNFO SHM — each its own file
- the chain-walking ``WindowsSensorEnumerator``

``WindowsPlatform`` itself stays in ``windows_platform.py`` (one file per
``Platform`` ABC subclass).  This package is what ``WindowsPlatform``
imports for sensor work.
"""
from __future__ import annotations

from trcc.legacy.adapters.system.windows.enumerator import WindowsSensorEnumerator
from trcc.legacy.adapters.system.windows.sources import WindowsSensorSource

__all__ = ['WindowsSensorEnumerator', 'WindowsSensorSource']
