"""The Modules page's "Get modules" -- the shared ``ModulesView``, as both
windows show it: the index's list, the permission screen, Install, Update,
Remove, and following what other UIs do.

The App is a stand-in that answers each Command from a table and records what
it was sent, so a test reads exactly what the window asked for.  The user's
answer to a question is the test's (``ModulesView.ask``).
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trcc.core.commands import (
    BrowseModules,
    Command,
    InstallModule,
    InstallModuleFolder,
    ListModules,
    ModuleIcon,
    ReadModuleFolder,
    RemoveModule,
    SetDeveloperMode,
    SetModuleEnabled,
)
from trcc.core.events import DeveloperModeChanged, EventBus, ModuleInstalled, ModuleRemoved
from trcc.core.models import MODULE_CAPABILITIES, AvailableModule, ModuleAsks, ModuleInfo
from trcc.core.ports import CommandBus
from trcc.core.results import (
    ModuleCatalogResult,
    ModuleFolderResult,
    ModuleIconResult,
    ModuleResult,
    ModulesResult,
    Result,
)
from trcc.ui.presentation.module_catalog import REOPEN

_LISTED = (
    AvailableModule(key="weather", title="Weather", summary="The forecast.",
                    version="3", status="available",
                    asks=ModuleAsks(("events", "network"), ("api.open-meteo.com",))),
    AvailableModule(key="ticker", title="Ticker", summary="Prices.", version="2",
                    status="update", installed_version="1"),
    AvailableModule(key="clock", title="Clock", summary="Time.", version="1",
                    status="installed", installed_version="1"),
    AvailableModule(key="spy", title="Spy", summary="Gone.", version="1",
                    status="revoked", reason="sends your readings away"),
)


class _App(CommandBus):
    """Answers the page's Commands; records every one."""

    def __init__(self) -> None:
        self.sent: list[Command[Any]] = []
        self.tiles: tuple[ModuleInfo, ...] = (
            ModuleInfo(key="rgb", title="RGB", summary="Lights.", enabled=True),
            ModuleInfo(key="ticker", title="Ticker", summary="Prices.", enabled=True,
                       origin="installed"))
        self.catalog = ModuleCatalogResult(ok=True, modules=_LISTED)
        self.installed = ModuleResult(ok=True, key="weather", message="Weather 3 installed")
        self.removed = ModuleResult(ok=True, key="ticker", message="ticker removed")
        self.developer = False
        self.folder = ModuleFolderResult(ok=True, module=AvailableModule(
            key="hello", title="Hello", summary="Says hello.", version="0.1",
            status="available", asks=ModuleAsks(("events",))))
        self.from_folder = ModuleResult(
            ok=True, key="hello", message="Hello 0.1 installed unsigned -- off until "
                                          "you switch it on")

    def dispatch(self, cmd: Command[Any]) -> Any:
        self.sent.append(cmd)
        if isinstance(cmd, SetDeveloperMode):
            self.developer = cmd.on
        answers: dict[type, Result] = {
            ListModules: ModulesResult(ok=True, modules=self.tiles,
                                       developer_mode=self.developer),
            BrowseModules: self.catalog, InstallModule: self.installed,
            RemoveModule: self.removed, ModuleIcon: ModuleIconResult(ok=False, message="none"),
            SetDeveloperMode: ModuleResult(ok=True, enabled=self.developer),
            ReadModuleFolder: self.folder, InstallModuleFolder: self.from_folder}
        return answers[type(cmd)]

    @property
    def events(self) -> EventBus:
        return EventBus()

    @property
    def remote(self) -> bool:
        return False

    def of(self, kind: type) -> list[Command[Any]]:
        return [c for c in self.sent if isinstance(c, kind)]


def _switch(key: str, enabled: bool) -> SetModuleEnabled:
    return SetModuleEnabled(key=key, enabled=enabled)


def _install(key: str, asks: ModuleAsks) -> InstallModule:
    return InstallModule(key=key, asks=asks)


#: Developer mode's Commands, as both windows pass them.
_DEVELOPER: dict[str, Any] = {"developer": SetDeveloperMode,
                              "read_folder": ReadModuleFolder,
                              "install_folder": InstallModuleFolder}


@pytest.fixture
def app() -> _App:
    return _App()


@pytest.fixture
def view(qtbot: Any, app: _App) -> Iterator[Any]:
    from trcc.ui.qt_modules import ModulesView

    page = ModulesView(app, ListModules, _switch, ModuleIcon, browse=BrowseModules,
                       install=_install, remove=RemoveModule, **_DEVELOPER)
    qtbot.addWidget(page)
    yield page


def _answers(view: Any, *answers: bool) -> list[tuple[str, str, str]]:
    """The user's answers to the next questions; the questions asked."""
    asked: list[tuple[str, str, str]] = []
    queue = list(answers)

    def ask(question: str, details: str, confirm: str) -> bool:
        asked.append((question, details, confirm))
        return queue.pop(0)

    view.ask = ask
    return asked


def _browse(qtbot: Any, view: Any) -> None:
    view.open_catalog()
    qtbot.waitUntil(lambda: bool(view.actions), timeout=3000)


# ── the list ──────────────────────────────────────────────────────────────


def test_get_modules_lists_each_with_its_button(qtbot: Any, view: Any, app: _App) -> None:
    _browse(qtbot, view)
    assert app.of(BrowseModules) == [BrowseModules()]
    assert {k: (b.text(), b.isEnabled()) for k, b in view.actions.items()} == {
        "weather": ("Install", True), "ticker": ("Update", True),
        "clock": ("Installed", False), "spy": ("Taken back", False)}
    assert view.switches == {}                       # the tiles are not shown


def test_an_index_that_cannot_be_read_says_why(qtbot: Any, view: Any, app: _App) -> None:
    app.catalog = ModuleCatalogResult(ok=False, message="the module list could not be "
                                                        "read: HTTP 404")
    view.open_catalog()
    qtbot.waitUntil(lambda: view.catalog_note.endswith("404"), timeout=3000)
    assert view.catalog == () and view.actions == {}


def test_back_shows_the_tiles_again(qtbot: Any, view: Any) -> None:
    _browse(qtbot, view)
    view.close_catalog()
    assert set(view.switches) == {"rgb", "ticker"} and view.actions == {}


# ── installing ────────────────────────────────────────────────────────────


def test_an_install_asks_first_then_sends_exactly_what_was_shown(
        qtbot: Any, view: Any, app: _App) -> None:
    """The permission screen lists every capability and site; what it lists
    is what InstallModule says the user accepted."""
    _browse(qtbot, view)
    asked = _answers(view, True)
    view.actions["weather"].click()
    qtbot.waitUntil(lambda: bool(app.of(InstallModule)), timeout=3000)
    assert app.of(InstallModule) == [InstallModule(
        key="weather", asks=ModuleAsks(("events", "network"), ("api.open-meteo.com",)))]
    (question, details, confirm), = asked
    assert (question, confirm) == ("Install Weather 3?", "Install")
    assert details.splitlines() == [
        "Reviewed by TRCC -- not guaranteed safe.", "It may:",
        f"  - {MODULE_CAPABILITIES['events']}", f"  - {MODULE_CAPABILITIES['network']}",
        "  - fetches from api.open-meteo.com"]
    qtbot.waitUntil(lambda: view.actions["weather"].text() == "Installed", timeout=3000)
    assert view.catalog_note == "Weather 3 installed"
    assert len(app.of(BrowseModules)) == 1          # no second download


def test_a_declined_install_sends_nothing(qtbot: Any, view: Any, app: _App) -> None:
    _browse(qtbot, view)
    _answers(view, False)
    view.actions["weather"].click()
    assert app.of(InstallModule) == []
    assert view.actions["weather"].isEnabled()


def test_an_update_asks_with_update(qtbot: Any, view: Any, app: _App) -> None:
    _browse(qtbot, view)
    asked = _answers(view, True)
    view.actions["ticker"].click()
    qtbot.waitUntil(lambda: bool(app.of(InstallModule)), timeout=3000)
    assert asked[0][2] == "Update"


def test_a_refused_install_says_why_and_stays_installable(
        qtbot: Any, view: Any, app: _App) -> None:
    app.installed = ModuleResult(ok=False, message="greeter 2: not signed by its author")
    _browse(qtbot, view)
    _answers(view, True)
    view.actions["weather"].click()
    qtbot.waitUntil(lambda: view.catalog_note.endswith("author"), timeout=3000)
    assert view.actions["weather"].text() == "Install"


# ── removing ──────────────────────────────────────────────────────────────


def test_only_an_installed_module_has_a_remove(view: Any) -> None:
    assert set(view.removes) == {"ticker"}           # rgb is built in


def test_a_removal_asks_first(qtbot: Any, view: Any, app: _App) -> None:
    asked = _answers(view, False, True)
    view.removes["ticker"].click()
    assert app.of(RemoveModule) == []
    view.removes["ticker"].click()
    qtbot.waitUntil(lambda: bool(app.of(RemoveModule)), timeout=3000)
    assert app.of(RemoveModule) == [RemoveModule(key="ticker")]
    assert asked[0][:1] == ("Remove Ticker? Themes made with it stay.",)


# ── following other UIs ───────────────────────────────────────────────────


def test_an_update_to_a_module_this_window_loaded_asks_to_reopen(
        qtbot: Any, app: _App, monkeypatch: pytest.MonkeyPatch) -> None:
    """Python cannot unload a module's code: a window that built its tab
    from the old copy finishes the update when reopened -- and says so.  A
    module it never loaded gets no such note."""
    from trcc.ui import qt_modules

    monkeypatch.setattr(qt_modules, "face_loaded", lambda key: key == "ticker")
    page = qt_modules.ModulesView(app, ListModules, _switch, ModuleIcon,
                                  browse=BrowseModules, install=_install,
                                  remove=RemoveModule, **_DEVELOPER)
    qtbot.addWidget(page)
    page.on_event(ModuleInstalled(key="ticker", version="2"))
    page.on_event(ModuleInstalled(key="weather", version="3"))
    assert page.notes == {"ticker": REOPEN}
    page.on_event(ModuleRemoved(key="ticker"))
    assert page.notes == {}


# ── developer mode: a folder, installed unsigned ─────────────────────────


def test_the_folder_button_shows_only_in_developer_mode(view: Any, app: _App) -> None:
    """The switch asks the App; the page follows the event every UI hears."""
    assert view.developer.switch is not None and view.developer.button is None
    view.developer.switch.setChecked(True)
    assert app.of(SetDeveloperMode) == [SetDeveloperMode(on=True)]
    view.on_event(DeveloperModeChanged(on=True))
    assert view.developer.button is not None and view.developer.switch.isChecked()
    app.developer = False
    view.on_event(DeveloperModeChanged(on=False))
    assert view.developer.button is None


def test_a_folder_install_says_unsigned_then_sends_what_was_shown(
        qtbot: Any, view: Any, app: _App, tmp_path: Path) -> None:
    app.developer = True
    view.refresh()
    view.developer.choose_folder = lambda: tmp_path
    asked = _answers(view, True)
    assert view.developer.button is not None
    view.developer.button.click()
    qtbot.waitUntil(lambda: bool(app.of(InstallModuleFolder)), timeout=3000)
    assert app.of(ReadModuleFolder) == [ReadModuleFolder(path=tmp_path)]
    assert app.of(InstallModuleFolder) == [InstallModuleFolder(
        path=tmp_path, asks=ModuleAsks(("events",)))]
    (question, details, confirm), = asked
    assert (question, confirm) == ("Install Hello 0.1?", "Install")
    assert details.splitlines() == [
        "Unsigned: nobody reviewed it. Install only code you trust.", "It may:",
        f"  - {MODULE_CAPABILITIES['events']}"]
    qtbot.waitUntil(lambda: view.developer.note == app.from_folder.message, timeout=3000)


def test_a_declined_folder_install_sends_nothing(view: Any, app: _App,
                                                 tmp_path: Path) -> None:
    app.developer = True
    view.refresh()
    view.developer.choose_folder = lambda: tmp_path
    _answers(view, False)
    assert view.developer.button is not None
    view.developer.button.click()
    assert app.of(InstallModuleFolder) == []


def test_a_folder_that_cannot_be_read_says_why(view: Any, app: _App,
                                               tmp_path: Path) -> None:
    app.developer = True
    app.folder = ModuleFolderResult(ok=False, message="core/link.py: a symbolic link")
    view.refresh()
    view.developer.choose_folder = lambda: tmp_path
    asked = _answers(view)
    assert view.developer.button is not None
    view.developer.button.click()
    assert (asked, app.of(InstallModuleFolder)) == ([], [])
    assert view.developer.note == "core/link.py: a symbolic link"


def test_a_tiles_switch_speaks_the_windows_language(view: Any) -> None:
    """``On`` / ``Off`` were drawn in English whatever the window's language."""
    view.set_language("de")
    assert (view.switches["rgb"].text(), view.switches["ticker"].text()) == ("Ein", "Ein")
    view.set_language("en")
    assert view.switches["rgb"].text() == "On"
