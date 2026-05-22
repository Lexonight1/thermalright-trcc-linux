"""Windows sensor sources — strategy-chain registry.

Importing this package side-effects every concrete source into
``WindowsSensorSource._registry`` **on Windows only**.  On Linux / macOS /
BSD only the ABC is exposed; the concrete sources stay unloaded because
they'd be dormant anyway (``probe()`` short-circuits on non-Windows).

This keeps the OS boundary clean: Windows-only code lives in Windows
territory; the app DI-receives whichever ``Platform`` matches the host
and the rest never touches the import graph.  Tests on Linux can still
``import HWiNFOSource from trcc.legacy.adapters.system.windows.sources.hwinfo``
directly (module path), which registers it ad-hoc for that process.

Adding a new source = one new module + one ``@WindowsSensorSource.register('key')``
decorator + one import line below.  Zero touchpoints in the enumerator.
"""
from __future__ import annotations

import sys

from trcc.legacy.adapters.system.windows.sources._base import WindowsSensorSource

if sys.platform == 'win32':
    # Import-for-side-effects on Windows only — each module's @register call
    # populates the registry.  Listed in priority order for readability; the
    # registry sorts by class-level ``priority`` so order here doesn't matter.
    from trcc.legacy.adapters.system.windows.sources.hwinfo import HWiNFOSource
    from trcc.legacy.adapters.system.windows.sources.lhm import LHMSource
    from trcc.legacy.adapters.system.windows.sources.msacpi import MSAcpiSource

    __all__ = ['HWiNFOSource', 'LHMSource', 'MSAcpiSource', 'WindowsSensorSource']
else:
    __all__ = ['WindowsSensorSource']
