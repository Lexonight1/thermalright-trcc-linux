"""Every module's state and lifecycle -- the one owner.

A module goes through two steps, and they run at different times:

* **enable / disable** -- register its features (visuals, frame hooks,
  Command routing) or take them out.  Cheap and hardware-free, so it runs in
  every App: the saved choices are applied while the App is built.
* **start / stop** -- the work that touches hardware or runs a thread.  Only
  inside a session: ``start_session`` starts what is on, ``close`` stops it,
  and a module switched on inside a session is started at once.

A module that fails on the way in is undone and reported -- never left half
on, and never allowed to break the App that is loading it.

Each module arrives as a :class:`ModuleSpec`: its manifest, and how to load
its class.  The class is loaded -- its code imported -- only when the module
is first switched on, so a module that is off costs nothing.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..core.errors import ModuleUnavailable
from ..core.models import ModuleInfo, ModuleManifest
from ..core.ports import Module, ModuleHost
from .settings import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """A module as the App first meets it: what it is, and how to load it."""
    manifest: ModuleManifest
    load: Callable[[], type[Module]]

    @classmethod
    def of(cls, module: type[Module]) -> ModuleSpec:
        """A class already in hand -- a test's, or one registered in code --
        with the manifest its class attributes describe."""
        log.debug("ModuleSpec.of: %s", module.key)

        def loaded() -> type[Module]:
            log.debug("ModuleSpec.of: %s is already loaded", module.key)
            return module
        return cls(ModuleManifest(key=module.key, title=module.title,
                                  summary=module.summary,
                                  default_on=module.default_on,
                                  capabilities=module.capabilities),
                   loaded)


class ModuleScope(ModuleHost):
    """One module's view of its host: only the capabilities its manifest
    declares, and every subscription it made -- all taken back when it is
    switched off, so a module cannot leak a listener even if it forgets."""

    def __init__(self, host: ModuleHost, manifest: ModuleManifest) -> None:
        log.debug("ModuleScope: %s %s", manifest.key, manifest.capabilities)
        self._host, self._manifest = host, manifest
        self._subscriptions: list[tuple[Any, Any]] = []

    def publish(self, event: Any) -> None:
        log.debug("ModuleScope.publish: %s %s", self._manifest.key,
                  type(event).__name__)
        self._host.publish(event)

    def subscribe(self, event_type: Any, handler: Any) -> None:
        self._need("events")
        log.info("ModuleScope.subscribe: %s hears %s", self._manifest.key,
                 event_type.__name__)
        self._host.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def unsubscribe(self, event_type: Any, handler: Any) -> None:
        log.info("ModuleScope.unsubscribe: %s %s", self._manifest.key,
                 event_type.__name__)
        self._host.unsubscribe(event_type, handler)
        if (event_type, handler) in self._subscriptions:
            self._subscriptions.remove((event_type, handler))

    def release(self) -> None:
        """Take back everything the module still holds."""
        log.info("ModuleScope.release: %s, %d subscription(s)",
                 self._manifest.key, len(self._subscriptions))
        for event_type, handler in reversed(self._subscriptions):
            self._host.unsubscribe(event_type, handler)
        self._subscriptions.clear()

    def _need(self, capability: str) -> None:
        """Refuse a capability the manifest does not declare."""
        log.debug("ModuleScope._need: %s %s", self._manifest.key, capability)
        if capability not in self._manifest.capabilities:
            raise ModuleUnavailable(
                f"{self._manifest.title} asked for {capability!r} without "
                "declaring it in its module.json")


class ModuleRunner:
    """The modules an App has, which are on, and which are running."""

    def __init__(self, specs: Mapping[str, ModuleSpec], host: ModuleHost,
                 settings: Settings) -> None:
        log.info("ModuleRunner: %s", sorted(specs))
        self._specs = dict(specs)
        #: Loaded modules only: one is imported the first time it switches on.
        self._modules: dict[str, Module] = {}
        #: Each loaded module's own view of the host.
        self._scopes: dict[str, ModuleScope] = {}
        self._host, self._settings = host, settings
        self._on: set[str] = set()
        self._started: set[str] = set()
        self._session = False
        # Two UIs can switch the same module at once; one change at a time.
        self._lock = threading.RLock()

    def __contains__(self, key: object) -> bool:
        return key in self._specs

    def manifest(self, key: str) -> ModuleManifest:
        """What module *key* is -- known without loading it."""
        log.debug("ModuleRunner.manifest: %s", key)
        return self._specs[key].manifest

    @property
    def loaded(self) -> frozenset[str]:
        """The modules whose code has been imported -- the off ones never are."""
        log.debug("ModuleRunner.loaded: %s", sorted(self._modules))
        return frozenset(self._modules)

    def root(self, key: str) -> Module:
        """Module *key*'s root -- what its Commands run against.  Only ever
        asked for a module that is on (``App.dispatch`` refuses its Commands
        otherwise); ``ModuleUnavailable`` if it is not."""
        log.debug("ModuleRunner.root: %s", key)
        if key not in self._on:
            raise ModuleUnavailable(f"module {key!r} is not on")
        return self._modules[key]

    def is_on(self, key: str) -> bool:
        """Whether module *key* is switched on."""
        log.debug("ModuleRunner.is_on: %s", key)
        return key in self._on

    @property
    def on(self) -> frozenset[str]:
        """The modules switched on, as a snapshot."""
        log.debug("ModuleRunner.on: %s", sorted(self._on))
        return frozenset(self._on)

    def infos(self) -> tuple[ModuleInfo, ...]:
        """Every module as the Modules page lists it."""
        log.debug("ModuleRunner.infos: %d", len(self._specs))
        return tuple(self._info(key) for key in sorted(self._specs))

    def _info(self, key: str) -> ModuleInfo:
        """One tile.  A loaded module can say why it cannot run here; one
        never loaded shows its manifest alone."""
        manifest = self._specs[key].manifest
        module = self._modules.get(key)
        log.debug("ModuleRunner._info: %s loaded=%s", key, module is not None)
        return ModuleInfo(key=key, title=manifest.title, summary=manifest.summary,
                          enabled=key in self._on,
                          problem=module.problem() if module is not None else "",
                          icon=manifest.icon, guide=manifest.guide)

    def enable_saved(self) -> None:
        """Register each module the user has on, or that is on by default.
        One that cannot run here, or fails, stays off; the choice is kept."""
        log.info("ModuleRunner.enable_saved")
        for key, spec in self._specs.items():
            if not self._settings.module_enabled(key, spec.manifest.default_on):
                log.info("enable_saved: %s is off", key)
                continue
            try:
                self._enable(key)
            except ModuleUnavailable as e:
                log.warning("enable_saved: %s stays off: %s", key, e)
        log.info("enable_saved: on: %s", sorted(self._on))

    def start_session(self) -> None:
        """The session has begun: start every module that is on."""
        log.info("ModuleRunner.start_session: %s", sorted(self._on))
        with self._lock:
            self._session = True
            for key in sorted(self._on):
                try:
                    self._start(key)
                except ModuleUnavailable as e:
                    log.warning("start_session: %s stays off: %s", key, e)

    def switch(self, key: str, enabled: bool) -> None:
        """Switch module *key* on or off -- started too, inside a session.
        ``ModuleUnavailable`` when it cannot run here or failed; nothing changed."""
        log.info("ModuleRunner.switch: %s -> %s", key, enabled)
        with self._lock:
            if enabled:
                self._enable(key)
                if self._session:
                    self._start(key)
            else:
                self._disable(key)

    def close(self) -> None:
        """The App is closing: stop and switch off every module.  The user's
        choices stay saved -- the next App switches the same ones back on."""
        log.info("ModuleRunner.close: %s", sorted(self._on))
        with self._lock:
            for key in sorted(self._on):
                try:
                    self._disable(key)
                except ModuleUnavailable as e:
                    log.warning("close: %s did not switch off cleanly: %s", key, e)
            self._session = False

    def _module(self, key: str) -> Module:
        """Module *key*, loaded on first use.  A module whose code cannot be
        loaded cannot run here, and says so."""
        if (module := self._modules.get(key)) is not None:
            return module
        manifest = self._specs[key].manifest
        log.info("ModuleRunner._module: loading %s", key)
        try:
            module = self._specs[key].load()()
        except Exception as e:
            log.exception("_module: %s could not be loaded", key)
            raise ModuleUnavailable(f"{manifest.title} could not be loaded: {e}") from e
        self._modules[key] = module
        self._scopes[key] = module.host = ModuleScope(self._host, manifest)
        return module

    def _enable(self, key: str) -> None:
        """Register *key*; a failure is undone and reported."""
        module = self._module(key)
        log.debug("ModuleRunner._enable: %s", key)
        try:
            module.enable(self._scopes[key])
        except ModuleUnavailable:
            raise
        except Exception as e:
            log.exception("_enable: %s failed while switching on", key)
            self._undo(key, module)
            raise ModuleUnavailable(f"{module.title} did not switch on: {e}") from e
        self._on.add(key)

    def _start(self, key: str) -> None:
        """Start *key*'s work; a failure switches it back off and is reported."""
        module = self._modules[key]
        log.debug("ModuleRunner._start: %s", key)
        try:
            module.start(self._scopes[key])
        except Exception as e:
            log.exception("_start: %s failed to start", key)
            self._undo(key, module)
            self._on.discard(key)
            raise ModuleUnavailable(f"{module.title} did not start: {e}") from e
        self._started.add(key)

    def _disable(self, key: str) -> None:
        """Stop *key* if it runs, then take its features out."""
        module = self._modules[key]
        log.debug("ModuleRunner._disable: %s started=%s", key, key in self._started)
        try:
            if key in self._started:
                module.stop(self._scopes[key])
            module.disable(self._scopes[key])
        except Exception as e:
            log.exception("_disable: %s failed while switching off", key)
            raise ModuleUnavailable(f"{module.title} did not switch off cleanly: {e}") from e
        finally:
            self._scopes[key].release()
            self._started.discard(key)
            self._on.discard(key)

    def _undo(self, key: str, module: Module) -> None:
        """Best effort after a failed switch-on: take back what it registered."""
        log.info("ModuleRunner._undo: %s", key)
        try:
            module.disable(self._scopes[key])
        except Exception:
            log.exception("_undo: %s could not be taken back out", key)
        finally:
            self._scopes[key].release()
