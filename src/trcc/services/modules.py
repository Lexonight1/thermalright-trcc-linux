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
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from ..core.errors import ModuleUnavailable
from ..core.models import ModuleInfo, ModuleManifest, RawFrame
from ..core.ports import (
    BackgroundKind,
    BackgroundPort,
    FilesPort,
    FrameSource,
    GlSurface,
    GpuPort,
    Module,
    ModuleHost,
    ModuleServices,
    PanelsPort,
    SettingsPort,
    SoundInput,
    SoundPort,
    ThemesPort,
)
from .settings import Settings

log = logging.getLogger(__name__)

_C = TypeVar("_C", bound=type)


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


class _Panels(PanelsPort):
    def __init__(self, services: ModuleServices) -> None:
        log.debug("_Panels.__init__")
        self._services = services

    def attached(self) -> tuple[str, ...]:
        log.debug("_Panels.attached")
        return self._services.panel_keys()

    def size(self, key: str) -> tuple[int, int] | None:
        log.debug("_Panels.size")
        return self._services.panel_size(key)


class _Background(BackgroundPort):
    def __init__(self, services: ModuleServices, module: str) -> None:
        log.debug("_Background.__init__: %s", module)
        self._services, self._module = services, module

    def show(self, key: str, source: FrameSource, fps: float) -> None:
        log.info("background.show: %s on %s", self._module, key)
        self._services.show_source(self._module, key, source, fps)

    def end(self, key: str, keep_choice: bool = False) -> None:
        log.info("background.end: %s on %s keep=%s", self._module, key, keep_choice)
        self._services.end_source(key, keep_choice)

    def source(self, key: str) -> FrameSource | None:
        log.debug("_Background.source")
        return self._services.source(key)

    def draws(self, key: str) -> bool:
        log.debug("_Background.draws")
        return self._services.panel_source(key) == self._module


class _Settings(SettingsPort):
    def __init__(self, services: ModuleServices, module: str) -> None:
        log.debug("_Settings.__init__: %s", module)
        self._services, self._module = services, module

    def app(self) -> dict[str, Any]:
        log.debug("_Settings.app")
        return self._services.module_settings(self._module)

    def save_app(self, data: Mapping[str, Any]) -> None:
        log.debug("_Settings.save_app")
        self._services.save_module_settings(self._module, data)

    def panel(self, key: str) -> dict[str, Any]:
        log.debug("_Settings.panel")
        return self._services.panel_settings(self._module, key)

    def save_panel(self, key: str, data: Mapping[str, Any]) -> None:
        log.debug("_Settings.save_panel")
        self._services.save_panel_settings(self._module, key, data)


class _Themes(ThemesPort):
    def __init__(self, scope: ModuleScope) -> None:
        log.debug("_Themes.__init__")
        self._scope = scope

    def add_kind(self, kind: BackgroundKind) -> None:
        log.debug("_Themes.add_kind")
        self._scope._add_kind(kind)


class _Sound(SoundPort):
    def __init__(self, services: ModuleServices) -> None:
        log.debug("_Sound.__init__")
        self._services = services

    def input(self) -> SoundInput:
        log.debug("_Sound.input")
        return self._services.sound_input()

    def problem(self) -> str:
        log.debug("_Sound.problem")
        return self._services.sound_problem()

    def worker_thread(self) -> AbstractContextManager[None]:
        log.debug("_Sound.worker_thread")
        return self._services.worker_thread()


class _Gpu(GpuPort):
    def __init__(self, services: ModuleServices) -> None:
        log.debug("_Gpu.__init__")
        self._services = services

    def surface(self) -> type[GlSurface] | None:
        log.debug("_Gpu.surface")
        return self._services.gl_surface()

    def substitute(self, cls: _C) -> _C:
        log.debug("_Gpu.substitute")
        return self._services.substitute(cls)


class _Files(FilesPort):
    def __init__(self, services: ModuleServices) -> None:
        log.debug("_Files.__init__")
        self._services = services

    def data_root(self) -> Path:
        log.debug("_Files.data_root")
        return self._services.data_root()

    def encode_png(self, frame: RawFrame) -> bytes:
        log.debug("_Files.encode_png")
        return self._services.encode_png(frame)


class ModuleScope(ModuleHost):
    """One module's view of the App: only the capabilities its manifest
    declares, with its own key bound in -- and everything it still holds is
    taken back when it is switched off (its subscriptions, its background
    kinds, the panels it draws, each keeping its choice), so a module cannot
    leak even if it forgets."""

    def __init__(self, services: ModuleServices, manifest: ModuleManifest) -> None:
        log.debug("ModuleScope: %s %s", manifest.key, manifest.capabilities)
        self._services, self._manifest = services, manifest
        self._key = manifest.key
        self._subscriptions: list[tuple[Any, Any]] = []
        self._kinds: list[str] = []
        self._ports: dict[str, Any] = {
            "panels": _Panels(services),
            "background": _Background(services, self._key),
            "settings": _Settings(services, self._key),
            "themes": _Themes(self),
            "sound": _Sound(services),
            "gpu": _Gpu(services),
            "files": _Files(services),
        }

    def publish(self, event: Any) -> None:
        log.debug("ModuleScope.publish: %s %s", self._key, type(event).__name__)
        self._services.publish(event)

    def subscribe(self, event_type: Any, handler: Any) -> None:
        self._need("events")
        log.info("ModuleScope.subscribe: %s hears %s", self._key,
                 event_type.__name__)
        self._services.subscribe(event_type, handler)
        self._subscriptions.append((event_type, handler))

    def unsubscribe(self, event_type: Any, handler: Any) -> None:
        log.info("ModuleScope.unsubscribe: %s %s", self._key, event_type.__name__)
        self._services.unsubscribe(event_type, handler)
        if (event_type, handler) in self._subscriptions:
            self._subscriptions.remove((event_type, handler))

    @property
    def panels(self) -> PanelsPort:
        log.debug("ModuleScope.panels")
        return self._port("panels")

    @property
    def background(self) -> BackgroundPort:
        log.debug("ModuleScope.background")
        return self._port("background")

    @property
    def settings(self) -> SettingsPort:
        log.debug("ModuleScope.settings")
        return self._port("settings")

    @property
    def themes(self) -> ThemesPort:
        log.debug("ModuleScope.themes")
        return self._port("themes")

    @property
    def sound(self) -> SoundPort:
        log.debug("ModuleScope.sound")
        return self._port("sound")

    @property
    def gpu(self) -> GpuPort:
        log.debug("ModuleScope.gpu")
        return self._port("gpu")

    @property
    def files(self) -> FilesPort:
        log.debug("ModuleScope.files")
        return self._port("files")

    def _port(self, capability: str) -> Any:
        """The port for *capability*, if the manifest declares it."""
        log.debug("ModuleScope._port")
        self._need(capability)
        return self._ports[capability]

    def _add_kind(self, kind: BackgroundKind) -> None:
        """A background kind the module draws -- one its manifest declares."""
        if kind.prefix not in self._manifest.backgrounds:
            raise ModuleUnavailable(
                f"{self._manifest.title} draws {kind.prefix!r} backgrounds "
                "without declaring them in its module.json")
        log.info("ModuleScope._add_kind: %s %s", self._key, kind.prefix)
        self._services.add_background_kind(self._key, kind)
        self._kinds.append(kind.prefix)

    def resumed(self) -> None:
        """The module has started: its panels draw again."""
        log.info("ModuleScope.resumed: %s", self._key)
        self._services.resume_backgrounds(self._key)

    def release(self, end_sources: bool = True) -> None:
        """Take back everything the module still holds.  *end_sources*: stop
        the panels it draws, each keeping its choice -- not while the App is
        closing, which stops every panel anyway."""
        log.info("ModuleScope.release: %s, %d subscription(s), kinds %s",
                 self._key, len(self._subscriptions), self._kinds)
        for event_type, handler in reversed(self._subscriptions):
            self._services.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        for prefix in self._kinds:
            self._services.remove_background_kind(prefix)
        self._kinds.clear()
        if end_sources:
            for key in self._services.panel_keys():
                if (self._services.panel_source(key) == self._key
                        and self._services.source(key) is not None):
                    self._services.end_source(key, keep_choice=True)

    def _need(self, capability: str) -> None:
        """Refuse a capability the manifest does not declare."""
        log.debug("ModuleScope._need: %s %s", self._key, capability)
        if capability not in self._manifest.capabilities:
            raise ModuleUnavailable(
                f"{self._manifest.title} asked for {capability!r} without "
                "declaring it in its module.json")


class ModuleRunner:
    """The modules an App has, which are on, and which are running."""

    def __init__(self, specs: Mapping[str, ModuleSpec], host: ModuleServices,
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

    def __iter__(self) -> Iterator[str]:
        """Every module's key, on or off."""
        return iter(sorted(self._specs))

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
                    self._disable(key, end_sources=False)
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
        try:
            self._scopes[key].resumed()
        except Exception:
            # Its panels stay as they are; the module itself is running.
            log.exception("_start: %s's panels did not resume", key)

    def _disable(self, key: str, end_sources: bool = True) -> None:
        """Stop *key* if it runs, then take its features out -- and, unless
        the App is closing, end the panels it draws (each keeps its choice)."""
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
            self._scopes[key].release(end_sources)
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
