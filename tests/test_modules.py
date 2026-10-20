"""The Module ABC: a feature switched on or off, hooks around its work.

``enable`` runs pre_enable -> _enable -> post_enable, ``disable`` runs
pre_disable -> _disable -> post_disable; ``SetModuleEnabled`` and
``ListModules`` are what every UI's Modules page sends.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.modules import MODULES, BaseModule
from trcc.app import App
from trcc.core.commands import ListModules, SetModuleEnabled
from trcc.core.errors import ModuleUnavailable
from trcc.core.events import Event, ModuleChanged
from trcc.core.ports import Module, ModuleHost

from .conftest import FakePlatform

#: Every hook call, in order, across the fakes.
CALLS: list[str] = []


class _Recording(Module):
    """Records each step; ``key`` comes from the dict the App is given."""
    title, summary = "Recording", "Records its hooks."

    def pre_enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:pre_enable")

    def _enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:enable")

    def post_enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:post_enable")

    def pre_disable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:pre_disable")

    def _disable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:disable")

    def post_disable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:post_disable")


class _On(_Recording):
    key = "on"


class _Off(_Recording):
    key, default_on = "off", False


class _Refuses(_Recording):
    """Cannot run here: its pre_enable says why."""
    key, default_on = "refuses", False

    def pre_enable(self, host: ModuleHost) -> None:
        CALLS.append(f"{self.key}:pre_enable")
        raise ModuleUnavailable("needs a thing this computer lacks")

    def problem(self) -> str:
        return "needs a thing this computer lacks"


_FAKES: dict[str, type[Module]] = {"on": _On, "off": _Off, "refuses": _Refuses}


def _app(tmp_home: Path) -> App:
    CALLS.clear()
    return App(platform=FakePlatform(tmp_home), modules=_FAKES)


def test_a_default_on_module_is_switched_on_at_start(tmp_home: Path) -> None:
    app = _app(tmp_home)
    assert (app.enabled_modules, CALLS) == (
        {"on"}, ["on:pre_enable", "on:enable", "on:post_enable"])


def test_the_hooks_run_in_order_around_the_work(tmp_home: Path) -> None:
    """MUTATION CHECK: call _disable before pre_disable in Module.disable."""
    app = _app(tmp_home)
    CALLS.clear()
    assert app.dispatch(SetModuleEnabled(key="off", enabled=True)).ok
    assert app.dispatch(SetModuleEnabled(key="off", enabled=False)).ok
    assert CALLS == ["off:pre_enable", "off:enable", "off:post_enable",
                     "off:pre_disable", "off:disable", "off:post_disable"]


def test_a_module_that_cannot_run_refuses_and_nothing_changes(
        tmp_home: Path) -> None:
    """pre_enable raising stops it before its work.  MUTATION CHECK: catch
    the refusal inside Module.enable and carry on."""
    app = _app(tmp_home)
    CALLS.clear()
    result = app.dispatch(SetModuleEnabled(key="refuses", enabled=True))
    assert (result.ok, result.message, CALLS, "refuses" in app.enabled_modules) == (
        False, "needs a thing this computer lacks", ["refuses:pre_enable"], False)
    assert app.settings.module_enabled("refuses", False) is False


def test_the_choice_survives_a_restart(tmp_home: Path) -> None:
    """Switched off and on again by hand: a new App starts that way."""
    app = _app(tmp_home)
    app.dispatch(SetModuleEnabled(key="on", enabled=False))
    app.dispatch(SetModuleEnabled(key="off", enabled=True))
    app.close()
    again = _app(tmp_home)
    assert again.enabled_modules == {"off"}


def test_every_ui_hears_a_switch_and_sees_every_module(tmp_home: Path) -> None:
    app = _app(tmp_home)
    heard: list[tuple[str, bool]] = []

    def on_module(e: Event) -> None:
        assert isinstance(e, ModuleChanged)
        heard.append((e.key, e.enabled))
    app.events.subscribe(ModuleChanged, on_module)
    app.dispatch(SetModuleEnabled(key="off", enabled=True))
    listed = {m.key: (m.enabled, m.problem)
              for m in app.dispatch(ListModules()).modules}
    assert heard == [("off", True)]
    assert listed == {"on": (True, ""), "off": (True, ""),
                      "refuses": (False, "needs a thing this computer lacks")}


def test_closing_the_app_switches_modules_off_but_keeps_the_choice(
        tmp_home: Path) -> None:
    app = _app(tmp_home)
    CALLS.clear()
    app.close()
    assert CALLS == ["on:pre_disable", "on:disable", "on:post_disable"]
    assert app.settings.module_enabled("on", False) is False  # never chosen
    assert _app(tmp_home).enabled_modules == {"on"}           # still default on


def test_an_unknown_module_is_refused(tmp_home: Path) -> None:
    result = _app(tmp_home).dispatch(SetModuleEnabled(key="nope", enabled=True))
    assert (result.ok, result.message) == (False, "no module 'nope'")


def test_a_module_registers_by_the_key_in_its_class_line() -> None:
    class _Registered(BaseModule, key="test-registered"):
        title = "Registered"

        def _enable(self, host: ModuleHost) -> None: ...

        def _disable(self, host: ModuleHost) -> None: ...

    try:
        assert (MODULES["test-registered"], _Registered.key) == (
            _Registered, "test-registered")
    finally:
        MODULES._table.pop("test-registered", None)



@pytest.fixture
def registered() -> Iterator[str]:
    """A real registered module, for the faces that build their own App."""
    class _Face(BaseModule, key="test-face"):
        title, summary, default_on = "Face", "Reached from every UI.", False

        def _enable(self, host: ModuleHost) -> None:
            CALLS.append("test-face:enable")

        def _disable(self, host: ModuleHost) -> None:
            CALLS.append("test-face:disable")

    try:
        yield _Face.key
    finally:
        MODULES._table.pop("test-face", None)


def test_the_cli_lists_and_switches(cli_runner: Any, registered: str,
                                    cli_app: App) -> None:
    """The CLI is a remote for the one App.  Here that App is the test's own
    in-process one (a stand-in platform never shares the user's daemon), so
    the module is registered before ``cli_app`` builds it -- as every real
    module is, at import."""
    from trcc.ui.cli.main import app

    listed = cli_runner.invoke(app, ["system", "modules", "list"])
    on = cli_runner.invoke(app, ["system", "modules", "on", registered])
    nope = cli_runner.invoke(app, ["system", "modules", "on", "no-such"])
    assert (listed.exit_code, on.exit_code, nope.exit_code) == (0, 0, 1)
    assert "[off] test-face        Face -- Reached from every UI." in listed.output
    assert (on.output.strip(), nope.output.strip().splitlines()[-1]) == (
        "Face on", "no module 'no-such'")


def test_the_api_lists_and_switches(fake_platform: FakePlatform,
                                    registered: str) -> None:
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.api.main import build_app

    from .conftest import loopback_client

    with loopback_client(build_app(trcc=App(platform=fake_platform,
                                            renderer=QtRenderer()))) as client:
        before = client.get("/system/modules").json()
        switched = client.post(f"/system/modules/{registered}",
                               json={"enabled": True})
        missing = client.post("/system/modules/no-such", json={"enabled": True})
    assert [(m["key"], m["enabled"]) for m in before["modules"]] == [
        (registered, False)]
    assert (switched.status_code, switched.json()["enabled"],
            missing.status_code) == (200, True, 404)


def test_a_module_reaches_the_app_only_through_its_host() -> None:
    """Hexagonal: a module is handed a ModuleHost, never the App -- a fake
    host is all it needs."""
    class _Host(ModuleHost):
        def __init__(self) -> None:
            self.published: list[object] = []

        def publish(self, event: object) -> None:
            self.published.append(event)

    class _Announces(Module):
        key = "announces"

        def _enable(self, host: ModuleHost) -> None:
            host.publish(ModuleChanged(key=self.key, enabled=True))

        def _disable(self, host: ModuleHost) -> None: ...

    host = _Host()
    _Announces().enable(host)
    assert host.published == [ModuleChanged(key="announces", enabled=True)]


# ── The Modules page, in both windows ────────────────────────────────

def _switch(key: str, enabled: bool) -> SetModuleEnabled:
    return SetModuleEnabled(key=key, enabled=enabled)


def test_the_page_lists_switches_and_follows_other_uis(
        qtbot: Any, tmp_home: Path) -> None:
    """The shared list: a tile per module, its switch sends SetModuleEnabled,
    and a switch made in another UI shows.  MUTATION CHECK: drop the
    refresh in ModulesView.on_event."""
    from trcc.ui.qt_modules import ModulesView

    app = _app(tmp_home)
    view = ModulesView(app.dispatch, ListModules, _switch)
    qtbot.addWidget(view)
    assert {k: s.isChecked() for k, s in view.switches.items()} == {
        "off": False, "on": True, "refuses": False}
    view.switches["off"].click()                        # this UI
    assert app.enabled_modules == {"on", "off"}
    app.dispatch(SetModuleEnabled(key="on", enabled=False))   # another UI
    view.on_event(ModuleChanged(key="on", enabled=False))
    assert {k: s.isChecked() for k, s in view.switches.items()} == {
        "off": True, "on": False, "refuses": False}


def test_a_refused_switch_stays_off(qtbot: Any, tmp_home: Path) -> None:
    from trcc.ui.qt_modules import ModulesView

    view = ModulesView(_app(tmp_home).dispatch, ListModules, _switch)
    qtbot.addWidget(view)
    view.switches["refuses"].click()
    assert view.switches["refuses"].isChecked() is False


def test_no_modules_says_so(qtbot: Any, tmp_home: Path) -> None:
    from PySide6.QtWidgets import QLabel

    from trcc.ui.qt_modules import NONE_YET, ModulesView

    view = ModulesView(App(platform=FakePlatform(tmp_home), modules={}).dispatch,
                       ListModules, _switch)
    qtbot.addWidget(view)
    assert [w.text() for w in view.findChildren(QLabel)] == [NONE_YET]


def test_the_control_center_bar_opens_the_modules_page(
        qtbot: Any, fake_platform: FakePlatform) -> None:
    """Control Center · Modules · About -- Modules opens its own page."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.gui.trcc_app import TRCCApp

    window = TRCCApp(app=App(platform=fake_platform, renderer=QtRenderer()))
    qtbot.addWidget(window)
    keys = [key for _w, key in window._i18n_labels]
    assert keys.index("Control Center") < keys.index("Modules") < keys.index("About")
    window._show_view("about")
    window.uc_about.modules_link.click()
    assert (window.uc_modules.isVisibleTo(window),
            window.uc_about.isVisibleTo(window)) == (True, False)
    window.close()


def test_qtgui_has_the_modules_page_above_about() -> None:
    from trcc.ui.qtgui.panels.sidebar import _ENTRIES

    keys = [entry.key for entry in _ENTRIES]
    assert keys.index("modules") == keys.index("about") - 1


def test_a_switch_turns_a_module_off_too(qtbot: Any, tmp_home: Path) -> None:
    """MUTATION CHECK: a switch that only ever sends enabled=True."""
    from trcc.ui.qt_modules import ModulesView

    app = _app(tmp_home)
    view = ModulesView(app.dispatch, ListModules, _switch)
    qtbot.addWidget(view)
    view.switches["on"].click()
    assert app.enabled_modules == set()
