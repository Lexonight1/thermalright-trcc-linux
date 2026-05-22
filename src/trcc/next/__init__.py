"""Clean-slate TRCC architecture.

Hexagonal (ports & adapters) with a Command bus.  Five roles:

    Platform       OS I/O primitives (ABC, one per OS)
    Transport      byte mover — BulkTransport (USB) or ScsiTransport (CDB-level)
    Device         physical device, knows its wire protocol (ABC, one per protocol)
    App            holds Platform + devices, dispatches Commands
    UIs            thin adapters (CLI / GUI / API), speak Commands + Results

Built inside-out: core → adapters → UIs.  Existing code in sibling
packages is untouched during the build; switchover happens once feature
parity lands.
"""
from __future__ import annotations

try:
    from .. import __version__
except ImportError:
    # When run from a different tree (parity tests, etc.) the parent
    # package may not expose its version constant.  Fall back to 0.0.0
    # so the update-check Command can still execute.
    __version__ = "0.0.0"
