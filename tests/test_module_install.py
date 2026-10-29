"""Installing, updating and removing modules -- the App's ``ModuleInstaller``,
driven through its Commands on a real App (FakePlatform).

Only three things are stand-ins: the trust root (throwaway keys, signed by
``dev/tools/sign_module_index.py``), the web (a dict of URL -> bytes) and the
index address.  The store, the runner, the settings and the imports are the
App's own: an installed module really is unpacked, imported and switched on.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import zipfile
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import module_bundle
import new_module
import sign_module_index as tool

from trcc import ipc
from trcc import modules as module_folders
from trcc.adapters.modules import (
    BundleRefused,
    Ed25519Checker,
    FolderModuleStore,
    read_bundle,
)
from trcc.app import App
from trcc.core.commands import (
    BrowseModules,
    InstallModule,
    InstallModuleFolder,
    ListModules,
    RemoveModule,
    SetDeveloperMode,
    SetModuleEnabled,
)
from trcc.core.errors import HttpFetchError
from trcc.core.events import ModuleChanged, ModuleInstalled, ModuleRemoved
from trcc.core.models import ModuleAsks, ModuleInstall
from trcc.core.ports import HttpFetcher, ModuleDevices
from trcc.services.module_install import ModuleInstaller
from trcc.services.module_trust import ModuleTrust
from trcc.services.settings import config_path

from .conftest import loopback_client

KEY = "greeter"
#: The name the greeter's author signs as -- in its module.json and the index.
AUTHOR = "Ada Author"
INDEX = "https://index.test/modules/"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


class _Web(HttpFetcher):
    """The internet: a URL -> bytes table, every request recorded."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.asked: list[str] = []

    def fetch(self, url: str, timeout_s: float = 30.0, max_bytes: int | None = None,
              allow: Callable[[str], bool] | None = None) -> bytes:
        self.asked.append(url)
        if allow is not None and not allow(url):
            raise HttpFetchError(f"GET {url} is not allowed")
        if (data := self.files.get(url)) is None:
            raise HttpFetchError(f"GET {url} -> HTTP 404")
        if max_bytes is not None and len(data) > max_bytes:
            raise HttpFetchError(f"GET {url} is larger than {max_bytes} bytes")
        return data


class _Devices(ModuleDevices):
    """Writes down what the installer grants and takes back; a grant answers
    *answer* -- "" for reachable, else what stands in the way."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.answer = ""

    def grant(self, key: str, devices: tuple[str, ...]) -> str:
        self.calls.append(("grant", key, *devices))
        return self.answer

    def revoke(self, key: str) -> None:
        self.calls.append(("revoke", key))


class Shop:
    """The maintainer's side: keys, a root, and an index of greeter releases
    -- each one a real module folder, packed and signed."""

    def __init__(self, folder: Path, web: _Web) -> None:
        self._folder, self.web = folder, web
        self.devices = _Devices()
        folder.mkdir()
        for name in ("root", "index", "author", "thief"):
            tool.keygen(folder / name)
        self.trust = ModuleTrust(Ed25519Checker(), [self.raw("root")])
        self.now = datetime.now(timezone.utc)
        root = tool.make_root(1, [self.raw("index")], 365, self.now,
                              self.key("root"), self.trust)
        self.root = self.trust.open_root(root)
        web.files[f"{INDEX}root.json"] = root
        self.signed = tool._signed(Path("/nonexistent"))
        self._source = self._author_copy()

    def key(self, name: str) -> Path:
        return self._folder / f"{name}.key"

    def raw(self, name: str) -> str:
        return tool.public_key(self._folder / f"{name}.pub")

    def _author_copy(self) -> Path:
        """``greeter``, made by ``new_module.py`` as an author would -- its
        Query ``GreeterGreet``, named for it by the skeleton's rename."""
        repo = self._folder / "author"
        return new_module.create(KEY, "Greeter", "Says hello.", repo=repo,
                                 author=AUTHOR)

    def bundle(self, version: str, capabilities: tuple[str, ...] = ("events",),
               hosts: tuple[str, ...] = (), devices: tuple[str, ...] = ()) -> bytes:
        """The greeter at *version*, as its author packs it."""
        manifest = json.loads((self._source / "module.json").read_text(encoding="utf-8"))
        manifest.update(version=version, capabilities=list(capabilities),
                        network_hosts=list(hosts), devices=list(devices))
        (self._source / "module.json").write_text(json.dumps(manifest), encoding="utf-8")
        return module_bundle.pack(self._source)

    def list(self, version: str, *, author: str = "author", min_trcc: str = "9.0.0",
             capabilities: tuple[str, ...] = ("events",), hosts: tuple[str, ...] = (),
             devices: tuple[str, ...] = (), bundle: bytes | None = None, listed_capabilities: tuple[str, ...] | None = None,
             listed_author: str | None = None) -> bytes:
        """Publish *version* in a new index; the bundle served at its URL."""
        data = (bundle if bundle is not None
                else self.bundle(version, capabilities, hosts, devices))
        url = f"https://github.test/greeter-{version}.zip"
        self.web.files[url] = data
        listed = listed_capabilities if listed_capabilities is not None else capabilities
        listed_author = AUTHOR if listed_author is None else listed_author
        entry = {"version": version, "url": url, "commit": COMMIT, "min_trcc": min_trcc,
                 "capabilities": list(listed), "network_hosts": list(hosts),
                 "devices": list(devices),
                 "author_key": self.raw(author), "title": "Greeter",
                 "summary": "Says hello.", "author": listed_author,
                 "author_sig": tool.author_sign(data, KEY, version, self.key(author))}
        # Listed afresh: whether a new author key may replace the old is the
        # app's to judge, on the machine that installed the old one.
        self.signed["modules"].pop(KEY, None)
        self.signed = tool.add(self.signed, KEY, data, entry, self.trust)
        self._publish()
        return data

    def revoke(self, reason: str) -> None:
        self.signed = tool.revoke(self.signed, KEY, reason)
        self._publish()

    def _publish(self) -> None:
        data = tool.reissue(self.signed, self.root, 90, self.now, self.key("index"),
                            self.trust)
        self.signed = json.loads(data)["signed"]
        self.web.files[f"{INDEX}index.json"] = data


@pytest.fixture
def heard(cli_app: App) -> list[Any]:
    events: list[Any] = []
    for event in (ModuleChanged, ModuleInstalled, ModuleRemoved):
        cli_app.events.subscribe(event, events.append)
    return events


@pytest.fixture
def shop(cli_app: App, tmp_path: Path) -> Iterator[Shop]:
    """The App's installer, pointed at a throwaway index; the module search
    path and the greeter's imports put back afterwards."""
    from trcc.ui.cli import main as cli_main

    web = _Web()
    shop = Shop(tmp_path / "shop", web)
    search = list(module_folders.__path__)
    users = set(module_folders._USERS)
    cli_app.module_installer = ModuleInstaller(
        trust=shop.trust, http=web,
        store=FolderModuleStore(cli_app.platform.paths().user_modules_dir()),
        settings=cli_app.settings, runner=cli_app.modules,
        load_spec=cli_app._installed_spec, forget=App._forget_module,
        built_in=App._built_in_module, publish=cli_app.events.publish,
        devices=shop.devices, trcc_version="9.12.0", index_url=INDEX)
    yield shop
    if KEY in cli_app.modules:
        cli_app.modules.remove(KEY)
    App._forget_module(KEY)
    module_folders.__path__[:] = search
    module_folders._USERS.clear()
    module_folders._USERS.update(users)
    # A command the CLI added for the greeter goes with it.
    for group in cli_main._GROUPS.values():
        group.registered_commands[:] = [
            c for c in group.registered_commands
            if not (getattr(c.callback, "__module__", "") or "").startswith(
                f"{module_folders.__name__}.{KEY}.")]
    cli_main._ADDED.discard(KEY)


def _install(app: App, capabilities: tuple[str, ...] = ("events",),
             hosts: tuple[str, ...] = (), devices: tuple[str, ...] = ()) -> Any:
    return app.dispatch(InstallModule(key=KEY, asks=ModuleAsks(
        capabilities=capabilities, network_hosts=hosts, devices=devices)))


def _installed_folder(app: App) -> Path:
    return app.platform.paths().user_modules_dir() / KEY


# ── browsing ──────────────────────────────────────────────────────────────


def test_the_list_shows_what_the_index_offers(cli_app: App, shop: Shop) -> None:
    shop.list("1")
    result = cli_app.dispatch(BrowseModules())
    assert result.ok
    (greeter,) = result.modules
    assert (greeter.key, greeter.title, greeter.version, greeter.status,
            greeter.asks) == (KEY, "Greeter", "1", "available", ModuleAsks(("events",)))


def test_no_index_is_a_refusal_with_a_reason(cli_app: App, shop: Shop) -> None:
    result = cli_app.dispatch(BrowseModules())
    assert not result.ok
    assert result.message.startswith("the module list could not be read: ")


# ── installing ────────────────────────────────────────────────────────────


def test_an_install_unpacks_imports_and_switches_it_on(
        cli_app: App, shop: Shop, heard: list[Any]) -> None:
    shop.list("1")
    result = _install(cli_app)
    assert (result.ok, result.enabled, result.message) == (True, True,
                                                           "Greeter 1 installed")
    assert (_installed_folder(cli_app) / "module.json").is_file()
    assert cli_app.modules.is_on(KEY)
    assert {m.key: m.enabled for m in cli_app.dispatch(ListModules()).modules}[KEY]
    assert ModuleInstalled(key=KEY, version="1") in heard
    record = cli_app.settings.module_install(KEY)
    assert record is not None and (record.version, record.author_key) == (
        "1", shop.raw("author"))
    assert f"trcc.modules.{KEY}.app" in sys.modules


def test_consent_must_be_exactly_what_it_asks_for(cli_app: App, shop: Shop) -> None:
    """Nothing is downloaded or installed until the user accepted what the
    index lists -- not less, not other."""
    shop.list("1", capabilities=("events", "network"), hosts=("api.example.com",))
    asked_before = len(shop.web.asked)
    for capabilities, hosts in ((("events",), ()), (("events", "network"), ()),
                                (("events", "network"), ("evil.example.net",))):
        result = _install(cli_app, capabilities, hosts)
        assert not result.ok
        assert result.message == ("Greeter 1 asks for: events, network; sites: "
                                  "api.example.com -- confirm that first")
    assert not _installed_folder(cli_app).exists()
    assert not any("greeter-1.zip" in url for url in shop.web.asked[asked_before:])
    assert _install(cli_app, ("network", "events"), ("api.example.com",)).ok


def test_consent_names_every_usb_device_and_the_record_keeps_them(
        cli_app: App, shop: Shop) -> None:
    """A device is asked for like a capability: an install that did not
    accept it is refused before anything is downloaded."""
    shop.list("1", capabilities=("events", "hardware"), devices=("0b05:19af",))
    asked_before = len(shop.web.asked)
    for devices in ((), ("0b05:1aa6",)):
        result = _install(cli_app, ("events", "hardware"), devices=devices)
        assert (result.ok, result.message) == (
            False, "Greeter 1 asks for: events, hardware; devices: 0b05:19af -- "
                   "confirm that first")
    assert not any("greeter-1.zip" in url for url in shop.web.asked[asked_before:])
    assert _install(cli_app, ("events", "hardware"), devices=("0b05:19af",)).ok
    record = cli_app.settings.module_install(KEY)
    assert record is not None and record.asks.devices == ("0b05:19af",)


def test_a_module_needing_a_newer_trcc_is_refused(cli_app: App, shop: Shop) -> None:
    shop.list("1", min_trcc="10.0.0")
    result = _install(cli_app)
    assert (result.ok, result.message) == (False, "Greeter needs TRCC 10.0.0 -- this "
                                                  "is 9.12.0")


def test_a_tampered_bundle_is_refused_and_nothing_lands(cli_app: App, shop: Shop) -> None:
    data = shop.list("1")
    shop.web.files["https://github.test/greeter-1.zip"] = data[:-1] + bytes([data[-1] ^ 1])
    result = _install(cli_app)
    assert (result.ok, result.message) == (False, "greeter 1: not the bundle the index "
                                                  "lists")
    assert not _installed_folder(cli_app).exists()


@pytest.mark.parametrize("unlike", ["capabilities", "author"])
def test_a_bundle_unlike_its_index_entry_is_refused(cli_app: App, shop: Shop,
                                                    unlike: str) -> None:
    """Signed and listed, but its own module.json says something the index
    does not -- asks for more, or names another author than the one
    reviewed: refused after unpacking aside, before it lands."""
    if unlike == "author":
        shop.list("1", listed_author="Someone Reviewed")
        result = _install(cli_app)
    else:
        shop.list("1", listed_capabilities=("events", "readings"))
        result = _install(cli_app, ("events", "readings"))
    assert (result.ok, result.message) == (False, "greeter: its module.json is not "
                                                  "what the index lists -- refused")
    store = cli_app.platform.paths().user_modules_dir()
    assert sorted(p.name for p in store.iterdir()) == []      # no staging left behind


@pytest.mark.parametrize("key", ["rgb", "visualizer"])
def test_a_built_in_module_is_never_installed_over_or_removed(
        cli_app: App, shop: Shop, key: str) -> None:
    install = cli_app.dispatch(InstallModule(key=key, asks=ModuleAsks(("events",))))
    remove = cli_app.dispatch(RemoveModule(key=key))
    assert (install.message, remove.message) == (
        f"{key} is built into TRCC -- it is not installed",
        f"{key} is built into TRCC -- it cannot be removed")
    assert shop.web.asked == []


@pytest.mark.parametrize("key", ["../evil", "Greeter", "a", ""])
def test_a_key_that_is_not_a_module_key_is_refused(cli_app: App, shop: Shop,
                                                   key: str) -> None:
    for command in (InstallModule(key=key), RemoveModule(key=key)):
        result = cli_app.dispatch(command)
        assert (result.ok, result.message) == (False, f"{key!r} is not a module key")


# ── updating ──────────────────────────────────────────────────────────────


def test_an_update_replaces_it_and_keeps_it_on(cli_app: App, shop: Shop) -> None:
    shop.list("1")
    assert _install(cli_app).ok
    shop.list("2")
    assert [m.status for m in cli_app.dispatch(BrowseModules()).modules] == ["update"]
    result = _install(cli_app)
    assert (result.ok, result.message, result.enabled) == (True, "Greeter 2 installed",
                                                           True)
    assert json.loads((_installed_folder(cli_app) / "module.json").read_text())[
        "version"] == "2"
    assert [m.status for m in cli_app.dispatch(BrowseModules()).modules] == ["installed"]


def test_an_update_off_stays_off(cli_app: App, shop: Shop) -> None:
    shop.list("1")
    assert _install(cli_app).ok
    assert cli_app.dispatch(SetModuleEnabled(key=KEY, enabled=False)).ok
    shop.list("2")
    result = _install(cli_app)
    assert (result.ok, result.enabled) == (True, False)


def test_an_update_from_another_author_is_refused_and_the_old_runs_on(
        cli_app: App, shop: Shop) -> None:
    """Key continuity: listed and countersigned, but not by the author key
    the installed copy came with -- a stolen index key cannot swap code in."""
    shop.list("1")
    assert _install(cli_app).ok
    shop.list("2", author="thief")
    result = _install(cli_app)
    assert (result.ok, result.message) == (False, "greeter 2: signed by a different "
                                                  "author key than the installed copy")
    assert cli_app.modules.is_on(KEY)
    record = cli_app.settings.module_install(KEY)
    assert record is not None and record.version == "1"


def test_a_failed_update_brings_the_old_copy_back_on(cli_app: App, shop: Shop) -> None:
    shop.list("1")
    assert _install(cli_app).ok
    shop.list("2", listed_capabilities=("events", "readings"))
    assert not _install(cli_app, ("events", "readings")).ok
    assert cli_app.modules.is_on(KEY)
    assert json.loads((_installed_folder(cli_app) / "module.json").read_text())[
        "version"] == "1"


def test_an_older_index_is_refused_as_a_rollback(cli_app: App, shop: Shop) -> None:
    shop.list("1")
    old_index = shop.web.files[f"{INDEX}index.json"]
    shop.list("2")
    assert cli_app.dispatch(BrowseModules()).ok
    shop.web.files[f"{INDEX}index.json"] = old_index
    result = cli_app.dispatch(BrowseModules())
    assert (result.ok, result.message) == (False, "the module list could not be read: "
                                                  "index.json: version 1 is older than "
                                                  "2, already accepted -- a rollback")


# ── taking back, and removing ─────────────────────────────────────────────


def test_a_revoked_module_is_switched_off_and_says_why(
        cli_app: App, shop: Shop, heard: list[Any]) -> None:
    shop.list("1")
    assert _install(cli_app).ok
    shop.revoke("sends your readings away")
    (listed,) = cli_app.dispatch(BrowseModules()).modules
    assert (listed.status, listed.reason) == ("revoked", "sends your readings away")
    assert not cli_app.modules.is_on(KEY)
    assert ModuleChanged(key=KEY, enabled=False,
                         note="taken back: sends your readings away") in heard
    tile = {m.key: m for m in cli_app.dispatch(ListModules()).modules}[KEY]
    assert tile.problem == "taken back: sends your readings away"
    on = cli_app.dispatch(SetModuleEnabled(key=KEY, enabled=True))
    assert (on.ok, on.message) == (False, "Greeter was taken back: sends your "
                                          "readings away")


def test_a_removal_deletes_it_and_forgets_it(cli_app: App, shop: Shop,
                                             heard: list[Any]) -> None:
    shop.list("1")
    assert _install(cli_app).ok
    assert "GreeterGreet" in ipc.COMMAND_TYPES       # on the wire while installed
    result = cli_app.dispatch(RemoveModule(key=KEY))
    assert (result.ok, result.message) == (True, "greeter removed")
    assert not _installed_folder(cli_app).exists()
    assert KEY not in cli_app.modules
    assert cli_app.settings.module_install(KEY) is None
    assert not any(name.startswith(f"trcc.modules.{KEY}") for name in sys.modules)
    # Nothing of it left on the wire either -- CI met a removed module's
    # Command still registered, in a later test in the same process.
    assert "GreeterGreet" not in ipc.COMMAND_TYPES
    assert heard[-2:] == [ModuleChanged(key=KEY, enabled=False), ModuleRemoved(key=KEY)]


_HW = ("events", "hardware")


def test_an_install_unlocks_its_usb_devices_and_a_removal_takes_them_back(
        cli_app: App, shop: Shop) -> None:
    shop.list("1", capabilities=_HW, devices=("0b05:19af",))
    assert _install(cli_app, _HW, devices=("0b05:19af",)).ok
    assert cli_app.dispatch(RemoveModule(key=KEY)).ok
    assert shop.devices.calls == [("grant", KEY, "0b05:19af"), ("revoke", KEY)]


def test_an_update_naming_no_device_takes_the_old_ones_back(
        cli_app: App, shop: Shop) -> None:
    """A grant of nothing is how the port hears "none now" -- the adapter
    takes back what the old release had."""
    shop.list("1", capabilities=_HW, devices=("0b05:19af",))
    assert _install(cli_app, _HW, devices=("0b05:19af",)).ok
    shop.list("2")
    assert _install(cli_app).ok
    assert shop.devices.calls == [("grant", KEY, "0b05:19af"), ("grant", KEY)]


def test_devices_left_locked_install_it_and_say_what_stands_in_the_way(
        cli_app: App, shop: Shop) -> None:
    """A cancelled password is not a failed install: it runs, and says so."""
    shop.devices.answer = "its USB devices stay locked -- the password was not given"
    shop.list("1", capabilities=_HW, devices=("0b05:19af",))
    result = _install(cli_app, _HW, devices=("0b05:19af",))
    assert (result.ok, result.note) == (
        True, "its USB devices stay locked -- the password was not given")
    assert cli_app.settings.module_install(KEY) is not None


def test_a_module_taken_back_loses_its_usb_devices(cli_app: App, shop: Shop) -> None:
    shop.list("1", capabilities=_HW, devices=("0b05:19af",))
    assert _install(cli_app, _HW, devices=("0b05:19af",)).ok
    shop.revoke("reads your keyboard")
    cli_app.dispatch(BrowseModules())
    assert shop.devices.calls == [("grant", KEY, "0b05:19af"), ("revoke", KEY)]


def test_removing_what_is_not_installed_is_refused(cli_app: App, shop: Shop) -> None:
    result = cli_app.dispatch(RemoveModule(key=KEY))
    assert (result.ok, result.message) == (False, "no installed module 'greeter'")


def test_the_daily_check_reads_the_index_once_a_day_and_only_with_a_module(
        cli_app: App, shop: Shop) -> None:
    installer = cli_app.module_installer
    shop.list("1")
    installer.check_daily(shop.now)
    assert shop.web.asked == []                 # nothing installed: nothing to check
    assert _install(cli_app).ok
    asked = len(shop.web.asked)
    installer.check_daily(shop.now)
    assert len(shop.web.asked) == asked         # read minutes ago
    shop.revoke("broken")
    installer.check_daily(datetime.fromtimestamp(
        cli_app.settings.module_index_checked() + 86401, timezone.utc))
    assert len(shop.web.asked) == asked + 2     # root + index
    assert not cli_app.modules.is_on(KEY)


def test_the_records_survive_a_restart(cli_app: App, shop: Shop) -> None:
    shop.list("1")
    assert _install(cli_app).ok
    from trcc.services.settings import Settings

    again = Settings(cli_app.platform.paths())
    assert again.module_install(KEY) == cli_app.settings.module_install(KEY)
    assert again.module_index_seen() == (1, 1)


# ── copied in by hand ─────────────────────────────────────────────────────
#
# Only an install checks what a module is, so a folder put into the user's
# modules folder by hand is listed -- saying so -- and never run: none of its
# code imported, by the App or by any UI.


#: Each part a UI or the App would import, marked if it ever is.
_MARKED = ("core", "ui/cli", "ui/api")


def _copy_in_by_hand(app: App, shop: Shop, key: str = KEY) -> Path:
    """The greeter as *key*, put into the user's modules folder by hand: on
    by default, claiming a background, and leaving a mark if imported."""
    folder = app.platform.paths().user_modules_dir() / key
    shutil.copytree(shop._source, folder, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = json.loads((folder / "module.json").read_text(encoding="utf-8"))
    manifest.update(key=key, default_on=True, backgrounds=["greeter_bg"])
    (folder / "module.json").write_text(json.dumps(manifest), encoding="utf-8")
    for part in _MARKED:
        with (folder / part / "__init__.py").open("a", encoding="utf-8") as f:
            f.write("\nfrom pathlib import Path\nPath(__file__).with_name('RAN').touch()\n")
    return folder


def _ran(folder: Path) -> list[str]:
    """The parts of *folder* that were imported."""
    return sorted(p.parent.relative_to(folder).as_posix() for p in folder.rglob("RAN"))


def _restart(app: App) -> App:
    """The next start of the App on this machine: what it finds on disk."""
    return App(platform=app.platform)


def _tile(app: App, key: str) -> Any:
    return next(m for m in app.dispatch(ListModules()).modules if m.key == key)


def test_a_module_copied_in_by_hand_is_listed_and_never_run(
        cli_app: App, shop: Shop) -> None:
    """Listed, saying why -- never imported, never on (its manifest asks to
    be), claiming nothing.  MUTATION CHECK: drop the refusal in
    ``App._not_installed`` and its core is imported and it switches on."""
    folder = _copy_in_by_hand(cli_app, shop)
    again = _restart(cli_app)
    tile = _tile(again, KEY)
    switched = again.dispatch(SetModuleEnabled(key=KEY, enabled=True))
    assert (tile.origin, tile.allowed, tile.enabled, tile.author) == (
        "by hand", False, False, AUTHOR)
    assert tile.problem == ("greeter was copied into TRCC's modules folder by hand, "
                            "not installed through TRCC -- TRCC does not run it")
    assert (switched.ok, switched.message) == (False, tile.problem)
    assert again.background_owner("greeter_bg") is None
    assert _ran(folder) == []
    assert [n for n in sys.modules if n.startswith(f"trcc.modules.{KEY}")] == []


def test_one_copied_in_by_hand_never_stands_in_for_a_built_in(
        cli_app: App, shop: Shop) -> None:
    """A folder named like TRCC's own module: TRCC's runs, the copy is not
    even read.  The user's folder is searched first, as every process's
    ``UserInterface.start`` does.  MUTATION CHECK: let the last folder of a
    key win in ``modules.folders``, or in ``catalog.specs``'s user folder,
    and the Visualizer's tile is the copy's."""
    folder = _copy_in_by_hand(cli_app, shop, key="visualizer")
    module_folders.use_folder(cli_app.platform.paths().user_modules_dir())
    tile = _tile(_restart(cli_app), "visualizer")
    assert (tile.title, tile.origin, tile.allowed, tile.author) == (
        "Audio Visualizer", "built-in", True, "Lexonight")
    assert _ran(folder) == []


def test_an_installed_module_runs_after_a_restart(cli_app: App, shop: Shop) -> None:
    """Installed through TRCC, it is found again at the next start: listed
    as installed, with its version and its reviewed author, and on."""
    shop.list("1")
    assert _install(cli_app).ok
    App._forget_module(KEY)
    tile = _tile(_restart(cli_app), KEY)
    assert (tile.origin, tile.allowed, tile.enabled, tile.version, tile.author,
            tile.problem) == ("installed", True, True, "1", AUTHOR, "")


def test_the_cli_list_says_where_each_module_came_from(
        cli_runner: Any, cli_app: App, shop: Shop) -> None:
    from trcc.ui.cli.main import app

    shop.list("1")
    assert _install(cli_app).ok
    listed = cli_runner.invoke(app, ["system", "modules", "list"])
    assert listed.exit_code == 0
    lines = listed.output.splitlines()
    at = lines.index("  [on ] greeter          Greeter -- Says hello.")
    assert lines[at + 1] == "        Version 1 · by Ada Author · Installed"


def test_the_cli_asks_the_app_for_an_installed_modules_command(
        cli_runner: Any, cli_app: App, shop: Shop) -> None:
    """Not a command this process has: the CLI asks the App which modules it
    runs, adds theirs, and runs it.  MUTATION CHECK: return the miss in
    ``_AsksTheApp.get_command`` and it is "No such command"."""
    from trcc.ui.cli.main import app

    shop.list("1")
    assert _install(cli_app).ok
    ran = cli_runner.invoke(app, ["greeter", "Ada"])
    assert (ran.exit_code, ran.output) == (0, "Greeter, Ada!\n")


def test_the_cli_never_runs_a_module_copied_in_by_hand(
        cli_runner: Any, cli_app: App, shop: Shop) -> None:
    """The App does not run it, so the CLI never imports it: no such
    command.  MUTATION CHECK: list every folder in ``_module_faces.listed``
    and its CLI part is imported."""
    from trcc.ui.cli.main import app

    folder = _copy_in_by_hand(cli_app, shop)
    cli_app.modules.add(_restart(cli_app).modules._specs[KEY])
    ran = cli_runner.invoke(app, ["greeter", "Ada"])
    assert ran.exit_code == 2
    assert _ran(folder) == []


def test_the_api_serves_what_the_app_runs_and_nothing_copied_in(
        cli_app: App, shop: Shop, tmp_path: Path) -> None:
    """Built with an App, the API serves the routes of the modules that App
    runs -- an installed one -- and none of a copy.  MUTATION CHECK: build
    module routes from every folder and the copy's API part is imported."""
    from trcc.ui.api.main import build_app

    shop.list("1")
    assert _install(cli_app).ok
    copied = _copy_in_by_hand(cli_app, shop, key="copied")
    cli_app.modules.add(_restart(cli_app).modules._specs["copied"])
    with loopback_client(build_app(trcc=cli_app)) as client:
        greeted = client.get("/modules/greeter", params={"name": "Ada"})
    assert (greeted.status_code, greeted.json()["greeting"]) == (200, "Greeter, Ada!")
    assert _ran(copied) == []


# ── the bundle, unpacked ──────────────────────────────────────────────────


def _zip(entries: dict[str, bytes], *, compress: int = zipfile.ZIP_STORED,
         link: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compress) as z:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compress
            if name == link:
                info.external_attr = 0o120777 << 16
            z.writestr(info, data)
    return buffer.getvalue()


@pytest.mark.parametrize(("entries", "kwargs", "message"), [
    ({"greeter/../../.bashrc": b"x"}, {}, "not a path inside the module"),
    ({"/etc/passwd": b"x"}, {}, "not a path inside the module"),
    ({"greeter\\..\\x": b"x"}, {}, "not a path inside the module"),
    ({"C:greeter/x": b"x"}, {}, "not a path inside the module"),
    ({"greeter/module.json": b"{}"}, {"link": "greeter/module.json"}, "a symbolic link"),
    ({"greeter/module.json": b"{}"}, {"compress": zipfile.ZIP_DEFLATED},
     "compressed -- a bundle is stored"),
    ({"greeter/module.json": b"{}", "greeter/run.sh": b"x"}, {}, "not part of a module"),
    ({"greeter/module.json": b"{}", "other/module.json": b"{}"}, {},
     "not one module's folder"),
    ({"Greeter/module.json": b"{}"}, {}, "not a module key"),
])
def test_a_bundle_is_refused_before_a_byte_is_written(
        tmp_path: Path, entries: dict[str, bytes], kwargs: dict[str, Any],
        message: str) -> None:
    store = FolderModuleStore(tmp_path / "store")

    def accept(manifest: Any) -> None:
        pass

    with pytest.raises(BundleRefused) as e:
        store.install(_zip(entries, **kwargs), accept)
    assert message in str(e.value)
    assert sorted(p.name for p in (tmp_path / "store").iterdir()) == []
    assert not (tmp_path / ".bashrc").exists()


def test_the_bundle_reads_its_key_and_manifest(tmp_path: Path) -> None:
    folder = new_module.create(KEY, "Greeter", "Says hello.", repo=tmp_path)
    key, manifest = read_bundle(module_bundle.pack(folder))
    assert (key, manifest["key"], manifest["title"]) == (KEY, KEY, "Greeter")


def test_a_refused_install_leaves_the_old_copy_whole(tmp_path: Path) -> None:
    folder = new_module.create(KEY, "Greeter", "Says hello.", repo=tmp_path / "src")
    store = FolderModuleStore(tmp_path / "store")

    def accept(manifest: Any) -> None:
        pass

    def refuse(manifest: Any) -> None:
        raise RuntimeError("no")

    store.install(module_bundle.pack(folder), accept)
    (folder / "app.py").write_text("# changed\n")
    with pytest.raises(RuntimeError):
        store.install(module_bundle.pack(folder), refuse)
    installed = tmp_path / "store" / KEY / "app.py"
    assert installed.read_text() != "# changed\n"
    assert sorted(p.name for p in (tmp_path / "store").iterdir()) == [KEY]
    shutil.rmtree(tmp_path / "store")


# ── the faces: the CLI and the API send the same Commands ─────────────────


def test_the_cli_says_what_it_may_do_then_installs_and_removes(
        cli_runner: Any, cli_app: App, shop: Shop) -> None:
    from trcc.ui.cli.main import app

    shop.list("1", capabilities=("events", "network"), hosts=("api.example.com",))
    listed = cli_runner.invoke(app, ["system", "modules", "available"])
    declined = cli_runner.invoke(app, ["system", "modules", "install", KEY], input="n\n")
    installed = cli_runner.invoke(app, ["system", "modules", "install", KEY, "--yes"])
    removed = cli_runner.invoke(app, ["system", "modules", "remove", KEY, "--yes"])
    assert (listed.exit_code, declined.exit_code, installed.exit_code,
            removed.exit_code) == (0, 1, 0, 0)
    assert "[available] greeter          1        Greeter -- Says hello." in listed.output
    assert installed.output.splitlines() == [
        "Reviewed by TRCC -- not guaranteed safe.",
        "It may:",
        "  - hears what happens in the app",
        "  - fetches from the internet sites its module.json names, and no others",
        "  - fetches from api.example.com",
        "Greeter 1 installed"]
    assert removed.output.strip() == "greeter removed"
    assert not _installed_folder(cli_app).exists()


def test_the_cli_fails_on_a_module_the_list_does_not_have(
        cli_runner: Any, cli_app: App, shop: Shop) -> None:
    from trcc.ui.cli.main import app

    shop.list("1")
    result = cli_runner.invoke(app, ["system", "modules", "install", "nosuch", "--yes"])
    assert (result.exit_code, result.output.strip()) == (
        1, "no module 'nosuch' in the list -- see: trcc system modules available")


def test_the_api_lists_installs_and_removes(cli_app: App, shop: Shop) -> None:
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    shop.list("1")
    with loopback_client(build_app(trcc=cli_app)) as client:
        listed = client.get("/system/modules/available")
        refused = client.post(f"/system/modules/{KEY}/install",
                              json={"capabilities": []})
        installed = client.post(f"/system/modules/{KEY}/install",
                                json={"capabilities": ["events"]})
        removed = client.delete(f"/system/modules/{KEY}")
        built_in = client.delete("/system/modules/rgb")
    assert [m["status"] for m in listed.json()["modules"]] == ["available"]
    assert (refused.status_code, installed.status_code, removed.status_code,
            built_in.status_code) == (409, 200, 200, 409)
    assert installed.json()["message"] == "Greeter 1 installed"


def test_the_api_install_body_carries_the_devices_accepted(
        cli_app: App, shop: Shop) -> None:
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    shop.list("1", capabilities=("events", "hardware"), devices=("0b05:19af",))
    body = {"capabilities": ["events", "hardware"]}
    with loopback_client(build_app(trcc=cli_app)) as client:
        refused = client.post(f"/system/modules/{KEY}/install", json=body)
        installed = client.post(f"/system/modules/{KEY}/install",
                                json={**body, "devices": ["0b05:19af"]})
    assert (refused.status_code, installed.status_code) == (409, 200)


def test_the_gui_page_installs_updates_and_removes(
        qtbot: Any, cli_app: App, shop: Shop, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gui's own Modules page on the real App: every Command through the
    App, every event back through the window's bridge -- as the gui runs it.
    The user says yes to each question."""
    from trcc.ui.bus_bridge import BusBridge
    from trcc.ui.gui.uc_modules import UCModules

    shop.list("1")
    bridge = BusBridge(cli_app.events)
    page = UCModules(cli_app, bridge)
    qtbot.addWidget(page)
    view = page.view
    monkeypatch.setattr(view, "ask", lambda question, details, confirm: True)

    view.open_catalog()
    qtbot.waitUntil(lambda: KEY in view.actions, timeout=5000)
    assert view.actions[KEY].text() == "Install"
    view.actions[KEY].click()
    qtbot.waitUntil(lambda: view.actions[KEY].text() == "Installed", timeout=5000)
    assert cli_app.modules.is_on(KEY)

    shop.list("2")
    view.open_catalog()
    qtbot.waitUntil(lambda: KEY in view.actions and view.actions[KEY].text() == "Update",
                    timeout=5000)
    view.actions[KEY].click()
    qtbot.waitUntil(lambda: view.actions[KEY].text() == "Installed", timeout=5000)
    record = cli_app.settings.module_install(KEY)
    assert record is not None and record.version == "2"

    view.close_catalog()
    assert KEY in view.removes
    view.removes[KEY].click()
    qtbot.waitUntil(lambda: KEY not in view.switches, timeout=5000)
    assert KEY not in cli_app.modules and not _installed_folder(cli_app).exists()


# ── developer mode: a folder, installed unsigned ─────────────────────────


def _folder(shop: Shop, version: str = "1", capabilities: tuple[str, ...] = ("events",),
            devices: tuple[str, ...] = ()) -> Path:
    """The greeter's source folder, its module.json at *version*, asking for
    *capabilities* (and *devices*) and to be on by default -- what a developer
    points developer mode at."""
    shop.bundle(version, capabilities, devices=devices)
    manifest_path = shop._source / "module.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["default_on"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return shop._source


def test_a_folder_install_never_unlocks_usb_devices_and_says_so(
        cli_app: App, shop: Shop) -> None:
    """Developer mode does no root setup: the developer writes the rule."""
    cli_app.dispatch(SetDeveloperMode(on=True))
    folder = _folder(shop, capabilities=_HW, devices=("0b05:19af",))
    result = cli_app.dispatch(InstallModuleFolder(
        path=folder, asks=ModuleAsks(_HW, devices=("0b05:19af",))))
    assert (result.ok, result.note) == (
        True, "developer mode unlocks no USB devices -- give 0b05:19af a udev "
              "rule of your own")
    assert shop.devices.calls == []


def _install_folder(app: App, folder: Path,
                    capabilities: tuple[str, ...] = ("events",)) -> Any:
    return app.dispatch(InstallModuleFolder(path=folder,
                                            asks=ModuleAsks(capabilities=capabilities)))


def test_a_folder_install_is_refused_without_developer_mode(
        cli_app: App, shop: Shop) -> None:
    assert cli_app.dispatch(ListModules()).developer_mode is False
    refused = _install_folder(cli_app, _folder(shop))
    assert (refused.ok, refused.message) == (
        False, "installing a module from a folder needs developer mode -- "
               "switch it on first")
    assert not _installed_folder(cli_app).exists()
    assert cli_app.settings.module_install(KEY) is None


def test_a_folder_installs_unsigned_and_off_and_stays_off_after_a_restart(
        cli_app: App, shop: Shop) -> None:
    """Its manifest asks to be on by default; installed unsigned it is off,
    there and at the next start, until the user switches it on.  MUTATION
    CHECK: drop the ``set_module_enabled(key, False)`` in ``install_folder``
    and the restart switches it on."""
    assert cli_app.dispatch(SetDeveloperMode(on=True)).ok
    assert cli_app.dispatch(ListModules()).developer_mode is True
    installed = _install_folder(cli_app, _folder(shop))
    assert (installed.ok, installed.key, installed.enabled, installed.message) == (
        True, KEY, False, "Greeter 1 installed unsigned -- off until you switch it on")
    record = cli_app.settings.module_install(KEY)
    assert record is not None
    assert (record.unsigned, record.author_key, record.commit, record.asks) == (
        True, "", "", ModuleAsks(("events",)))
    tile = _tile(cli_app, KEY)
    assert (tile.origin, tile.allowed, tile.enabled) == ("unsigned", True, False)
    App._forget_module(KEY)
    again = _restart(cli_app)
    tile = _tile(again, KEY)
    assert (tile.origin, tile.enabled) == ("unsigned", False)
    assert again.dispatch(SetModuleEnabled(key=KEY, enabled=True)).ok
    assert again.modules.is_on(KEY)


def test_a_folder_install_asks_exactly_what_its_module_json_asks(
        cli_app: App, shop: Shop) -> None:
    cli_app.dispatch(SetDeveloperMode(on=True))
    refused = _install_folder(cli_app, _folder(shop), capabilities=())
    assert (refused.ok, refused.message) == (
        False, "Greeter 1 asks for: events -- confirm that first")
    assert not _installed_folder(cli_app).exists()


def test_a_folder_never_replaces_a_built_in_module(cli_app: App, shop: Shop,
                                                  tmp_path: Path) -> None:
    cli_app.dispatch(SetDeveloperMode(on=True))
    rgb = tmp_path / "rgb"
    shutil.copytree(_folder(shop), rgb, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = json.loads((rgb / "module.json").read_text(encoding="utf-8"))
    manifest["key"] = "rgb"
    (rgb / "module.json").write_text(json.dumps(manifest), encoding="utf-8")
    refused = _install_folder(cli_app, rgb)
    assert (refused.ok, refused.message) == (
        False, "rgb is built into TRCC -- a folder cannot replace it")
    assert _tile(cli_app, "rgb").origin == "built-in"


def test_a_folder_never_replaces_a_signed_install_but_a_signed_one_replaces_it(
        cli_app: App, shop: Shop) -> None:
    """Signed beats unsigned, one way only.  Over an unsigned copy a signed
    install is a first install: on, and no author key pinned from the copy."""
    cli_app.dispatch(SetDeveloperMode(on=True))
    assert _install_folder(cli_app, _folder(shop)).ok
    shop.list("2")
    signed = _install(cli_app)
    assert (signed.ok, signed.enabled) == (True, True)
    record = cli_app.settings.module_install(KEY)
    assert record is not None and (record.unsigned, record.version) == (False, "2")
    assert _tile(cli_app, KEY).origin == "installed"
    refused = _install_folder(cli_app, _folder(shop, "3"))
    assert (refused.ok, refused.message) == (
        False, "greeter is installed from the module index, signed -- remove it "
               "before installing a folder over it")
    record = cli_app.settings.module_install(KEY)
    assert record is not None and (record.unsigned, record.version) == (False, "2")


def test_the_daily_sweep_leaves_an_unsigned_module_alone(
        cli_app: App, shop: Shop) -> None:
    """The index takes back the greeter it lists; the unsigned copy of that
    key is not what it lists, so it runs on.  MUTATION CHECK: drop the skip in
    ``_sweep`` and this switches it off."""
    cli_app.dispatch(SetDeveloperMode(on=True))
    assert _install_folder(cli_app, _folder(shop)).ok
    assert cli_app.dispatch(SetModuleEnabled(key=KEY, enabled=True)).ok
    shop.list("1")
    shop.revoke("sends your readings away")
    (listed,) = cli_app.dispatch(BrowseModules()).modules
    assert listed.status == "revoked"
    assert cli_app.modules.is_on(KEY)
    assert _tile(cli_app, KEY).problem == ""


def test_the_index_shows_an_unsigned_copy_as_not_installed(
        cli_app: App, shop: Shop) -> None:
    cli_app.dispatch(SetDeveloperMode(on=True))
    assert _install_folder(cli_app, _folder(shop)).ok
    shop.list("1")
    (listed,) = cli_app.dispatch(BrowseModules()).modules
    assert (listed.status, listed.installed_version) == ("available", "")


def test_every_start_warns_which_unsigned_modules_are_on(
        cli_app: App, shop: Shop, caplog: pytest.LogCaptureFixture) -> None:
    cli_app.dispatch(SetDeveloperMode(on=True))
    assert _install_folder(cli_app, _folder(shop)).ok
    assert cli_app.dispatch(SetModuleEnabled(key=KEY, enabled=True)).ok
    App._forget_module(KEY)
    with caplog.at_level("WARNING", logger="trcc.app"):
        _restart(cli_app)
    assert ("unsigned modules ON (installed from a folder in developer mode, "
            "never reviewed): greeter") in caplog.messages


def test_developer_mode_off_keeps_what_it_installed(cli_app: App, shop: Shop) -> None:
    cli_app.dispatch(SetDeveloperMode(on=True))
    assert _install_folder(cli_app, _folder(shop)).ok
    assert cli_app.dispatch(SetDeveloperMode(on=False)).ok
    assert _tile(cli_app, KEY).origin == "unsigned"
    refused = _install_folder(cli_app, _folder(shop, "2"))
    assert refused.ok is False
    assert cli_app.dispatch(RemoveModule(key=KEY)).ok
    assert not _installed_folder(cli_app).exists()


def test_a_record_from_before_developer_mode_reads_as_signed(cli_app: App) -> None:
    """``trcc.json`` written by an older TRCC has no ``unsigned``."""
    cli_app.settings.app.module_installs[KEY] = {
        "key": KEY, "version": "1", "sha256": "ab" * 32, "author_key": "k",
        "commit": COMMIT, "capabilities": ["events"], "network_hosts": []}
    record = cli_app.settings.module_install(KEY)
    assert record is not None and record.unsigned is False
    cli_app.settings.app.module_installs.pop(KEY)


def test_a_record_is_saved_flat_as_it_always_was(cli_app: App) -> None:
    """``ModuleAsks`` changed no file: a record keeps its asks beside its
    other fields, as every TRCC before wrote and read it -- and it comes back
    whole after a restart."""
    record = ModuleInstall(key=KEY, version="1", sha256="ab" * 32, author_key="k",
                           commit=COMMIT, asks=ModuleAsks(("events", "network", "hardware"),
                                                          ("api.example.com",),
                                                          ("0b05:19af",)))
    cli_app.settings.set_module_install(KEY, record)
    saved = json.loads(config_path(cli_app.platform.paths()).read_text())
    assert saved["app"]["module_installs"][KEY] == {
        "key": KEY, "version": "1", "sha256": "ab" * 32, "author_key": "k",
        "commit": COMMIT, "unsigned": False,
        "capabilities": ["events", "network", "hardware"],
        "network_hosts": ["api.example.com"], "devices": ["0b05:19af"]}
    assert _restart(cli_app).settings.module_install(KEY) == record
    cli_app.settings.set_module_install(KEY, None)


def test_the_cli_switches_developer_mode_and_installs_a_folder(
        cli_runner: Any, cli_app: App, shop: Shop) -> None:
    from trcc.ui.cli.main import app

    folder = _folder(shop)
    refused = cli_runner.invoke(app, ["system", "modules", "install-folder", str(folder),
                                      "--yes"])
    switched = cli_runner.invoke(app, ["system", "modules", "developer", "on"])
    declined = cli_runner.invoke(app, ["system", "modules", "install-folder", str(folder)],
                                 input="n\n")
    installed = cli_runner.invoke(app, ["system", "modules", "install-folder", str(folder),
                                        "--yes"])
    listed = cli_runner.invoke(app, ["system", "modules", "list"])
    assert (refused.exit_code, switched.exit_code, declined.exit_code,
            installed.exit_code, listed.exit_code) == (1, 0, 1, 0, 0)
    assert refused.output.splitlines()[-1] == (
        "installing a module from a folder needs developer mode -- switch it on first")
    assert switched.output.strip() == "developer mode on"
    assert installed.output.splitlines() == [
        "Unsigned: nobody reviewed it. Install only code you trust.",
        "It may:",
        "  - hears what happens in the app",
        "Greeter 1 installed unsigned -- off until you switch it on"]
    lines = listed.output.splitlines()
    at = lines.index("  [off] greeter          Greeter -- Says hello.")
    assert lines[at + 1] == ("        Version 1 · by Ada Author · Unsigned -- never "
                             "reviewed")
