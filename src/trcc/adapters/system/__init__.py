"""Platform implementations — one module per OS, keyed by ``sys.platform``.

The registry IS the OCP chokepoint for OS support: a new OS adds one file under
``adapters/system/`` whose class names its key in its own class line::

    class HaikuPlatform(BaseOS, key="haiku"): ...

``BaseOS.__init_subclass__`` registers it — no decorator to forget.  Importing
this package fires the side-effect import of every OS file, which is what
defines those classes, which is what populates :data:`PLATFORMS`.

``core/ports.Platform`` stays a pure ABC — no ``_BY_OS`` dispatch table, no
``importlib.import_module`` from core, no DIP inversion.

See ``memory/project_hexagonal_solid_dry_plan`` §2.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from ._base import PLATFORMS

if TYPE_CHECKING:
    from ...core.ports import Platform

log = logging.getLogger(__name__)


def current_platform() -> Platform:
    """Construct a fresh :class:`Platform` for the running OS.

    The only part of OS dispatch that is not a plain table lookup: the key has
    to be *derived* (BSD variants — ``freebsd14``, ``openbsd7`` — all normalise
    to the shared ``"bsd"`` registration), and the result is *instantiated*
    rather than returned as a class.  An unknown platform falls back to Linux
    with a warning via the registry's ``FallBackTo`` policy.
    """
    key = "bsd" if "bsd" in sys.platform else sys.platform
    platform_cls = PLATFORMS[key]
    log.info("current_platform: %s → building %s", key, platform_cls.__name__)
    return platform_cls()


# Side-effect imports: load each OS module so defining its class registers it.
# Order is irrelevant — each registration is independent.  Anything that imports
# ``trcc.adapters.system`` triggers all four.
from . import bsd as _bsd  # noqa: E402, F401
from . import linux as _linux  # noqa: E402, F401
from . import macos as _macos  # noqa: E402, F401
from . import windows as _windows  # noqa: E402, F401

__all__ = ["PLATFORMS", "current_platform"]
