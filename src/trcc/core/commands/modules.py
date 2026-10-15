"""Modules: what the Modules page lists, and switching one on or off.

Every UI's Modules page -- gui, qtgui, ``trcc module``, the API -- is these
two.  A module's own work runs in its ``Module.enable`` / ``disable``, hooks
and all; this only asks, records and announces.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..errors import ModuleUnavailable
from ..events import ModuleChanged
from ..models import ModuleInfo
from ..results import ModuleResult, ModulesResult
from ._base import Command, Query

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ListModules(Query[ModulesResult]):
    """Every module, on or off, with why one cannot run here."""

    def execute(self, app: App) -> ModulesResult:
        infos = tuple(
            ModuleInfo(key=key, title=module.title, summary=module.summary,
                       enabled=key in app.enabled_modules,
                       problem=module.problem())
            for key, module in sorted(app.modules.items()))
        log.info("ListModules: %s", {m.key: m.enabled for m in infos})
        return ModulesResult(ok=True, modules=infos)


@dataclass(frozen=True, slots=True)
class SetModuleEnabled(Command[ModuleResult]):
    """Switch module *key* on or off -- remembered, and every UI hears it.
    A module that cannot run here refuses, and nothing changes."""
    key: str
    enabled: bool

    def execute(self, app: App) -> ModuleResult:
        log.info("SetModuleEnabled: %s -> %s", self.key, self.enabled)
        module = app.modules.get(self.key)
        if module is None:
            log.warning("SetModuleEnabled: no module %r", self.key)
            return ModuleResult(ok=False, key=self.key,
                                message=f"no module {self.key!r}")
        if (self.key in app.enabled_modules) == self.enabled:
            log.info("SetModuleEnabled: %s already %s", self.key,
                     "on" if self.enabled else "off")
            return ModuleResult(ok=True, key=self.key, enabled=self.enabled,
                                message=f"{module.title} already "
                                        f"{'on' if self.enabled else 'off'}")
        try:
            if self.enabled:
                module.enable(app)
                app.enabled_modules.add(self.key)
            else:
                module.disable(app)
                app.enabled_modules.discard(self.key)
        except ModuleUnavailable as e:
            log.warning("SetModuleEnabled: %s refused: %s", self.key, e)
            return ModuleResult(ok=False, key=self.key,
                                enabled=self.key in app.enabled_modules,
                                message=str(e))
        app.settings.set_module_enabled(self.key, self.enabled)
        app.events.publish(ModuleChanged(key=self.key, enabled=self.enabled))
        return ModuleResult(ok=True, key=self.key, enabled=self.enabled,
                            message=f"{module.title} "
                                    f"{'on' if self.enabled else 'off'}")
