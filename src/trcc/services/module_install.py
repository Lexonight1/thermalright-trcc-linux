"""Installing, updating and removing the modules the index lists -- in the App.

Every UI's "Get modules" is these: ``BrowseModules``, ``InstallModule``,
``RemoveModule`` dispatch here, so no window installs anything itself.

An install is the trust design's chain, in order, and stops at the first
link that does not hold (memory: project_modules_trust_design):

1. the root and the index, verified (``ModuleTrust``) -- never older than the
   newest this machine accepted, and current;
2. the release needs no newer TRCC than this one;
3. **the user's consent**: what they accepted is exactly what the index lists
   -- capabilities and sites -- so an update that asks for more asks again;
4. the bundle, downloaded and checked: the listed bytes, not taken back,
   signed by its author -- and, for an update, by the author key it was
   installed with;
5. unpacked aside, its own ``module.json`` matching the index, and only then
   in place of the old copy (``ModuleStore``) -- a failure leaves the old one.

Built-in modules are never installed over, nor removed.  An installed module
the index later takes back is switched off, and says why on its tile.

**Developer mode** adds one more way in, ``adb install``'s: a module folder the
user picks, packed and put through the same store (``install_folder``).  It is
the trust chain with the signature taken out, and says so everywhere -- an
``unsigned`` record and origin, off after install, never updated nor swept,
refused over a built-in or a signed install.  The consent link stays: what the
user accepted is exactly what its ``module.json`` asks for.  A signed install
of the same key replaces an unsigned one, as a first install would.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..core._version import parse_version
from ..core.errors import HttpFetchError, ModuleUnavailable, UntrustedModule
from ..core.events import Event, ModuleChanged, ModuleInstalled, ModuleRemoved
from ..core.models import (
    MODULE_BUNDLE_MAX_BYTES,
    MODULE_INDEX_CHECK_S,
    MODULE_INDEX_MAX_BYTES,
    MODULE_INDEX_URL,
    MODULE_KEY,
    MODULE_ROOT_MAX_BYTES,
    AvailableModule,
    ModuleAsks,
    ModuleIndex,
    ModuleInstall,
    ModuleManifest,
    ModuleRelease,
)
from ..core.ports import HttpFetcher, ModuleStore
from .module_trust import ModuleTrust
from .modules import ModuleRunner, ModuleSpec
from .settings import Settings

log = logging.getLogger(__name__)

#: How long one index or bundle download may take.
_FETCH_S = 60.0


def _a_key(key: str) -> None:
    """Refuse a *key* that is not a module's -- it names a folder."""
    log.debug("module_install._a_key: %r", key)
    if not MODULE_KEY.fullmatch(key):
        raise ModuleUnavailable(f"{key!r} is not a module key")


def _https(url: str) -> bool:
    """A bundle's redirects stay on https -- its bytes are checked after."""
    ok = url.startswith("https://")
    log.debug("module_install._https: %s -> %s", url, ok)
    return ok


def _as_written(manifest: ModuleManifest) -> ModuleManifest:
    """*manifest* as its ``module.json`` says it: the icon a file name, not
    the path the reader resolved it to."""
    log.debug("_as_written: %s", manifest.key)
    return dataclasses.replace(manifest, icon=Path(manifest.icon).name if manifest.icon else "")


class ModuleInstaller:
    """The App's installer.  One change at a time: two UIs installing at once
    take turns."""

    def __init__(self, *, trust: ModuleTrust, http: HttpFetcher, store: ModuleStore,
                 settings: Settings, runner: ModuleRunner,
                 load_spec: Callable[[str], ModuleSpec], forget: Callable[[str], None],
                 built_in: Callable[[str], bool], publish: Callable[[Event], None],
                 trcc_version: str, index_url: str = MODULE_INDEX_URL) -> None:
        log.info("ModuleInstaller: index %s, store %s", index_url, store.root())
        self._trust, self._http, self._store = trust, http, store
        self._settings, self._runner = settings, runner
        self._load_spec, self._forget, self._built_in = load_spec, forget, built_in
        self._publish, self._url, self._version = publish, index_url, trcc_version
        self._lock = threading.Lock()

    # ── the index ─────────────────────────────────────────────────────────

    def index(self, now: datetime) -> ModuleIndex:
        """The index, fetched and verified -- and what it takes back, taken
        back.  ``UntrustedModule`` or ``HttpFetchError`` when it is not to be had."""
        log.info("ModuleInstaller.index: %s", self._url)
        root_seen, index_seen = self._settings.module_index_seen()
        root = self._trust.open_root(self._http.fetch(
            f"{self._url}root.json", timeout_s=_FETCH_S,
            max_bytes=MODULE_ROOT_MAX_BYTES, allow=_https), seen=root_seen)
        index = self._trust.open_index(self._http.fetch(
            f"{self._url}index.json", timeout_s=_FETCH_S,
            max_bytes=MODULE_INDEX_MAX_BYTES, allow=_https), root, seen=index_seen)
        self._settings.set_module_index_seen(root.version, index.version,
                                             now.timestamp())
        self._sweep(index)
        return index

    def check_daily(self, now: datetime) -> None:
        """Read the index if a day has passed since the last time, so a module
        taken back stops without the user asking.  A failure is logged; the
        next day tries again."""
        since = now.timestamp() - self._settings.module_index_checked()
        log.info("ModuleInstaller.check_daily: %.0f s since the last read", since)
        if since < MODULE_INDEX_CHECK_S or not self._settings.installed_modules():
            return
        try:
            with self._lock:
                self.index(now)
        except (UntrustedModule, HttpFetchError) as e:
            log.warning("ModuleInstaller.check_daily: no index today -- %s", e)

    def _sweep(self, index: ModuleIndex) -> None:
        """Take back each installed module *index* revokes; give back one it
        no longer does (it stays off until the user switches it on)."""
        for key in self._settings.installed_modules():
            record = self._settings.module_install(key)
            if record is not None and record.unsigned:
                # Nothing of it is in the index to take back -- a revocation of
                # the same key, or of an empty hash, is about another module.
                log.debug("ModuleInstaller._sweep: %s is unsigned -- skipped", key)
                continue
            reason = (self._trust.revoked(index, key, record.sha256) or ""
                      if record is not None else "")
            log.debug("ModuleInstaller._sweep: %s -> %r", key, reason)
            if reason == self._settings.module_revoked(key):
                continue
            self._settings.set_module_revoked(key, reason)
            if reason and self._runner.is_on(key):
                log.warning("ModuleInstaller._sweep: %s taken back -- %s", key, reason)
                self._runner.switch(key, False)
                self._publish(ModuleChanged(key=key, enabled=False,
                                            note=f"taken back: {reason}"))

    # ── browsing ──────────────────────────────────────────────────────────

    def browse(self, now: datetime) -> tuple[AvailableModule, ...]:
        """Every module the index lists, and where this machine stands with it."""
        log.info("ModuleInstaller.browse")
        with self._lock:
            index = self.index(now)
        found = tuple(self._available(index, r)
                      for _, r in sorted(index.releases.items()))
        log.info("ModuleInstaller.browse: %s", {m.key: m.status for m in found})
        return found

    def _available(self, index: ModuleIndex, release: ModuleRelease) -> AvailableModule:
        record = self._signed_install(release.key)
        reason = self._trust.revoked(index, release.key,
                                     record.sha256 if record else release.sha256) or ""
        status = ("revoked" if reason else "available" if record is None
                  else "installed" if record.version == release.version else "update")
        log.debug("ModuleInstaller._available: %s -> %s", release.key, status)
        return AvailableModule(
            key=release.key, title=release.title or release.key,
            summary=release.summary, version=release.version, status=status,
            asks=release.asks, installed_version=record.version if record else "", reason=reason,
            author=release.author)

    # ── installing ────────────────────────────────────────────────────────

    def install(self, key: str, accepted: ModuleAsks,
                now: datetime) -> tuple[ModuleInstall, str]:
        """Install module *key*, or update it -- the record, and what switching
        it on had to say ("" for nothing).  Refused, with why, at the first
        link of the chain that does not hold (the module docstring)."""
        log.info("ModuleInstaller.install: %s", key)
        _a_key(key)
        if self._built_in(key):
            raise ModuleUnavailable(f"{key} is built into TRCC -- it is not installed")
        with self._lock:
            index = self.index(now)
            release = index.releases.get(key)
            if release is None:
                raise UntrustedModule(f"{key}: not in the module index")
            self._may_install(release, accepted)
            bundle = self._http.fetch(release.url, timeout_s=_FETCH_S,
                                      max_bytes=release.size, allow=_https)
            old = self._settings.module_install(key)
            signed = self._signed_install(key)
            self._trust.check_bundle(index, key, bundle, now,
                                     pinned_author=signed.author_key if signed else None)
            record = ModuleInstall(key=key, version=release.version, sha256=release.sha256,
                                   author_key=release.author_key, commit=release.commit,
                                   asks=release.asks)
            # Over an unsigned copy it is a first install: on, nothing pinned.
            return self._put_in_place(record, bundle, self._matches_release(release),
                                      switch_on=signed is None or self._runner.is_on(key),
                                      restore=old is not None)

    def install_folder(self, folder: Path, accepted: ModuleAsks) -> ModuleInstall:
        """Install the module in *folder*, unsigned -- developer mode only.
        Refused, before anything is touched, over a built-in or a signed
        install, or when what the user accepted is not exactly what its
        ``module.json`` asks for.  Off after install, whatever its default."""
        log.info("ModuleInstaller.install_folder: %s", folder)
        if not self._settings.app.developer_mode:
            raise ModuleUnavailable("installing a module from a folder needs "
                                    "developer mode -- switch it on first")
        bundle, manifest = self._store.pack(folder)
        key = manifest.key
        if self._built_in(key):
            raise ModuleUnavailable(f"{key} is built into TRCC -- a folder cannot "
                                    "replace it")
        if len(bundle) > MODULE_BUNDLE_MAX_BYTES:
            raise ModuleUnavailable(f"{key}: {len(bundle)} bytes -- larger than a "
                                    "module may be")
        if not accepted.matches(manifest.asks):
            raise ModuleUnavailable(f"{manifest.title} {manifest.version} asks for: "
                                    f"{manifest.asks.describe()} -- confirm that first")
        with self._lock:
            old = self._settings.module_install(key)
            if old is not None and not old.unsigned:
                raise ModuleUnavailable(f"{key} is installed from the module index, "
                                        "signed -- remove it before installing a "
                                        "folder over it")
            record = ModuleInstall(key=key, version=manifest.version,
                                   sha256=hashlib.sha256(bundle).hexdigest(),
                                   author_key="", commit="", asks=manifest.asks,
                                   unsigned=True)
            was_on = self._runner.is_on(key)

            def matches(unpacked: ModuleManifest) -> None:
                """What was unpacked is what was packed -- its icon read where
                each copy sits, so compared by name."""
                if _as_written(unpacked) != _as_written(manifest):
                    log.warning("matches: packed %s, unpacked %s", manifest, unpacked)
                    raise UntrustedModule(f"{key}: the bundle is not the folder it "
                                          "was packed from -- refused")

            self._put_in_place(record, bundle, matches, switch_on=False,
                               restore=old is not None)
            # Off, remembered: a default_on module must not come on at the
            # next start without the user switching it on.
            self._settings.set_module_enabled(key, False)
            if was_on:
                self._publish(ModuleChanged(key=key, enabled=False))
            log.info("ModuleInstaller.install_folder: %s %s installed UNSIGNED "
                     "from %s -- off until switched on", key, manifest.version, folder)
            return record

    def read_folder(self, folder: Path) -> AvailableModule:
        """The module in *folder* as its permission screen shows it -- packed
        and read as ``install_folder`` will, so what the user is asked about
        is what installs."""
        log.info("ModuleInstaller.read_folder: %s", folder)
        _, manifest = self._store.pack(folder)
        return AvailableModule(key=manifest.key, title=manifest.title,
                               summary=manifest.summary, version=manifest.version,
                               status="available", asks=manifest.asks,
                               author=manifest.author)

    def _signed_install(self, key: str) -> ModuleInstall | None:
        """Module *key*'s record when it came from the index -- None for none,
        or for one installed unsigned from a folder."""
        record = self._settings.module_install(key)
        signed = record if record is not None and not record.unsigned else None
        log.debug("ModuleInstaller._signed_install: %s -> %s", key, signed is not None)
        return signed

    def _may_install(self, release: ModuleRelease, accepted: ModuleAsks) -> None:
        """Refuse a release this TRCC is too old for, one too large, or one
        asking for more -- or other -- than the user accepted."""
        log.debug("ModuleInstaller._may_install: %s", release.key)
        if parse_version(release.min_trcc) > parse_version(self._version):
            raise ModuleUnavailable(f"{release.title or release.key} needs TRCC "
                                    f"{release.min_trcc} -- this is {self._version}")
        if release.size > MODULE_BUNDLE_MAX_BYTES:
            raise ModuleUnavailable(f"{release.key}: {release.size} bytes -- larger "
                                    "than a module may be")
        if not accepted.matches(release.asks):
            raise ModuleUnavailable(f"{release.title or release.key} {release.version} "
                                    f"asks for: {release.asks.describe()} -- confirm "
                                    "that first")

    @staticmethod
    def _matches_release(release: ModuleRelease) -> Callable[[ModuleManifest], None]:
        """The check that a bundle's own ``module.json`` says what the index
        says -- its author too, so the name a tile shows is the one reviewed."""
        log.debug("ModuleInstaller._matches_release: %s %s", release.key, release.version)

        def matches(manifest: ModuleManifest) -> None:
            said = (manifest.key, manifest.version, manifest.author)
            listed = (release.key, release.version, release.author)
            log.debug("matches: %s vs %s", said, listed)
            if said != listed or not manifest.asks.matches(release.asks):
                raise UntrustedModule(f"{release.key}: its module.json is not what "
                                      "the index lists -- refused")
        return matches

    def _put_in_place(self, record: ModuleInstall, bundle: bytes,
                      matches: Callable[[ModuleManifest], None], *, switch_on: bool,
                      restore: bool) -> tuple[ModuleInstall, str]:
        """The *bundle*, passing *matches*, in place of any old copy and
        remembered as *record* -- switched on when *switch_on*.  A failure
        brings the old copy back as it was, when there is one (*restore*)."""
        key = record.key
        was_on = self._runner.is_on(key)
        log.info("ModuleInstaller._put_in_place: %s %s (unsigned=%s, on after=%s)",
                 key, record.version, record.unsigned, switch_on)
        self._runner.remove(key)
        self._forget(key)
        try:
            self._store.install(bundle, matches)
        except Exception:
            log.exception("ModuleInstaller._put_in_place: %s refused or failed", key)
            if restore:
                self._restore(key, was_on)
            raise
        self._settings.set_module_install(key, record)
        self._settings.set_module_revoked(key, "")
        self._runner.add(self._load_spec(key))
        note = self._switch_on(key) if switch_on else ""
        self._publish(ModuleInstalled(key=key, version=record.version))
        return record, note

    def _restore(self, key: str, was_on: bool) -> None:
        """The old copy, still on disk, back in the App as it was."""
        log.info("ModuleInstaller._restore: %s on=%s", key, was_on)
        self._runner.add(self._load_spec(key))
        if was_on:
            self._switch_on(key)

    def _switch_on(self, key: str) -> str:
        """Switch *key* on, remembered; "" or why it cannot run here."""
        log.info("ModuleInstaller._switch_on: %s", key)
        try:
            self._runner.switch(key, True)
        except ModuleUnavailable as e:
            log.warning("ModuleInstaller._switch_on: %s stays off -- %s", key, e)
            return str(e)
        self._settings.set_module_enabled(key, True)
        self._publish(ModuleChanged(key=key, enabled=True))
        return ""

    # ── removing ──────────────────────────────────────────────────────────

    def remove(self, key: str) -> None:
        """Remove installed module *key*: off, its files deleted, forgotten.
        Themes made with it stay -- they say which module they need."""
        log.info("ModuleInstaller.remove: %s", key)
        _a_key(key)
        if self._built_in(key):
            raise ModuleUnavailable(f"{key} is built into TRCC -- it cannot be removed")
        with self._lock:
            if self._settings.module_install(key) is None:
                raise ModuleUnavailable(f"no installed module {key!r}")
            was_on = self._runner.is_on(key)
            self._runner.remove(key)
            self._forget(key)
            self._store.remove(key)
            self._settings.set_module_install(key, None)
            self._settings.set_module_revoked(key, "")
            if was_on:
                self._publish(ModuleChanged(key=key, enabled=False))
            self._publish(ModuleRemoved(key=key))
