"""The ``MODULES`` registry, beside the base every module extends.

A module names its key in its class line and is registered by defining it,
the way a device or an OS is::

    class KarmaVizModule(BaseModule, key="karmaviz"):
        title = "KarmaViz"
        summary = "Shader visuals that dance to what is playing."

        def _enable(self, app): ...
        def _disable(self, app): ...

The registry lives here, in the adapter layer, because it names concrete
modules; core holds only the ``Module`` port.
"""
from __future__ import annotations

import logging
from typing import Any

from ...core.errors import TrccError
from ...core.factory import Registry, Reject
from ...core.ports import Module

log = logging.getLogger(__name__)


class ModuleNotFound(TrccError):
    """No module is registered under that key."""


MODULES: Registry[str, type[Module]] = Registry(
    "module", on_missing=Reject(ModuleNotFound),
)


class BaseModule(Module):
    """What a module extends: registers itself under the ``key=`` it names.
    ``key=None`` is an intermediate base, not registered."""

    def __init_subclass__(cls, key: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if key is None:
            log.debug("%s: intermediate module base, not registered",
                      cls.__name__)
            return
        log.debug("%s: registering as module %r", cls.__name__, key)
        cls.key = key
        MODULES.register(key)(cls)
