"""Hello's Commands and Results -- the light half of a module.

Loaded with the manifest, in every process (the App, each window, the CLI,
the API), so the wire knows ``HelloGreet`` and the App can refuse it while Hello
is off.  Nothing heavy belongs here: no hardware, no threads, no big imports.
A ``ModuleQuery`` (or ``ModuleCommand``) runs against the module's root --
``app.py`` -- never the App.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from trcc.core.commands._base import ModuleQuery
from trcc.core.results import Result

if TYPE_CHECKING:
    from ..app import HelloModule

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HelloResult(Result):
    """What ``HelloGreet`` answers."""
    greeting: str = ""


@dataclass(frozen=True, slots=True)
class HelloGreet(ModuleQuery[HelloResult, "HelloModule"]):
    """Hello's greeting for *name* -- answered by the module, not the App."""
    MODULE: ClassVar[str] = "hello"
    name: str = "world"

    def run(self, module: HelloModule) -> HelloResult:
        log.debug("HelloGreet.run: %s", self.name)
        greeting = module.greet(self.name)
        return HelloResult(ok=True, greeting=greeting, message=greeting)
