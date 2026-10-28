"""Modules: what the Modules page lists, switching one on or off, and getting
more from the signed index.

Every UI's Modules page -- gui, qtgui, ``trcc system modules``, the API -- is these
Commands.  A module's own work runs in its ``Module.enable`` / ``disable``, hooks
and all; this only asks, records and announces.

Developer mode (``SetDeveloperMode``, ``InstallModuleFolder``) is the one part no
API route reaches: it installs code nobody reviewed, so only a UI on this
machine -- a window, or ``trcc`` -- may switch it on or use it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from ..errors import HttpFetchError, ModuleUnavailable, TrccError
from ..events import DeveloperModeChanged, ModuleChanged
from ..results import (
    ModuleCatalogResult,
    ModuleFolderResult,
    ModuleIconResult,
    ModuleResult,
    ModulesResult,
)
from ._base import Command, Query

if TYPE_CHECKING:
    from ...app import App

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ListModules(Query[ModulesResult]):
    """Every module, on or off, with why one cannot run here."""

    def execute(self, app: App) -> ModulesResult:
        infos = app.modules.infos()
        developer = app.settings.app.developer_mode
        log.info("ListModules: %s developer_mode=%s",
                 {m.key: m.enabled for m in infos}, developer)
        return ModulesResult(ok=True, modules=infos, developer_mode=developer)


@dataclass(frozen=True, slots=True)
class ModuleIcon(Query[ModuleIconResult]):
    """Module *key*'s symbol, as bytes -- every window's tile and the API's
    route draw it from here, never from a path."""
    key: str

    def execute(self, app: App) -> ModuleIconResult:
        log.debug("ModuleIcon: %s", self.key)
        if (icon := app.modules.icon(self.key)) is None:
            log.warning("ModuleIcon: no symbol for %r", self.key)
            return ModuleIconResult(ok=False, message=f"no icon for module {self.key!r}",
                                    key=self.key, found=False)
        image, media_type = icon
        return ModuleIconResult(ok=True, key=self.key, image=image,
                                media_type=media_type)


@dataclass(frozen=True, slots=True)
class SetModuleEnabled(Command[ModuleResult]):
    """Switch module *key* on or off -- remembered, and every UI hears it.
    A module that cannot run here refuses, and nothing changes."""
    #: Switching off may ask for the password (a module taking back a
    #: permission it was granted), so no timeout cuts the prompt off.
    WAITS_ON_USER: ClassVar[bool] = True
    key: str
    enabled: bool

    def execute(self, app: App) -> ModuleResult:
        log.info("SetModuleEnabled: %s -> %s", self.key, self.enabled)
        if self.key not in app.modules:
            log.warning("SetModuleEnabled: no module %r", self.key)
            return ModuleResult(ok=False, key=self.key, found=False,
                                message=f"no module {self.key!r}")
        module = app.modules.manifest(self.key)
        if app.modules.is_on(self.key) == self.enabled:
            log.info("SetModuleEnabled: %s already %s", self.key,
                     "on" if self.enabled else "off")
            return ModuleResult(ok=True, key=self.key, enabled=self.enabled,
                                message=f"{module.title} already "
                                        f"{'on' if self.enabled else 'off'}")
        try:
            note = app.modules.switch(self.key, self.enabled)
        except ModuleUnavailable as e:
            log.warning("SetModuleEnabled: %s refused: %s", self.key, e)
            return ModuleResult(ok=False, key=self.key,
                                enabled=app.modules.is_on(self.key),
                                message=str(e))
        app.settings.set_module_enabled(self.key, self.enabled)
        if note:
            log.warning("SetModuleEnabled: %s -- %s", self.key, note)
        app.events.publish(ModuleChanged(key=self.key, enabled=self.enabled,
                                         note=note))
        return ModuleResult(ok=True, key=self.key, enabled=self.enabled,
                            message=f"{module.title} "
                                    f"{'on' if self.enabled else 'off'}",
                            note=note)


def _now() -> datetime:
    """The time an index is judged by."""
    now = datetime.now(timezone.utc)
    log.debug("modules._now: %s", now.isoformat())
    return now


@dataclass(frozen=True, slots=True)
class BrowseModules(Query[ModuleCatalogResult]):
    """Every module the signed index lists -- available, installed, with an
    update, or taken back."""
    #: Two downloads, the root and the index.
    TAKES_S: ClassVar[float | None] = 130.0

    def execute(self, app: App) -> ModuleCatalogResult:
        log.info("BrowseModules")
        try:
            modules = app.module_installer.browse(_now())
        except (TrccError, HttpFetchError) as e:
            log.warning("BrowseModules: %s", e)
            return ModuleCatalogResult(ok=False, message=f"the module list could not "
                                                         f"be read: {e}")
        return ModuleCatalogResult(ok=True, modules=modules)


@dataclass(frozen=True, slots=True)
class InstallModule(Command[ModuleResult]):
    """Install module *key* from the index, or update it.  *capabilities*
    and *network_hosts* are what the user accepted: they must be exactly what
    the index lists, or nothing is installed -- an update asking for more is
    asked about again."""
    #: The root, the index and the bundle.
    TAKES_S: ClassVar[float | None] = 200.0
    key: str
    capabilities: tuple[str, ...] = ()
    network_hosts: tuple[str, ...] = ()

    def execute(self, app: App) -> ModuleResult:
        log.info("InstallModule: %s accepting %s %s", self.key, self.capabilities,
                 self.network_hosts)
        try:
            record, note = app.module_installer.install(
                self.key, self.capabilities, self.network_hosts, _now())
        except (TrccError, HttpFetchError) as e:
            log.warning("InstallModule: %s refused: %s", self.key, e)
            return ModuleResult(ok=False, message=str(e))
        return ModuleResult(ok=True, key=self.key, enabled=app.modules.is_on(self.key),
                            message=f"{app.modules.manifest(self.key).title} "
                                    f"{record.version} installed", note=note)


@dataclass(frozen=True, slots=True)
class SetDeveloperMode(Command[ModuleResult]):
    """Developer mode on or off: whether a module may be installed from a
    folder, unsigned.  Switching it off installs nothing more; modules already
    installed that way stay, still saying "unsigned"."""
    on: bool

    def execute(self, app: App) -> ModuleResult:
        log.info("SetDeveloperMode: %s", self.on)
        app.settings.set_developer_mode(self.on)
        app.events.publish(DeveloperModeChanged(on=self.on))
        return ModuleResult(ok=True, enabled=self.on,
                            message=f"developer mode {'on' if self.on else 'off'}")


@dataclass(frozen=True, slots=True)
class ReadModuleFolder(Query[ModuleFolderResult]):
    """The module in folder *path*: what it is and what it asks for -- the
    permission screen an ``InstallModuleFolder`` is asked after.  The App
    reads it, so every UI asks about the same thing."""
    path: Path

    def execute(self, app: App) -> ModuleFolderResult:
        log.info("ReadModuleFolder: %s", self.path)
        try:
            module = app.module_installer.read_folder(self.path)
        except TrccError as e:
            log.warning("ReadModuleFolder: %s: %s", self.path, e)
            return ModuleFolderResult(ok=False, message=str(e))
        return ModuleFolderResult(ok=True, module=module)


@dataclass(frozen=True, slots=True)
class InstallModuleFolder(Command[ModuleResult]):
    """Install the module in folder *path*, unsigned -- developer mode only.
    *capabilities* and *network_hosts* are what the user accepted: exactly what
    its ``module.json`` asks for, or nothing is installed.  It is off after
    install; the user switches it on."""
    path: Path
    capabilities: tuple[str, ...] = ()
    network_hosts: tuple[str, ...] = ()

    def execute(self, app: App) -> ModuleResult:
        log.info("InstallModuleFolder: %s accepting %s %s", self.path,
                 self.capabilities, self.network_hosts)
        try:
            record = app.module_installer.install_folder(
                self.path, self.capabilities, self.network_hosts)
        except TrccError as e:
            log.warning("InstallModuleFolder: %s refused: %s", self.path, e)
            return ModuleResult(ok=False, message=str(e))
        return ModuleResult(ok=True, key=record.key, enabled=False,
                            message=f"{app.modules.manifest(record.key).title} "
                                    f"{record.version} installed unsigned -- off "
                                    "until you switch it on")


@dataclass(frozen=True, slots=True)
class RemoveModule(Command[ModuleResult]):
    """Remove installed module *key*.  A built-in module cannot be removed;
    themes made with it stay."""
    key: str

    def execute(self, app: App) -> ModuleResult:
        log.info("RemoveModule: %s", self.key)
        try:
            app.module_installer.remove(self.key)
        except TrccError as e:
            log.warning("RemoveModule: %s refused: %s", self.key, e)
            return ModuleResult(ok=False, message=str(e))
        return ModuleResult(ok=True, key=self.key, message=f"{self.key} removed")
